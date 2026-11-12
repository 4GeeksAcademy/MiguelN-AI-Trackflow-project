"""The run trace: which nodes ran, in what order, what each read and produced, and why.

Every run of the graph leaves one `agent_runs` row and one `agent_run_events`
row per step: a `node` row for each node that ran (its declared inputs, its
state update or its error, its timing) and an `edge` row for each routing
decision (where it went and the reason). Rows are numbered by `seq` in the order
they happened; a resumed run continues the numbering with the next `attempt`.

The tables live in the agent store file next to LangGraph's checkpoints
(`AGENT_STORE_PATH`), so a run stays queryable after it ends:

    sqlite3 data/agent/agent_runs.sqlite \
      "SELECT seq, kind, name, status FROM agent_run_events WHERE run_id = '<id>' ORDER BY seq"

Each write opens its own short connection, which keeps the store safe to use
from FastAPI's worker threads.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal

from services.agent.config import get_agent_settings

RunStatus = Literal["running", "completed", "failed"]
EventKind = Literal["node", "edge"]
EventStatus = Literal["ok", "error"]

SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id        TEXT PRIMARY KEY,
    user_id       TEXT,
    graph_version TEXT NOT NULL,
    question      TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    outcome       TEXT CHECK (outcome IN ('answered', 'no_information', 'rejected')),
    answer        TEXT,
    attempts      INTEGER NOT NULL DEFAULT 1,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    error_node    TEXT,
    error_type    TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS agent_runs_started_at_idx ON agent_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS agent_runs_user_id_idx ON agent_runs (user_id, started_at DESC);
CREATE TABLE IF NOT EXISTS agent_run_events (
    run_id        TEXT NOT NULL REFERENCES agent_runs (run_id),
    seq           INTEGER NOT NULL,
    attempt       INTEGER NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('node', 'edge')),
    name          TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    duration_ms   REAL,
    input_json    TEXT,
    output_json   TEXT,
    error_type    TEXT,
    error_message TEXT,
    PRIMARY KEY (run_id, seq)
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _dump(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _load(text: str | None) -> Any:
    return None if text is None else json.loads(text)


@dataclass(frozen=True)
class TraceEvent:
    seq: int
    attempt: int
    kind: EventKind
    name: str
    status: EventStatus
    started_at: str
    finished_at: str | None
    duration_ms: float | None
    input: Any
    output: Any
    error_type: str | None
    error_message: str | None


@dataclass(frozen=True)
class RunTrace:
    run_id: str
    user_id: str | None
    graph_version: str
    question: str
    status: RunStatus
    outcome: str | None
    answer: str | None
    attempts: int
    started_at: str
    finished_at: str | None
    error_node: str | None
    error_type: str | None
    error_message: str | None
    events: tuple[TraceEvent, ...] = ()

    def node_names(self) -> list[str]:
        """The nodes that ran, in order (a failed node included)."""
        return [event.name for event in self.events if event.kind == "node"]

    def node_events(self, name: str) -> list[TraceEvent]:
        return [event for event in self.events if event.kind == "node" and event.name == name]

    def edge_events(self) -> list[TraceEvent]:
        return [event for event in self.events if event.kind == "edge"]


def _run_from_row(row: sqlite3.Row, events: tuple[TraceEvent, ...] = ()) -> RunTrace:
    return RunTrace(
        run_id=row["run_id"],
        user_id=row["user_id"],
        graph_version=row["graph_version"],
        question=row["question"],
        status=row["status"],
        outcome=row["outcome"],
        answer=row["answer"],
        attempts=row["attempts"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error_node=row["error_node"],
        error_type=row["error_type"],
        error_message=row["error_message"],
        events=events,
    )


def _event_from_row(row: sqlite3.Row) -> TraceEvent:
    return TraceEvent(
        seq=row["seq"],
        attempt=row["attempt"],
        kind=row["kind"],
        name=row["name"],
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        duration_ms=row["duration_ms"],
        input=_load(row["input_json"]),
        output=_load(row["output_json"]),
        error_type=row["error_type"],
        error_message=row["error_message"],
    )


class TraceStore:
    """Reads and writes run traces in one SQLite file; creates its tables on first use."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    # --- Writing -------------------------------------------------------------------

    def start_run(
        self, run_id: str, *, question: str, user_id: str | None, graph_version: str
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO agent_runs (run_id, user_id, graph_version, question, status, "
                "started_at) VALUES (?, ?, ?, ?, 'running', ?)",
                (run_id, user_id, graph_version, question, utc_now()),
            )

    def start_attempt(self, run_id: str) -> int:
        """Mark a failed run as running again; returns the new attempt number."""
        with self._transaction() as connection:
            connection.execute(
                "UPDATE agent_runs SET attempts = attempts + 1, status = 'running', "
                "finished_at = NULL, error_node = NULL, error_type = NULL, error_message = NULL "
                "WHERE run_id = ?",
                (run_id,),
            )
            row = connection.execute(
                "SELECT attempts FROM agent_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return int(row["attempts"])

    def add_event(
        self,
        run_id: str,
        *,
        attempt: int,
        kind: EventKind,
        name: str,
        status: EventStatus,
        started_at: str,
        finished_at: str | None = None,
        duration_ms: float | None = None,
        input: Any = None,
        output: Any = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> int:
        """Append one event; returns its `seq`."""
        with self._transaction() as connection:
            seq = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM agent_run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO agent_run_events (run_id, seq, attempt, kind, name, status, "
                "started_at, finished_at, duration_ms, input_json, output_json, error_type, "
                "error_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    seq,
                    attempt,
                    kind,
                    name,
                    status,
                    started_at,
                    finished_at,
                    duration_ms,
                    _dump(input),
                    _dump(output),
                    error_type,
                    error_message,
                ),
            )
        return int(seq)

    def finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        outcome: str | None = None,
        answer: str | None = None,
        error_node: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE agent_runs SET status = ?, outcome = ?, answer = ?, finished_at = ?, "
                "error_node = ?, error_type = ?, error_message = ? WHERE run_id = ?",
                (
                    status,
                    outcome,
                    answer,
                    utc_now(),
                    error_node,
                    error_type,
                    error_message,
                    run_id,
                ),
            )

    # --- Reading -------------------------------------------------------------------

    def get_trace(self, run_id: str, *, owner: str | None = None) -> RunTrace | None:
        """The run and its events in order, or None. With `owner`, only that user's run."""
        with self._transaction() as connection:
            run = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None or (owner is not None and run["user_id"] != owner):
                return None
            rows = connection.execute(
                "SELECT * FROM agent_run_events WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
        return _run_from_row(run, tuple(_event_from_row(row) for row in rows))

    def list_runs(self, *, limit: int = 20, user_id: str | None = None) -> list[RunTrace]:
        """The most recent runs, newest first, without their events."""
        query = "SELECT * FROM agent_runs"
        params: tuple[Any, ...] = ()
        if user_id is not None:
            query += " WHERE user_id = ?"
            params = (user_id,)
        query += " ORDER BY started_at DESC, rowid DESC LIMIT ?"
        with self._transaction() as connection:
            rows = connection.execute(query, (*params, limit)).fetchall()
        return [_run_from_row(row) for row in rows]


@lru_cache(maxsize=1)
def get_trace_store() -> TraceStore:
    return TraceStore(get_agent_settings().agent_store_path)
