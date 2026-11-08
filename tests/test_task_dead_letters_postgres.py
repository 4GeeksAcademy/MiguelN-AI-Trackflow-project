"""`task_dead_letters` on a real PostgreSQL, opt-in.

Set `INVENTORY_TEST_DATABASE_URL` to run them, as for the job runner,
inventory, telemetry and reporting suites. Each test works in its own
disposable schemas, selected with `SET LOCAL search_path` at the start of
every transaction, so the unqualified table resolves there, also behind
Supabase's transaction pooler.

They cover what SQLite cannot: the migration file and `ensure_schema` building
the same table, JSONB columns, RLS, and `ON CONFLICT DO NOTHING` on the
PostgreSQL dialect.
"""

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from services.tasks import dead_letters
from services.tasks.dead_letters import DeadLetter

REAL_CONNECT = psycopg2.connect
MIGRATION = Path(dead_letters.__file__).parent / "migrations" / "001_create_task_dead_letters.sql"


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
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
        isolation_level="READ COMMITTED",
        connect_args={"sslmode": url.query.get("sslmode", "require"), "connect_timeout": 10},
    )
    schemas = [f"task_dlq_test_{uuid4().hex}", f"task_dlq_test_{uuid4().hex}"]
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


def shape(engine, schema):
    """Everything that defines the table, with the schema name taken out."""
    with engine.begin() as connection:
        columns = connection.execute(
            text(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = 'task_dead_letters' "
                "ORDER BY ordinal_position"
            ),
            {"schema": schema},
        ).all()
        constraints = connection.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'task_dead_letters'::regclass ORDER BY conname"
            )
        ).all()
        indexes = connection.execute(
            text(
                "SELECT indexname, replace(indexdef, :prefix, '') FROM pg_indexes "
                "WHERE schemaname = :schema AND tablename = 'task_dead_letters' "
                "ORDER BY indexname"
            ),
            {"schema": schema, "prefix": f"{schema}."},
        ).all()
        rls = connection.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE oid = 'task_dead_letters'::regclass")
        ).scalar_one()
    return columns, constraints, indexes, rls


def apply_migration(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql(MIGRATION.read_text(encoding="utf-8"))


def test_ensure_schema_builds_the_table_with_rls_and_is_idempotent(postgres):
    engine, schemas, _ = postgres
    dead_letters.ensure_schema(engine)
    first = shape(engine, schemas[0])

    dead_letters.ensure_schema(engine)

    assert shape(engine, schemas[0]) == first
    columns, constraints, indexes, rls = first
    assert [(name, data_type, nullable) for name, data_type, nullable, _ in columns] == [
        ("id", "uuid", "NO"),
        ("task_id", "text", "NO"),
        ("task_name", "text", "NO"),
        ("args", "jsonb", "NO"),
        ("kwargs", "jsonb", "NO"),
        ("attempt", "integer", "NO"),
        ("reason", "text", "NO"),
        ("error_message", "text", "NO"),
        ("failed_at", "timestamp with time zone", "NO"),
        ("recorded_at", "timestamp with time zone", "NO"),
    ]
    assert {name for name, _ in constraints} == {
        "task_dead_letters_pkey",
        "task_dead_letters_task_id_key",
        "task_dead_letters_attempt_ck",
        "task_dead_letters_reason_ck",
    }
    assert "task_dead_letters_failed_at_idx" in dict(indexes)
    assert rls is True


def test_the_migration_file_builds_the_same_table_as_ensure_schema(postgres):
    engine, schemas, current = postgres
    dead_letters.ensure_schema(engine)
    current["schema"] = schemas[1]
    apply_migration(engine)

    assert shape(engine, schemas[1]) == shape(engine, schemas[0])

    # Either path run over the other changes nothing.
    dead_letters.ensure_schema(engine)
    current["schema"] = schemas[0]
    apply_migration(engine)
    assert shape(engine, schemas[0]) == shape(engine, schemas[1])


def test_a_redelivered_failure_is_recorded_once_with_its_jsonb_arguments(postgres):
    engine, _, _ = postgres
    dead_letters.ensure_schema(engine)
    letter = DeadLetter(
        task_id="report-pg-1",
        task_name="telemetry.generate_report",
        args=["2026-10-13T00:00:00+00:00", "2026-10-20T00:00:00+00:00"],
        kwargs={},
        attempt=4,
        reason="retries_exhausted",
        error_message="ReportUnavailable: OperationalError",
        failed_at=datetime(2026, 10, 27, 9, 0, tzinfo=UTC),
    )

    assert dead_letters.insert_dead_letter(engine, letter) is True
    assert dead_letters.insert_dead_letter(engine, letter) is False

    [row] = dead_letters.list_dead_letters(engine)
    assert row["args"] == letter.args
    assert row["failed_at"] == letter.failed_at
    assert row["recorded_at"] is not None
