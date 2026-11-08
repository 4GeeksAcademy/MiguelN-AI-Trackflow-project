"""The `task_dead_letters` table: the Dead Letter Queue of Celery tasks (DEV-55).

A task lands here when it fails for good: it used up its retries
(`retries_exhausted`), or it raised an error that is not worth retrying
(`not_retryable`, a bug or bad input). Each row records which task failed, its
arguments (enough to replay it), the attempt it failed on, the error and
when it failed.

`task_id` is unique. With late acknowledgement a worker killed between this
insert and the broker's ack gets the message redelivered, and the same final
failure is recorded again; the insert skips it instead of failing.

`failed_at` is the worker's clock at the failure; `recorded_at` is the
database's clock at the insert. They differ when the database was down at the
failure and the row arrived later through the `tasks.record_dead_letter`
recorder task (services/tasks/base.py).

Like `job_runs`, the table lives in `public`, is named without a schema (the
PostgreSQL tests redirect it with `search_path`), takes ids generated in
Python (valid on SQLite for the unit tests), and every function takes the
engine, so the module needs no settings. `migrations/001_create_task_dead_letters.sql`
is the same table as reviewable SQL.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal, cast, get_args
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    select,
    text,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.schema import CreateIndex, CreateTable

DeadLetterReason = Literal["retries_exhausted", "not_retryable"]
REASONS: Final[tuple[DeadLetterReason, ...]] = get_args(DeadLetterReason)

metadata = MetaData()

task_dead_letters = Table(
    "task_dead_letters",
    metadata,
    Column("id", Uuid, primary_key=True, default=uuid4),
    Column("task_id", Text, nullable=False),
    Column("task_name", Text, nullable=False),
    Column("args", JSON().with_variant(JSONB, "postgresql"), nullable=False),
    Column("kwargs", JSON().with_variant(JSONB, "postgresql"), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("reason", Text, nullable=False),
    Column("error_message", Text, nullable=False),
    Column("failed_at", DateTime(timezone=True), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("task_id", name="task_dead_letters_task_id_key"),
    CheckConstraint("attempt >= 1", name="task_dead_letters_attempt_ck"),
    CheckConstraint(
        "reason in ('retries_exhausted', 'not_retryable')", name="task_dead_letters_reason_ck"
    ),
)
Index("task_dead_letters_failed_at_idx", task_dead_letters.c.failed_at)


@dataclass(frozen=True)
class DeadLetter:
    """One final task failure, as recorded in `task_dead_letters`."""

    task_id: str
    task_name: str
    args: list[Any]
    kwargs: dict[str, Any]
    attempt: int
    reason: DeadLetterReason
    error_message: str
    failed_at: datetime

    def to_message(self) -> dict[str, Any]:
        """JSON-safe form, the argument of the `tasks.record_dead_letter` recorder task."""
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "args": self.args,
            "kwargs": self.kwargs,
            "attempt": self.attempt,
            "reason": self.reason,
            "error_message": self.error_message,
            "failed_at": self.failed_at.isoformat(),
        }

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> "DeadLetter":
        reason = message["reason"]
        if reason not in REASONS:
            raise ValueError(f"Unknown dead letter reason: {reason!r}")
        return cls(
            task_id=str(message["task_id"]),
            task_name=str(message["task_name"]),
            args=list(message["args"]),
            kwargs=dict(message["kwargs"]),
            attempt=int(message["attempt"]),
            reason=cast(DeadLetterReason, reason),
            error_message=str(message["error_message"]),
            failed_at=datetime.fromisoformat(message["failed_at"]),
        )


# --- Schema --------------------------------------------------------------------------


def ensure_schema(engine: Engine) -> None:
    """Create `task_dead_letters`, its index and its RLS flag where missing. Idempotent.

    The same pattern as `job_runs`: a transaction-scoped advisory lock on
    PostgreSQL so two workers starting together cannot collide, and RLS
    switched on only when it is off.
    """
    with engine.begin() as connection:
        is_postgresql = connection.dialect.name == "postgresql"
        if is_postgresql:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('task_dead_letters:schema'))")
            )
        connection.execute(CreateTable(task_dead_letters, if_not_exists=True))
        for index in task_dead_letters.indexes:
            connection.execute(CreateIndex(index, if_not_exists=True))
        if is_postgresql and not _row_level_security_on(connection):
            connection.execute(text("ALTER TABLE task_dead_letters ENABLE ROW LEVEL SECURITY"))


def _row_level_security_on(connection: Connection) -> bool:
    return bool(
        connection.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass('task_dead_letters')")
        ).scalar()
    )


# --- Write and read ------------------------------------------------------------------


def insert_dead_letter(engine: Engine, letter: DeadLetter) -> bool:
    """Record a final failure. False when the task id was already recorded."""
    values = {
        "id": uuid4(),
        "task_id": letter.task_id,
        "task_name": letter.task_name,
        "args": letter.args,
        "kwargs": letter.kwargs,
        "attempt": letter.attempt,
        "reason": letter.reason,
        "error_message": letter.error_message,
        "failed_at": letter.failed_at,
    }
    with engine.begin() as connection:
        dialect = postgresql if connection.dialect.name == "postgresql" else sqlite
        statement = (
            dialect.insert(task_dead_letters)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["task_id"])
        )
        return connection.execute(statement).rowcount == 1


def list_dead_letters(engine: Engine, limit: int = 100) -> list[dict[str, Any]]:
    """The most recent failures first."""
    query = (
        select(task_dead_letters)
        .order_by(task_dead_letters.c.failed_at.desc(), task_dead_letters.c.recorded_at.desc())
        .limit(limit)
    )
    with engine.connect() as connection:
        return [dict(row._mapping) for row in connection.execute(query)]
