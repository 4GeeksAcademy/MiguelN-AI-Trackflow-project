"""`reporting.pipeline_runs`: one row per run, shared by every future pipeline.

The row is opened before anything is read and fixes the run's snapshot
(`arrival_cutoff`). It is marked `completed` only inside the load transaction,
so `completed` always means "the data is committed" (`PIPELINE_DESIGN.md` 3.4).

Concurrency (3.3): `open_run` holds a transaction-scoped advisory lock while it
decides, so two runs starting together cannot both get the slot. It is
idempotent per Prefect flow run id: a retried task or flow finds its own row
and resumes it. A different run that finds the slot taken records a `skipped`
row instead of failing.

Readers (the API) get `None` instead of an error when the schema does not exist
yet, so a database the pipeline never ran against reads as "never computed".
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal, cast
from uuid import UUID

from sqlalchemy import and_, func, insert, select, text, update
from sqlalchemy.engine import Connection, Engine

from data.pipelines.weekly_warehouse_client_performance.schema import (
    PIPELINE_NAME,
    advisory_key,
    pipeline_runs,
    reporting_schema,
)

# Older than the longest possible run: 4 attempts x 30 min plus 3 x 15 min of
# retry delay. A process killed outright can leave Prefect showing `Running`
# forever, so past this age a row is abandoned whatever Prefect says.
ABANDON_AFTER: Final = timedelta(hours=3)

Trigger = Literal["schedule", "manual", "backfill"]
ErrorStage = Literal["extract", "transform", "validate", "load"]
# What `fail_run` did: marked the row failed, found it already final (a run
# that skipped itself, say), or found no row at all for that flow run.
FailOutcome = Literal["failed", "unchanged", "missing"]

# Exposed by `GET /reporting/pipeline-runs/latest`. `flow_run_id`,
# `triggered_by` and `error_message` stay internal (design 5.3).
PUBLIC_FIELDS: Final = (
    "pipeline_name",
    "status",
    "trigger",
    "started_at",
    "finished_at",
    "weeks_from",
    "weeks_to",
    "arrival_cutoff",
    "events_read",
    "events_excluded",
    "rows_inserted",
    "rows_updated",
    "rows_unchanged",
    "rows_deleted",
    "weeks_revised",
    "late_events_outside_window",
    "telemetry_dropped",
    "min_capture_coverage",
    "quality_status",
    "error_stage",
    "error_class",
)


@dataclass(frozen=True)
class RunHandle:
    """What the flow needs to know about its run row."""

    run_id: UUID
    status: Literal["running", "completed", "skipped"]
    arrival_cutoff: datetime
    previous_cutoff: datetime | None  # the latest completed run's cutoff, for late events
    blocked_by_run_id: UUID | None = None
    resumed: bool = False


class RunNotRunning(RuntimeError):
    """The run row left `running` while the run was still working (swept, or failed)."""


def _lock(connection: Connection) -> None:
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": advisory_key(connection, "runs")},
    )


def _has_run_log(connection: Connection) -> bool:
    name = f'"{reporting_schema(connection)}".pipeline_runs'
    return (
        connection.execute(text("SELECT to_regclass(:name)"), {"name": name}).scalar() is not None
    )


def _previous_cutoff(connection: Connection, pipeline_name: str) -> datetime | None:
    return connection.execute(
        select(func.max(pipeline_runs.c.arrival_cutoff)).where(
            pipeline_runs.c.pipeline_name == pipeline_name,
            pipeline_runs.c.status == "completed",
        )
    ).scalar()


def _sweep_abandoned(
    connection: Connection,
    pipeline_name: str,
    own_flow_run_id: UUID,
    flow_run_is_terminal: Callable[[UUID], bool | None],
) -> None:
    """Mark `failed` the running rows whose flow run is dead.

    Dead means Prefect reports the flow run finished (`Failed`, `Crashed`,
    `Cancelled`), or the row is older than `ABANDON_AFTER`, which no live run
    can be given the flow's own timeouts and retries. A younger row whose
    flow run Prefect reports alive - including one waiting between retries -
    or cannot report on at all, is left alone.
    """
    now = connection.execute(select(func.now())).scalar_one()
    running = connection.execute(
        select(pipeline_runs.c.id, pipeline_runs.c.flow_run_id, pipeline_runs.c.started_at).where(
            pipeline_runs.c.pipeline_name == pipeline_name,
            pipeline_runs.c.status == "running",
            pipeline_runs.c.flow_run_id.is_distinct_from(own_flow_run_id),
        )
    ).all()
    for row in running:
        expired = now - row.started_at >= ABANDON_AFTER
        if not expired and not (row.flow_run_id and flow_run_is_terminal(row.flow_run_id)):
            continue
        connection.execute(
            update(pipeline_runs)
            .where(pipeline_runs.c.id == row.id, pipeline_runs.c.status == "running")
            .values(
                status="failed",
                finished_at=func.now(),
                error_class="Abandoned",
                error_message="The run stopped without recording an outcome",
            )
        )


def open_run(
    engine: Engine,
    *,
    flow_run_id: UUID,
    trigger: Trigger,
    triggered_by: UUID | None,
    weeks_from: date,
    weeks_to: date,
    code_version: str | None,
    pipeline_name: str = PIPELINE_NAME,
    flow_run_is_terminal: Callable[[UUID], bool | None] = lambda _flow_run_id: None,
) -> RunHandle:
    """Open, resume or skip this flow run's row, and fix its `arrival_cutoff`.

    - The flow run already has a row: resume it as it is. A `running` row keeps
      its cutoff, so the retry reads the same snapshot. A `failed` row (a
      Prefect "retry" of a failed flow run) is reopened if the slot is free.
    - Another run holds the slot: record a `skipped` row naming it.
    - Otherwise: insert a `running` row whose cutoff is the database's now().
    """
    with engine.begin() as connection:
        _lock(connection)
        previous = _previous_cutoff(connection, pipeline_name)
        own = connection.execute(
            select(pipeline_runs).where(pipeline_runs.c.flow_run_id == flow_run_id)
        ).first()

        _sweep_abandoned(connection, pipeline_name, flow_run_id, flow_run_is_terminal)
        holder = connection.execute(
            select(pipeline_runs.c.id).where(
                pipeline_runs.c.pipeline_name == pipeline_name,
                pipeline_runs.c.status == "running",
                pipeline_runs.c.flow_run_id.is_distinct_from(flow_run_id),
            )
        ).scalar()

        if own is not None:
            status: Literal["running", "completed", "skipped"]
            if own.status == "failed" and holder is None:
                connection.execute(
                    update(pipeline_runs)
                    .where(pipeline_runs.c.id == own.id)
                    .values(
                        status="running",
                        finished_at=None,
                        error_stage=None,
                        error_class=None,
                        error_message=None,
                    )
                )
                status = "running"
            elif own.status in ("running", "completed", "skipped"):
                status = cast(Literal["running", "completed", "skipped"], own.status)
            else:
                return RunHandle(own.id, "skipped", own.arrival_cutoff, previous, holder, True)
            return RunHandle(
                own.id, status, own.arrival_cutoff, previous, own.blocked_by_run_id, True
            )

        values: dict[str, Any] = {
            "pipeline_name": pipeline_name,
            "flow_run_id": flow_run_id,
            "trigger": trigger,
            "triggered_by": triggered_by,
            "weeks_from": weeks_from,
            "weeks_to": weeks_to,
            "arrival_cutoff": func.now(),
            "code_version": code_version,
        }
        if holder is not None:
            values |= {
                "status": "skipped",
                "finished_at": func.now(),
                "blocked_by_run_id": holder,
                "error_class": "ConcurrentRun",
                "error_message": "Another run of this pipeline was in progress",
            }
        else:
            values["status"] = "running"
        row = connection.execute(
            insert(pipeline_runs)
            .values(**values)
            .returning(pipeline_runs.c.id, pipeline_runs.c.status, pipeline_runs.c.arrival_cutoff)
        ).one()
        return RunHandle(row.id, row.status, row.arrival_cutoff, previous, holder)


def complete_run(connection: Connection, run_id: UUID, **fields: Any) -> None:
    """Mark the run `completed` inside the caller's (load) transaction."""
    result = connection.execute(
        update(pipeline_runs)
        .where(pipeline_runs.c.id == run_id, pipeline_runs.c.status == "running")
        .values(status="completed", finished_at=func.now(), **fields)
    )
    if result.rowcount != 1:
        raise RunNotRunning("The run row is no longer running; the load was rolled back")


