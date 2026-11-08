"""`services/job_runner` on SQLite: the lifecycle, the lock, per-day idempotency, the sweep.

The table definition compiles on SQLite, so these tests run the real SQL,
partial unique index included, without PostgreSQL. The PostgreSQL-only parts
(the migration file, RLS, the advisory lock) are in
`tests/test_job_runner_postgres.py`, which is opt-in.
"""

import logging
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, insert, inspect, update
from sqlalchemy.exc import IntegrityError, OperationalError

from services.job_runner import service
from services.job_runner.models import job_runs

JOB = "nightly_export"
DAY = date(2026, 10, 24)
AT = datetime(2026, 10, 25, 3, 30, tzinfo=timezone.utc)


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.sqlite'}")
    service.ensure_schema(engine)
    yield engine
    engine.dispose()


def runs(engine, job_name=JOB):
    return service.list_job_runs(engine, job_name, limit=100)


def active_run(engine, job_name=JOB, target_date=DAY):
    """A run that is working right now: claimed and marked processing."""
    run_id = service.create_job_run(engine, job_name, target_date)
    service.mark_processing(engine, run_id)
    return run_id


def completed_run(engine, job_name=JOB, target_date=DAY):
    run_id = active_run(engine, job_name, target_date)
    service.mark_completed(engine, run_id)
    return run_id


def backdate(engine, run_id, hours):
    created_at = service.get_job_run(engine, run_id).created_at
    with engine.begin() as connection:
        connection.execute(
            update(job_runs)
            .where(job_runs.c.id == run_id)
            .values(created_at=created_at - timedelta(hours=hours))
        )


# --- Schema --------------------------------------------------------------------------


def test_ensure_schema_is_idempotent_and_builds_the_ticket_columns(engine):
    service.ensure_schema(engine)

    inspector = inspect(engine)
    columns = [column["name"] for column in inspector.get_columns("job_runs")]
    assert columns == [
        "id",
        "job_name",
        "target_date",
        "status",
        "started_at",
        "finished_at",
        "error_message",
        "created_at",
    ]
    nullable = {column["name"]: column["nullable"] for column in inspector.get_columns("job_runs")}
    assert nullable["target_date"] is False
    indexes = {index["name"]: index for index in inspector.get_indexes("job_runs")}
    assert indexes["job_runs_job_name_target_date_idx"]["column_names"] == [
        "job_name",
        "target_date",
    ]
    assert indexes["job_runs_one_active_idx"]["unique"]


@pytest.mark.parametrize(
    "values",
    [
        {"status": "running"},
        {"status": "failed", "finished_at": AT},
        {"status": "completed", "started_at": AT},
        {"status": "processing"},
    ],
    ids=["unknown status", "failed without error", "completed unfinished", "processing unstarted"],
)
def test_the_table_refuses_rows_that_break_the_lifecycle(engine, values):
    row = {"job_name": JOB, "target_date": DAY, "status": "pending"} | values
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(insert(job_runs).values(**row))


# --- Create and update ---------------------------------------------------------------


def test_a_run_moves_from_pending_to_processing_to_completed(engine):
    run_id = service.create_job_run(engine, JOB, DAY)
    claimed = service.get_job_run(engine, run_id)
    assert (claimed.status, claimed.job_name, claimed.target_date) == ("pending", JOB, DAY)
    assert claimed.started_at is None and claimed.finished_at is None
    assert claimed.created_at is not None

    service.mark_processing(engine, run_id)
    working = service.get_job_run(engine, run_id)
    assert working.status == "processing" and working.started_at is not None

    service.mark_completed(engine, run_id)
    done = service.get_job_run(engine, run_id)
    assert done.status == "completed"
    assert done.finished_at is not None and done.error_message is None


@pytest.mark.parametrize("started", [False, True], ids=["from pending", "from processing"])
def test_mark_failed_records_the_error_and_the_finish_time(engine, started):
    run_id = service.create_job_run(engine, JOB, DAY)
    if started:
        service.mark_processing(engine, run_id)

    service.mark_failed(engine, run_id, "ValueError: boom")

    failed = service.get_job_run(engine, run_id)
    assert failed.status == "failed"
    assert failed.error_message == "ValueError: boom"
    assert failed.finished_at is not None


def test_transitions_refuse_a_row_in_the_wrong_status(engine):
    pending = service.create_job_run(engine, JOB, DAY)
    with pytest.raises(service.JobRunStateError):
        service.mark_completed(engine, pending)

    service.mark_processing(engine, pending)
    service.mark_completed(engine, pending)
    with pytest.raises(service.JobRunStateError):
        service.mark_failed(engine, pending, "too late")
    with pytest.raises(service.JobRunStateError):
        service.mark_processing(engine, pending)
    assert service.get_job_run(engine, pending).status == "completed"


