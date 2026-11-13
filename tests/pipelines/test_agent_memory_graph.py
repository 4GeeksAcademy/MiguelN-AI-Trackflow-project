"""Memory in the agent graph (MEM-092): whole conversations through the real graph.

The same graph as always, with one extra self-evaluation step. The model calls
are fakes (`FakeRag` for planning and generation, a scripted classifier, a
deterministic embedder); the memory store is the real one, in the per-test
file `isolated_agent_store` sets. Covered here, end to end:

- proposal -> approval -> the memory is used in a later conversation (cycle A);
- proposal -> rejection -> memory unchanged, the record of it kept (cycle B);
- a change of topic discards; an unclear reply discards; an answer plus a new
  question resolves and continues; an edit is confirmed before it is stored;
- one pending proposal at a time; forbidden content never reaches the user or
  the store; most interactions propose nothing.
"""

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from data.process import knowledge_base
from services.agent import runner
from services.agent.graph import build_agent_graph
from services.agent.memory import classifier
from services.agent.memory import store as memory_store
from services.agent.memory.classifier import ReplyClassification
from services.agent.memory.policy import MemoryDraft
from services.agent.state import (
    CLASSIFY_MEMORY_REPLY,
    COMPOSE_RESPONSE,
    GENERATE_ANSWER,
    MEMORY_DISCARDED_TOPIC,
    MEMORY_DISCARDED_UNCLEAR,
    MEMORY_QUESTION,
    MEMORY_REJECTED,
    MEMORY_REVISED_QUESTION,
    MEMORY_SAVED,
    NO_INFORMATION_ANSWER,
    PLAN_SOURCES,
    PROPOSE_MEMORY,
    RECALL_MEMORY,
    RECEIVE_QUESTION,
    RESOLVE_MEMORY_PROPOSAL,
    RETRIEVE,
)
from services.agent.tracing import TraceStore
from tests.pipelines.rag_support import offline_rag  # noqa: F401
from tests.pipelines.test_agent_graph import ANSWER, FakeRag, install_fakes, plan, step

SEUR_CORRECTION = (
    "Actually SEUR no longer covers that rural area of Zaragoza, we've had to use the local "
    "carrier since last month."
)
SEUR_MEMORY = (
    "SEUR no longer covers rural Zaragoza; the local carrier is used there since October 2026."
)
COSMETICS_PREFERENCE = (
    "The cosmetics client always wants their monthly report with the returns breakdown first, "
    "before shipment volume."
)
COSMETICS_MEMORY = (
    "The cosmetics client wants its monthly report with the returns breakdown first, before "
    "shipment volume."
)

SEUR_DRAFT = MemoryDraft(
    category="carrier_rule",
    content=SEUR_MEMORY,
    reason="A carrier changed its coverage for a zone.",
    carrier="SEUR",
    country="ES",
)
COSMETICS_DRAFT = MemoryDraft(
    category="client_report_preference",
    content=COSMETICS_MEMORY,
    reason="A recurring B2B client's report preference.",
    client="the cosmetics client",
)

TOPICS = ("seur", "zaragoza", "rural", "cosmetic", "report", "returns")


def topic_vector(text: str) -> list[float]:
    lowered = text.lower()
    return [float(word in lowered) for word in TOPICS] + [0.05]


class ScriptedModel(FakeRag):
    """FakeRag whose generation self-evaluates per message, and a scripted classifier."""

    def __init__(self) -> None:
        super().__init__()
        self.proposals: dict[str, MemoryDraft] = {}
        self.checks: dict[str, str] = {}
        self.updates: set[str] = set()
        self.replies: dict[str, ReplyClassification] = {}
        self.user_updates: list[bool] = []

    def choose_sources(self, question: str) -> dict[str, Any]:
        self.plan_calls.append(question)
        return {**plan(step("knowledge_base")), "user_update": question in self.updates}

    def generate_response(self, question, context, *, memories=None, user_update=False):
        self.user_updates.append(user_update)
        draft = self.proposals.get(question)
        self.memory_check = "propose" if draft else self.checks.get(question, "nothing_to_remember")
        self.proposal = draft
        return super().generate_response(
            question, context, memories=memories, user_update=user_update
        )

    def classify(self, message: str, proposal: dict[str, Any]) -> ReplyClassification:
        return self.replies[message]


@pytest.fixture
def model(monkeypatch, offline_rag):  # noqa: F811
    fake = ScriptedModel()
    install_fakes(monkeypatch, fake)
    monkeypatch.setattr(classifier, "call_classifier", fake.classify)
    monkeypatch.setattr(knowledge_base, "embed", topic_vector)
    return fake


@pytest.fixture
def graph():
    return build_agent_graph(InMemorySaver())


@pytest.fixture
def traces(tmp_path) -> TraceStore:
    return TraceStore(tmp_path / "traces.sqlite")


