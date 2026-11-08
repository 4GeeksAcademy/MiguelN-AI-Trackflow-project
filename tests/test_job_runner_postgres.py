"""`job_runs` and the nightly export's read on a real PostgreSQL, opt-in.

Set `INVENTORY_TEST_DATABASE_URL` to run them, as for the inventory,
telemetry and reporting suites. Each test works in its own disposable schemas,
selected with `SET LOCAL search_path` at the start of every transaction, so
the unqualified `job_runs` and `telemetry_events` resolve there, also behind
Supabase's transaction pooler.

They cover what SQLite cannot: the migration file and `ensure_schema` building
the same table, RLS, the unique index deciding a real race between two
connections, the database clock in the sweep, and the export's `READ ONLY`
day query.
"""

import csv
import json
import os
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
import pytest
from sqlalchemy import create_engine, event, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateIndex, CreateSchema, CreateTable, DropSchema

from scripts import nightly_export
from services.job_runner import service
from services.job_runner.models import job_runs
from services.telemetry.database import telemetry_events

REAL_CONNECT = psycopg2.connect
MIGRATION = Path(service.__file__).parent / "migrations" / "001_create_job_runs.sql"
JOB = "nightly_export"
DAY = date(2026, 10, 24)


@pytest.fixture
def postgres(monkeypatch):
    """An engine whose transactions run in `schemas[0]` unless a test switches it."""
    value = os.getenv("INVENTORY_TEST_DATABASE_URL")
    if not value:
        pytest.skip(
            "Set INVENTORY_TEST_DATABASE_URL explicitly for disposable-schema PostgreSQL tests"
        )
    url = make_url(value)
    if url.get_backend_name() != "postgresql":
        pytest.fail("PostgreSQL integration tests require a PostgreSQL URL")
    url = url.set(drivername="postgresql+psycopg2")
    monkeypatch.setattr(psycopg2, "connect", REAL_CONNECT)
    engine = create_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_size=4,
        max_overflow=0,
        pool_pre_ping=True,
        isolation_level="READ COMMITTED",
        connect_args={"sslmode": url.query.get("sslmode", "require"), "connect_timeout": 10},
    )
    schemas = [f"job_runner_test_{uuid4().hex}", f"job_runner_test_{uuid4().hex}"]
    current = {"schema": schemas[0]}

    def use_test_schema(connection):
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{current["schema"]}"')

    created = []
    try:
        with engine.begin() as connection:
            for schema in schemas:
                connection.execute(CreateSchema(schema))
                created.append(schema)
        event.listen(engine, "begin", use_test_schema)
        yield engine, schemas, current
    finally:
        event.remove(engine, "begin", use_test_schema)
        with engine.begin() as connection:
            for schema in created:
                connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


@pytest.fixture
def engine(postgres):
    engine, _, _ = postgres
    service.ensure_schema(engine)
    return engine


def shape(engine, schema):
    """Everything that defines the table, with the schema name taken out."""
    with engine.begin() as connection:
        columns = connection.execute(
            text(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = 'job_runs' "
                "ORDER BY ordinal_position"
            ),
            {"schema": schema},
        ).all()
        constraints = connection.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'job_runs'::regclass ORDER BY conname"
            )
        ).all()
        indexes = connection.execute(
            text(
                "SELECT indexname, replace(indexdef, :prefix, '') FROM pg_indexes "
                "WHERE schemaname = :schema AND tablename = 'job_runs' ORDER BY indexname"
            ),
            {"schema": schema, "prefix": f"{schema}."},
        ).all()
        rls = connection.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE oid = 'job_runs'::regclass")
        ).scalar_one()
    return columns, constraints, indexes, rls


