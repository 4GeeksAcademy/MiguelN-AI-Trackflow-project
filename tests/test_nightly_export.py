"""`scripts/nightly_export.py`: target date, export, pipeline trigger, and the whole run.

The job store is a real `job_runs` table on SQLite. The two things that need
PostgreSQL or a long process are replaced: the event stream (its SQL is
checked in `tests/test_job_runner_postgres.py`) and the pipeline subprocess,
whose process-group signals are recorded instead of sent.
"""

import csv
import io
import json
import logging
import re
import signal
import subprocess
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
import time_machine
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from scripts import nightly_export
from services.core.config import ConfigurationError
from services.job_runner import service as job_runner

DAY = date(2026, 10, 24)  # a Saturday
NOW = datetime(2026, 10, 26, 3, 30, tzinfo=timezone.utc)

EVENTS = [
    {
        "id": UUID("00000000-0000-4000-8000-000000000001"),
        # 21:15 in Monterrey is 03:15 UTC the next day: still inside the UTC day below.
        "timestamp": datetime(2026, 10, 23, 21, 15, tzinfo=ZoneInfo("America/Monterrey")),
        "event_type": "inbound_order_recorded",
        "payload": {"warehouse": "Monterrey", "quantity": 3, "note": "señal"},
        "tags": {"sessionId": "s-1", "section": "inventory", "schemaVersion": "1.0.0"},
        "created_at": datetime(2026, 10, 24, 3, 15, 2, tzinfo=timezone.utc),
    },
    {
        "id": UUID("00000000-0000-4000-8000-000000000002"),
        "timestamp": datetime(2026, 10, 24, 23, 59, 59, 999999, tzinfo=timezone.utc),
        "event_type": "section_viewed",
        "payload": {},
        "tags": {"userId": None},
        "created_at": datetime(2026, 10, 25, 0, 0, 1, tzinfo=timezone.utc),
    },
]

SUMMARY = {
    "pipeline_name": "weekly_warehouse_client_performance",
    "status": "completed",
    "weeks": ["2026-10-12"],
    "events_read": 12,
    "rows_inserted": 0,
    "rows_updated": 1,
    "rows_unchanged": 5,
    "rows_deleted": 0,
}


@pytest.fixture(autouse=True)
def detached_log_handlers():
    """`main` attaches a stderr handler to the `jobs` logger; drop it after each test."""
    yield
    jobs = logging.getLogger("jobs")
    for handler in list(jobs.handlers):
        if getattr(handler, "_nightly_export", False):
            jobs.removeHandler(handler)


@pytest.fixture
def raw_dir(tmp_path, monkeypatch):
    path = tmp_path / "raw"
    monkeypatch.setattr(nightly_export, "RAW_DIR", path)
    return path


def stream_of(rows, calls=None):
    @contextmanager
    def fake_stream(engine, target_date):
        if calls is not None:
            calls.append(target_date)
        yield iter(rows)

    return fake_stream


# --- Target date ---------------------------------------------------------------------


def test_the_default_target_date_is_yesterday_in_utc():
    assert nightly_export.resolve_target_date({}, now=NOW) == date(2026, 10, 25)
    # 22:00 on the 25th in Monterrey is already the 26th in UTC.
    evening = datetime(2026, 10, 25, 22, 0, tzinfo=ZoneInfo("America/Monterrey"))
    assert nightly_export.resolve_target_date({}, now=evening) == date(2026, 10, 25)
    assert nightly_export.resolve_target_date({"TARGET_DATE": "  "}, now=NOW) == date(2026, 10, 25)


@time_machine.travel(datetime(2026, 10, 26, 0, 5, tzinfo=timezone.utc), tick=False)
def test_the_default_reads_the_real_clock_in_utc():
    assert nightly_export.resolve_target_date({}) == date(2026, 10, 25)


def test_target_date_overrides_the_default():
    assert nightly_export.resolve_target_date({"TARGET_DATE": "2026-10-20"}, now=NOW) == date(
        2026, 10, 20
    )


@pytest.mark.parametrize("raw", ["2026-1-5", "20261005", "2026/10/05", "yesterday", "2026-10-05x"])
def test_a_malformed_target_date_is_refused(raw):
    with pytest.raises(nightly_export.InvalidTargetDate, match="YYYY-MM-DD"):
        nightly_export.resolve_target_date({"TARGET_DATE": raw}, now=NOW)


def test_an_impossible_calendar_date_is_refused():
    with pytest.raises(nightly_export.InvalidTargetDate, match="calendar"):
        nightly_export.resolve_target_date({"TARGET_DATE": "2026-02-30"}, now=NOW)


