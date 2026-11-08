"""`telemetry.generate_report`: the report, its retries with backoff, and the dead letter queue.

The task runs eagerly through `.apply()`: every retry runs at once, and the
countdown a worker would wait is read from the `task_retry` signal. The metric
functions are replaced by recorders; what they calculate is covered by
`tests/test_telemetry_analysis.py`.
"""

import logging
from datetime import UTC, datetime

import pytest
from kombu.exceptions import OperationalError as BrokerError
from sqlalchemy.exc import OperationalError

from services.tasks import base, dead_letters
from services.telemetry import analysis
from services.telemetry.report import ReportUnavailable
from services.telemetry.tasks import generate_telemetry_report

START = "2026-10-13T00:00:00+00:00"
END = "2026-10-20T00:00:00+00:00"

ROWS = {
    "events_per_day": [
        {"date": "2026-10-19", "events": 3, "sessions": 1, "event_types": 2, "partial_day": False}
    ],
    "error_rate_by_type": [],
    "latency_by_route": [],
    "event_type_share": [
        {"date": "2026-10-19", "event_type": "section_viewed", "events": 3, "share": 1.0}
    ],
}


def unreachable_database():
    return OperationalError("SELECT ...", {}, Exception("password=secret host=db.internal"))


def refuse_write(letter):
    raise unreachable_database()


@pytest.fixture
def metrics(monkeypatch):
    """Every metric call, as `(name, start, end)`."""
    calls = []

    def recorder(name):
        def metric(start, end):
            calls.append((name, start, end))
            return ROWS[name]

        return metric

    for name in ROWS:
        monkeypatch.setattr(analysis, name, recorder(name))
    return calls


@pytest.fixture
def failing_reads(monkeypatch, metrics):
    """Make one metric fail the first `count` reads; returns the list of attempts."""
    attempts = []

    def install(count, error=unreachable_database):
        def metric(start, end):
            attempts.append(start)
            if len(attempts) <= count:
                raise error()
            return ROWS["latency_by_route"]

        monkeypatch.setattr(analysis, "latency_by_route", metric)
        return attempts

    return install


def test_the_task_returns_the_report_as_the_endpoint_used_to(dlq_engine, metrics):
    result = generate_telemetry_report.apply(args=[START, END])

    assert result.state == "SUCCESS"
    assert result.get() == {
        "period": {"from": "2026-10-13T00:00:00Z", "to": "2026-10-20T00:00:00Z"},
        "metrics": ROWS,
    }
    assert {(start.isoformat(), end.isoformat()) for _, start, end in metrics} == {(START, END)}
    assert dead_letters.list_dead_letters(dlq_engine) == []


def test_a_transient_failure_is_retried_and_then_succeeds(dlq_engine, failing_reads, countdowns):
    attempts = failing_reads(2)

    result = generate_telemetry_report.apply(args=[START, END])

    assert result.state == "SUCCESS"
    assert len(attempts) == 3
    assert countdowns == [5, 10]
    assert dead_letters.list_dead_letters(dlq_engine) == []


def test_three_retries_with_growing_countdowns_then_the_dead_letter_queue(
    dlq_engine, failing_reads, countdowns
):
    attempts = failing_reads(99)

    result = generate_telemetry_report.apply(args=[START, END], task_id="report-1")

    assert result.state == "FAILURE"
    assert isinstance(result.result, ReportUnavailable)
    assert len(attempts) == 4  # the first run and max_retries=3 retries
    assert countdowns == [5, 10, 20]

    [row] = dead_letters.list_dead_letters(dlq_engine)
    assert row["task_id"] == "report-1"
    assert row["task_name"] == "telemetry.generate_report"
    assert row["args"] == [START, END]
    assert row["attempt"] == 4
    assert row["reason"] == "retries_exhausted"
    assert row["error_message"] == "ReportUnavailable: OperationalError"
    assert row["failed_at"] is not None
    assert "secret" not in str(row)


def test_an_error_that_is_not_retryable_fails_at_once_into_the_dead_letter_queue(
    dlq_engine, failing_reads, countdowns
):
    attempts = failing_reads(1, error=lambda: ValueError("sample_rate is not a number"))

    result = generate_telemetry_report.apply(args=[START, END], task_id="report-2")

    assert result.state == "FAILURE"
    assert len(attempts) == 1
    assert countdowns == []
    [row] = dead_letters.list_dead_letters(dlq_engine)
    assert row["attempt"] == 1
    assert row["reason"] == "not_retryable"
    assert row["error_message"] == "ValueError: sample_rate is not a number"


