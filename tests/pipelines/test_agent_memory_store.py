"""The memory store (MEM-092): its explicit interface, the rules the database itself
enforces, the append-only decision log, consolidation, expiry and the caps.

Every test uses a store in `tmp_path`, a fixed clock it can move, and a
deterministic embedder - no model, no Qdrant.
"""

import ast
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from services.agent.memory import policy
from services.agent.memory.policy import MemoryDraft
from services.agent.memory.store import (
    ConversationNotFound,
    MemoryStore,
    ReplyDecision,
    cosine,
)

AGENT_DIR = Path(__file__).resolve().parents[2] / "services" / "agent"
START = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)

# Words the fake embedder turns into dimensions: texts sharing them are similar.
TOPICS = ("seur", "zaragoza", "rural", "cosmetic", "report", "strike", "port", "mrw", "huesca")


def topic_vector(text: str) -> list[float]:
    lowered = text.lower()
    return [float(word in lowered) for word in TOPICS] + [0.05]


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path, clock) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.sqlite", embedder=topic_vector, clock=clock)


def carrier_draft(**overrides: Any) -> MemoryDraft:
    values = {
        "category": "carrier_rule",
        "content": "SEUR no longer covers rural Zaragoza; the local carrier is used there.",
        "reason": "A carrier changed its coverage for a zone.",
        "carrier": "SEUR",
        "country": "ES",
        **overrides,
    }
    return MemoryDraft.model_validate(values)


def report_draft(**overrides: Any) -> MemoryDraft:
    values = {
        "category": "client_report_preference",
        "content": "The cosmetics client wants the returns breakdown first in its monthly report.",
        "reason": "A recurring B2B client's report preference.",
        "client": "Glow Cosmetics",
        **overrides,
    }
    return MemoryDraft.model_validate(values)


def propose(store: MemoryStore, draft: MemoryDraft, conversation: str, user: str = "valentina"):
    return store.open_proposal(
        draft, conversation_id=conversation, run_id="run-1", user_id=user, message=draft.content
    )


def approve(store: MemoryStore, proposal_id: str, user: str = "valentina"):
    return store.resolve_proposal(
        proposal_id,
        ReplyDecision("approve", "the user approved it"),
        user_id=user,
        run_id="run-2",
        message="yes",
    )


def remember(store: MemoryStore, draft: MemoryDraft, user: str = "valentina") -> str:
    conversation = store.ensure_conversation(None, user)
    proposal = propose(store, draft, conversation, user).proposal
    assert proposal is not None
    resolution = approve(store, proposal.proposal_id, user)
    assert resolution.memory_id is not None
    return resolution.memory_id


def events(store: MemoryStore, conversation: str | None = None) -> list[str]:
    return [decision.event for decision in store.decision_log(conversation_id=conversation)]


# --- Conversations -------------------------------------------------------------------


def test_a_conversation_belongs_to_the_user_who_started_it(store):
    conversation = store.ensure_conversation(None, "valentina")

    assert store.ensure_conversation(conversation, "valentina") == conversation
    with pytest.raises(ConversationNotFound):
        store.ensure_conversation(conversation, "someone-else")
    with pytest.raises(ConversationNotFound):
        store.ensure_conversation(str(uuid.uuid4()), "valentina")


# --- Proposals -----------------------------------------------------------------------


def test_a_proposal_is_offered_never_written_as_a_memory(store):
    conversation = store.ensure_conversation(None, "valentina")

    result = propose(store, carrier_draft(), conversation)

    assert result.status == "pending"
    assert result.proposal.topic_key == "carrier_rule|seur|ES"
    assert store.pending_proposal(conversation) == result.proposal
    assert store.active_memories() == []
    assert events(store) == ["proposed"]


def test_only_one_proposal_can_be_pending_in_a_conversation(store):
    conversation = store.ensure_conversation(None, "valentina")
    first = propose(store, carrier_draft(), conversation).proposal

    second = propose(store, report_draft(), conversation)

    assert second.status == "suppressed"
    assert first.proposal_id in second.reason
    assert store.pending_proposal(conversation) == first
    assert events(store) == ["proposed", "suppressed"]


