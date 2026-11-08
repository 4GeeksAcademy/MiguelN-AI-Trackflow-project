"""Task log lines: task id, attempt, status and duration on every line; failures in full."""

import io
import logging
import re

import pytest
from billiard.exceptions import WorkerLostError
from sqlalchemy.exc import OperationalError

from services.tasks import logs, signals
from services.telemetry import analysis
from services.telemetry.report import ReportUnavailable
from services.telemetry.tasks import generate_telemetry_report

START = "2026-10-13T00:00:00+00:00"
END = "2026-10-20T00:00:00+00:00"
METRICS = ("events_per_day", "error_rate_by_type", "latency_by_route", "event_type_share")


@pytest.fixture
def reads(monkeypatch):
    """Metrics that answer `[]`, the first `failures` reads of one of them failing."""

    def install(failures=0):
        attempts = []

        def latency(start, end):
            attempts.append(start)
            if len(attempts) <= failures:
                raise OperationalError("SELECT ...", {}, Exception("server closed the connection"))
            return []

        for name in METRICS:
            monkeypatch.setattr(analysis, name, lambda start, end: [])
        monkeypatch.setattr(analysis, "latency_by_route", latency)

    return install


@pytest.fixture
def task_records(caplog, dlq_engine):
    caplog.set_level(logging.INFO, logger="tasks")
    task_name = generate_telemetry_report.name
    return lambda: [r for r in caplog.records if r.name == f"tasks.{task_name}"]


def test_a_successful_task_logs_its_start_and_its_outcome(reads, task_records):
    reads()

    generate_telemetry_report.apply(args=[START, END], task_id="log-1")

    started, finished = task_records()
    assert (started.status, started.task_id, started.attempt) == ("started", "log-1", "1")
    assert (finished.status, finished.task_id, finished.attempt) == ("success", "log-1", "1")
    assert finished.levelno == logging.INFO
    assert float(finished.duration_ms) >= 0


def test_each_retry_logs_its_attempt_duration_countdown_and_full_error(reads, task_records):
    reads(failures=1)

    generate_telemetry_report.apply(args=[START, END], task_id="log-2")

    statuses = [(r.status, r.attempt) for r in task_records()]
    assert statuses == [("started", "1"), ("retry", "1"), ("started", "2"), ("success", "2")]
    retry = task_records()[1]
    assert retry.levelno == logging.WARNING
    assert float(retry.duration_ms) >= 0
    message = retry.getMessage()
    assert "retrying in 5s" in message
    assert "ReportUnavailable: OperationalError <- caused by OperationalError:" in message
    assert "server closed the connection" in message


def test_a_final_failure_logs_the_full_error_and_traceback(reads, task_records):
    reads(failures=99)

    generate_telemetry_report.apply(args=[START, END], task_id="log-3")

    [failure] = [r for r in task_records() if r.status == "failure"]
    assert failure.levelno == logging.ERROR
    assert (failure.task_id, failure.attempt) == ("log-3", "4")
    assert float(failure.duration_ms) >= 0
    assert "server closed the connection" in failure.getMessage()
    assert failure.exc_info is not None and failure.exc_info[0] is ReportUnavailable


def test_a_failure_reported_by_the_parent_process_has_no_attempt_or_duration(caplog):
    """A killed pool child: the parent sends `task_failure` with no request of the task."""
    caplog.set_level(logging.INFO, logger="tasks")

    signals.log_task_failure(
        sender=generate_telemetry_report,
        task_id="lost-1",
        exception=WorkerLostError("Worker exited prematurely: signal 9 (SIGKILL)."),
    )

    [record] = [r for r in caplog.records if getattr(r, "task_id", None) == "lost-1"]
    assert (record.status, record.attempt, record.duration_ms) == ("failure", "-", "-")
    assert "SIGKILL" in record.getMessage()


def test_full_error_message_follows_the_whole_chain():
    try:
        try:
            raise OSError("connection refused")
        except OSError as cause:
            raise ReportUnavailable("OperationalError") from cause
    except ReportUnavailable as error:
        described = signals.full_error_message(error)

    assert described == (
        "ReportUnavailable: OperationalError <- caused by OSError: connection refused"
    )


@pytest.fixture
def tasks_logger_state():
    """`configure_task_logging` changes the `tasks` logger for the process; put it back."""
    tasks = logging.getLogger(logs.ROOT_LOGGER)
    saved = (list(tasks.handlers), tasks.propagate, tasks.level)
    yield tasks
    tasks.handlers[:], tasks.propagate, tasks.level = saved[0], saved[1], saved[2]


def test_the_worker_format_is_one_utc_key_value_line(tasks_logger_state):
    stream = io.StringIO()
    logs.configure_task_logging(stream)
    logs.configure_task_logging(stream)  # idempotent: one handler, one line per record

    logs.task_logger("telemetry.generate_report").info(
        "task finished",
        extra=logs.log_fields("telemetry.generate_report", "abc-1", 2, "success", 812.44),
    )
    logs.task_logger("telemetry.generate_report").warning("no task fields at all")

    first, second = stream.getvalue().splitlines()
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z INFO task=telemetry\.generate_report "
        r"task_id=abc-1 attempt=2 status=success duration_ms=812\.4 task finished",
        first,
    )
    assert second.endswith(
        "WARNING task=- task_id=- attempt=- status=- duration_ms=- no task fields at all"
    )
    assert tasks_logger_state.propagate is False
