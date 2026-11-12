"""`scripts/record_agent_traces.py`, offline: what it records, and when it refuses to write."""

import json

import pytest

from data.process.knowledge_base import KnowledgeBaseError
from scripts import record_agent_traces
from services.agent.config import get_agent_settings
from services.agent.graph import graph_fingerprint
from services.agent.tools import incidents
from tests.pipelines.rag_support import offline_rag, pinned_rag_settings  # noqa: F401
from tests.pipelines.test_agent_graph import (
    CHUNK,
    FakeRag,
    install_fakes,
    plan,
    step,
    stock_result,
    ticket_result,
)

CASES = record_agent_traces.load_cases(record_agent_traces.DEFAULT_CASES)


@pytest.fixture
def fake_rag(monkeypatch, offline_rag):  # noqa: F811
    fake = install_fakes(monkeypatch, FakeRag())
    # The preflight lookups of ticket TRF-999999 and an unknown product.
    fake.ticket = ticket_result("not_found", reference="TRF-999999")
    fake.stock = stock_result("not_found", product="recorder preflight")
    monkeypatch.setattr(record_agent_traces, "get_rag_settings", lambda: offline_rag)
    return fake


def write_cases(tmp_path, cases):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    return path


def test_the_shipped_cases_are_valid_and_cover_every_route():
    outcomes = {case["checks"]["outcome"] for case in CASES}

    assert len(CASES) >= 3
    assert outcomes == {"answered", "no_information", "rejected"}
    assert any("grounded_facts" in case["checks"] for case in CASES)
    resolved_by = {case["checks"].get("resolved_by") for case in CASES}
    assert {"tool", "rag", "both"} <= resolved_by, "a routing eval for each kind of source"
    assert any(case.get("services") for case in CASES), "the outage fallback is recorded"


def test_records_one_trace_per_case_with_the_graph_version(fake_rag, tmp_path):
    output = tmp_path / "traces.json"

    code = record_agent_traces.main(["--output", str(output)])

    assert code == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["graph_version"] == graph_fingerprint()
    assert document["generation_model"] == "test-generation-model"
    assert document["tool_timeout_seconds"] == 4.0
    assert [case["id"] for case in document["cases"]] == [case["id"] for case in CASES]
    for recorded in document["cases"]:
        trace = recorded["trace"]
        assert trace["status"] == "completed"
        assert trace["events"][0]["name"] == "receive_question"
    last = document["cases"][-1]["trace"]["events"]
    (retrieved,) = [event for event in last if event["name"] == "retrieve"]
    assert retrieved["output"] == {"context": [CHUNK]}


def test_a_failed_run_writes_nothing(fake_rag, tmp_path):
    fake_rag.generate_error = KnowledgeBaseError("The generation model could not be reached")
    output = tmp_path / "traces.json"

    assert record_agent_traces.main(["--output", str(output)]) == 1
    assert not output.exists()


def test_unconfigured_models_exit_2_before_any_run(fake_rag, monkeypatch, tmp_path):
    unconfigured = pinned_rag_settings(generation_model=None)
    monkeypatch.setattr(record_agent_traces, "get_rag_settings", lambda: unconfigured)
    output = tmp_path / "traces.json"

    assert record_agent_traces.main(["--output", str(output)]) == 2
    assert fake_rag.retrieve_calls == []
    assert not output.exists()


@pytest.mark.parametrize(
    "cases",
    [
        [{"id": "a", "question": "q", "checks": {"outcome": "answered"}}] * 2,
        [{"id": "a", "checks": {"outcome": "answered"}}],
        [{"id": "a", "question": "q", "checks": {}}],
        [{"question": "q", "checks": {"outcome": "answered"}}],
    ],
    ids=["duplicate id", "no question", "no expected outcome", "no id"],
)
def test_an_invalid_cases_file_exits_2(fake_rag, tmp_path, cases):
    output = tmp_path / "traces.json"

    code = record_agent_traces.main(
        ["--cases", str(write_cases(tmp_path, cases)), "--output", str(output)]
    )

    assert code == 2
    assert not output.exists()


def test_a_service_the_cases_need_must_answer_before_any_run(fake_rag, tmp_path):
    fake_rag.ticket = ticket_result("unavailable", failure="unreachable")
    output = tmp_path / "traces.json"

    assert record_agent_traces.main(["--output", str(output)]) == 2
    assert fake_rag.plan_calls == [], "nothing was run"
    assert not output.exists()


def test_only_the_services_some_case_needs_are_checked():
    ticket_case = {
        "id": "t",
        "question": "q",
        "checks": {"outcome": "answered", "nodes": ["lookup_ticket"]},
    }
    outage_case = {**ticket_case, "id": "o", "services": {"incident_manager": "unreachable"}}
    rag_case = {
        "id": "r",
        "question": "q",
        "checks": {"outcome": "answered", "nodes": ["retrieve"]},
    }

    assert record_agent_traces.sources_needed([ticket_case, rag_case]) == {"incident_manager"}
    assert record_agent_traces.sources_needed([outage_case, rag_case]) == set()
    assert record_agent_traces.sources_needed(CASES) == {"incident_manager", "inventory_manager"}


def test_an_unreachable_service_is_unreachable_for_its_case_only(fake_rag, tmp_path, monkeypatch):
    seen: list[str] = []

    def lookup_ticket(request, **_):
        seen.append(get_agent_settings().incidents_api_url)
        return fake_rag.ticket

    monkeypatch.setattr(incidents, "lookup_ticket", lookup_ticket)
    fake_rag.plan = plan(step("incident_manager", reference="12"))
    case = {"id": "a", "question": "Ticket 12?", "checks": {"outcome": "no_information"}}
    cases = [
        case,
        {**case, "id": "b", "services": {"incident_manager": "unreachable"}},
        {**case, "id": "c"},
    ]
    output = tmp_path / "traces.json"

    code = record_agent_traces.main(
        ["--cases", str(write_cases(tmp_path, cases)), "--output", str(output)]
    )

    assert code == 0
    assert seen == [
        "http://incidents.invalid",
        record_agent_traces.UNREACHABLE_URL,
        "http://incidents.invalid",
    ]


def test_a_case_may_only_make_a_known_service_unreachable(fake_rag, tmp_path):
    case = {"id": "a", "question": "q", "checks": {"outcome": "answered"}}
    output = tmp_path / "traces.json"

    for services in ({"knowledge_base": "unreachable"}, {"incident_manager": "slow"}):
        path = write_cases(tmp_path, [{**case, "services": services}])
        assert record_agent_traces.main(["--cases", str(path), "--output", str(output)]) == 2
    assert not output.exists()
