"""The `job_runs` table: one row per execution of a scheduled job (ticket DEV-53).

It records which job ran, for which day, when it started and ended, and how
it ended. It is deliberately not `reporting.pipeline_runs`: that table belongs
to the data layer and describes what a pipeline computed (weeks, snapshot
cutoff, row counts), while this one describes the operational job that called
it. A nightly run therefore leaves one row here and, through the pipeline it
triggers, one row there.

Lifecycle: `pending` (claimed) -> `processing` (working) -> `completed` or
`failed`. Two indexes carry the rules the scheduler relies on:

- `job_runs_job_name_target_date_idx` serves the per-day idempotency check.
- `job_runs_one_active_idx` lets a job hold at most one `pending` or
  `processing` row. It is the database half of the processing lock: two
  instances that start together cannot both insert one.

The table lives in `public`, next to `telemetry_events`, and is named without
a schema, so the PostgreSQL tests can point it at a disposable schema through
`search_path`. The ids are generated in Python, which keeps the definition
valid on SQLite for the unit tests. `migrations/001_create_job_runs.sql` is
the same table as reviewable SQL.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Index,
    MetaData,
    Table,
    Text,
    Uuid,
    func,
    text,
)

JobStatus = Literal["pending", "processing", "completed", "failed"]

# The statuses that hold a job's slot: a run has claimed it and not finished.
ACTIVE_STATUSES: Final[tuple[JobStatus, ...]] = ("pending", "processing")

metadata = MetaData()

job_runs = Table(
    "job_runs",
    metadata,
    Column("id", Uuid, primary_key=True, default=uuid4),
    Column("job_name", Text, nullable=False),
    # Required: idempotency is per job and per day, never per job alone.
    Column("target_date", Date, nullable=False),
    Column("status", Text, nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("error_message", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "status in ('pending', 'processing', 'completed', 'failed')", name="job_runs_status_ck"
    ),
    # A run that is working or has completed has started; a pending run that
    # failed before it could start has not.
    CheckConstraint(
        "status in ('pending', 'failed') or started_at is not null", name="job_runs_started_ck"
    ),
    CheckConstraint(
        "(status in ('completed', 'failed')) = (finished_at is not null)",
        name="job_runs_finished_ck",
    ),
    CheckConstraint(
        "status <> 'failed' or error_message is not null", name="job_runs_error_message_ck"
    ),
)
Index("job_runs_job_name_target_date_idx", job_runs.c.job_name, job_runs.c.target_date)
Index(
    "job_runs_one_active_idx",
    job_runs.c.job_name,
    unique=True,
    postgresql_where=text("status in ('pending', 'processing')"),
    sqlite_where=text("status in ('pending', 'processing')"),
)


@dataclass(frozen=True)
class JobRun:
    """One `job_runs` row, as the query functions return it."""

    id: UUID
    job_name: str
    target_date: date
    status: JobStatus
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    created_at: datetime
