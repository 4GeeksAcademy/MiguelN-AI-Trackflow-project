"""Status control for scheduled jobs: the `job_runs` record of every execution.

`run_job` is the one entry point a scheduled script needs. It decides whether
this execution may run at all, records it, and guarantees how the record
ends:

1. Rows a killed process left `pending` or `processing` are marked `failed`
   once they are older than any real run can be (`sweep_abandoned`).
2. Another execution holds the job (`has_processing_lock`): abort silently,
   log the cancellation, and write nothing.
3. The job already completed for this date (`has_completed_for_date`): skip,
   log it as a duplicate, and write nothing.
4. Otherwise claim a `pending` row, mark it `processing`, do the work, and
   mark it `completed`. Any exception at all, Ctrl-C and SIGTERM included,
   marks it `failed` with the error and is raised again for the caller.

The lock is the `processing` status itself, made race-free by the
`job_runs_one_active_idx` unique index: there is no lock table or column.

Every function takes the engine, so this module needs no API settings: the
caller decides which database the record goes to.
"""

import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, Final, Literal
from uuid import UUID, uuid4

from sqlalchemy import exists, func, insert, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.schema import CreateIndex, CreateTable

from services.job_runner.models import ACTIVE_STATUSES, JobRun, JobStatus, job_runs

# Older than the longest run any job can take: the nightly export bounds its
# pipeline at 3 hours, so a row still active after 6 hours belongs to a process
# that was killed (SIGKILL, power loss) before it could record its outcome.
STALE_AFTER: Final = timedelta(hours=6)

# Room for an exception class and a useful sentence; tracebacks go to the log.
MAX_ERROR_MESSAGE: Final = 2000

ABANDONED_MESSAGE: Final = "Abandoned: the run stopped without recording an outcome"

JobOutcome = Literal["completed", "skipped_duplicate", "aborted_locked"]


class JobAlreadyRunning(RuntimeError):
    """Another execution of the job holds its `pending` or `processing` row."""


class JobRunStateError(RuntimeError):
    """A run row was not in the status a transition expected (swept, or already final)."""


# --- Logging -------------------------------------------------------------------------


def job_logger(job_name: str) -> logging.Logger:
    return logging.getLogger(f"jobs.{job_name}")


def log_fields(job_name: str, target_date: date | None, status: str) -> dict[str, str]:
    """The `extra` every job log line carries, so each line names its job and status."""
    return {
        "job_name": job_name,
        "target_date": target_date.isoformat() if target_date else "-",
        "status": status,
    }


def describe_error(error: BaseException) -> str:
    """`ExceptionClass: message`, as stored in `error_message`.

    Database errors keep only their class: a driver message can quote the
    server's host, and the record outlives the log. The traceback, with the
    full message, goes to the log instead.
    """
    name = type(error).__name__
    if isinstance(error, SQLAlchemyError):
        return f"{name}: a database operation failed"
    detail = " ".join(str(error).split())
    described = f"{name}: {detail}" if detail else name
    return described[:MAX_ERROR_MESSAGE]


# --- Schema --------------------------------------------------------------------------


def ensure_schema(engine: Engine) -> None:
    """Create `job_runs`, its indexes and its RLS flag where missing. Idempotent.

    On PostgreSQL a transaction-scoped advisory lock serialises two scripts
    starting together on a fresh database, where concurrent `CREATE TABLE IF
    NOT EXISTS` can still collide. RLS is switched on only when it is off,
    because `ALTER TABLE` takes an exclusive lock on the table.
    """
    with engine.begin() as connection:
        postgresql = connection.dialect.name == "postgresql"
        if postgresql:
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('job_runs:schema'))"))
        connection.execute(CreateTable(job_runs, if_not_exists=True))
        for index in job_runs.indexes:
            connection.execute(CreateIndex(index, if_not_exists=True))
        if postgresql and not _row_level_security_on(connection):
            connection.execute(text("ALTER TABLE job_runs ENABLE ROW LEVEL SECURITY"))


def _row_level_security_on(connection: Connection) -> bool:
    return bool(
        connection.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass('job_runs')")
        ).scalar()
    )


# --- Create and update ---------------------------------------------------------------


def create_job_run(engine: Engine, job_name: str, target_date: date) -> UUID:
    """Claim the job for `target_date` with a `pending` row and return its id.

    Raises `JobAlreadyRunning` when another execution already holds the job,
    including one that claimed it a moment ago: the unique index decides the
    race that a check made just before the insert cannot.
    """
    run_id = uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(job_runs).values(
                    id=run_id, job_name=job_name, target_date=target_date, status="pending"
                )
            )
    except IntegrityError:
        if has_processing_lock(engine, job_name):
            raise JobAlreadyRunning(f"{job_name} is already running") from None
        raise
    return run_id


def _transition(
    engine: Engine, run_id: UUID, from_statuses: tuple[JobStatus, ...], **values: Any
) -> None:
    with engine.begin() as connection:
        result = connection.execute(
            update(job_runs)
            .where(job_runs.c.id == run_id, job_runs.c.status.in_(from_statuses))
            .values(**values)
        )
    if result.rowcount != 1:
        raise JobRunStateError(
            f"Run {run_id} is no longer {' or '.join(from_statuses)}; it was not updated"
        )


def mark_processing(engine: Engine, run_id: UUID) -> None:
    """`pending` -> `processing`, stamping `started_at`."""
    _transition(engine, run_id, ("pending",), status="processing", started_at=func.now())