def test_the_database_itself_refuses_a_second_pending_proposal(store):
    conversation = store.ensure_conversation(None, "valentina")
    pending = propose(store, carrier_draft(), conversation).proposal
    connection = sqlite3.connect(store.path)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO memory_proposals (proposal_id, conversation_id, originating_message, "
            "category, topic_key, content, reason, valid_until, status, proposed_at) "
            "SELECT 'other', conversation_id, originating_message, category, topic_key, "
            "content, reason, valid_until, 'pending', proposed_at FROM memory_proposals "
            "WHERE proposal_id = ?",
            (pending.proposal_id,),
        )


def test_a_forbidden_draft_is_blocked_and_its_content_never_logged(store):
    conversation = store.ensure_conversation(None, "valentina")
    forbidden = carrier_draft(content="Deliver SEUR parcels for this recipient to Calle Mayor 12")

    result = store.open_proposal(
        forbidden,
        conversation_id=conversation,
        run_id="run-1",
        user_id="valentina",
        message="Remember: the recipient is at Calle Mayor 12, Zaragoza",
    )

    assert result.status == "blocked"
    assert result.refusal == ("b2c_location",)
    assert store.pending_proposal(conversation) is None
    (logged,) = store.decision_log()
    assert logged.event == "blocked"
    assert logged.content is None
    assert "Calle Mayor 12" not in (logged.message or "")


def test_a_duplicate_of_an_active_memory_is_suppressed(store):
    remember(store, carrier_draft())
    conversation = store.ensure_conversation(None, "carlos")

    result = propose(
        store,
        carrier_draft(
            content="seur NO LONGER covers rural Zaragoza;  the local carrier is used there."
        ),
        conversation,
    )

    assert result.status == "suppressed"
    assert result.reason.startswith("already remembered")


def test_a_new_fact_on_the_same_key_is_offered_as_an_update_showing_what_it_replaces(store):
    old = remember(store, carrier_draft())
    conversation = store.ensure_conversation(None, "carlos")

    result = propose(
        store,
        carrier_draft(
            content="SEUR no longer covers rural Zaragoza or Huesca; use the local carrier."
        ),
        conversation,
        "carlos",
    )

    assert result.status == "pending"
    assert result.proposal.replaces_memory_id == old
    assert result.proposal.replaces_content == carrier_draft().content


# --- Resolving a proposal ------------------------------------------------------------


def test_approval_writes_the_memory_with_who_authorized_it(store, clock):
    conversation = store.ensure_conversation(None, "valentina")
    proposal = propose(store, carrier_draft(), conversation).proposal

    resolution = approve(store, proposal.proposal_id)

    (memory,) = store.active_memories()
    assert resolution.outcome == "approved"
    assert memory.memory_id == resolution.memory_id
    assert memory.approved_by == "valentina"
    assert memory.source_proposal_id == proposal.proposal_id
    assert memory.content == proposal.content
    assert memory.valid_until == (START + timedelta(days=180)).isoformat(timespec="milliseconds")
    assert store.pending_proposal(conversation) is None
    approved = store.decision_log()[-1]
    assert (approved.event, approved.user_id, approved.memory_id) == (
        "approved",
        "valentina",
        memory.memory_id,
    )


def test_a_rejected_proposal_leaves_memory_unchanged_but_its_record_stays(store):
    before = remember(store, report_draft())
    conversation = store.ensure_conversation(None, "valentina")
    proposal = propose(store, carrier_draft(), conversation).proposal

    resolution = store.resolve_proposal(
        proposal.proposal_id,
        ReplyDecision("reject", 'the user rejected it ("no")', label="reject", confidence=0.97),
        user_id="valentina",
        run_id="run-2",
        message="No, don't save that",
    )

    assert resolution.outcome == "rejected"
    assert [m.memory_id for m in store.active_memories()] == [before]
    (record,) = store.proposals(conversation)
    assert record.status == "rejected"
    rejected = store.decision_log(conversation_id=conversation)[-1]
    assert rejected.event == "rejected"
    assert rejected.content == proposal.content  # what was proposed
    assert rejected.message == "No, don't save that"  # what decided it
    assert (rejected.label, rejected.confidence) == ("reject", 0.97)
    assert rejected.decided_at  # and when


