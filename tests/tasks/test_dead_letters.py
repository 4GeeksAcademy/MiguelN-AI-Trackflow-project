"""`task_dead_letters` on SQLite: schema, idempotent inserts, constraints, the message form.

`tests/test_task_dead_letters_postgres.py` checks the same table on PostgreSQL,
against the migration file.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, inspect
from sqlalchemy.exc import IntegrityError

from services.tasks import dead_letters
from services.tasks.dead_letters import DeadLetter, task_dead_letters

FAILED_AT = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)


def letter(**changes):
    values = {
        "task_id": "4f0c2b8e-0000-4000-8000-000000000001",
        "task_name": "telemetry.generate_report",
        "args": ["2026-10-20T00:00:00+00:00", "2026-10-27T00:00:00+00:00"],
        "kwargs": {},
        "attempt": 4,
        "reason": "retries_exhausted",
        "error_message": "ReportUnavailable: OperationalError",
        "failed_at": FAILED_AT,
    }
    values.update(changes)
    return DeadLetter(**values)


def test_ensure_schema_creates_the_table_once(dlq_engine):
    dead_letters.ensure_schema(dlq_engine)  # the fixture already ran it once

    inspector = inspect(dlq_engine)
    assert {column["name"] for column in inspector.get_columns("task_dead_letters")} == {
        "id",
        "task_id",
        "task_name",
        "args",
        "kwargs",
        "attempt",
        "reason",
        "error_message",
        "failed_at",
        "recorded_at",
    }
    assert {index["name"] for index in inspector.get_indexes("task_dead_letters")} == {
        "task_dead_letters_failed_at_idx"
    }


def test_a_dead_letter_is_recorded_with_every_field(dlq_engine):
    assert dead_letters.insert_dead_letter(dlq_engine, letter()) is True

    [row] = dead_letters.list_dead_letters(dlq_engine)
    assert row["task_id"] == "4f0c2b8e-0000-4000-8000-000000000001"
    assert row["task_name"] == "telemetry.generate_report"
    assert row["args"] == ["2026-10-20T00:00:00+00:00", "2026-10-27T00:00:00+00:00"]
    assert row["kwargs"] == {}
    assert row["attempt"] == 4
    assert row["reason"] == "retries_exhausted"
    assert row["error_message"] == "ReportUnavailable: OperationalError"
    assert row["failed_at"].replace(tzinfo=UTC) == FAILED_AT
    assert row["recorded_at"] is not None


def test_the_same_task_id_is_recorded_once(dlq_engine):
    """A late-acked message redelivered after its failure was recorded."""
    assert dead_letters.insert_dead_letter(dlq_engine, letter()) is True
    assert dead_letters.insert_dead_letter(dlq_engine, letter(attempt=1)) is False

    [row] = dead_letters.list_dead_letters(dlq_engine)
    assert row["attempt"] == 4


@pytest.mark.parametrize(
    "changes", [{"attempt": 0}, {"reason": "gave_up"}, {"error_message": None}]
)
def test_the_table_refuses_impossible_rows(dlq_engine, changes):
    values = {
        "task_id": "t-1",
        "task_name": "telemetry.generate_report",
        "args": [],
        "kwargs": {},
        "attempt": 1,
        "reason": "not_retryable",
        "error_message": "ValueError: bad",
        "failed_at": FAILED_AT,
        **changes,
    }

    with pytest.raises(IntegrityError), dlq_engine.begin() as connection:
        connection.execute(insert(task_dead_letters).values(**values))


def test_the_most_recent_failures_come_first(dlq_engine):
    for minutes in (0, 10, 5):
        dead_letters.insert_dead_letter(
            dlq_engine,
            letter(task_id=f"t-{minutes}", failed_at=FAILED_AT + timedelta(minutes=minutes)),
        )

    rows = dead_letters.list_dead_letters(dlq_engine)

    assert [row["task_id"] for row in rows] == ["t-10", "t-5", "t-0"]


def test_the_message_form_survives_a_json_round_trip():
    original = letter(kwargs={"lookback": 2})

    assert DeadLetter.from_message(original.to_message()) == original


def test_a_message_with_an_unknown_reason_is_refused():
    message = letter().to_message() | {"reason": "gave_up"}

    with pytest.raises(ValueError, match="gave_up"):
        DeadLetter.from_message(message)
