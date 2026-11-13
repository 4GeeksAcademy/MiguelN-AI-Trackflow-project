"""The agent's memory store: SQLite, with an explicit read/write interface (MEM-092).

Four tables in their own file (`AGENT_MEMORY_PATH`), apart from the run traces
and nowhere near the knowledge base (Qdrant `trackflow_knowledge`, read-only):

- `memory_conversations`  who a conversation belongs to;
- `memory_proposals`      what the agent offered to remember, to whom, from which
                          message, and how it ended (pending until the user's next
                          message decides it);
- `memory_decisions`      the audit log: one row per event - proposed, approved,
                          rejected, revised, discarded, expired, blocked,
                          suppressed, superseded, evicted - with who, the message
                          that decided it and when. Append-only: triggers refuse
                          UPDATE and DELETE;
- `agent_memories`        the approved memories. Never written except by
                          approving a proposal; ended by being superseded,
                          expiring or being evicted, never deleted.

The database itself enforces the two rules nothing may break: one pending
proposal per conversation, and one active memory per consolidation key
(partial unique indexes). Every write that changes more than one row runs in
one transaction.

Reads: `recall`, `pending_proposal`, `active_memories`, `get_memory`,
`proposals`, `decision_log`. Writes: `ensure_conversation`, `open_proposal`,
`resolve_proposal`, `record_event`, `cleanup`. Nothing else touches the tables.

Each operation opens its own short connection, like the trace store, so the
store is safe to use from FastAPI's worker threads.
"""

import json
import logging
import math
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal

from services.agent.config import get_agent_settings
from services.agent.memory import policy
from services.agent.memory.policy import PENDING_TTL, MemoryDraft

logger = logging.getLogger("api.agent.memory")

# Recall: at most this many memories reach the prompt, never the whole store.
RECALL_TOP_K: Final = 3
# Cosine similarity a memory needs to be recalled for a question it does not name.
# Measured on 2026-11-12 with pplx-embed-v1-0.6b (`npm run agent:memory:calibrate`):
# the 10 questions related to a memory score >= 0.5085, the 91 unrelated pairs (the
# RAG test queries, ticket/stock questions, closings) <= 0.4072. 0.45 sits in that
# gap, nearer the unrelated side: recall first, as for `rag.MIN_SCORE`. The test
# query "Which carrier best covers rural Aragón?" scores 0.5236 against the SEUR
# correction and is recalled - rightly, it is the question that correction answers.
MIN_MEMORY_SCORE: Final = 0.45

SYSTEM_USER: Final = "system"

ProposalStatus = Literal["pending", "approved", "rejected", "discarded", "revised", "expired"]
MemoryStatus = Literal["active", "superseded", "expired", "evicted"]
DecisionEvent = Literal[
    "proposed",
    "approved",
    "rejected",
    "revised",
    "discarded",
    "expired",
    "blocked",
    "suppressed",
    "superseded",
    "evicted",
]
ReplyOutcome = Literal["approve", "reject", "edit", "discard"]

_CATEGORIES = ", ".join(f"'{kind.category}'" for kind in policy.MEMORABLE)