def test_a_second_claim_while_the_job_is_active_is_refused(engine):
    active_run(engine)

    # Even for another day: the lock is per job, not per date.
    with pytest.raises(service.JobAlreadyRunning):
        service.create_job_run(engine, JOB, DAY - timedelta(days=1))
    assert len(runs(engine)) == 1

    # Another job is not blocked.
    service.create_job_run(engine, "weekly_backup", DAY)


def test_the_job_can_be_claimed_again_once_its_run_has_finished(engine):
    completed_run(engine)
    failed = service.create_job_run(engine, JOB, DAY + timedelta(days=1))
    service.mark_failed(engine, failed, "RuntimeError: once")

    service.create_job_run(engine, JOB, DAY + timedelta(days=1))
    assert len(runs(engine)) == 3


# --- Queries -------------------------------------------------------------------------


def test_has_processing_lock_covers_pending_and_processing_rows_of_that_job(engine):
    assert not service.has_processing_lock(engine, JOB)

    run_id = service.create_job_run(engine, JOB, DAY)
    assert service.has_processing_lock(engine, JOB)
    assert not service.has_processing_lock(engine, "weekly_backup")

    service.mark_processing(engine, run_id)
    assert service.has_processing_lock(engine, JOB)

    service.mark_completed(engine, run_id)
    assert not service.has_processing_lock(engine, JOB)


def test_has_completed_for_date_needs_both_the_job_and_the_date(engine):
    completed_run(engine)
    failed = service.create_job_run(engine, JOB, DAY - timedelta(days=1))
    service.mark_failed(engine, failed, "RuntimeError: no")

    assert service.has_completed_for_date(engine, JOB, DAY)
    assert not service.has_completed_for_date(engine, JOB, DAY + timedelta(days=1))
    assert not service.has_completed_for_date(engine, JOB, DAY - timedelta(days=1))
    assert not service.has_completed_for_date(engine, "weekly_backup", DAY)


def test_list_job_runs_filters_by_date(engine):
    completed_run(engine)
    completed_run(engine, target_date=DAY - timedelta(days=1))

    assert [run.target_date for run in service.list_job_runs(engine, JOB, DAY)] == [DAY]
    assert len(service.list_job_runs(engine, JOB)) == 2
    assert service.get_job_run(engine, service.uuid4()) is None


# --- Sweep ---------------------------------------------------------------------------


def test_the_sweep_fails_only_stale_active_rows_of_that_job(engine):
    stale = active_run(engine)
    backdate(engine, stale, hours=7)
    old_completed = completed_run(engine, "weekly_backup")
    backdate(engine, old_completed, hours=30)
    other_job_stale = service.create_job_run(engine, "hourly_ping", DAY)
    backdate(engine, other_job_stale, hours=7)

    assert service.sweep_abandoned(engine, JOB) == 1

    swept = service.get_job_run(engine, stale)
    assert swept.status == "failed"
    assert swept.error_message == service.ABANDONED_MESSAGE
    assert swept.finished_at is not None
    assert service.get_job_run(engine, old_completed).status == "completed"
    assert service.get_job_run(engine, other_job_stale).status == "pending"


def test_the_sweep_leaves_a_young_run_alone(engine):
    young = active_run(engine)
    backdate(engine, young, hours=1)

    assert service.sweep_abandoned(engine, JOB) == 0
    assert service.get_job_run(engine, young).status == "processing"


# --- run_job: the status control -----------------------------------------------------


class Work:
    """Counts its calls; raises `error` when given one."""

    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    def __call__(self):
        self.calls += 1
        if self.error is not None:
            raise self.error


def test_run_job_records_a_completed_run(engine):
    work = Work()

    assert service.run_job(engine, JOB, DAY, work) == "completed"

    assert work.calls == 1
    [run] = runs(engine)
    assert (run.status, run.target_date, run.error_message) == ("completed", DAY, None)
    assert run.started_at is not None and run.finished_at is not None


def test_running_twice_on_the_same_day_does_the_work_once(engine, caplog):
    caplog.set_level(logging.INFO, logger="jobs")
    work = Work()

    service.run_job(engine, JOB, DAY, work)
    assert service.run_job(engine, JOB, DAY, work) == "skipped_duplicate"

    assert work.calls == 1
    assert len(runs(engine)) == 1
    skipped = caplog.records[-1]
    assert skipped.levelno == logging.INFO
    assert skipped.status == "skipped"
    assert "duplicate" in skipped.getMessage()


def test_the_next_day_is_not_a_duplicate(engine):
    work = Work()
    service.run_job(engine, JOB, DAY, work)

    assert service.run_job(engine, JOB, DAY + timedelta(days=1), work) == "completed"
    assert work.calls == 2


def test_a_failed_day_runs_again_and_keeps_both_records(engine):
    with pytest.raises(RuntimeError):
        service.run_job(engine, JOB, DAY, Work(RuntimeError("database away")))

    retry = Work()
    assert service.run_job(engine, JOB, DAY, retry) == "completed"

    assert retry.calls == 1
    assert sorted(run.status for run in runs(engine)) == ["completed", "failed"]