def test_an_edit_closes_the_proposal_and_asks_again_with_the_users_text(store):
    conversation = store.ensure_conversation(None, "valentina")
    proposal = propose(store, report_draft(), conversation).proposal
    edited = "Glow Cosmetics wants returns first, then shipment volume, as a PDF."

    resolution = store.resolve_proposal(
        proposal.proposal_id,
        ReplyDecision("edit", "the user asked for a change", edited_content=edited),
        user_id="valentina",
        run_id="run-2",
        message=f"Yes but make it: {edited}",
    )

    assert resolution.outcome == "revised"
    assert store.active_memories() == [], "an edit is never stored without a confirmation"
    pending = store.pending_proposal(conversation)
    assert pending == resolution.revised
    assert pending.content == edited
    assert pending.revises_proposal_id == proposal.proposal_id
    assert [p.status for p in store.proposals(conversation)] == ["revised", "pending"]

    approve(store, pending.proposal_id)
    assert [m.content for m in store.active_memories()] == [edited]


def test_an_edit_with_forbidden_content_is_refused_and_nothing_stays_pending(store):
    conversation = store.ensure_conversation(None, "valentina")
    proposal = propose(store, report_draft(), conversation).proposal

    resolution = store.resolve_proposal(
        proposal.proposal_id,
        ReplyDecision(
            "edit", "change", edited_content="Send it to their warehouse at 455 Industrial Way"
        ),
        user_id="valentina",
        run_id="run-2",
        message="edit: their warehouse at 455 Industrial Way",
    )

    assert resolution.outcome == "blocked"
    assert resolution.refusal == ("b2b_location",)
    assert store.pending_proposal(conversation) is None
    assert store.active_memories() == []
    blocked = store.decision_log()[-1]
    assert blocked.event == "blocked" and blocked.content is None
    assert "Industrial Way" not in (blocked.message or "")


def test_a_discarded_proposal_writes_nothing(store):
    conversation = store.ensure_conversation(None, "valentina")
    proposal = propose(store, carrier_draft(), conversation).proposal

    resolution = store.resolve_proposal(
        proposal.proposal_id,
        ReplyDecision("discard", "the user moved on", label="unrelated", confidence=0.9),
        user_id="valentina",
        run_id="run-2",
        message="What's the return window?",
    )

    assert resolution.outcome == "discarded"
    assert store.active_memories() == []
    assert events(store) == ["proposed", "discarded"]


def test_a_proposal_is_resolved_once(store):
    conversation = store.ensure_conversation(None, "valentina")
    proposal = propose(store, carrier_draft(), conversation).proposal
    approve(store, proposal.proposal_id)

    again = store.resolve_proposal(
        proposal.proposal_id,
        ReplyDecision("reject", "no"),
        user_id="valentina",
        run_id="run-3",
        message="no",
    )

    assert again.outcome == "already_resolved"
    assert len(store.active_memories()) == 1


def test_an_approval_without_a_user_is_never_written(store):
    conversation = store.ensure_conversation(None, None)
    proposal = propose(store, carrier_draft(), conversation, user=None).proposal

    resolution = approve(store, proposal.proposal_id, user=None)

    assert resolution.outcome == "discarded"
    assert resolution.reason == "no authenticated user to record as the approver"
    assert store.active_memories() == []


def test_approving_an_update_supersedes_the_old_memory_which_stays_for_the_audit(store):
    old = remember(store, carrier_draft())
    new = remember(store, carrier_draft(content="SEUR covers neither rural Zaragoza nor Huesca."))

    (active,) = store.active_memories()
    assert active.memory_id == new
    replaced = store.get_memory(old)
    assert (replaced.status, replaced.superseded_by) == ("superseded", new)
    assert "superseded" in events(store)


