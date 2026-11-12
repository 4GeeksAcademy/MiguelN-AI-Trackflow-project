"""Knowledge agent evals: every criterion is checked against a recorded run trace.

    npm run agent:eval        (uv run pytest tests/pipelines/test_agent_evals.py -v)

The cases are in `data/eval/agent/eval-cases.json`. `npm run agent:record` ran
each one once through the real graph (Qdrant and the 4Geeks models) and stored
the traces in `data/eval/agent/traces.json`; these tests read only that file,
so they need no service and give the same result every run.

Three kinds of criteria, all over the trace:
- routing: which nodes ran, in what order, which never ran, and that every
  transition is a recorded decision leading to the node that ran next;
- node contract: `retrieve` ran exactly once, before `generate_answer`, and the
  generation step received exactly the context `retrieve` produced;
- grounding (the acceptance gate, in addition to the RAG tests): the expected
  fact is in the answer **and** in a retrieved chunk of the knowledge base, the
  business rules' wording is there, and every figure in an answer appears in
  the retrieved chunks (BRIEFING section 4, faithfulness).
"""

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from services.agent.graph import graph_fingerprint
from services.agent.state import (
    ANSWER_NO_INFORMATION,
    GENERATE_ANSWER,
    NO_INFORMATION_ANSWER,
    RECEIVE_QUESTION,
    RETRIEVE,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT_DIR / "data" / "eval" / "agent" / "eval-cases.json"
TRACES_PATH = ROOT_DIR / "data" / "eval" / "agent" / "traces.json"
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
        return self.node(RETRIEVE)["output"]["context"]


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
def test_generation_uses_exactly_the_context_retrieve_produced(case, runs):
    run = runs[case["id"]]
    (retrieved,) = run.nodes(RETRIEVE)
    (generated,) = run.nodes(GENERATE_ANSWER)
    received = run.node(RECEIVE_QUESTION)

    assert retrieved["seq"] < generated["seq"], "retrieve must run before generate_answer"
    assert run.context(), "an answered run must have retrieved context"
    assert generated["input"]["context"] == run.context()
    assert generated["input"]["question"] == received["output"]["question"]
    assert generated["output"]["answer"] == run.answer


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


@pytest.mark.parametrize("case", _cases_where("answered"))
def test_every_figure_in_the_answer_comes_from_the_retrieved_chunks(case, runs):
    run = runs[case["id"]]
    body = re.split(r"(?im)^\s*\**sources?\**\s*:", run.answer)[0]
    allowed = figures(case["question"]).union(*(figures(c["text"]) for c in run.context()))

    invented = figures(body) - allowed
    assert not invented, f"figures not found in the retrieved chunks: {sorted(invented)}"