@pytest.fixture
def memory() -> memory_store.MemoryStore:
    return memory_store.get_memory_store()


def say(message: str, graph, traces, conversation: str | None = None, user: str = "valentina"):
    return runner.run_agent(
        message, user_id=user, conversation_id=conversation, graph=graph, store=traces
    )


def approve(span: str = "yes", **extra: Any) -> ReplyClassification:
    return ReplyClassification(decision="approve", confidence=0.96, decision_span=span, **extra)


def reject(span: str = "no") -> ReplyClassification:
    return ReplyClassification(decision="reject", confidence=0.95, decision_span=span)


def snapshot(memory: memory_store.MemoryStore) -> list[tuple[str, str, str]]:
    return [(m.memory_id, m.content, m.status) for m in memory.all_memories()]


# --- Self-evaluation and proposal ------------------------------------------------------


def test_a_correction_is_proposed_in_the_response_and_nothing_is_stored(
    model, graph, traces, memory
):
    model.proposals[SEUR_CORRECTION] = SEUR_DRAFT
    model.updates.add(SEUR_CORRECTION)

    result = say(SEUR_CORRECTION, graph, traces)

    assert result.answer == f"{ANSWER}\n\n{MEMORY_QUESTION.format(content=SEUR_MEMORY)}"
    assert result.memory["pending_proposal"]["content"] == SEUR_MEMORY
    assert memory.active_memories() == []
    assert memory.pending_proposal(result.conversation_id).content == SEUR_MEMORY
    trace = traces.get_trace(result.run_id)
    assert trace.node_names() == [
        RECEIVE_QUESTION,
        RECALL_MEMORY,
        PLAN_SOURCES,
        RETRIEVE,
        GENERATE_ANSWER,
        PROPOSE_MEMORY,
        COMPOSE_RESPONSE,
    ]
    assert model.user_updates == [True]
    decision = [e for e in trace.edge_events() if e.name == "route_after_answer"][0]
    assert decision.output["decision"] == "propose"


@pytest.mark.parametrize(
    "message",
    [
        "Where's the package with tracking XJ4471?",
        "Great, that's resolved.",
        "Translate this into English for the client.",
    ],
)
def test_most_interactions_have_nothing_to_remember(model, graph, traces, memory, message):
    result = say(message, graph, traces)

    assert result.memory is None
    assert MEMORY_QUESTION.split("{", 1)[0] not in (result.answer or "")
    assert PROPOSE_MEMORY not in traces.get_trace(result.run_id).node_names()
    assert memory.decision_log() == []


def test_an_update_with_no_evidence_still_reaches_the_self_evaluation(model, graph, traces):
    model.context = []
    model.updates.add(COSMETICS_PREFERENCE)
    model.proposals[COSMETICS_PREFERENCE] = COSMETICS_DRAFT

    result = say(COSMETICS_PREFERENCE, graph, traces)

    assert result.outcome == "answered"
    assert result.memory["pending_proposal"]["category"] == "client_report_preference"
    after_retrieve = [
        e for e in traces.get_trace(result.run_id).edge_events() if e.name == "route_after_retrieve"
    ][0]
    assert after_retrieve.output["decision"] == "generate"
    assert after_retrieve.output["reason"].startswith("no evidence, but the user gives an update")


def test_without_evidence_and_without_an_update_the_answer_stays_the_fixed_one(
    model, graph, traces
):
    model.context = []

    result = say("Who won the last football World Cup?", graph, traces)

    assert result.answer == NO_INFORMATION_ANSWER
    assert model.generate_calls == []


def test_a_forbidden_proposal_never_reaches_the_user_or_the_store(model, graph, traces, memory):
    message = "Remember that the recipient on this route is at Calle Mayor 12, Zaragoza."
    model.proposals[message] = SEUR_DRAFT.model_copy(
        update={"content": "Deliver SEUR parcels for that recipient to Calle Mayor 12, Zaragoza."}
    )

    result = say(message, graph, traces)

    assert result.answer == ANSWER, "no question about a memory that may never be kept"
    assert result.memory is None
    (blocked,) = memory.decision_log()
    assert blocked.event == "blocked"
    assert blocked.content is None
    assert "Calle Mayor 12" not in (blocked.message or "")


# --- Cycle A: approved, then used in a later conversation ------------------------------