SCHEMA: Final = f"""
CREATE TABLE IF NOT EXISTS memory_conversations (
    conversation_id TEXT PRIMARY KEY,
    user_id         TEXT,
    started_at      TEXT NOT NULL,
    last_message_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_proposals (
    proposal_id         TEXT PRIMARY KEY,
    conversation_id     TEXT NOT NULL REFERENCES memory_conversations (conversation_id),
    run_id              TEXT,
    proposed_to         TEXT,
    originating_message TEXT NOT NULL,
    category            TEXT NOT NULL CHECK (category IN ({_CATEGORIES})),
    topic_key           TEXT NOT NULL,
    content             TEXT NOT NULL,
    reason              TEXT NOT NULL,
    carrier             TEXT,
    country             TEXT,
    route_or_area       TEXT,
    client              TEXT,
    valid_until         TEXT NOT NULL,
    replaces_memory_id  TEXT REFERENCES agent_memories (memory_id),
    replaces_content    TEXT,
    revises_proposal_id TEXT REFERENCES memory_proposals (proposal_id),
    status              TEXT NOT NULL CHECK (status IN
                            ('pending', 'approved', 'rejected', 'discarded', 'revised', 'expired')),
    proposed_at         TEXT NOT NULL,
    resolved_at         TEXT
);
-- One proposal at a time: a conversation can never hold two pending proposals.
CREATE UNIQUE INDEX IF NOT EXISTS memory_one_pending_per_conversation
    ON memory_proposals (conversation_id) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS agent_memories (
    memory_id          TEXT PRIMARY KEY,
    category           TEXT NOT NULL CHECK (category IN ({_CATEGORIES})),
    topic_key          TEXT NOT NULL,
    content            TEXT NOT NULL,
    carrier            TEXT,
    country            TEXT,
    route_or_area      TEXT,
    client             TEXT,
    embedding_json     TEXT,
    status             TEXT NOT NULL CHECK (status IN
                           ('active', 'superseded', 'expired', 'evicted')),
    approved_by        TEXT NOT NULL,
    approved_at        TEXT NOT NULL,
    source_proposal_id TEXT NOT NULL REFERENCES memory_proposals (proposal_id),
    valid_until        TEXT NOT NULL,
    last_recalled_at   TEXT,
    recall_count       INTEGER NOT NULL DEFAULT 0,
    superseded_by      TEXT,
    ended_at           TEXT
);
-- Consolidation: one active memory per carrier + country (or route, or client).
CREATE UNIQUE INDEX IF NOT EXISTS memory_one_active_per_topic
    ON agent_memories (topic_key) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS memory_decisions (
    decision_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    event           TEXT NOT NULL CHECK (event IN ('proposed', 'approved', 'rejected', 'revised',
                        'discarded', 'expired', 'blocked', 'suppressed', 'superseded', 'evicted')),
    proposal_id     TEXT,
    memory_id       TEXT,
    conversation_id TEXT,
    run_id          TEXT,
    user_id         TEXT,
    message         TEXT,
    label           TEXT,
    confidence      REAL,
    reason          TEXT NOT NULL,
    content         TEXT,
    decided_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_decisions_conversation_idx
    ON memory_decisions (conversation_id, decision_id);
-- The audit log is append-only.
CREATE TRIGGER IF NOT EXISTS memory_decisions_no_update BEFORE UPDATE ON memory_decisions
BEGIN SELECT RAISE(ABORT, 'memory_decisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS memory_decisions_no_delete BEFORE DELETE ON memory_decisions
BEGIN SELECT RAISE(ABORT, 'memory_decisions is append-only'); END;
"""


class ConversationNotFound(LookupError):
    """No conversation with that id, or it belongs to another user."""


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds")


def _now() -> datetime:
    return datetime.now(UTC)


def _words(text: str) -> str:
    """`text` as space-separated words, padded, for whole-word matching."""
    return f" {' '.join(re.sub(r'[^a-z0-9]+', ' ', policy.normalise_text(text)).split())} "


def cosine(a: list[float], b: list[float]) -> float:
    if not a or len(a) != len(b):
        return 0.0
    dot = math.fsum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(math.fsum(x * x for x in a)) * math.sqrt(math.fsum(y * y for y in b))
    return dot / norm if norm else 0.0


# --- Records ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    conversation_id: str
    run_id: str | None
    proposed_to: str | None
    originating_message: str
    category: str
    topic_key: str
    content: str
    reason: str
    carrier: str | None
    country: str | None
    route_or_area: str | None
    client: str | None
    valid_until: str
    replaces_memory_id: str | None
    replaces_content: str | None
    revises_proposal_id: str | None
    status: ProposalStatus
    proposed_at: str
    resolved_at: str | None

    def to_state(self) -> dict[str, Any]:
        """The JSON-mode dict the graph carries (`pending_proposal`, `memory_proposal`)."""
        return asdict(self)

    def scope(self) -> str:
        return policy.describe_scope(
            self.category, self.carrier, self.country, self.route_or_area, self.client
        )


@dataclass(frozen=True)
class Memory:
    memory_id: str
    category: str
    topic_key: str
    content: str
    carrier: str | None
    country: str | None
    route_or_area: str | None
    client: str | None
    status: MemoryStatus
    approved_by: str
    approved_at: str
    source_proposal_id: str
    valid_until: str
    last_recalled_at: str | None
    recall_count: int
    superseded_by: str | None
    ended_at: str | None

    def scope(self) -> str:
        return policy.describe_scope(
            self.category, self.carrier, self.country, self.route_or_area, self.client
        )


