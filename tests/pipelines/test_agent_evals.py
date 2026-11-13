"""Knowledge agent evals: every criterion is checked against a recorded run trace.

    npm run agent:eval        (uv run pytest tests/pipelines/test_agent_evals.py -v)

The cases are in `data/eval/agent/eval-cases.json`. `npm run agent:record` ran
each one once through the real graph (Qdrant and the 4Geeks models) and stored
the traces in `data/eval/agent/traces.json`; these tests read only that file,
so they need no service and give the same result every run.

Four kinds of criteria, all over the trace:
- routing: which sources were consulted and in what order, that the planner
  chose them itself (RAG, a tool, or both - never its fallback), which nodes
  ran and which never ran, that every transition is a recorded decision
  leading to the node that ran next, and that every decision is the one the
  graph made before the lookups moved to the MCP server
  (`routing-baseline.json`, frozen from the Part 2 recording);
- node contract: every planned source ran exactly once, before
  `generate_answer`, and generation received exactly what they produced;
- live data: each lookup ended as expected and went through the MCP server's
  read tools (the agent has no other path), the answer states the value the
  live record holds, and a failed lookup gets its fixed honest answer with no
  generation at all;
- grounding (the acceptance gate, in addition to the RAG tests): the expected
  fact is in the answer **and** in a retrieved chunk of the knowledge base, the
  business rules' wording is there, and every figure in an answer appears in
  the question, a retrieved chunk or a live record (BRIEFING section 4,
  faithfulness).
"""

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from services.agent import nodes
from services.agent.graph import graph_fingerprint
from services.agent.state import (
    ANSWER_LOOKUP_FAILED,
    ANSWER_NO_INFORMATION,
    GENERATE_ANSWER,
    INCIDENT_MANAGER,
    INVENTORY_MANAGER,
    KNOWLEDGE_BASE,
    LOOKUP_FIELDS,
    NO_INFORMATION_ANSWER,
    NODE_OF_SOURCE,
    PLAN_SOURCES,
    RECEIVE_QUESTION,
    RESULT_FIELD_OF_SOURCE,
    RETRIEVE,
    SOURCE_OF_NODE,
)
from services.agent.tools.contracts import StockLookupResult, TicketLookupResult
from services.agent.tools.passages import stock_passages, ticket_passages

ROOT_DIR = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT_DIR / "data" / "eval" / "agent" / "eval-cases.json"
TRACES_PATH = ROOT_DIR / "data" / "eval" / "agent" / "traces.json"
BASELINE_PATH = ROOT_DIR / "data" / "eval" / "agent" / "routing-baseline.json"
# The MCP read tools a lookup may use, and the inventory reads.
MCP_LOOKUP = re.compile(
    r"^MCP (get_incident|search_incidents|query_inventory)\((?:.*operation=(\w+))?.*\)$"
)
RERECORD = "re-record them with `LLM_TIMEOUT_SECONDS=120 npm run agent:record`"

CASES: list[dict[str, Any]] = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


def _cases_with(check: str) -> list[Any]:
    return [pytest.param(case, id=case["id"]) for case in CASES if check in case["checks"]]


def _cases_where(outcome: str) -> list[Any]:
    return [
        pytest.param(case, id=case["id"]) for case in CASES if case["checks"]["outcome"] == outcome
    ]


ALL_CASES = [pytest.param(case, id=case["id"]) for case in CASES]