def test_run_job_aborts_silently_while_another_execution_holds_the_job(engine, caplog):
    caplog.set_level(logging.INFO, logger="jobs")
    holder = active_run(engine, target_date=DAY - timedelta(days=1))
    work = Work()

    assert service.run_job(engine, JOB, DAY, work) == "aborted_locked"

    assert work.calls == 0
    assert [run.id for run in runs(engine)] == [holder]
    assert service.get_job_run(engine, holder).status == "processing"
    cancelled = caplog.records[-1]
    assert (cancelled.levelno, cancelled.status) == (logging.INFO, "aborted")
    assert "cancelled" in cancelled.getMessage()


def test_losing_the_claim_race_is_an_abort_too(engine, monkeypatch):
    """Both instances checked before either inserted; the unique index decides."""
    active_run(engine)
    real_check = service.has_processing_lock
    checks = []

    def misses_the_rival_once(engine, job_name):
        checks.append(job_name)
        return len(checks) > 1 and real_check(engine, job_name)

    monkeypatch.setattr(service, "has_processing_lock", misses_the_rival_once)
    work = Work()

    assert service.run_job(engine, JOB, DAY + timedelta(days=1), work) == "aborted_locked"
    assert work.calls == 0
    assert len(runs(engine)) == 1


@pytest.mark.parametrize(
    ("error", "stored"),
    [
        (ValueError("bad   row\n in batch"), "ValueError: bad row in batch"),
        (KeyboardInterrupt(), "KeyboardInterrupt"),
        (SystemExit(3), "SystemExit: 3"),
    ],
    ids=["exception", "ctrl-c", "exit"],
)
def test_any_error_marks_the_run_failed_and_is_raised_again(engine, caplog, error, stored):
    caplog.set_level(logging.INFO, logger="jobs")

    with pytest.raises(type(error)):
        service.run_job(engine, JOB, DAY, Work(error))

    [run] = runs(engine)
    assert run.status == "failed"
    assert run.error_message == stored
    assert run.finished_at is not None
    assert not service.has_processing_lock(engine, JOB)
    failure = caplog.records[-1]
    assert (failure.levelno, failure.status) == (logging.ERROR, "failed")
    assert failure.exc_info is not None


def test_a_database_error_is_stored_without_the_driver_message(engine):
    error = OperationalError("SELECT 1", {}, Exception("could not reach db.internal.example"))

    with pytest.raises(OperationalError):
        service.run_job(engine, JOB, DAY, Work(error))

    [run] = runs(engine)
    assert run.error_message == "OperationalError: a database operation failed"


def test_a_failure_that_cannot_be_recorded_is_logged_for_the_sweep(engine, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="jobs")

    def database_gone(*args, **kwargs):
        raise OperationalError("UPDATE", {}, Exception("gone"))

    monkeypatch.setattr(service, "mark_failed", database_gone)

    with pytest.raises(LookupError):
        service.run_job(engine, JOB, DAY, Work(LookupError("missing")))

    [run] = runs(engine)
    assert run.status == "processing"  # left for the next run's sweep
    messages = [record.getMessage() for record in caplog.records if record.levelno == logging.ERROR]
    assert any("next run's sweep" in message for message in messages)
    assert messages[-1] == "Failed: LookupError: missing"


def test_run_job_sweeps_an_abandoned_run_before_checking_the_lock(engine, caplog):
    caplog.set_level(logging.INFO, logger="jobs")
    abandoned = active_run(engine, target_date=DAY - timedelta(days=1))
    backdate(engine, abandoned, hours=8)

    assert service.run_job(engine, JOB, DAY, Work()) == "completed"

    assert service.get_job_run(engine, abandoned).status == "failed"
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_an_error_before_the_claim_is_logged_and_writes_nothing(engine, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="jobs")

    def unreachable(*args, **kwargs):
        raise OperationalError("SELECT", {}, Exception("timeout"))

    monkeypatch.setattr(service, "sweep_abandoned", unreachable)

    with pytest.raises(OperationalError):
        service.run_job(engine, JOB, DAY, Work())

    assert runs(engine) == []
    failure = caplog.records[-1]
    assert (failure.levelno, failure.status) == (logging.ERROR, "failed")


def test_every_log_line_names_the_job_the_date_and_the_status(engine, caplog):
    caplog.set_level(logging.INFO, logger="jobs")

    service.run_job(engine, JOB, DAY, Work())

    assert [(record.levelname, record.status) for record in caplog.records] == [
        ("INFO", "pending"),
        ("INFO", "processing"),
        ("INFO", "completed"),
    ]
    for record in caplog.records:
        assert record.name == f"jobs.{JOB}"
        assert (record.job_name, record.target_date) == (JOB, DAY.isoformat())


def test_describe_error_keeps_the_stored_message_bounded():
    described = service.describe_error(RuntimeError("x" * 5000))
    assert described.startswith("RuntimeError: x")
    assert len(described) == service.MAX_ERROR_MESSAGE