def test_cycle_a_an_approved_memory_is_used_in_a_new_conversation(model, graph, traces, memory):
    model.proposals[SEUR_CORRECTION] = SEUR_DRAFT
    proposed = say(SEUR_CORRECTION, graph, traces)
    model.replies["Yes, remember it."] = approve("Yes, remember it")

    approved = say("Yes, remember it.", graph, traces, proposed.conversation_id)

    assert approved.outcome == "memory_reply"
    assert approved.answer == MEMORY_SAVED.format(content=SEUR_MEMORY)
    assert approved.memory["decision"]["outcome"] == "approved"
    assert traces.get_trace(approved.run_id).node_names() == [
        RECEIVE_QUESTION,
        CLASSIFY_MEMORY_REPLY,
        RESOLVE_MEMORY_PROPOSAL,
        COMPOSE_RESPONSE,
    ]
    (stored,) = memory.active_memories()
    assert (stored.content, stored.approved_by) == (SEUR_MEMORY, "valentina")

    # A colleague, a new conversation: the memory is recalled and handed to generation.
    later = say("Which carrier should I use for rural Zaragoza?", graph, traces, user="carlos")

    assert later.outcome == "answered"
    (recalled,) = model.generate_memories[-1]
    assert recalled["content"] == SEUR_MEMORY
    assert recalled["memory_id"] == stored.memory_id
    events = [d.event for d in memory.decision_log()]
    assert events == ["proposed", "approved"]


# --- Cycle B: rejected, memory unchanged ------------------------------------------------


def test_cycle_b_a_rejected_proposal_leaves_memory_unchanged(model, graph, traces, memory):
    model.proposals[COSMETICS_PREFERENCE] = COSMETICS_DRAFT
    before = snapshot(memory)
    proposed = say(COSMETICS_PREFERENCE, graph, traces)
    model.replies["No, that was just this once."] = reject("No")

    rejected = say("No, that was just this once.", graph, traces, proposed.conversation_id)

    assert rejected.answer == MEMORY_REJECTED
    assert rejected.memory["decision"]["outcome"] == "rejected"
    assert snapshot(memory) == before == []
    (record,) = memory.proposals(proposed.conversation_id)
    assert (record.status, record.content) == ("rejected", COSMETICS_MEMORY)
    last = memory.decision_log()[-1]
    assert (last.event, last.message, last.label) == (
        "rejected",
        "No, that was just this once.",
        "reject",
    )

    later = say("How does the cosmetics client want its monthly report?", graph, traces)
    assert model.generate_memories[-1] == []
    assert later.memory is None


# --- The reply to a pending proposal ---------------------------------------------------


def test_changing_the_topic_discards_the_proposal_and_answers_the_new_question(
    model, graph, traces, memory
):
    model.proposals[SEUR_CORRECTION] = SEUR_DRAFT
    proposed = say(SEUR_CORRECTION, graph, traces)
    question = "What's the standard return window?"
    model.replies[question] = ReplyClassification(decision="unrelated", confidence=0.9)

    result = say(question, graph, traces, proposed.conversation_id)

    assert result.answer == f"{MEMORY_DISCARDED_TOPIC}\n\n{ANSWER}"
    assert result.memory["decision"]["outcome"] == "discarded"
    assert memory.active_memories() == []
    assert model.plan_calls[-1] == question
    assert traces.get_trace(result.run_id).node_names()[:4] == [
        RECEIVE_QUESTION,
        CLASSIFY_MEMORY_REPLY,
        RESOLVE_MEMORY_PROPOSAL,
        RECALL_MEMORY,
    ]


def test_an_unclear_reply_is_never_taken_as_approval(model, graph, traces, memory):
    model.proposals[SEUR_CORRECTION] = SEUR_DRAFT
    proposed = say(SEUR_CORRECTION, graph, traces)
    model.replies["hmm, maybe"] = ReplyClassification(
        decision="approve", confidence=0.55, decision_span="maybe"
    )

    result = say("hmm, maybe", graph, traces, proposed.conversation_id)

    assert result.answer == MEMORY_DISCARDED_UNCLEAR
    assert memory.active_memories() == []
    assert memory.pending_proposal(proposed.conversation_id) is None


def test_an_answer_and_a_new_question_in_one_message(model, graph, traces, memory):
    model.proposals[SEUR_CORRECTION] = SEUR_DRAFT
    proposed = say(SEUR_CORRECTION, graph, traces)
    message = "Yes, and what's the standard return window?"
    model.replies[message] = approve("Yes", other_request="what's the standard return window?")

    result = say(message, graph, traces, proposed.conversation_id)

    assert result.answer == f"{MEMORY_SAVED.format(content=SEUR_MEMORY)}\n\n{ANSWER}"
    assert model.plan_calls[-1] == "what's the standard return window?"
    assert len(memory.active_memories()) == 1
    assert traces.get_trace(result.run_id).question == message