def test_the_database_itself_keeps_one_active_memory_per_key(store):
    remember(store, carrier_draft())
    connection = sqlite3.connect(store.path)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO agent_memories (memory_id, category, topic_key, content, status, "
            "approved_by, approved_at, source_proposal_id, valid_until) SELECT 'copy', "
            "category, topic_key, content, 'active', approved_by, approved_at, "
            "source_proposal_id, valid_until FROM agent_memories"
        )


# --- The decision log ----------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    ["UPDATE memory_decisions SET reason = 'edited'", "DELETE FROM memory_decisions"],
)
def test_the_decision_log_is_append_only(store, statement):
    remember(store, carrier_draft())
    connection = sqlite3.connect(store.path)

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute(statement)


def test_every_decision_is_logged_with_its_message_and_time(store, clock):
    conversation = store.ensure_conversation(None, "valentina")
    proposal = propose(store, carrier_draft(), conversation).proposal
    clock.advance(minutes=2)
    approve(store, proposal.proposal_id)

    proposed, approved = store.decision_log(conversation_id=conversation)
    assert proposed.event == "proposed" and proposed.message == carrier_draft().content
    assert proposed.decided_at == START.isoformat(timespec="milliseconds")
    assert approved.event == "approved" and approved.message == "yes"
    assert approved.decided_at == (START + timedelta(minutes=2)).isoformat(timespec="milliseconds")
    assert {proposed.proposal_id, approved.proposal_id} == {proposal.proposal_id}


# --- Recall --------------------------------------------------------------------------


def test_recall_returns_relevant_memories_never_the_whole_store(store):
    remember(store, carrier_draft())
    remember(store, report_draft())

    recalled = store.recall("Which carrier covers rural Zaragoza?", min_score=0.5)

    assert recalled.outcome == "ok"
    assert [item["scope"] for item in recalled.items] == ["SEUR, ES"]
    assert recalled.items[0]["matched_by"] == "similarity"


def test_a_named_carrier_or_client_is_recalled_even_below_the_threshold(store):
    remember(store, carrier_draft())
    remember(store, report_draft())

    by_carrier = store.recall("Is seur reliable?", min_score=0.99)
    by_client = store.recall("Anything about glow cosmetics?", min_score=0.99)

    assert [(i["scope"], i["matched_by"]) for i in by_carrier.items] == [("SEUR, ES", "named")]
    assert [i["scope"] for i in by_client.items] == ["client: Glow Cosmetics"]


def test_recall_returns_at_most_three(store):
    for carrier, country in (("SEUR", "ES"), ("MRW", "ES"), ("DHL", "ES"), ("UPS", "US")):
        remember(
            store,
            carrier_draft(carrier=carrier, country=country, content=f"{carrier} rural Zaragoza."),
        )

    assert len(store.recall("SEUR MRW DHL UPS rural Zaragoza", min_score=0.0).items) == 3


def test_recall_updates_usage_for_the_eviction_policy(store):
    memory_id = remember(store, carrier_draft())

    store.recall("seur?")

    assert store.get_memory(memory_id).recall_count == 1


def test_without_embeddings_only_named_memories_come_back(tmp_path, clock):
    def down(text: str) -> list[float]:
        raise RuntimeError("embeddings model unavailable")

    writer = MemoryStore(tmp_path / "m.sqlite", embedder=topic_vector, clock=clock)
    remember(writer, carrier_draft())
    reader = MemoryStore(tmp_path / "m.sqlite", embedder=down, clock=clock)

    named = reader.recall("What does SEUR cover?")
    unnamed = reader.recall("Who covers rural Zaragoza?")

    assert named.outcome == "unavailable" and len(named.items) == 1
    assert unnamed.outcome == "unavailable" and unnamed.items == ()