@pytest.mark.parametrize("raw", ["2026-10-26", "2026-12-01"], ids=["today", "future"])
def test_a_day_that_has_not_ended_in_utc_is_refused(raw):
    with pytest.raises(nightly_export.InvalidTargetDate, match="already ended"):
        nightly_export.resolve_target_date({"TARGET_DATE": raw}, now=NOW)


def test_day_bounds_are_one_utc_day_with_an_exclusive_end():
    start, end = nightly_export.day_bounds(DAY)
    assert start == datetime(2026, 10, 24, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 25, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("target", "week"),
    [
        (date(2026, 10, 25), date(2026, 10, 19)),  # Sunday: the week it ends
        (date(2026, 10, 24), date(2026, 10, 12)),  # Saturday: the week before
        (date(2026, 10, 19), date(2026, 10, 12)),  # Monday: the week before
        (date(2026, 10, 18), date(2026, 10, 12)),  # Sunday
    ],
)
def test_the_pipeline_week_is_the_last_one_closed_by_the_end_of_the_day(target, week):
    assert nightly_export.pipeline_week(target) == week


# --- Export --------------------------------------------------------------------------


def test_write_csv_copies_the_table_columns_in_utc_with_stable_json(tmp_path):
    path = tmp_path / "out.csv"

    assert nightly_export.write_csv(EVENTS, path) == 2

    with path.open(encoding="utf-8", newline="") as written:
        rows = list(csv.reader(written))
    assert rows[0] == ["id", "timestamp", "event_type", "payload", "tags", "created_at"]
    assert rows[1] == [
        "00000000-0000-4000-8000-000000000001",
        "2026-10-24T03:15:00+00:00",
        "inbound_order_recorded",
        '{"note":"señal","quantity":3,"warehouse":"Monterrey"}',
        '{"schemaVersion":"1.0.0","section":"inventory","sessionId":"s-1"}',
        "2026-10-24T03:15:02+00:00",
    ]
    assert rows[2][1] == "2026-10-24T23:59:59.999999+00:00"
    assert rows[2][3:5] == ["{}", '{"userId":null}']