class RecordedRun:
    """A recorded trace, read back from `traces.json`."""

    def __init__(self, trace: dict[str, Any]) -> None:
        self.trace = trace
        self.events: list[dict[str, Any]] = sorted(trace["events"], key=lambda e: e["seq"])

    @property
    def outcome(self) -> str:
        return self.trace["outcome"]

    @property
    def answer(self) -> str:
        return self.trace["answer"] or ""

    def node_names(self) -> list[str]:
        return [event["name"] for event in self.events if event["kind"] == "node"]

    def nodes(self, name: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["kind"] == "node" and e["name"] == name]

    def node(self, name: str) -> dict[str, Any]:
        (event,) = self.nodes(name)
        return event

    def context(self) -> list[dict[str, Any]]:
        """The retrieved chunks; [] when the knowledge base was not searched."""
        retrieved = self.nodes(RETRIEVE)
        return retrieved[0]["output"]["context"] if retrieved else []

    def plan(self) -> dict[str, Any]:
        return self.node(PLAN_SOURCES)["output"]["plan"]

    def sources(self) -> list[str]:
        consulted: list[str] = []
        for name in self.node_names():
            source = SOURCE_OF_NODE.get(name)
            if source is not None and source not in consulted:
                consulted.append(source)
        return consulted

    def lookup(self, source: str) -> dict[str, Any] | None:
        """The recorded result of a tool lookup, or None if it did not run."""
        ran = self.nodes(NODE_OF_SOURCE[source])
        return ran[0]["output"][RESULT_FIELD_OF_SOURCE[source]] if ran else None

    def live_passages(self) -> list[dict[str, Any]]:
        """The successful lookups as the passages generation received."""
        passages: list[dict[str, Any]] = []
        if (ticket := self.lookup(INCIDENT_MANAGER)) is not None:
            passages += ticket_passages(TicketLookupResult.model_validate(ticket))
        if (stock := self.lookup(INVENTORY_MANAGER)) is not None:
            passages += stock_passages(StockLookupResult.model_validate(stock))
        return passages