def test_an_edit_is_asked_again_and_stored_only_once_confirmed(model, graph, traces, memory):
    model.proposals[COSMETICS_PREFERENCE] = COSMETICS_DRAFT
    proposed = say(COSMETICS_PREFERENCE, graph, traces)
    edited = "The cosmetics client wants returns first, then shipment volume, as a PDF."
    message = "Almost: say it also wants it as a PDF."
    model.replies[message] = ReplyClassification(
        decision="edit", confidence=0.9, decision_span="Almost", edited_content=edited
    )

    revised = say(message, graph, traces, proposed.conversation_id)

    assert revised.answer == MEMORY_REVISED_QUESTION.format(content=edited)
    assert revised.memory["pending_proposal"]["content"] == edited
    assert memory.active_memories() == []

    model.replies["yes"] = approve()
    say("yes", graph, traces, proposed.conversation_id)
    assert [m.content for m in memory.active_memories()] == [edited]


def test_no_second_proposal_while_one_is_pending(model, graph, traces, memory):
    model.proposals[COSMETICS_PREFERENCE] = COSMETICS_DRAFT
    proposed = say(COSMETICS_PREFERENCE, graph, traces)
    message = "Almost: as a PDF. Also, SEUR no longer covers rural Zaragoza."
    model.replies[message] = ReplyClassification(
        decision="edit",
        confidence=0.9,
        decision_span="Almost: as a PDF",
        edited_content="The cosmetics client wants returns first, as a PDF.",
        other_request="SEUR no longer covers rural Zaragoza.",
    )
    model.proposals["SEUR no longer covers rural Zaragoza."] = SEUR_DRAFT

    result = say(message, graph, traces, proposed.conversation_id)

    # The edit's confirmation is pending, so the SEUR proposal waits its turn.
    assert result.memory["pending_proposal"]["category"] == "client_report_preference"
    assert result.answer.endswith(
        MEMORY_REVISED_QUESTION.format(
            content="The cosmetics client wants returns first, as a PDF."
        )
    )
    assert SEUR_MEMORY not in result.answer
    suppressed = memory.decision_log()[-1]
    assert suppressed.event == "suppressed"
    assert "only one at a time" in suppressed.reason


def test_a_classifier_outage_discards_and_the_message_goes_on(
    model, graph, traces, memory, monkeypatch
):
    model.proposals[SEUR_CORRECTION] = SEUR_DRAFT
    proposed = say(SEUR_CORRECTION, graph, traces)

    def down(message: str, proposal: dict[str, Any]) -> ReplyClassification:
        raise classifier.KnowledgeBaseError("The generation model could not be reached")

    monkeypatch.setattr(classifier, "call_classifier", down)

    result = say("yes", graph, traces, proposed.conversation_id)

    assert result.memory["decision"]["outcome"] == "discarded"
    assert memory.active_memories() == []


def test_a_proposal_left_unanswered_expires_and_the_next_message_is_a_new_question(
    model, graph, traces, memory, monkeypatch
):
    model.proposals[SEUR_CORRECTION] = SEUR_DRAFT
    proposed = say(SEUR_CORRECTION, graph, traces)
    later = memory_store._now() + memory_store.PENDING_TTL * 2
    monkeypatch.setattr(memory, "_clock", lambda: later)

    result = say("yes", graph, traces, proposed.conversation_id)

    assert CLASSIFY_MEMORY_REPLY not in traces.get_trace(result.run_id).node_names()
    assert memory.active_memories() == []
    assert [d.event for d in memory.decision_log()][-1] == "expired"


def test_another_users_conversation_cannot_be_continued(model, graph, traces):
    started = say("Return window?", graph, traces, user="valentina")

    with pytest.raises(memory_store.ConversationNotFound):
        say("yes", graph, traces, started.conversation_id, user="mallory")


# --- Over HTTP -------------------------------------------------------------------------


def test_the_api_carries_the_conversation_and_the_memory(client, monkeypatch, model):
    from services.core import security
    from tests.test_agent_api import sign_in

    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)
    headers, user_id = sign_in(client, "cx@trackflow.com")
    model.proposals[SEUR_CORRECTION] = SEUR_DRAFT
    model.replies["yes"] = approve()

    first = client.post("/agent/query", json={"question": SEUR_CORRECTION}, headers=headers).json()
    assert first["memory"]["pending_proposal"]["content"] == SEUR_MEMORY

    second = client.post(
        "/agent/query",
        json={"question": "yes", "conversation_id": first["conversation_id"]},
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["memory"]["decision"]["outcome"] == "approved"

    listed = client.get("/agent/memory", headers=headers).json()["memories"]
    assert [(m["content"], m["approved_by"], m["scope"]) for m in listed] == [
        (SEUR_MEMORY, user_id, "SEUR, ES")
    ]

    other, _ = sign_in(client, "other@trackflow.com")
    stolen = client.post(
        "/agent/query",
        json={"question": "no", "conversation_id": first["conversation_id"]},
        headers=other,
    )
    assert stolen.status_code == 404
    assert stolen.json() == {"detail": "Conversation not found"}
    assert client.get("/agent/memory").status_code == 401