def skip_run(
    engine: Engine,
    run_id: UUID,
    *,
    error_class: str,
    blocked_by_run_id: UUID,
    message: str,
    events_read: int | None = None,
) -> None:
    """Record that a running run gave way to another (`StaleSnapshot`), and what it had read."""
    with engine.begin() as connection:
        connection.execute(
            update(pipeline_runs)
            .where(pipeline_runs.c.id == run_id, pipeline_runs.c.status == "running")
            .values(
                status="skipped",
                finished_at=func.now(),
                blocked_by_run_id=blocked_by_run_id,
                error_class=error_class,
                error_message=message,
                events_read=events_read,
            )
        )


def fail_run(
    engine: Engine,
    *,
    flow_run_id: UUID,
    error_class: str,
    error_message: str,
    error_stage: ErrorStage | None = None,
    events_read: int | None = None,
) -> FailOutcome:
    """Mark this flow run's row `failed` if it is still running, with what it had read.

    Called from the flow's state hooks. Returns `missing` when the flow run has
    no row - it failed before `open_run` could write one, or the schema does
    not exist yet - so the caller can record the run somewhere else. When the
    database itself is unreachable this raises, with the same consequence.
    """
    with engine.begin() as connection:
        if not _has_run_log(connection):
            return "missing"
        result = connection.execute(
            update(pipeline_runs)
            .where(pipeline_runs.c.flow_run_id == flow_run_id, pipeline_runs.c.status == "running")
            .values(
                status="failed",
                finished_at=func.now(),
                error_stage=error_stage,
                error_class=error_class,
                error_message=error_message,
                events_read=events_read,
            )
        )
        if result.rowcount == 1:
            return "failed"
        exists = connection.execute(
            select(pipeline_runs.c.id).where(pipeline_runs.c.flow_run_id == flow_run_id)
        ).first()
        return "unchanged" if exists is not None else "missing"