@pytest.fixture(scope="module")
def recording() -> dict[str, Any]:
    if not TRACES_PATH.exists():
        pytest.fail(f"{TRACES_PATH.relative_to(ROOT_DIR)} is missing: {RERECORD}")
    return json.loads(TRACES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def runs(recording) -> dict[str, RecordedRun]:
    return {case["id"]: RecordedRun(case["trace"]) for case in recording["cases"]}


def _normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


# --- The recording itself ------------------------------------------------------------


def test_traces_were_recorded_by_the_current_graph(recording):
    assert recording["graph_version"] == graph_fingerprint(), (
        "The traces come from another version of the graph, prompt or threshold: " + RERECORD
    )


def test_every_case_has_exactly_one_recorded_run(recording):
    recorded = {case["id"]: case["question"] for case in recording["cases"]}
    assert recorded == {case["id"]: case["question"] for case in CASES}, RERECORD


def test_every_recorded_run_completed_with_the_recorded_graph(recording, runs):
    for case_id, run in runs.items():
        assert run.trace["status"] == "completed", case_id
        assert run.trace["graph_version"] == recording["graph_version"], case_id
        assert all(event["status"] == "ok" for event in run.events), case_id


# --- Routing ---------------------------------------------------------------------------

TOOL_SOURCES = {INCIDENT_MANAGER, INVENTORY_MANAGER}


@pytest.mark.parametrize("case", _cases_with("sources"))
def test_the_trace_names_the_sources_consulted_in_order(case, runs):
    assert runs[case["id"]].sources() == case["checks"]["sources"]


@pytest.mark.parametrize("case", _cases_with("resolved_by"))
def test_the_planner_itself_chose_the_kind_of_source(case, runs):
    run, kind = runs[case["id"]], case["checks"]["resolved_by"]
    plan = run.plan()
    consulted = set(run.sources())

    assert plan["decided_by"] == "model", (
        f"the route came from the planner's fallback, not its choice: {plan['ignored']}"
    )
    if kind == "rag":
        assert consulted == {KNOWLEDGE_BASE}, "a knowledge question must not call a tool"
    elif kind == "tool":
        assert consulted and consulted <= TOOL_SOURCES, "a live-data question must not use the RAG"
    else:
        assert KNOWLEDGE_BASE in consulted and consulted & TOOL_SOURCES


@pytest.mark.parametrize("case", ALL_CASES)
def test_the_run_takes_the_expected_route(case, runs):
    run, checks = runs[case["id"]], case["checks"]

    assert run.outcome == checks["outcome"]
    assert run.node_names() == checks["nodes"]
    for node in checks.get("never", []):
        assert node not in run.node_names(), f"{node} must not run for this question"


@pytest.mark.parametrize("case", ALL_CASES)
def test_every_transition_is_a_recorded_decision(case, runs):
    run = runs[case["id"]]
    for index, event in enumerate(run.events):
        if event["kind"] != "edge":
            continue
        assert event["output"]["reason"], f"{event['name']} recorded no reason"
        following = [e["name"] for e in run.events[index + 1 :] if e["kind"] == "node"]
        if event["output"]["to"] == "__end__":
            assert following == [], f"{event['name']} ended the run, yet {following} ran"
        else:
            assert following[:1] == [event["output"]["to"]]


@pytest.mark.parametrize("case", ALL_CASES)
def test_the_routing_is_unchanged_since_the_direct_tools(case, runs):
    """Moving the lookups to the MCP server changed which node calls the tool, not
    a single routing decision: same nodes (two renamed), same decisions in order."""
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    case_id = baseline["renamed_cases"].get(case["id"], case["id"])
    assert case_id in baseline["cases"], f"{case['id']} has no baseline"
    expected = baseline["cases"][case_id]
    renamed = baseline["renamed_nodes"]
    run = runs[case["id"]]

    assert run.node_names() == [renamed.get(name, name) for name in expected["nodes"]]
    assert [
        [event["name"], event["output"]["decision"]]
        for event in run.events
        if event["kind"] == "edge"
    ] == expected["decisions"]


@pytest.mark.parametrize("case", _cases_where("rejected"))
def test_a_rejected_question_is_never_retrieved(case, runs):
    run = runs[case["id"]]
    (decision,) = [e for e in run.events if e["kind"] == "edge"]

    assert run.node_names() == [RECEIVE_QUESTION]
    assert decision["output"]["to"] == "__end__"
    assert run.answer == ""


@pytest.mark.parametrize("case", _cases_with("answer_is_no_information"))
def test_without_context_the_agent_answers_honestly_without_generating(case, runs):
    run = runs[case["id"]]

    assert run.context() == []
    assert run.nodes(GENERATE_ANSWER) == []
    assert run.answer == NO_INFORMATION_ANSWER
    assert run.node(ANSWER_NO_INFORMATION)["output"] == {"answer": NO_INFORMATION_ANSWER}


# --- Node contract -----------------------------------------------------------------------


@pytest.mark.parametrize("case", _cases_where("answered"))
def test_generation_uses_exactly_what_the_planned_sources_produced(case, runs):
    run = runs[case["id"]]
    (generated,) = run.nodes(GENERATE_ANSWER)
    received = run.node(RECEIVE_QUESTION)
    planned = [step["source"] for step in run.plan()["steps"]]

    assert run.sources() == planned, "every planned source runs, in the planned order"
    for source in planned:
        (consulted,) = run.nodes(NODE_OF_SOURCE[source])
        assert consulted["seq"] < generated["seq"], f"{source} must be consulted before generating"
    assert run.context() or run.live_passages(), "an answered run must have evidence"
    assert generated["input"].get("context") == (
        run.context() if RETRIEVE in run.node_names() else None
    )
    for source, field in LOOKUP_FIELDS:
        assert generated["input"].get(field) == run.lookup(source)
    assert generated["input"]["question"] == received["output"]["question"]
    assert generated["output"]["answer"] == run.answer


# --- Live data ----------------------------------------------------------------------------


@pytest.mark.parametrize("case", _cases_with("lookups"))
def test_each_lookup_ended_as_expected(case, runs):
    run = runs[case["id"]]
    for source, outcome in case["checks"]["lookups"].items():
        result = run.lookup(source)
        assert result is not None, f"{source} was not consulted"
        assert result["outcome"] == outcome
        match = MCP_LOOKUP.match(result["endpoint"])
        assert match, f"{result['endpoint']}: a lookup only reads, through the MCP server"
        if match.group(1) == "query_inventory":
            assert match.group(2) == "list_products"
    for source, failure in case["checks"].get("lookup_failures", {}).items():
        assert run.lookup(source)["failure"] == failure


@pytest.mark.parametrize("case", _cases_with("answer_is_lookup_fallback"))
def test_a_failed_lookup_gets_its_fixed_answer_and_nothing_is_generated(case, runs):
    run = runs[case["id"]]
    state = {field: run.lookup(source) for source, field in LOOKUP_FIELDS if run.lookup(source)}

    assert run.nodes(GENERATE_ANSWER) == [], "no model may write a status it does not have"
    assert run.answer == "\n\n".join(nodes.failure_notices(state))
    assert run.node(ANSWER_LOOKUP_FAILED)["output"] == {"answer": run.answer}


_NUMBER_WORDS = ["no|none|zero", "one", "two", "three", "four", "five", "six", "seven", "eight"]


def _live_values(run: RecordedRun, field: str) -> list[str]:
    """Regular expressions for each value of `field` the live records hold."""
    ticket = run.lookup(INCIDENT_MANAGER) or {}
    stock = run.lookup(INVENTORY_MANAGER) or {}
    if field == "status":
        return [t["status"].replace("_", "[ _-]") for t in ticket["tickets"]]
    if field == "current_stock":
        return [rf"\b{p['current_stock']}\b" for p in stock["products"]]
    if field == "total":
        total = (ticket or stock)["total"]
        words = f"|{_NUMBER_WORDS[total]}" if total < len(_NUMBER_WORDS) else ""
        return [rf"\b({total}{words})\b"]
    raise AssertionError(f"unknown live field {field!r}")


@pytest.mark.parametrize("case", _cases_with("states_live"))
def test_the_answer_states_the_live_value_the_tool_returned(case, runs):
    run = runs[case["id"]]
    for field in case["checks"]["states_live"]:
        patterns = _live_values(run, field)
        assert patterns, f"the lookup returned no {field}"
        for pattern in patterns:
            assert re.search(pattern, run.answer, re.IGNORECASE), (
                f"the answer does not state the live {field} ({pattern!r})"
            )


# --- Grounding ----------------------------------------------------------------------------


@pytest.mark.parametrize("case", _cases_with("retrieved_from"))
def test_retrieval_reaches_the_expected_document(case, runs):
    sources = {chunk["source_document"] for chunk in runs[case["id"]].context()}
    assert case["checks"]["retrieved_from"] in sources


@pytest.mark.parametrize("case", _cases_with("grounded_facts"))
def test_the_answer_states_facts_found_in_the_retrieved_chunks(case, runs):
    run = runs[case["id"]]
    answer = _normalise(run.answer)
    chunks = [_normalise(chunk["text"]) for chunk in run.context()]
    for fact in case["checks"]["grounded_facts"]:
        expected = _normalise(fact)
        assert expected in answer, f"the answer does not state {fact!r}"
        assert any(expected in chunk for chunk in chunks), (
            f"{fact!r} is in the answer but in no retrieved chunk: it is not grounded"
        )


@pytest.mark.parametrize("case", _cases_with("answer_matches"))
def test_the_answer_follows_the_business_rules(case, runs):
    answer = runs[case["id"]].answer
    for pattern in case["checks"]["answer_matches"]:
        assert re.search(pattern, answer), f"the answer does not match {pattern!r}"


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_PASSAGE_REFERENCE = re.compile(r"\[\d+\]")
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)