def test_an_empty_memory_calls_no_model(tmp_path, clock):
    def forbidden(text: str) -> list[float]:
        raise AssertionError("no embedding needed for an empty memory")

    empty = MemoryStore(tmp_path / "m.sqlite", embedder=forbidden, clock=clock)

    assert empty.recall("anything").to_state() == {
        "outcome": "ok",
        "items": [],
        "reason": "the memory holds no active entries",
    }


def test_cosine():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([], []) == 0.0


# --- Expiry, cleanup and caps --------------------------------------------------------


def test_an_expired_memory_is_never_recalled_and_cleanup_logs_it(store, clock):
    memory_id = remember(
        store,
        MemoryDraft(
            category="recurring_incident",
            content="Delays on Los Angeles incidents this week are due to the port strike.",
            reason="recurring",
            country="US",
            route_or_area="Los Angeles port",
        ),
    )
    clock.advance(days=15)

    assert store.recall("port strike delays?", min_score=0.0).items == ()
    report = store.cleanup()

    assert report.expired_memories == [memory_id]
    assert store.get_memory(memory_id).status == "expired"
    expired = store.decision_log()[-1]
    assert (expired.event, expired.user_id) == ("expired", "system")


def test_an_unanswered_proposal_expires_and_is_never_approved_later(store, clock):
    conversation = store.ensure_conversation(None, "valentina")
    proposal = propose(store, carrier_draft(), conversation).proposal
    clock.advance(hours=25)

    assert store.pending_proposal(conversation) is None
    assert approve(store, proposal.proposal_id).outcome == "already_resolved"
    assert store.active_memories() == []
    assert events(store) == ["proposed", "expired"]


def test_a_category_over_its_cap_evicts_the_least_recently_used(store, clock, monkeypatch):
    # The store applies the caps in `policy.MEMORABLE`: two client preferences at most.
    small = tuple(
        k if k.category != "client_report_preference" else type(k)(**{**k.__dict__, "cap": 2})
        for k in policy.MEMORABLE
    )
    monkeypatch.setattr(policy, "MEMORABLE", small)
    first = remember(store, report_draft(client="Brand A", content="Brand A wants volume first."))
    clock.advance(minutes=1)
    second = remember(store, report_draft(client="Brand B", content="Brand B wants a PDF."))
    clock.advance(minutes=1)
    # Brand A is named, so recalled (no similarity can pass 1.1): used more recently than B.
    store.recall("brand a?", min_score=1.1)
    clock.advance(minutes=1)
    third = remember(store, report_draft(client="Brand C", content="Brand C wants returns first."))

    assert {m.memory_id for m in store.active_memories()} == {first, third}
    assert store.get_memory(second).status == "evicted"
    assert "evicted" in events(store)


# --- Memory never touches the knowledge base -------------------------------------------


def _code_strings_and_imports(path: Path) -> tuple[str, set[str], set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(owner.body[0].value)
        for owner in ast.walk(tree)
        if isinstance(owner, ast.Module | ast.ClassDef | ast.FunctionDef)
        and owner.body
        and isinstance(owner.body[0], ast.Expr)
        and isinstance(owner.body[0].value, ast.Constant)
    }
    strings = "\n".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    return strings, imported, called | names


def test_memory_never_writes_to_the_knowledge_base():
    for path in (AGENT_DIR / "memory").glob("*.py"):
        strings, imported, used = _code_strings_and_imports(path)
        assert not any(module.startswith("qdrant_client") for module in imported), path.name
        assert "trackflow_knowledge" not in strings, path.name
        assert not {"COLLECTION_NAME", "get_qdrant_client", "upsert", "setup"} & used, path.name


def test_no_agent_module_writes_to_qdrant():
    for path in AGENT_DIR.rglob("*.py"):
        _, imported, used = _code_strings_and_imports(path)
        assert not any(module.startswith("qdrant_client") for module in imported), path.name
        assert not {"upsert", "upload_points", "delete_collection", "create_collection"} & used