@dataclass(frozen=True)
class Decision:
    decision_id: int
    event: DecisionEvent
    proposal_id: str | None
    memory_id: str | None
    conversation_id: str | None
    run_id: str | None
    user_id: str | None
    message: str | None
    label: str | None
    confidence: float | None
    reason: str
    content: str | None
    decided_at: str


@dataclass(frozen=True)
class RecallResult:
    """What `recall` found: `ok` (possibly nothing) or `unavailable` (no embeddings)."""

    outcome: Literal["ok", "unavailable"]
    items: tuple[dict[str, Any], ...] = ()
    reason: str = ""

    def to_state(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "items": list(self.items), "reason": self.reason}


@dataclass(frozen=True)
class ProposalResult:
    """How `open_proposal` ended: `pending` (shown to the user), `blocked` or `suppressed`."""

    status: Literal["pending", "blocked", "suppressed"]
    reason: str
    proposal: Proposal | None = None
    refusal: tuple[str, ...] = ()

    def to_state(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "refusal": list(self.refusal),
            "proposal": None if self.proposal is None else self.proposal.to_state(),
        }


@dataclass(frozen=True)
class ReplyDecision:
    """The user's decision on a pending proposal, as the classifier settled it."""

    outcome: ReplyOutcome
    reason: str
    label: str | None = None
    confidence: float | None = None
    edited_content: str | None = None


@dataclass(frozen=True)
class Resolution:
    """What resolving a proposal did."""

    outcome: Literal["approved", "rejected", "revised", "discarded", "blocked", "already_resolved"]
    reason: str
    proposal_id: str
    memory_id: str | None = None
    content: str | None = None
    # For `revised`: the new pending proposal with the user's edit.
    revised: Proposal | None = None
    refusal: tuple[str, ...] = ()
    superseded_memory_id: str | None = None

    def to_state(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "proposal_id": self.proposal_id,
            "memory_id": self.memory_id,
            "content": self.content,
            "revised": None if self.revised is None else self.revised.to_state(),
            "refusal": list(self.refusal),
            "superseded_memory_id": self.superseded_memory_id,
        }


@dataclass
class CleanupReport:
    expired_memories: list[str] = field(default_factory=list)
    expired_proposals: list[str] = field(default_factory=list)
    evicted_memories: list[str] = field(default_factory=list)


def _proposal(row: sqlite3.Row) -> Proposal:
    return Proposal(**{name: row[name] for name in Proposal.__dataclass_fields__})


def _memory(row: sqlite3.Row) -> Memory:
    return Memory(**{name: row[name] for name in Memory.__dataclass_fields__})


def _decision(row: sqlite3.Row) -> Decision:
    return Decision(**{name: row[name] for name in Decision.__dataclass_fields__})


# --- The store -------------------------------------------------------------------------