def figures(text: str) -> set[str]:
    """The numbers a text states, ignoring passage references and list numbering."""
    text = _LIST_MARKER.sub(" ", _PASSAGE_REFERENCE.sub(" ", text))
    return set(_NUMBER.findall(text))


def test_figures_ignores_references_and_list_numbering():
    assert figures("1. Standard: 3 to 5 business days [2]\n2) 90% on time, €1,250.50") == {
        "3",
        "5",
        "90",
        "1,250.50",
    }


def _canonical(figure: str) -> str:
    """'04' and '4' are the same figure (a time written two ways)."""
    return figure.lstrip("0") or "0" if figure.isdigit() else figure


@pytest.mark.parametrize("case", _cases_where("answered"))
def test_every_figure_in_the_answer_comes_from_the_evidence(case, runs):
    run = runs[case["id"]]
    body = re.split(r"(?im)^\s*\**sources?\**\s*:", run.answer)[0]
    evidence = [case["question"]] + [p["text"] for p in run.context() + run.live_passages()]
    evidence += [p["section"] for p in run.live_passages()]
    allowed = {_canonical(f) for text in evidence for f in figures(text)}

    invented = {f for f in figures(body) if _canonical(f) not in allowed}
    assert not invented, f"figures not found in the evidence: {sorted(invented)}"
