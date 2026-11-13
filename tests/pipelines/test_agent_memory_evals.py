"""Memory evals (MEM-092): every criterion checked against recorded conversations.

    npm run agent:memory:eval   (uv run pytest tests/pipelines/test_agent_memory_evals.py -v)

`npm run agent:record -- --memory` ran each case of
`data/eval/agent/memory-cases.json` once through the real agent - the real
models, Qdrant, the real memory store (a fresh one per case) - and stored every
turn, the memory after it and the case's decision log in
`data/eval/agent/memory-traces.json`. These tests read only that file.

Criteria:
- self-evaluation: the briefing's three memorable examples are proposed, in
  the right category; its three counter-examples, and every kind of forbidden
  content, are not;
- proposal and confirmation: a proposal is asked inside the response with its
  exact text and never written on its own; the next message's decision is the
  expected one (approved, rejected, revised, discarded); one pending proposal at
  a time; an answer plus a question is resolved, then answered;
- auditable log: every proposal and decision is logged with what was proposed,
  the decision, the originating message and when; approvals name the user;
  forbidden content never reaches the log;
- evidence: cycle A (approved, then reflected in a new conversation) and cycle
  B (rejected, memory unchanged).
"""

import json
from pathlib import Path
from typing import Any

import pytest

from services.agent.graph import graph_fingerprint
from services.agent.memory import policy
from services.agent.state import (
    CLASSIFY_MEMORY_REPLY,
    GENERATE_ANSWER,
    MEMORY_QUESTION,
    PLAN_SOURCES,
    PROPOSE_MEMORY,
    RECALL_MEMORY,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT_DIR / "data" / "eval" / "agent" / "memory-cases.json"
TRACES_PATH = ROOT_DIR / "data" / "eval" / "agent" / "memory-traces.json"
RERECORD = "re-record them with `LLM_TIMEOUT_SECONDS=120 npm run agent:record -- --memory`"

CASES: list[dict[str, Any]] = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
TURNS = [
    pytest.param(case["id"], index, turn, id=f"{case['id']}#{index + 1}")
    for case in CASES
    for index, turn in enumerate(case["turns"])
]


def _turns_with(check: str) -> list[Any]:
    return [
        pytest.param(case["id"], index, turn, id=f"{case['id']}#{index + 1}")
        for case in CASES
        for index, turn in enumerate(case["turns"])
        if check in turn["checks"]
    ]


@pytest.fixture(scope="module")
def recording() -> dict[str, Any]:
    if not TRACES_PATH.exists():
        pytest.fail(f"{TRACES_PATH.relative_to(ROOT_DIR)} is missing: {RERECORD}")
    return json.loads(TRACES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cases(recording) -> dict[str, dict[str, Any]]:
    return {case["id"]: case for case in recording["cases"]}


def nodes(turn: dict[str, Any]) -> list[str]:
    return [
        e["name"]
        for e in sorted(turn["trace"]["events"], key=lambda e: e["seq"])
        if e["kind"] == "node"
    ]


def node_output(turn: dict[str, Any], name: str) -> dict[str, Any] | None:
    found = [e for e in turn["trace"]["events"] if e["kind"] == "node" and e["name"] == name]
    return found[0]["output"] if found else None


def recorded(cases, case_id: str, index: int) -> dict[str, Any]:
    return cases[case_id]["turns"][index]


# --- The recording -------------------------------------------------------------------


def test_the_conversations_were_recorded_by_the_current_graph(recording):
    assert recording["graph_version"] == graph_fingerprint(), (
        "The memory traces come from another version of the graph, prompt or policy: " + RERECORD
    )


def test_every_case_and_turn_was_recorded(cases):
    assert {case["id"]: [t["message"] for t in case["turns"]] for case in CASES} == {
        case_id: [t["message"] for t in case["turns"]] for case_id, case in cases.items()
    }, RERECORD
    for case in cases.values():
        for turn in case["turns"]:
            assert turn["trace"]["status"] == "completed", case["id"]


# --- Self-evaluation -----------------------------------------------------------------


@pytest.mark.parametrize(("case_id", "index", "turn"), _turns_with("self_evaluation"))
def test_the_self_evaluation_proposes_only_what_is_memorable(cases, case_id, index, turn):
    run = recorded(cases, case_id, index)
    expected = turn["checks"]["self_evaluation"]
    candidate = (node_output(run, GENERATE_ANSWER) or {}).get("memory_candidate")
    proposal = node_output(run, PROPOSE_MEMORY)

    if expected == "propose":
        assert candidate is not None, "the message never reached the self-evaluation"
        assert candidate["memory_check"] == "propose", candidate["reason"]
        if "category" in turn["checks"]:
            assert candidate["proposal"]["category"] == turn["checks"]["category"]
    else:
        # No generation at all (a fixed answer) is also "nothing proposed".
        assert (
            candidate is None
            or candidate["memory_check"] != "propose"
            or (proposal and proposal["memory_proposal"]["status"] == "blocked")
        ), candidate
        assert run["pending_after"] is None


@pytest.mark.parametrize(("case_id", "index", "turn"), _turns_with("proposal"))
def test_a_proposal_is_asked_in_the_response_and_never_written_on_its_own(
    cases, case_id, index, turn
):
    run = recorded(cases, case_id, index)
    expected = turn["checks"]["proposal"]

    if expected == "pending":
        pending = run["pending_after"]
        assert pending is not None
        assert pending["content"] in run["answer"], "the user sees the exact text"
        assert run["answer"].rstrip().endswith("Reply yes, no, or tell me what to change.")
        assert run["memory"]["pending_proposal"]["proposal_id"] == pending["proposal_id"]
        if "pending_category" in turn["checks"]:
            assert pending["category"] == turn["checks"]["pending_category"]
    else:
        assert run["pending_after"] is None
        assert MEMORY_QUESTION.split('"', 1)[0] not in (run["answer"] or "")


@pytest.mark.parametrize(("case_id", "index", "turn"), _turns_with("active_memories"))
def test_memory_holds_only_what_was_approved(cases, case_id, index, turn):
    run = recorded(cases, case_id, index)
    assert len(run["active_after"]) == turn["checks"]["active_memories"]


# --- Confirmation ----------------------------------------------------------------------


@pytest.mark.parametrize(("case_id", "index", "turn"), _turns_with("decision"))
def test_the_reply_is_classified_and_decided_explicitly(cases, case_id, index, turn):
    run = recorded(cases, case_id, index)
    reply = node_output(run, CLASSIFY_MEMORY_REPLY)

    assert reply is not None, "a pending proposal is evaluated before anything else"
    assert nodes(run)[1] == CLASSIFY_MEMORY_REPLY
    assert reply["memory_reply"]["classification"] is not None, "an explicit label was given"
    assert run["memory"]["decision"]["outcome"] == turn["checks"]["decision"], reply


@pytest.mark.parametrize(("case_id", "index", "turn"), _turns_with("outcome"))
def test_the_turn_ends_as_expected(cases, case_id, index, turn):
    assert recorded(cases, case_id, index)["outcome"] == turn["checks"]["outcome"]


@pytest.mark.parametrize(("case_id", "index", "turn"), _turns_with("planned"))
def test_after_the_decision_the_conversation_goes_on(cases, case_id, index, turn):
    run = recorded(cases, case_id, index)
    assert PLAN_SOURCES in nodes(run)
    assert node_output(run, CLASSIFY_MEMORY_REPLY)["question"], "the rest of the message goes on"


@pytest.mark.parametrize(("case_id", "index", "turn"), _turns_with("recalled"))
def test_a_later_conversation_recalls_what_was_approved_and_nothing_else(
    cases, case_id, index, turn
):
    run = recorded(cases, case_id, index)
    recall = node_output(run, RECALL_MEMORY)["memory_recall"]
    assert len(recall["items"]) == turn["checks"]["recalled"]
    if turn["checks"]["recalled"]:
        memory_ids = {m["memory_id"] for m in run["active_after"]}
        assert {item["memory_id"] for item in recall["items"]} <= memory_ids


@pytest.mark.parametrize(("case_id", "index", "turn"), _turns_with("answer_mentions_any"))
def test_the_answer_reflects_what_it_should(cases, case_id, index, turn):
    answer = recorded(cases, case_id, index)["answer"].casefold()
    assert any(phrase.casefold() in answer for phrase in turn["checks"]["answer_mentions_any"])


@pytest.mark.parametrize(("case_id", "index", "turn"), _turns_with("memory_mentions_any"))
def test_the_stored_memory_is_the_confirmed_text(cases, case_id, index, turn):
    run = recorded(cases, case_id, index)
    (memory,) = run["active_after"]
    assert any(
        p.casefold() in memory["content"].casefold() for p in turn["checks"]["memory_mentions_any"]
    )


_SPANISH = {"el", "la", "los", "las", "que", "del", "una", "por", "para", "con", "desde", "tú"}
_ENGLISH = {"the", "and", "of", "to", "is", "for", "with", "that", "you", "from", "since"}


def _words(text: str) -> list[str]:
    return [w.strip(".,;:!?\"'()").casefold() for w in text.split()]


@pytest.mark.parametrize(("case_id", "index", "turn"), TURNS)
def test_an_english_message_gets_an_english_answer_and_memory(cases, case_id, index, turn):
    """The eval messages are English: so are the answers, and every memory is English."""
    run = recorded(cases, case_id, index)
    words = _words(run["answer"] or "")
    assert sum(w in _SPANISH for w in words) <= sum(w in _ENGLISH for w in words), run["answer"]
    for memory in run["active_after"] + ([run["pending_after"]] if run["pending_after"] else []):
        text = _words(memory["content"])
        assert sum(w in _ENGLISH for w in text) >= sum(w in _SPANISH for w in text), memory


# --- The log and the invariants, per case ----------------------------------------------


@pytest.mark.parametrize("case_id", [case["id"] for case in CASES])
def test_every_decision_is_logged_completely(cases, case_id):
    case = cases[case_id]
    log = case["decision_log"]
    for row in log:
        assert row["reason"] and row["decided_at"], row
    for proposal in case["proposals"]:
        events = [row["event"] for row in log if row["proposal_id"] == proposal["proposal_id"]]
        assert events[0] == "proposed", "the proposal itself is logged"
        if proposal["status"] != "pending":
            assert len(events) >= 2, f"{proposal['status']} must be logged"
        proposed = next(r for r in log if r["proposal_id"] == proposal["proposal_id"])
        assert proposed["content"] == proposal["content"] and proposed["message"]
    for row in log:
        if row["event"] == "approved":
            assert row["user_id"] == case["user"], "an approval names who authorized it"
            assert row["memory_id"] and row["message"]
    for memory in case["memories"]:
        approvals = [
            r for r in log if r["event"] == "approved" and r["memory_id"] == memory["memory_id"]
        ]
        assert len(approvals) == 1, "a memory exists only through one logged approval"


@pytest.mark.parametrize("case_id", [case["id"] for case in CASES])
def test_never_two_pending_proposals_in_a_conversation(cases, case_id):
    pending = [p for p in cases[case_id]["proposals"] if p["status"] == "pending"]
    conversations = [p["conversation_id"] for p in pending]
    assert len(conversations) == len(set(conversations))


@pytest.mark.parametrize("case_id", [case["id"] for case in CASES])
def test_no_forbidden_content_is_ever_stored(cases, case_id):
    case = cases[case_id]
    for memory in case["memories"]:
        assert policy.forbidden_reasons(memory["content"]) == [], memory["content"]
    for proposal in case["proposals"]:
        assert policy.forbidden_reasons(proposal["content"]) == [], proposal["content"]


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(c, id=c["id"])
        for c in CASES
        if any("never_logged" in t["checks"] for t in c["turns"])
    ],
)
def test_forbidden_details_never_reach_the_log(cases, case):
    text = json.dumps(cases[case["id"]]["decision_log"]) + json.dumps(
        cases[case["id"]]["proposals"]
    )
    for turn in case["turns"]:
        for detail in turn["checks"].get("never_logged", []):
            assert detail not in text