def mark_completed(engine: Engine, run_id: UUID) -> None:
    """`processing` -> `completed`, stamping `finished_at`."""
    _transition(engine, run_id, ("processing",), status="completed", finished_at=func.now())


def mark_failed(engine: Engine, run_id: UUID, error_message: str) -> None:
    """`pending` or `processing` -> `failed`, with the error and `finished_at`."""
    _transition(
        engine,
        run_id,
        ACTIVE_STATUSES,
        status="failed",
        finished_at=func.now(),
        error_message=(error_message or "Unknown error")[:MAX_ERROR_MESSAGE],
    )


def sweep_abandoned(engine: Engine, job_name: str, older_than: timedelta = STALE_AFTER) -> int:
    """Mark `failed` the job's active rows older than `older_than`; return how many.

    Age is measured on the database clock, the one that stamped `created_at`.
    """
    with engine.begin() as connection:
        now = connection.execute(select(func.now())).scalar_one()
        result = connection.execute(
            update(job_runs)
            .where(
                job_runs.c.job_name == job_name,
                job_runs.c.status.in_(ACTIVE_STATUSES),
                job_runs.c.created_at < now - older_than,
            )
            .values(status="failed", finished_at=func.now(), error_message=ABANDONED_MESSAGE)
        )
    return result.rowcount


# --- Queries -------------------------------------------------------------------------


def has_processing_lock(engine: Engine, job_name: str) -> bool:
    """True while an execution of the job holds it: a `pending` or `processing` row."""
    with engine.connect() as connection:
        return bool(
            connection.execute(
                select(
                    exists().where(
                        job_runs.c.job_name == job_name, job_runs.c.status.in_(ACTIVE_STATUSES)
                    )
                )
            ).scalar()
        )


def has_completed_for_date(engine: Engine, job_name: str, target_date: date) -> bool:
    """True when the job already completed for `target_date` (both must match)."""
    with engine.connect() as connection:
        return bool(
            connection.execute(
                select(
                    exists().where(
                        job_runs.c.job_name == job_name,
                        job_runs.c.target_date == target_date,
                        job_runs.c.status == "completed",
                    )
                )
            ).scalar()
        )


def get_job_run(engine: Engine, run_id: UUID) -> JobRun | None:
    with engine.connect() as connection:
        row = connection.execute(select(job_runs).where(job_runs.c.id == run_id)).first()
    return JobRun(**row._mapping) if row is not None else None


def list_job_runs(
    engine: Engine, job_name: str, target_date: date | None = None, limit: int = 20
) -> list[JobRun]:
    """The job's most recent runs, newest first, optionally for one date only."""
    query = select(job_runs).where(job_runs.c.job_name == job_name)
    if target_date is not None:
        query = query.where(job_runs.c.target_date == target_date)
    query = query.order_by(job_runs.c.created_at.desc()).limit(limit)
    with engine.connect() as connection:
        return [JobRun(**row._mapping) for row in connection.execute(query)]


# --- The status control --------------------------------------------------------------


def run_job(
    engine: Engine,
    job_name: str,
    target_date: date,
    work: Callable[[], object],
    *,
    stale_after: timedelta = STALE_AFTER,
) -> JobOutcome:
    """Run `work` once for `job_name` and `target_date`, under the lock, and record it.

    Returns `aborted_locked` or `skipped_duplicate` without calling `work` or
    writing a row. Every exception is logged at ERROR before it propagates;
    one raised after the row exists first marks that row `failed`.
    """
    logger = job_logger(job_name)

    def log(level: int, status: str, message: str, *args: object, **kwargs: Any) -> None:
        logger.log(level, message, *args, extra=log_fields(job_name, target_date, status), **kwargs)

    try:
        swept = sweep_abandoned(engine, job_name, stale_after)
        if swept:
            log(logging.WARNING, "failed", "Marked %d abandoned run(s) failed", swept)
        if has_processing_lock(engine, job_name):
            log(logging.INFO, "aborted", "Another execution is in progress; this one was cancelled")
            return "aborted_locked"
        if has_completed_for_date(engine, job_name, target_date):
            log(logging.INFO, "skipped", "Already completed for this date; skipped as a duplicate")
            return "skipped_duplicate"
        run_id = create_job_run(engine, job_name, target_date)
    except JobAlreadyRunning:
        log(logging.INFO, "aborted", "Another execution started first; this one was cancelled")
        return "aborted_locked"
    except BaseException as error:
        log(logging.ERROR, "failed", "Could not check job_runs: %s", describe_error(error))
        raise
    log(logging.INFO, "pending", "Run %s recorded", run_id)

    try:
        mark_processing(engine, run_id)
        log(logging.INFO, "processing", "Started")
        work()
        mark_completed(engine, run_id)
    except BaseException as error:
        message = describe_error(error)
        try:
            mark_failed(engine, run_id, message)
            status = "failed"
        except Exception as unrecorded:  # noqa: BLE001 - the original error matters more
            status = "processing"
            log(
                logging.ERROR,
                status,
                "Could not mark run %s failed (%s); the next run's sweep will",
                run_id,
                describe_error(unrecorded),
            )
        log(logging.ERROR, status, "Failed: %s", message, exc_info=error)
        raise
    log(logging.INFO, "completed", "Finished")
    return "completed"
