"""Source planning: the model chooses the sources, the planner checks the choice."""

import json
from types import SimpleNamespace

import httpx2
import openai
import pytest

from services.agent import graph, planner
from services.agent.state import INCIDENT_MANAGER, INVENTORY_MANAGER, KNOWLEDGE_BASE
from tests.pipelines.rag_support import TEST_GENERATION_MODEL, pinned_rag_settings


def call(name, arguments=None, *, raw=None):
    """A tool call as the chat API returns it."""
    text = raw if raw is not None else json.dumps(arguments or {})
    return SimpleNamespace(type="function", function=SimpleNamespace(name=name, arguments=text))


class FakePlannerModel:
    """Answers `chat.completions.create` with fixed tool calls, or raises."""

    def __init__(self, calls=(), *, error=None):
        self.calls = list(calls)
        self.error = error
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        message = SimpleNamespace(content=None, tool_calls=self.calls or None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def model(monkeypatch):
    """Install a fake planner model; `model(calls)` sets what it answers."""
    monkeypatch.setattr(planner, "get_rag_settings", lambda: pinned_rag_settings())

    def install(calls=(), *, error=None):
        fake = FakePlannerModel(calls, error=error)
        monkeypatch.setattr(planner, "_planner_client", lambda: fake)
        return fake

    return install


def sources(plan):
    return [step["source"] for step in plan["steps"]]


# --- What the model is asked -------------------------------------------------------


def test_the_model_must_choose_among_the_three_sources_and_the_update_marker(model):
    fake = model([call("search_knowledge_base")])

    planner.choose_sources("What's the standard return window?")

    (request,) = fake.requests
    assert request["model"] == TEST_GENERATION_MODEL
    assert request["tool_choice"] == "required"
    assert request["temperature"] == 0
    assert [tool["function"]["name"] for tool in request["tools"]] == [
        "lookup_ticket",
        "lookup_stock",
        "search_knowledge_base",
        "note_team_update",
    ]
    assert request["messages"] == [
        {"role": "system", "content": planner.PLANNER_PROMPT},
        {"role": "user", "content": "What's the standard return window?"},
    ]


def test_the_tool_schemas_are_the_contracts_with_every_reference_inlined():
    tools = {tool["function"]["name"]: tool["function"] for tool in planner.tool_definitions()}
    text = json.dumps(tools)

    assert "$ref" not in text and "$defs" not in text
    ticket = tools["lookup_ticket"]["parameters"]
    assert set(ticket["properties"]) == {"reference", "status", "category", "origin", "branch"}
    status_choices = ticket["properties"]["status"]["anyOf"][0]["enum"]
    assert status_choices == ["open", "in_progress", "resolved", "discarded"]
    stock = tools["lookup_stock"]["parameters"]
    assert stock["required"] == ["product"]
    assert stock["properties"]["warehouse"]["anyOf"][0]["enum"] == ["Monterrey", "Zaragoza"]
    assert tools["search_knowledge_base"]["parameters"]["properties"] == {}
    assert all(tool["description"] for tool in tools.values())


# --- Turning the model's calls into a plan -----------------------------------------


def test_a_ticket_question_is_planned_as_a_ticket_lookup_only(model):
    model([call("lookup_ticket", {"reference": "482"})])

    plan = planner.choose_sources("What's the status of ticket 482?")

    assert plan == {
        "decided_by": "model",
        "steps": [
            {"source": INCIDENT_MANAGER, "tool": "lookup_ticket", "arguments": {"reference": "482"}}
        ],
        "ignored": [],
        "user_update": False,
    }


def test_a_policy_question_is_planned_as_a_knowledge_search_only(model):
    model([call("search_knowledge_base", {"query": "return window"})])

    plan = planner.choose_sources("What's the standard return window?")

    assert sources(plan) == [KNOWLEDGE_BASE]
    assert plan["steps"][0]["arguments"] == {}, "retrieve searches with the question itself"


def test_a_question_needing_both_runs_the_live_lookup_first(model):
    model(
        [
            call("search_knowledge_base", {"query": "lost parcel policy"}),
            call("lookup_ticket", {"reference": "TRF-000012"}),
        ]
    )

    plan = planner.choose_sources("What's the status of TRF-000012 and the lost parcel policy?")

    assert sources(plan) == [INCIDENT_MANAGER, KNOWLEDGE_BASE]


def test_a_stock_question_keeps_the_product_and_warehouse(model):
    model([call("lookup_stock", {"product": "black running shoes", "warehouse": "Monterrey"})])

    plan = planner.choose_sources("Do we have stock of the black running shoes in Monterrey?")

    assert plan["steps"] == [
        {
            "source": INVENTORY_MANAGER,
            "tool": "lookup_stock",
            "arguments": {"product": "black running shoes", "warehouse": "Monterrey"},
        }
    ]


def test_a_ticket_search_without_a_reference_keeps_its_filters(model):
    model([call("lookup_ticket", {"status": "open", "category": "lost_parcel"})])

    plan = planner.choose_sources("Which lost-parcel tickets are still open?")

    assert plan["steps"][0]["arguments"] == {"status": "open", "category": "lost_parcel"}


# --- What the planner refuses ------------------------------------------------------


def test_a_reference_the_question_does_not_contain_is_never_looked_up(model):
    model([call("lookup_ticket", {"reference": "TRF-000099"})])

    plan = planner.choose_sources("What's the status of my ticket?")

    assert plan["decided_by"] == "fallback"
    assert sources(plan) == [KNOWLEDGE_BASE]
    assert "reference 'TRF-000099' does not appear in the question" in plan["ignored"][0]


def test_an_invalid_call_is_dropped_and_the_valid_ones_kept(model):
    model(
        [
            call("lookup_ticket", raw="{not json"),
            call("lookup_stock", {"product": "shoes", "warehouse": "Los Angeles"}),
            call("delete_ticket", {"reference": "12"}),
            call("search_knowledge_base"),
        ]
    )

    plan = planner.choose_sources("Ticket 12, stock of shoes in LA, and the returns policy?")

    assert plan["decided_by"] == "model"
    assert sources(plan) == [KNOWLEDGE_BASE]
    assert plan["ignored"][0] == "lookup_ticket: arguments are not valid JSON"
    assert plan["ignored"][1].startswith("lookup_stock: warehouse: Input should be")
    assert plan["ignored"][2] == "unknown tool 'delete_ticket'"


def test_a_second_call_to_the_same_tool_is_ignored(model):
    model(
        [
            call("lookup_ticket", {"reference": "12"}),
            call("lookup_ticket", {"reference": "13"}),
        ]
    )

    plan = planner.choose_sources("Status of tickets 12 and 13?")

    assert plan["steps"][0]["arguments"] == {"reference": "12"}
    assert plan["ignored"] == ["lookup_ticket: called again; only the first call is used"]


def test_no_usable_call_falls_back_to_the_knowledge_base(model):
    model([])

    plan = planner.choose_sources("Hello?")

    assert plan["decided_by"] == "fallback"
    assert sources(plan) == [KNOWLEDGE_BASE]
    assert plan["ignored"] == ["no usable source was chosen"]


@pytest.mark.parametrize(
    "error",
    [
        openai.APITimeoutError(request=httpx2.Request("POST", "http://llm.invalid/v1")),
        openai.APIConnectionError(request=httpx2.Request("POST", "http://llm.invalid/v1")),
    ],
    ids=["timeout", "connection"],
)
def test_an_unreachable_model_falls_back_without_raising(model, error):
    model(error=error)

    plan = planner.choose_sources("What's the status of ticket 12?")

    assert plan["decided_by"] == "fallback"
    assert sources(plan) == [KNOWLEDGE_BASE]
    assert plan["ignored"] == [f"the planner model is unavailable ({type(error).__name__})"]


def test_an_unconfigured_model_falls_back_without_raising(monkeypatch):
    monkeypatch.setattr(planner, "get_rag_settings", lambda: pinned_rag_settings(llm_api_key=None))

    plan = planner.choose_sources("What's the status of ticket 12?")

    assert plan["ignored"] == ["the planner model is unavailable (RagConfigurationError)"]


@pytest.mark.parametrize(
    ("question", "reference", "mentioned"),
    [
        ("What's the status of ticket 482?", "482", True),
        ("What's the status of ticket 482?", "TRF-000482", True),
        ("Status of TRF-000012 please", "12", True),
        ("Status of trf 12?", "TRF-000012", True),
        ("What's the status of ticket 482?", "TRF-000483", False),
        ("What's the status of my ticket?", "1", False),
        (
            "Incident d1b51aae-447e-4f24-bcbe-c14f3bc8cba0?",
            "d1b51aae-447e-4f24-bcbe-c14f3bc8cba0",
            True,
        ),
        ("Incident d1b51aae?", "d1b51aae-447e-4f24-bcbe-c14f3bc8cba0", False),
    ],
)
def test_a_reference_counts_as_mentioned_in_any_written_form(question, reference, mentioned):
    assert planner.mentions_reference(question, reference) is mentioned


# --- Freshness ---------------------------------------------------------------------


def test_the_fingerprint_covers_the_planner_prompt_and_tool_schemas(monkeypatch):
    original = graph.graph_fingerprint()
    prompt = planner.PLANNER_PROMPT

    monkeypatch.setattr(planner, "PLANNER_PROMPT", prompt + " Be brief.")
    reworded = graph.graph_fingerprint()
    monkeypatch.setattr(planner, "PLANNER_PROMPT", prompt)
    monkeypatch.setattr(planner, "tool_definitions", lambda: [])
    without_tools = graph.graph_fingerprint()

    assert len({original, reworded, without_tools}) == 3


def test_the_fingerprint_ignores_deployment_settings(monkeypatch):
    original = graph.graph_fingerprint()
    monkeypatch.setenv("INCIDENTS_API_URL", "https://elsewhere.example.test")
    monkeypatch.setenv("AGENT_TOOL_TIMEOUT_SECONDS", "2")
    graph.get_agent_settings.cache_clear()

    assert graph.graph_fingerprint() == original


# --- The update marker (MEM-092) ------------------------------------------------------


def test_a_team_update_is_marked_without_becoming_a_source():
    plan = planner.plan_from_calls(
        "Actually SEUR no longer covers that rural area of Zaragoza.",
        [call("note_team_update"), call("search_knowledge_base")],
    )

    assert plan.user_update is True
    assert plan.decided_by == "model"
    assert [step.source for step in plan.steps] == ["knowledge_base"]
    assert plan.ignored == []


def test_a_team_update_alone_still_searches_the_knowledge_base_and_says_why():
    plan = planner.plan_from_calls(
        "The cosmetics client always wants the returns breakdown first.",
        [call("note_team_update")],
    )

    assert plan.user_update is True
    assert plan.decided_by == "fallback"
    assert [step.source for step in plan.steps] == ["knowledge_base"]
    assert plan.ignored == [
        "only note_team_update was called: the knowledge base is searched for what it "
        "documents on the subject"
    ]


def test_a_question_is_not_an_update():
    plan = planner.plan_from_calls("What's the return window?", [call("search_knowledge_base")])

    assert plan.user_update is False