# --- Evidence: the two complete cycles ---------------------------------------------------


def test_cycle_a_an_approved_memory_is_reflected_in_a_future_interaction(cases):
    case = cases["cycle-a-approved-and-reflected"]
    proposed, approved, later = case["turns"]

    assert proposed["pending_after"] is not None
    assert approved["memory"]["decision"]["outcome"] == "approved"
    (memory,) = case["memories"]
    assert memory["content"] == proposed["pending_after"]["content"]
    assert later["conversation"] != proposed["conversation"], "a new conversation"
    recalled = node_output(later, RECALL_MEMORY)["memory_recall"]["items"]
    assert [item["memory_id"] for item in recalled] == [memory["memory_id"]]


def test_cycle_b_a_rejected_memory_leaves_the_store_unchanged(cases):
    case = cases["cycle-b-rejected-memory-unchanged"]
    proposed, rejected, later = case["turns"]

    assert proposed["active_after"] == rejected["active_after"] == later["active_after"] == []
    assert case["memories"] == []
    (proposal,) = case["proposals"]
    assert proposal["status"] == "rejected", "the record that it was proposed and rejected stays"
    assert [r["event"] for r in case["decision_log"]] == ["proposed", "rejected"]
    assert node_output(later, RECALL_MEMORY)["memory_recall"]["items"] == []