class MemoryStore:
    """Reads and writes the agent's memory in one SQLite file; creates its tables on first use.

    `embedder` turns a text into a vector (default: the RAG project's embeddings
    model, the same one the knowledge base uses). It is called at approval, for
    the memory, and at recall, for the question; when it fails, recall falls back
    to memories the question names, and nothing else is lost.
    """

    def __init__(
        self,
        path: Path,
        *,
        embedder: Callable[[str], list[float]] | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.path = path
        self._embedder = embedder
        self._clock = clock
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _embed(self, text: str) -> list[float] | None:
        embedder = self._embedder
        if embedder is None:
            from data.process.knowledge_base import embed

            embedder = embed
        try:
            return embedder(text)
        except Exception as error:  # the model may be down: recall degrades, writes go on
            logger.warning("Memory embedding failed: %s", type(error).__name__)
            return None

    # --- Audit log -------------------------------------------------------------------

    @staticmethod
    def _log(
        connection: sqlite3.Connection,
        event: DecisionEvent,
        *,
        reason: str,
        decided_at: str,
        proposal_id: str | None = None,
        memory_id: str | None = None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        message: str | None = None,
        label: str | None = None,
        confidence: float | None = None,
        content: str | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO memory_decisions (event, proposal_id, memory_id, conversation_id, "
            "run_id, user_id, message, label, confidence, reason, content, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event,
                proposal_id,
                memory_id,
                conversation_id,
                run_id,
                user_id,
                None if message is None else policy.redact(message),
                label,
                confidence,
                reason,
                content,
                decided_at,
            ),
        )

    # --- Conversations ---------------------------------------------------------------

    def ensure_conversation(self, conversation_id: str | None, user_id: str | None) -> str:
        """The conversation to use: a new one, or an existing one of this user's.

        Raises ConversationNotFound for an unknown id or another user's
        conversation: whether an id exists is not revealed.
        """
        now = _iso(self._clock())
        with self._transaction() as connection:
            if conversation_id is None:
                conversation_id = str(uuid.uuid4())
                connection.execute(
                    "INSERT INTO memory_conversations (conversation_id, user_id, started_at, "
                    "last_message_at) VALUES (?, ?, ?, ?)",
                    (conversation_id, user_id, now, now),
                )
                return conversation_id
            row = connection.execute(
                "SELECT user_id FROM memory_conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None or row["user_id"] != user_id:
                raise ConversationNotFound(conversation_id)
            connection.execute(
                "UPDATE memory_conversations SET last_message_at = ? WHERE conversation_id = ?",
                (now, conversation_id),
            )
        return conversation_id

    # --- Proposals -------------------------------------------------------------------

    def pending_proposal(self, conversation_id: str) -> Proposal | None:
        """The conversation's open proposal, if any. One older than PENDING_TTL is
        expired (and logged) first: an unanswered offer is never approved later."""
        now = self._clock()
        with self._transaction() as connection:
            self._expire_stale_proposals(connection, now, conversation_id=conversation_id)
            row = connection.execute(
                "SELECT * FROM memory_proposals WHERE conversation_id = ? AND status = 'pending'",
                (conversation_id,),
            ).fetchone()
        return None if row is None else _proposal(row)

    def proposals(self, conversation_id: str) -> list[Proposal]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_proposals WHERE conversation_id = ? "
                "ORDER BY proposed_at, rowid",
                (conversation_id,),
            ).fetchall()
        return [_proposal(row) for row in rows]

    def record_event(
        self,
        event: DecisionEvent,
        *,
        reason: str,
        conversation_id: str | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        message: str | None = None,
        content: str | None = None,
    ) -> None:
        """Log a decision that touches no proposal row (e.g. a draft that was never shown)."""
        with self._transaction() as connection:
            self._log(
                connection,
                event,
                reason=reason,
                decided_at=_iso(self._clock()),
                conversation_id=conversation_id,
                run_id=run_id,
                user_id=user_id,
                message=message,
                content=content,
            )

    def open_proposal(
        self,
        draft: MemoryDraft,
        *,
        conversation_id: str,
        run_id: str | None,
        user_id: str | None,
        message: str,
    ) -> ProposalResult:
        """Offer `draft` to the user, after the checks it must pass. Never writes a memory.

        In order: the briefing's policy (`blocked`; the content is withheld from
        the log), a duplicate of an active memory (`suppressed`), an active
        memory on the same key (the proposal becomes an update that shows what
        it replaces), a proposal already pending in the conversation
        (`suppressed`). Only then is it stored as `pending` and logged
        `proposed`.
        """
        now = self._clock()
        stamp = _iso(now)
        check = policy.check_draft(draft)
        common: dict[str, Any] = {
            "conversation_id": conversation_id,
            "run_id": run_id,
            "user_id": user_id,
        }
        if not check.allowed:
            reason = f"refused by the memory policy: {', '.join(check.reasons)}"
            with self._transaction() as connection:
                self._log(
                    connection,
                    "blocked",
                    reason=reason,
                    decided_at=stamp,
                    message=message,
                    **common,
                )
            return ProposalResult("blocked", reason, refusal=check.reasons)
        assert check.topic_key is not None
        valid_until = _iso(policy.valid_until(draft, now=now))

        with self._transaction() as connection:
            current = connection.execute(
                "SELECT * FROM agent_memories WHERE topic_key = ? AND status = 'active' "
                "AND valid_until > ?",
                (check.topic_key, stamp),
            ).fetchone()
            if current is not None and policy.normalise_text(
                current["content"]
            ) == policy.normalise_text(draft.content):
                reason = f"already remembered as memory {current['memory_id']}"
                self._log(
                    connection,
                    "suppressed",
                    reason=reason,
                    decided_at=stamp,
                    memory_id=current["memory_id"],
                    message=message,
                    content=draft.content,
                    **common,
                )
                return ProposalResult("suppressed", reason)
            pending = connection.execute(
                "SELECT proposal_id FROM memory_proposals WHERE conversation_id = ? "
                "AND status = 'pending'",
                (conversation_id,),
            ).fetchone()
            if pending is not None:
                reason = (
                    f"proposal {pending['proposal_id']} is still pending in this conversation: "
                    "only one at a time"
                )
                self._log(
                    connection,
                    "suppressed",
                    reason=reason,
                    decided_at=stamp,
                    proposal_id=pending["proposal_id"],
                    message=message,
                    content=draft.content,
                    **common,
                )
                return ProposalResult("suppressed", reason)
            proposal = Proposal(
                proposal_id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                run_id=run_id,
                proposed_to=user_id,
                originating_message=policy.redact(message),
                category=draft.category,
                topic_key=check.topic_key,
                content=draft.content.strip(),
                reason=draft.reason.strip(),
                carrier=policy.canonical_carrier(draft.carrier),
                country=draft.country,
                route_or_area=draft.route_or_area,
                client=draft.client,
                valid_until=valid_until,
                replaces_memory_id=None if current is None else current["memory_id"],
                replaces_content=None if current is None else current["content"],
                revises_proposal_id=None,
                status="pending",
                proposed_at=stamp,
                resolved_at=None,
            )
            self._insert_proposal(connection, proposal)
            self._log(
                connection,
                "proposed",
                reason=proposal.reason
                + ("" if current is None else f" (updates memory {current['memory_id']})"),
                decided_at=stamp,
                proposal_id=proposal.proposal_id,
                message=message,
                content=proposal.content,
                **common,
            )
        return ProposalResult("pending", "offered to the user", proposal=proposal)

    @staticmethod
    def _insert_proposal(connection: sqlite3.Connection, proposal: Proposal) -> None:
        values = asdict(proposal)
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        connection.execute(
            f"INSERT INTO memory_proposals ({columns}) VALUES ({marks})", tuple(values.values())
        )

    def resolve_proposal(
        self,
        proposal_id: str,
        decision: ReplyDecision,
        *,
        user_id: str | None,
        run_id: str | None,
        message: str,
    ) -> Resolution:
        """Apply the user's decision to a pending proposal, in one transaction.

        - approve: the content is checked again, then written as an active
          memory with who approved it, superseding the active memory on the same
          key; expired entries are cleaned up and the category's cap applied;
        - reject: the proposal is closed as rejected; nothing is written;
        - edit: the proposal is closed as revised and a new pending proposal with
          the user's text is opened, so what is stored is always text the user
          saw and approved;
        - discard: closed, nothing written (ambiguous reply, change of topic).

        A proposal that is no longer pending (resolved by a concurrent request)
        comes back as `already_resolved`, untouched.
        """
        now = self._clock()
        stamp = _iso(now)
        # The vector is fetched before the transaction: a slow model must not hold the lock.
        embedding: list[float] | None = None
        if decision.outcome == "approve":
            with self._transaction() as connection:
                row = connection.execute(
                    "SELECT content FROM memory_proposals WHERE proposal_id = ?", (proposal_id,)
                ).fetchone()
            if row is not None:
                embedding = self._embed(row["content"])

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM memory_proposals WHERE proposal_id = ? AND status = 'pending'",
                (proposal_id,),
            ).fetchone()
            if row is None:
                return Resolution(
                    "already_resolved", "the proposal is no longer pending", proposal_id
                )
            proposal = _proposal(row)
            common: dict[str, Any] = {
                "proposal_id": proposal_id,
                "conversation_id": proposal.conversation_id,
                "run_id": run_id,
                "user_id": user_id,
                "message": message,
                "label": decision.label,
                "confidence": decision.confidence,
            }

            def close(status: ProposalStatus) -> None:
                updated = connection.execute(
                    "UPDATE memory_proposals SET status = ?, resolved_at = ? "
                    "WHERE proposal_id = ? AND status = 'pending'",
                    (status, stamp, proposal_id),
                ).rowcount
                if updated != 1:  # pragma: no cover - the SELECT above holds the row
                    raise RuntimeError(f"proposal {proposal_id} changed while being resolved")

            if decision.outcome == "reject":
                close("rejected")
                self._log(
                    connection,
                    "rejected",
                    reason=decision.reason,
                    decided_at=stamp,
                    content=proposal.content,
                    **common,
                )
                return Resolution(
                    "rejected", decision.reason, proposal_id, content=proposal.content
                )

            if decision.outcome == "discard":
                close("discarded")
                self._log(
                    connection,
                    "discarded",
                    reason=decision.reason,
                    decided_at=stamp,
                    content=proposal.content,
                    **common,
                )
                return Resolution(
                    "discarded", decision.reason, proposal_id, content=proposal.content
                )

            if decision.outcome == "edit":
                edited = (decision.edited_content or "").strip()
                draft = MemoryDraft(
                    category=proposal.category,  # type: ignore[arg-type]
                    content=edited[: policy.MAX_CONTENT_CHARS],
                    reason=proposal.reason,
                    carrier=proposal.carrier,
                    country=proposal.country,  # type: ignore[arg-type]
                    route_or_area=proposal.route_or_area,
                    client=proposal.client,
                )
                check = policy.check_draft(draft)
                if not check.allowed or len(edited) > policy.MAX_CONTENT_CHARS:
                    reasons = check.reasons or ("too_long",)
                    close("discarded")
                    reason = f"the edit was refused by the memory policy: {', '.join(reasons)}"
                    self._log(connection, "blocked", reason=reason, decided_at=stamp, **common)
                    return Resolution("blocked", reason, proposal_id, refusal=reasons)
                close("revised")
                self._log(
                    connection,
                    "revised",
                    reason=decision.reason,
                    decided_at=stamp,
                    content=proposal.content,
                    **common,
                )
                revised = Proposal(
                    **{
                        **asdict(proposal),
                        "proposal_id": str(uuid.uuid4()),
                        "run_id": run_id,
                        "proposed_to": user_id,
                        "originating_message": policy.redact(message),
                        "content": draft.content,
                        "revises_proposal_id": proposal_id,
                        "status": "pending",
                        "proposed_at": stamp,
                        "resolved_at": None,
                    }
                )
                self._insert_proposal(connection, revised)
                self._log(
                    connection,
                    "proposed",
                    reason=f"the user's edit of proposal {proposal_id}, offered for confirmation",
                    decided_at=stamp,
                    **{
                        **common,
                        "proposal_id": revised.proposal_id,
                        "label": None,
                        "confidence": None,
                    },
                    content=revised.content,
                )
                return Resolution(
                    "revised",
                    decision.reason,
                    proposal_id,
                    content=revised.content,
                    revised=revised,
                )

            # approve
            if not user_id:
                close("discarded")
                reason = "no authenticated user to record as the approver"
                self._log(connection, "discarded", reason=reason, decided_at=stamp, **common)
                return Resolution("discarded", reason, proposal_id, content=proposal.content)
            check = policy.check_text(proposal.content)
            if not check.allowed:
                close("discarded")
                reason = f"refused by the memory policy at approval: {', '.join(check.reasons)}"
                self._log(connection, "blocked", reason=reason, decided_at=stamp, **common)
                return Resolution("blocked", reason, proposal_id, refusal=check.reasons)

            self._cleanup(connection, now)
            memory_id = str(uuid.uuid4())
            superseded = connection.execute(
                "SELECT memory_id, content FROM agent_memories WHERE topic_key = ? "
                "AND status = 'active'",
                (proposal.topic_key,),
            ).fetchone()
            if superseded is not None:
                connection.execute(
                    "UPDATE agent_memories SET status = 'superseded', superseded_by = ?, "
                    "ended_at = ? WHERE memory_id = ?",
                    (memory_id, stamp, superseded["memory_id"]),
                )
                note = (
                    ""
                    if superseded["memory_id"] == proposal.replaces_memory_id
                    else " (it changed after the proposal was made)"
                )
                self._log(
                    connection,
                    "superseded",
                    reason=f"replaced by memory {memory_id} on {proposal.topic_key}{note}",
                    decided_at=stamp,
                    memory_id=superseded["memory_id"],
                    content=superseded["content"],
                    **common,
                )
            connection.execute(
                "INSERT INTO agent_memories (memory_id, category, topic_key, content, carrier, "
                "country, route_or_area, client, embedding_json, status, approved_by, "
                "approved_at, source_proposal_id, valid_until) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                (
                    memory_id,
                    proposal.category,
                    proposal.topic_key,
                    proposal.content,
                    proposal.carrier,
                    proposal.country,
                    proposal.route_or_area,
                    proposal.client,
                    None if embedding is None else json.dumps(embedding),
                    user_id,
                    stamp,
                    proposal_id,
                    proposal.valid_until,
                ),
            )
            close("approved")
            self._log(
                connection,
                "approved",
                reason=decision.reason,
                decided_at=stamp,
                memory_id=memory_id,
                content=proposal.content,
                **common,
            )
            self._enforce_caps(connection, now, keep=memory_id)
        return Resolution(
            "approved",
            decision.reason,
            proposal_id,
            memory_id=memory_id,
            content=proposal.content,
            superseded_memory_id=None if superseded is None else superseded["memory_id"],
        )

    # --- Memories --------------------------------------------------------------------

    def active_memories(self) -> list[Memory]:
        """Every memory that is active and not past its `valid_until`, newest first."""
        stamp = _iso(self._clock())
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_memories WHERE status = 'active' AND valid_until > ? "
                "ORDER BY approved_at DESC",
                (stamp,),
            ).fetchall()
        return [_memory(row) for row in rows]

    def get_memory(self, memory_id: str) -> Memory | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM agent_memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        return None if row is None else _memory(row)

    def all_memories(self) -> list[Memory]:
        """Every memory ever approved, ended ones included (for the audit CLI)."""
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_memories ORDER BY approved_at, rowid"
            ).fetchall()
        return [_memory(row) for row in rows]

    def decision_log(
        self, *, conversation_id: str | None = None, limit: int = 100
    ) -> list[Decision]:
        """The audit log, oldest first (the last `limit` rows)."""
        query = "SELECT * FROM memory_decisions"
        params: tuple[Any, ...] = ()
        if conversation_id is not None:
            query += " WHERE conversation_id = ?"
            params = (conversation_id,)
        query += " ORDER BY decision_id DESC LIMIT ?"
        with self._transaction() as connection:
            rows = connection.execute(query, (*params, limit)).fetchall()
        return [_decision(row) for row in reversed(rows)]

    def recall(
        self, question: str, *, k: int = RECALL_TOP_K, min_score: float = MIN_MEMORY_SCORE
    ) -> RecallResult:
        """The memories relevant to `question`: at most `k`, never the whole store.

        A memory is recalled when the question names its carrier or client, or
        when its embedding scores at least `min_score` against the question's.
        Expired memories are never recalled. Without embeddings (the model is
        down), only named memories come back and the outcome says
        `unavailable`.
        """
        memories = self.active_memories()
        if not memories:
            return RecallResult("ok", (), "the memory holds no active entries")
        named = {memory.memory_id for memory in memories if self._named(memory, question)}

        vectors = self._vectors(memories)
        question_vector = self._embed(question) if vectors else None
        similarity = bool(vectors) and question_vector is not None
        scored: list[tuple[Memory, float | None, bool]] = []
        for memory in memories:
            vector = vectors.get(memory.memory_id)
            score = (
                None
                if vector is None or question_vector is None
                else round(cosine(question_vector, vector), 4)
            )
            is_named = memory.memory_id in named
            if is_named or (score is not None and score >= min_score):
                scored.append((memory, score, is_named))
        scored.sort(key=lambda item: (item[2], item[1] or 0.0), reverse=True)
        chosen = scored[:k]

        if chosen:
            stamp = _iso(self._clock())
            with self._transaction() as connection:
                connection.executemany(
                    "UPDATE agent_memories SET last_recalled_at = ?, "
                    "recall_count = recall_count + 1 WHERE memory_id = ?",
                    [(stamp, memory.memory_id) for memory, _, _ in chosen],
                )
        items = tuple(
            {
                "memory_id": memory.memory_id,
                "category": memory.category,
                "scope": memory.scope(),
                "content": memory.content,
                "approved_at": memory.approved_at,
                "approved_by": memory.approved_by,
                "valid_until": memory.valid_until,
                "score": score,
                "matched_by": "named" if is_named else "similarity",
            }
            for memory, score, is_named in chosen
        )
        if not similarity:
            return RecallResult(
                "unavailable", items, "the embeddings model is unavailable: named memories only"
            )
        return RecallResult("ok", items, f"{len(items)} of {len(memories)} active memories")

    @staticmethod
    def _named(memory: Memory, question: str) -> bool:
        """Does the question name this memory's client, or its carrier (by name)?"""
        words = _words(question)
        if memory.category == "client_report_preference":
            client = _words(memory.client or "")
            return client.strip() != "" and client in words
        carrier = memory.carrier
        if not carrier or carrier == "Local carrier":
            return False
        return _words(carrier) in words

    def _vectors(self, memories: list[Memory]) -> dict[str, list[float]]:
        """Each memory's stored vector; one missing (the model was down at approval) is
        computed now and saved."""
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT memory_id, embedding_json FROM agent_memories WHERE memory_id IN "
                f"({', '.join('?' for _ in memories)})",
                [memory.memory_id for memory in memories],
            ).fetchall()
        vectors = {
            row["memory_id"]: json.loads(row["embedding_json"])
            for row in rows
            if row["embedding_json"]
        }
        missing = [memory for memory in memories if memory.memory_id not in vectors]
        for memory in missing:
            vector = self._embed(memory.content)
            if vector is None:
                break
            vectors[memory.memory_id] = vector
            with self._transaction() as connection:
                connection.execute(
                    "UPDATE agent_memories SET embedding_json = ? WHERE memory_id = ?",
                    (json.dumps(vector), memory.memory_id),
                )
        return vectors

    # --- Cleanup ---------------------------------------------------------------------

    def cleanup(self) -> CleanupReport:
        """Expire what is past its date and apply the caps. Logged; nothing is deleted."""
        with self._transaction() as connection:
            return self._cleanup(connection, self._clock())

    def _cleanup(self, connection: sqlite3.Connection, now: datetime) -> CleanupReport:
        report = CleanupReport()
        stamp = _iso(now)
        for row in connection.execute(
            "SELECT memory_id, content, valid_until FROM agent_memories "
            "WHERE status = 'active' AND valid_until <= ?",
            (stamp,),
        ).fetchall():
            connection.execute(
                "UPDATE agent_memories SET status = 'expired', ended_at = ? WHERE memory_id = ?",
                (stamp, row["memory_id"]),
            )
            self._log(
                connection,
                "expired",
                reason=f"past its valid_until ({row['valid_until']})",
                decided_at=stamp,
                memory_id=row["memory_id"],
                user_id=SYSTEM_USER,
                content=row["content"],
            )
            report.expired_memories.append(row["memory_id"])
        report.expired_proposals = self._expire_stale_proposals(connection, now)
        report.evicted_memories = self._enforce_caps(connection, now)
        return report

    def _expire_stale_proposals(
        self, connection: sqlite3.Connection, now: datetime, *, conversation_id: str | None = None
    ) -> list[str]:
        cutoff = _iso(now - PENDING_TTL)
        query = "SELECT * FROM memory_proposals WHERE status = 'pending' AND proposed_at <= ?"
        params: tuple[Any, ...] = (cutoff,)
        if conversation_id is not None:
            query += " AND conversation_id = ?"
            params = (cutoff, conversation_id)
        expired: list[str] = []
        stamp = _iso(now)
        for row in connection.execute(query, params).fetchall():
            connection.execute(
                "UPDATE memory_proposals SET status = 'expired', resolved_at = ? "
                "WHERE proposal_id = ?",
                (stamp, row["proposal_id"]),
            )
            self._log(
                connection,
                "expired",
                reason=f"unanswered for {PENDING_TTL}: approval is never assumed",
                decided_at=stamp,
                proposal_id=row["proposal_id"],
                conversation_id=row["conversation_id"],
                user_id=SYSTEM_USER,
                content=row["content"],
            )
            expired.append(row["proposal_id"])
        return expired

    def _enforce_caps(
        self, connection: sqlite3.Connection, now: datetime, *, keep: str | None = None
    ) -> list[str]:
        """Evict the least recently used active memories of any category over its cap."""
        evicted: list[str] = []
        stamp = _iso(now)
        for kind in policy.MEMORABLE:
            rows = connection.execute(
                "SELECT memory_id, content FROM agent_memories WHERE status = 'active' "
                "AND category = ? ORDER BY COALESCE(last_recalled_at, approved_at) ASC, "
                "approved_at ASC",
                (kind.category,),
            ).fetchall()
            excess = len(rows) - kind.cap
            for row in [r for r in rows if r["memory_id"] != keep][: max(excess, 0)]:
                connection.execute(
                    "UPDATE agent_memories SET status = 'evicted', ended_at = ? "
                    "WHERE memory_id = ?",
                    (stamp, row["memory_id"]),
                )
                self._log(
                    connection,
                    "evicted",
                    reason=(
                        f"{kind.category} is over its cap of {kind.cap}: the least recently "
                        "used entry goes"
                    ),
                    decided_at=stamp,
                    memory_id=row["memory_id"],
                    user_id=SYSTEM_USER,
                    content=row["content"],
                )
                evicted.append(row["memory_id"])
        return evicted


@lru_cache(maxsize=1)
def get_memory_store() -> MemoryStore:
    return MemoryStore(get_agent_settings().agent_memory_path)