def test_the_export_writes_the_day_once_and_leaves_no_partial_file(raw_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(nightly_export, "open_event_stream", stream_of(EVENTS, calls))

    result = nightly_export.export_telemetry(None, DAY)

    assert result == nightly_export.ExportResult(raw_dir / "telemetry_2026-10-24.csv", True, 2)
    assert calls == [DAY]
    assert [path.name for path in raw_dir.iterdir()] == ["telemetry_2026-10-24.csv"]


def test_an_existing_export_is_never_read_again_or_overwritten(raw_dir, monkeypatch):
    raw_dir.mkdir()
    existing = raw_dir / "telemetry_2026-10-24.csv"
    existing.write_text("kept as it is\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(nightly_export, "open_event_stream", stream_of(EVENTS, calls))

    result = nightly_export.export_telemetry(None, DAY)

    assert result == nightly_export.ExportResult(existing, created=False)
    assert calls == []
    assert existing.read_text(encoding="utf-8") == "kept as it is\n"


def test_an_empty_day_gives_a_header_only_file(raw_dir, monkeypatch):
    monkeypatch.setattr(nightly_export, "open_event_stream", stream_of([]))

    result = nightly_export.export_telemetry(None, DAY)

    assert result.rows == 0
    # The csv module's RFC 4180 line ending, kept byte for byte.
    assert result.path.read_bytes() == b"id,timestamp,event_type,payload,tags,created_at\r\n"


def test_an_export_that_fails_midway_leaves_nothing_behind(raw_dir, monkeypatch):
    def first_row_then_lost_connection():
        yield EVENTS[0]
        raise OperationalError("SELECT", {}, Exception("connection lost"))

    @contextmanager
    def failing_stream(engine, target_date):
        yield first_row_then_lost_connection()

    monkeypatch.setattr(nightly_export, "open_event_stream", failing_stream)

    with pytest.raises(OperationalError):
        nightly_export.export_telemetry(None, DAY)

    # No truncated file the next run could mistake for a finished export.
    assert list(raw_dir.iterdir()) == []


# --- Pipeline ------------------------------------------------------------------------


class FakePipeline:
    """Stands in for `subprocess.Popen`: writes its output, then exits or raises on `wait`."""

    pid = 424242

    def __init__(self, returncode=0, stdout=None, stderr="", error=None):
        self.calls = []
        self.waits = []
        self.signals = []  # sent to the pipeline process alone
        self.returncode = returncode
        self.stdout = json.dumps(SUMMARY, indent=2) if stdout is None else stdout
        self.stderr = stderr
        self.error = error

    def __call__(self, command, **options):
        self.calls.append((command, options))
        options["stdout"].write(self.stdout.encode())
        options["stderr"].write(self.stderr.encode())
        return self

    def send_signal(self, signum):
        self.signals.append(signum)

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.error is not None and len(self.waits) == 1:
            raise self.error
        return self.returncode


@pytest.fixture(autouse=True)
def group_signals(monkeypatch):
    """Record process-group signals instead of sending them: no test may kill anything."""
    sent = []
    monkeypatch.setattr(nightly_export, "_signal_group", lambda group, signum: sent.append(signum))
    monkeypatch.setattr(nightly_export, "_group_exists", lambda group: False)
    return sent


def test_the_pipeline_runs_as_a_subprocess_for_one_closed_week(monkeypatch, caplog, group_signals):
    caplog.set_level(logging.INFO, logger="jobs")
    fake = FakePipeline(stderr="03:30:02.000 | INFO    | Flow run 'brave-fox' - Finished\n")
    monkeypatch.setattr(nightly_export.subprocess, "Popen", fake)

    summary = nightly_export.trigger_pipeline(date(2026, 10, 19), DAY)

    assert summary == SUMMARY
    [(command, options)] = fake.calls
    assert command == [
        sys.executable,
        "-m",
        "data.pipelines.pipeline",
        "--week-start",
        "2026-10-19",
    ]
    assert options["cwd"] == nightly_export.ROOT_DIR
    # Its own process group, so stopping it also stops Prefect's temporary server.
    assert options["start_new_session"] is True
    assert fake.waits == [timedelta(hours=3).total_seconds()]
    # After a normal exit, only a SIGTERM for anything it left behind.
    assert group_signals == [signal.SIGTERM]
    # The pipeline's own log reaches this job's log, as job lines.
    [forwarded] = caplog.records
    assert forwarded.getMessage().endswith("Flow run 'brave-fox' - Finished")
    assert (forwarded.status, forwarded.target_date) == ("processing", DAY.isoformat())


def test_the_pipeline_log_keeps_prefect_levels_and_tracebacks_follow_their_line(caplog):
    caplog.set_level(logging.INFO, logger="jobs")
    stderr = (
        "03:30:01.000 | INFO    | prefect - Starting temporary server\n"
        "03:30:09.000 | ERROR   | Flow run 'brave-fox' - Encountered exception\n"
        "Traceback (most recent call last):\n"
        '  File "pipeline.py", line 325, in extract\n'
        "\n"
        "03:30:10.000 | WARNING | Flow run 'brave-fox' - Retrying\n"
    )

    nightly_export.forward_pipeline_log(stderr, DAY)

    assert [record.levelname for record in caplog.records] == [
        "INFO",
        "ERROR",
        "ERROR",
        "ERROR",
        "WARNING",
    ]
    assert all(record.getMessage().startswith("pipeline | ") for record in caplog.records)


def test_a_bare_python_traceback_from_the_pipeline_is_logged_as_an_error(caplog):
    caplog.set_level(logging.INFO, logger="jobs")
    stderr = (
        "03:30:01.000 | INFO    | prefect - Starting temporary server\n"
        "Traceback (most recent call last):\n"
        "RuntimeError: Failed to reach API\n"
    )

    nightly_export.forward_pipeline_log(stderr, DAY)

    assert [record.levelname for record in caplog.records] == ["INFO", "ERROR", "ERROR"]


def test_a_failed_pipeline_raises_with_its_exit_status_and_last_message(monkeypatch):
    fake = FakePipeline(
        returncode=1,
        stdout="",
        stderr="Traceback ...\nFailed: Flow run encountered an exception: PipelineStageError\n",
    )
    monkeypatch.setattr(nightly_export.subprocess, "Popen", fake)

    with pytest.raises(nightly_export.PipelineTriggerError) as raised:
        nightly_export.trigger_pipeline(date(2026, 10, 19))

    assert str(raised.value) == (
        "The data pipeline exited with status 1: "
        "Failed: Flow run encountered an exception: PipelineStageError"
    )


def test_a_pipeline_that_overruns_is_stopped_and_reported(monkeypatch, group_signals, caplog):
    caplog.set_level(logging.INFO, logger="jobs")
    expired = subprocess.TimeoutExpired(["python"], 10800)
    fake = FakePipeline(stderr="03:30:00.000 | INFO    | still extracting\n", error=expired)
    monkeypatch.setattr(nightly_export.subprocess, "Popen", fake)

    with pytest.raises(nightly_export.PipelineTriggerError, match="within 3 hours"):
        nightly_export.trigger_pipeline(date(2026, 10, 19))

    # The pipeline alone first, so its crash hook can still reach Prefect; then its group.
    assert fake.signals == [signal.SIGTERM]
    assert fake.waits[1] == nightly_export.STOP_GRACE_SECONDS
    assert group_signals == [signal.SIGTERM, signal.SIGKILL]
    assert caplog.records[-1].getMessage().endswith("still extracting")


def test_an_interrupted_run_stops_the_pipeline_before_raising(monkeypatch, group_signals):
    terminated = nightly_export.Terminated("received SIGTERM")
    fake = FakePipeline(error=terminated)
    monkeypatch.setattr(nightly_export.subprocess, "Popen", fake)

    with pytest.raises(nightly_export.Terminated):
        nightly_export.trigger_pipeline(date(2026, 10, 19))

    assert fake.signals == [signal.SIGTERM]
    assert group_signals == [signal.SIGTERM, signal.SIGKILL]


def test_a_pipeline_that_skipped_itself_succeeds_without_a_summary(monkeypatch):
    fake = FakePipeline(stdout="", stderr="Cancelled: Skipped: run 42 holds the slot\n")
    monkeypatch.setattr(nightly_export.subprocess, "Popen", fake)

    assert nightly_export.trigger_pipeline(date(2026, 10, 19)) is None


def test_the_summary_is_the_last_json_object_on_stdout():
    noisy = "Starting temporary server...\n" + json.dumps(SUMMARY, indent=2) + "\n"
    assert nightly_export.parse_summary(noisy) == SUMMARY
    assert nightly_export.parse_summary("{not json") is None
    assert nightly_export.parse_summary(None) is None


# --- The whole run -------------------------------------------------------------------


@pytest.fixture
def job_store(tmp_path, monkeypatch):
    """A real `job_runs` table on SQLite behind the script's engine."""
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.sqlite'}")
    monkeypatch.setattr(nightly_export, "get_engine", lambda: engine)
    monkeypatch.setattr(nightly_export, "dispose_engine", lambda: None)
    monkeypatch.setenv("TARGET_DATE", DAY.isoformat())
    yield engine
    engine.dispose()


@pytest.fixture
def stream_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(nightly_export, "open_event_stream", stream_of(EVENTS, calls))
    return calls


def runs(engine):
    return job_runner.list_job_runs(engine, "nightly_export", limit=100)


def test_a_nightly_run_exports_runs_the_pipeline_and_records_completion(
    job_store, raw_dir, stream_calls, monkeypatch
):
    pipeline = FakePipeline()
    monkeypatch.setattr(nightly_export.subprocess, "Popen", pipeline)

    assert nightly_export.main([]) == 0

    assert (raw_dir / "telemetry_2026-10-24.csv").exists()
    assert stream_calls == [DAY]
    assert pipeline.calls[0][0][-1] == "2026-10-12"
    [run] = runs(job_store)
    assert (run.status, run.target_date, run.error_message) == ("completed", DAY, None)
    assert run.started_at is not None and run.finished_at is not None


def test_a_second_run_the_same_day_exports_and_triggers_nothing(
    job_store, raw_dir, stream_calls, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger="jobs")
    pipeline = FakePipeline()
    monkeypatch.setattr(nightly_export.subprocess, "Popen", pipeline)
    nightly_export.main([])
    (raw_dir / "telemetry_2026-10-24.csv").unlink()  # even a lost backup is not redone

    assert nightly_export.main([]) == 0

    assert len(stream_calls) == 1 and len(pipeline.calls) == 1
    assert len(runs(job_store)) == 1
    assert "skipped as a duplicate" in caplog.records[-1].getMessage()


def test_a_failed_pipeline_fails_the_run_and_a_retry_reuses_the_export(
    job_store, raw_dir, stream_calls, monkeypatch
):
    monkeypatch.setattr(
        nightly_export.subprocess,
        "Popen",
        FakePipeline(returncode=1, stdout="", stderr="Failed: database unreachable\n"),
    )

    assert nightly_export.main([]) == 1

    [failed] = runs(job_store)
    assert failed.status == "failed"
    assert failed.error_message == (
        "PipelineTriggerError: The data pipeline exited with status 1: Failed: database unreachable"
    )
    assert (raw_dir / "telemetry_2026-10-24.csv").exists()

    retry = FakePipeline()
    monkeypatch.setattr(nightly_export.subprocess, "Popen", retry)
    assert nightly_export.main([]) == 0

    assert stream_calls == [DAY]  # the CSV was kept, not exported twice
    assert len(retry.calls) == 1
    assert sorted(run.status for run in runs(job_store)) == ["completed", "failed"]


def test_a_run_aborts_silently_while_another_one_is_processing(
    job_store, raw_dir, stream_calls, monkeypatch
):
    job_runner.ensure_schema(job_store)
    holder = job_runner.create_job_run(job_store, "nightly_export", DAY - timedelta(days=1))
    job_runner.mark_processing(job_store, holder)
    pipeline = FakePipeline()
    monkeypatch.setattr(nightly_export.subprocess, "Popen", pipeline)

    assert nightly_export.main([]) == 0

    assert stream_calls == [] and pipeline.calls == []
    assert [run.id for run in runs(job_store)] == [holder]


def test_sigterm_during_the_pipeline_marks_the_run_failed(
    job_store, raw_dir, stream_calls, monkeypatch
):
    terminated = nightly_export.Terminated("received SIGTERM")
    monkeypatch.setattr(nightly_export.subprocess, "Popen", FakePipeline(error=terminated))

    assert nightly_export.main([]) == 143

    [run] = runs(job_store)
    assert (run.status, run.error_message) == ("failed", "Terminated: received SIGTERM")


def test_an_invalid_target_date_stops_before_touching_the_database(monkeypatch):
    monkeypatch.setenv("TARGET_DATE", "26-10-2026")

    def no_database():
        raise AssertionError("the database must not be reached")

    monkeypatch.setattr(nightly_export, "get_engine", no_database)

    assert nightly_export.main([]) == 2


def test_invalid_settings_are_a_configuration_error(monkeypatch):
    monkeypatch.setenv("TARGET_DATE", DAY.isoformat())

    def unconfigured():
        raise ConfigurationError("Invalid configuration - DATABASE_URL: Field required.")

    monkeypatch.setattr(nightly_export, "get_engine", unconfigured)
    monkeypatch.setattr(nightly_export, "dispose_engine", lambda: None)

    assert nightly_export.main([]) == 2


def test_an_unreachable_job_store_fails_without_running_anything(
    job_store, stream_calls, monkeypatch
):
    def unreachable(engine):
        raise OperationalError("CREATE TABLE", {}, Exception("timeout"))

    monkeypatch.setattr(nightly_export.job_runner, "ensure_schema", unreachable)

    assert nightly_export.main([]) == 1
    assert stream_calls == []


# --- Log format ----------------------------------------------------------------------


LOG_LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z (INFO|WARNING|ERROR) "
    r"job=nightly_export target_date=(\d{4}-\d{2}-\d{2}|-) status=\S+ .+$"
)


def test_every_line_has_a_utc_timestamp_the_job_and_the_status(
    job_store, raw_dir, stream_calls, monkeypatch
):
    output = io.StringIO()
    nightly_export.configure_logging(output)
    nightly_export.configure_logging(output)  # idempotent: one handler, one line each
    monkeypatch.setattr(nightly_export.subprocess, "Popen", FakePipeline())

    nightly_export.main([])
    nightly_export.main([])

    lines = output.getvalue().splitlines()
    assert lines, "nothing was logged"
    assert all(LOG_LINE.match(line) for line in lines), lines
    assert len(lines) == len(set(lines))
    assert re.search(r"INFO .* status=processing Started$", lines[1])
    assert re.search(r"INFO .* status=completed Finished$", lines[-2])
    assert re.search(r"INFO .* status=skipped Already completed", lines[-1])


def test_a_failure_is_logged_at_error_with_its_traceback(
    job_store, raw_dir, stream_calls, monkeypatch
):
    output = io.StringIO()
    nightly_export.configure_logging(output)
    monkeypatch.setattr(
        nightly_export.subprocess, "Popen", FakePipeline(returncode=2, stdout="", stderr="usage\n")
    )

    assert nightly_export.main([]) == 1

    text = output.getvalue()
    assert re.search(
        r"ERROR job=nightly_export target_date=2026-10-24 status=failed "
        r"Failed: PipelineTriggerError: The data pipeline exited with status 2: usage",
        text,
    )
    assert "Traceback (most recent call last)" in text
