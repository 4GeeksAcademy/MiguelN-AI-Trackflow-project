"""`scripts/record_agent_traces.py`, offline: what it records, and when it refuses to write."""

import json

import pytest

from data.pipelines import rag
from data.process.knowledge_base import KnowledgeBaseError
from scripts import record_agent_traces
from services.agent.graph import graph_fingerprint
from tests.pipelines.rag_support import offline_rag, pinned_rag_settings  # noqa: F401
from tests.pipelines.test_agent_graph import CHUNK, FakeRag

CASES = record_agent_traces.load_cases(record_agent_traces.DEFAULT_CASES)


@pytest.fixture
def fake_rag(monkeypatch, offline_rag):  # noqa: F811
    fake = FakeRag()
    monkeypatch.setattr(rag, "retrieve", fake.retrieve)
    monkeypatch.setattr(rag, "generate_answer", fake.generate_answer)
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


def test_records_one_trace_per_case_with_the_graph_version(fake_rag, tmp_path):
    output = tmp_path / "traces.json"

    code = record_agent_traces.main(["--output", str(output)])

    assert code == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["graph_version"] == graph_fingerprint()
    assert document["generation_model"] == "test-generation-model"
    assert [case["id"] for case in document["cases"]] == [case["id"] for case in CASES]
    for recorded in document["cases"]:
        trace = recorded["trace"]
        assert trace["status"] == "completed"
        assert trace["events"][0]["name"] == "receive_question"
    retrieved = document["cases"][-1]["trace"]["events"][2]
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