def test_a_dead_letter_the_database_refuses_goes_to_the_recorder_task(
    dlq_engine, failing_reads, monkeypatch
):
    failing_reads(99)
    queued = []
    monkeypatch.setattr(base, "write_dead_letter", refuse_write)
    monkeypatch.setattr(base.record_dead_letter, "apply_async", lambda args: queued.append(args[0]))

    generate_telemetry_report.apply(args=[START, END], task_id="report-3")

    [message] = queued
    assert message["task_id"] == "report-3"
    assert message["attempt"] == 4
    assert message["reason"] == "retries_exhausted"
    assert dead_letters.list_dead_letters(dlq_engine) == []

    # The database is back: the recorder writes the row, and a replay writes nothing.
    monkeypatch.undo()
    monkeypatch.setattr(base, "dead_letter_engine", lambda: dlq_engine)
    assert base.record_dead_letter.apply(args=[message]).get() is True
    assert base.record_dead_letter.apply(args=[message]).get() is False
    [row] = dead_letters.list_dead_letters(dlq_engine)
    assert row["task_id"] == "report-3"


def test_the_recorder_retries_with_backoff_and_logs_the_record_it_could_not_write(
    dlq_engine, monkeypatch, countdowns, caplog
):
    message = dead_letters.DeadLetter(
        task_id="report-4",
        task_name="telemetry.generate_report",
        args=[START, END],
        kwargs={},
        attempt=4,
        reason="retries_exhausted",
        error_message="ReportUnavailable: OperationalError",
        failed_at=datetime(2026, 10, 27, tzinfo=UTC),
    ).to_message()
    monkeypatch.setattr(base, "write_dead_letter", refuse_write)
    caplog.set_level(logging.INFO, logger="tasks")

    result = base.record_dead_letter.apply(args=[message])

    assert result.state == "FAILURE"
    assert len(countdowns) == base.RECORDER_MAX_RETRIES
    assert countdowns[:6] == [15, 30, 60, 120, 240, 480]
    assert set(countdowns[6:]) == {base.RECORDER_BACKOFF_MAX_SECONDS}
    lost = [
        record for record in caplog.records if getattr(record, "status", None) == "dead_letter_lost"
    ]
    assert len(lost) == 1
    assert lost[0].levelno == logging.ERROR
    assert lost[0].task_id == "report-4"
    assert "ReportUnavailable: OperationalError" in lost[0].getMessage()


def test_a_dead_letter_with_neither_database_nor_redis_is_logged_whole(
    dlq_engine, failing_reads, monkeypatch, caplog
):
    failing_reads(99)

    def no_broker(args):
        raise BrokerError("Connection refused")

    monkeypatch.setattr(base, "write_dead_letter", refuse_write)
    monkeypatch.setattr(base.record_dead_letter, "apply_async", no_broker)
    caplog.set_level(logging.INFO, logger="tasks")

    generate_telemetry_report.apply(args=[START, END], task_id="report-5")

    [lost] = [
        record for record in caplog.records if getattr(record, "status", None) == "dead_letter_lost"
    ]
    assert lost.levelno == logging.ERROR
    assert lost.task_id == "report-5"
    assert lost.attempt == "4"
    message = lost.getMessage()
    assert "reason=retries_exhausted" in message
    assert "error=ReportUnavailable: OperationalError" in message
    assert "failed_at=" in message
    assert START in message


def test_a_failure_while_handling_the_dead_letter_never_hides_the_task_failure(
    failing_reads, monkeypatch, caplog
):
    failing_reads(99, error=lambda: ValueError("bad row"))

    def broken(letter):
        raise RuntimeError("bug in the dead letter path")

    monkeypatch.setattr(base, "send_to_dead_letter_queue", broken)

    result = generate_telemetry_report.apply(args=[START, END], task_id="report-6")

    assert result.state == "FAILURE"
    assert isinstance(result.result, ValueError)
    assert "dead letter handling failed for task report-6" in caplog.text