def apply_migration(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql(MIGRATION.read_text(encoding="utf-8"))


# --- Schema --------------------------------------------------------------------------


def test_ensure_schema_builds_the_table_with_rls_and_is_idempotent(postgres):
    engine, schemas, _ = postgres
    service.ensure_schema(engine)
    first = shape(engine, schemas[0])

    service.ensure_schema(engine)

    assert shape(engine, schemas[0]) == first
    columns, constraints, indexes, rls = first
    assert [(name, data_type, nullable) for name, data_type, nullable, _ in columns] == [
        ("id", "uuid", "NO"),
        ("job_name", "text", "NO"),
        ("target_date", "date", "NO"),
        ("status", "text", "NO"),
        ("started_at", "timestamp with time zone", "YES"),
        ("finished_at", "timestamp with time zone", "YES"),
        ("error_message", "text", "YES"),
        ("created_at", "timestamp with time zone", "NO"),
    ]
    assert {name for name, _ in constraints} == {
        "job_runs_pkey",
        "job_runs_status_ck",
        "job_runs_started_ck",
        "job_runs_finished_ck",
        "job_runs_error_message_ck",
    }
    found = dict(indexes)
    assert "(job_name, target_date)" in found["job_runs_job_name_target_date_idx"]
    assert found["job_runs_one_active_idx"].startswith("CREATE UNIQUE INDEX")
    assert "WHERE (status = ANY" in found["job_runs_one_active_idx"]
    assert rls is True


def test_the_migration_file_builds_the_same_table_as_ensure_schema(postgres):
    engine, schemas, current = postgres
    service.ensure_schema(engine)
    current["schema"] = schemas[1]
    apply_migration(engine)

    assert shape(engine, schemas[1]) == shape(engine, schemas[0])

    # Either path run over the other changes nothing.
    service.ensure_schema(engine)
    current["schema"] = schemas[0]
    apply_migration(engine)
    assert shape(engine, schemas[0]) == shape(engine, schemas[1])


# --- The lock and the lifecycle ------------------------------------------------------


def test_two_connections_claiming_at_once_get_exactly_one_run(engine):
    barrier = threading.Barrier(2)
    outcomes = []

    def claim(target_date):
        barrier.wait()
        try:
            outcomes.append(service.create_job_run(engine, JOB, target_date))
        except service.JobAlreadyRunning:
            outcomes.append("refused")

    threads = [threading.Thread(target=claim, args=(DAY + timedelta(days=n),)) for n in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes, key=str).count("refused") == 1
    assert [isinstance(outcome, UUID) for outcome in outcomes].count(True) == 1
    assert len(service.list_job_runs(engine, JOB)) == 1


def test_run_job_records_timestamps_and_skips_the_duplicate(engine):
    calls = []

    assert service.run_job(engine, JOB, DAY, lambda: calls.append(1)) == "completed"
    assert service.run_job(engine, JOB, DAY, lambda: calls.append(1)) == "skipped_duplicate"

    [run] = service.list_job_runs(engine, JOB)
    assert calls == [1]
    assert run.status == "completed"
    assert run.created_at.tzinfo is not None
    assert run.created_at <= run.started_at <= run.finished_at


def test_a_failure_is_recorded_and_the_day_can_run_again(engine):
    def broken():
        raise RuntimeError("pipeline exited with status 1")

    with pytest.raises(RuntimeError):
        service.run_job(engine, JOB, DAY, broken)
    assert not service.has_processing_lock(engine, JOB)

    assert service.run_job(engine, JOB, DAY, lambda: None) == "completed"
    statuses = sorted(run.status for run in service.list_job_runs(engine, JOB))
    assert statuses == ["completed", "failed"]


def test_the_sweep_uses_the_database_clock(engine):
    stale = service.create_job_run(engine, JOB, DAY)
    service.mark_processing(engine, stale)
    with engine.begin() as connection:
        connection.execute(
            update(job_runs)
            .where(job_runs.c.id == stale)
            .values(created_at=text("now() - interval '7 hours'"))
        )

    assert service.run_job(engine, JOB, DAY + timedelta(days=1), lambda: None) == "completed"

    swept = service.get_job_run(engine, stale)
    assert (swept.status, swept.error_message) == ("failed", service.ABANDONED_MESSAGE)


# --- The export's read ---------------------------------------------------------------


def add_event(engine, at, event_id=None, event_type="section_viewed"):
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO telemetry_events (id, timestamp, event_type, payload, tags) "
                "VALUES (CAST(:id AS uuid), :at, :event_type, "
                "CAST(:payload AS jsonb), CAST(:tags AS jsonb))"
            ),
            {
                "id": str(event_id or uuid4()),
                "at": at,
                "event_type": event_type,
                "payload": json.dumps({"route": "/inventory", "n": 1}),
                "tags": json.dumps({"sessionId": "s-1"}),
            },
        )


def test_the_export_reads_one_utc_day_oldest_first_in_a_read_only_transaction(engine, tmp_path):
    with engine.begin() as connection:
        connection.execute(CreateTable(telemetry_events, if_not_exists=True))
        for index in telemetry_events.indexes:
            connection.execute(CreateIndex(index, if_not_exists=True))
    midnight = datetime(2026, 10, 24, tzinfo=timezone.utc)
    first, second = UUID(int=1, version=4), UUID(int=2, version=4)
    add_event(engine, midnight - timedelta(microseconds=1))  # the day before
    add_event(engine, midnight + timedelta(hours=12), second)
    add_event(engine, midnight + timedelta(hours=12), first)  # same instant: id breaks the tie
    add_event(engine, midnight)
    add_event(engine, midnight + timedelta(days=1) - timedelta(microseconds=1))
    add_event(engine, midnight + timedelta(days=1))  # the day after
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        with nightly_export.open_event_stream(engine, DAY) as stream:
            rows = [dict(row) for row in stream]
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert [row["timestamp"] for row in rows] == [
        midnight,
        midnight + timedelta(hours=12),
        midnight + timedelta(hours=12),
        midnight + timedelta(days=1) - timedelta(microseconds=1),
    ]
    assert [row["id"] for row in rows[1:3]] == [first, second]
    assert rows[0]["payload"] == {"route": "/inventory", "n": 1}
    read_only = statements.index("SET TRANSACTION READ ONLY")
    assert read_only < next(i for i, s in enumerate(statements) if "FROM telemetry_events" in s)

    result = nightly_export.export_telemetry(engine, DAY, tmp_path)
    with result.path.open(encoding="utf-8", newline="") as written:
        assert len(list(csv.reader(written))) == 1 + 4