# --- Readers (the API) ---------------------------------------------------------------


def get_latest_run(engine: Engine, pipeline_name: str = PIPELINE_NAME) -> dict[str, Any] | None:
    """The latest run that did something, as `PUBLIC_FIELDS`, or None if there is none.

    `skipped` rows are left out: a run that lost a race would otherwise hide
    the one actually running or the last one that loaded data.
    """
    with engine.connect() as connection:
        if not _has_run_log(connection):
            return None
        row = connection.execute(
            select(*(pipeline_runs.c[name] for name in PUBLIC_FIELDS))
            .where(
                pipeline_runs.c.pipeline_name == pipeline_name, pipeline_runs.c.status != "skipped"
            )
            .order_by(pipeline_runs.c.started_at.desc())
            .limit(1)
        ).first()
    return dict(row._mapping) if row is not None else None


def latest_completed_week(engine: Engine, pipeline_name: str = PIPELINE_NAME) -> date | None:
    """The most recent computed week: the latest `weeks_to` of a completed run.

    Not `max(week_start)` of the report table, which would skip a week in which
    no client had any activity.
    """
    with engine.connect() as connection:
        if not _has_run_log(connection):
            return None
        return connection.execute(
            select(func.max(pipeline_runs.c.weeks_to)).where(
                pipeline_runs.c.pipeline_name == pipeline_name,
                pipeline_runs.c.status == "completed",
            )
        ).scalar()


def is_week_computed(engine: Engine, week: date, pipeline_name: str = PIPELINE_NAME) -> bool:
    """True when a completed run covered `week`, even if it produced no rows."""
    with engine.connect() as connection:
        if not _has_run_log(connection):
            return False
        return (
            connection.execute(
                select(pipeline_runs.c.id)
                .where(
                    and_(
                        pipeline_runs.c.pipeline_name == pipeline_name,
                        pipeline_runs.c.status == "completed",
                        pipeline_runs.c.weeks_from <= week,
                        pipeline_runs.c.weeks_to >= week,
                    )
                )
                .limit(1)
            ).first()
            is not None
        )


def is_running(engine: Engine, pipeline_name: str = PIPELINE_NAME) -> bool:
    """True while a run of the pipeline holds the slot. The API answers 409 then."""
    with engine.connect() as connection:
        if not _has_run_log(connection):
            return False
        return (
            connection.execute(
                select(pipeline_runs.c.id)
                .where(
                    pipeline_runs.c.pipeline_name == pipeline_name,
                    pipeline_runs.c.status == "running",
                )
                .limit(1)
            ).first()
            is not None
        )
