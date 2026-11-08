"""Telemetry table DDL against a real PostgreSQL, opt-in like the inventory tests.

Set `INVENTORY_TEST_DATABASE_URL` to run them. Each test gets its own schema,
selected with `SET LOCAL search_path` at the start of every transaction, so the
real `initialize_schema()` runs unchanged - its raw SQL included - and the
isolation still holds behind Supabase's transaction pooler.

Rows are written here the way PostgREST writes them - the mapped row as JSON,
cast by PostgreSQL - since the Supabase client itself needs a Supabase project.
"""

import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import psycopg2
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateSchema, DropSchema

from services.inventory import database as inventory_database
from services.telemetry import analysis, database
from services.telemetry.models import TelemetryEvent
from services.telemetry.service import to_row
from tests.test_telemetry_service import make_event

REAL_CONNECT = psycopg2.connect
INITIALIZE_SCHEMA = database.initialize_schema

# The table and indexes as they were created by hand in Supabase, before the
# API ever ran.
SUPABASE_TABLE = (
    """
    CREATE TABLE telemetry_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        timestamp timestamptz NOT NULL,
        event_type text NOT NULL,
        payload jsonb NOT NULL DEFAULT '{}'::jsonb,
        tags jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX telemetry_events_timestamp_idx ON telemetry_events (timestamp DESC)",
    "CREATE INDEX telemetry_events_event_type_idx ON telemetry_events (event_type)",
    "CREATE INDEX telemetry_events_tags_gin_idx ON telemetry_events USING gin (tags)",
)

SUPABASE_INDEXES = {
    "telemetry_events_pkey",
    "telemetry_events_timestamp_idx",
    "telemetry_events_event_type_idx",
    "telemetry_events_tags_gin_idx",
}

INSERT = text(
    "INSERT INTO telemetry_events (id, timestamp, event_type, payload, tags) "
    "VALUES (CAST(:id AS uuid), CAST(:timestamp AS timestamptz), :event_type, "
    "CAST(:payload AS jsonb), CAST(:tags AS jsonb))"
)


@pytest.fixture
def postgres(monkeypatch):
    """An engine bound to an empty, disposable schema."""
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
    schema = f"telemetry_test_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))

    @event.listens_for(engine, "begin")
    def use_test_schema(connection):
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')

    monkeypatch.setattr(inventory_database, "get_engine", lambda: engine)
    try:
        yield engine, schema
    finally:
        event.remove(engine, "begin", use_test_schema)
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


@pytest.fixture
def initialized(postgres):
    INITIALIZE_SCHEMA()
    return postgres


def query(engine, sql, **params):
    with engine.begin() as connection:
        return connection.execute(text(sql), params).all()


def insert(engine, *raw_events, on_conflict=""):
    rows = [to_row(TelemetryEvent.model_validate(raw)) for raw in raw_events]
    params = [
        {**row, "payload": json.dumps(row["payload"]), "tags": json.dumps(row["tags"])}
        for row in rows
    ]
    statement = INSERT if not on_conflict else text(f"{INSERT.text} {on_conflict}")
    with engine.begin() as connection:
        return connection.execute(statement, params).rowcount


def columns(engine, schema):
    return {
        name: (data_type, default)
        for name, data_type, default in query(
            engine,
            "SELECT column_name, data_type, column_default FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = 'telemetry_events'",
            schema=schema,
        )
    }


def indexes(engine, schema):
    return dict(
        query(
            engine,
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = :schema AND tablename = 'telemetry_events'",
            schema=schema,
        )
    )


def test_a_fresh_database_gets_the_supabase_table_shape(initialized):
    engine, schema = initialized

    assert columns(engine, schema) == {
        "id": ("uuid", "gen_random_uuid()"),
        "timestamp": ("timestamp with time zone", None),
        "event_type": ("text", None),
        "payload": ("jsonb", "'{}'::jsonb"),
        "tags": ("jsonb", "'{}'::jsonb"),
        "created_at": ("timestamp with time zone", "now()"),
    }


def test_a_fresh_database_gets_the_supabase_indexes(initialized):
    engine, schema = initialized
    found = indexes(engine, schema)

    assert set(found) == SUPABASE_INDEXES
    assert 'USING btree ("timestamp" DESC)' in found["telemetry_events_timestamp_idx"]
    assert "USING btree (event_type)" in found["telemetry_events_event_type_idx"]
    assert "USING gin (tags)" in found["telemetry_events_tags_gin_idx"]


def test_a_hand_made_table_gets_the_guard_without_duplicate_indexes(postgres):
    engine, schema = postgres
    with engine.begin() as connection:
        for statement in SUPABASE_TABLE:
            connection.execute(text(statement))
    insert(engine, make_event())
    before = (columns(engine, schema), indexes(engine, schema))

    INITIALIZE_SCHEMA()

    assert (columns(engine, schema), indexes(engine, schema)) == before
    assert set(before[1]) == SUPABASE_INDEXES
    assert query(engine, "SELECT count(*) FROM telemetry_events") == [(1,)]
    triggers = query(
        engine,
        "SELECT tgname FROM pg_trigger "
        "WHERE tgrelid = 'telemetry_events'::regclass AND NOT tgisinternal ORDER BY 1",
    )
    assert triggers == [("telemetry_events_no_truncate",), ("telemetry_events_no_update_delete",)]
    rls = query(
        engine, "SELECT relrowsecurity FROM pg_class WHERE oid = 'telemetry_events'::regclass"
    )
    assert rls == [(True,)]


def test_initialization_is_idempotent(initialized):
    INITIALIZE_SCHEMA()
    INITIALIZE_SCHEMA()


def test_mapped_rows_are_accepted_and_tags_are_searchable(initialized):
    engine, _ = initialized
    monterrey = make_event(timestamp="2026-10-17T10:00:00")
    zaragoza = make_event(properties={"warehouse": "Zaragoza", "sku": "BOX-9", "quantity": 2})
    insert(engine, monterrey, zaragoza)

    [stored] = query(
        engine,
        "SELECT id::text, timestamp AT TIME ZONE 'UTC', event_type, payload, tags, created_at "
        "FROM telemetry_events WHERE tags @> CAST(:filter AS jsonb)",
        filter=json.dumps({"warehouse": "Monterrey"}),
    )

    assert stored.id == monterrey["eventId"]
    assert str(stored[1]) == "2026-10-17 10:00:00"
    assert stored.event_type == "outbound_order_created"
    assert stored.payload == monterrey["properties"]
    assert stored.tags["sessionId"] == monterrey["sessionId"]
    assert stored.created_at is not None
    by_session = query(
        engine,
        "SELECT id::text FROM telemetry_events WHERE tags @> CAST(:filter AS jsonb)",
        filter=json.dumps({"sessionId": zaragoza["sessionId"]}),
    )
    assert by_session == [(zaragoza["eventId"],)]


def test_a_replayed_id_is_skipped_without_tripping_the_guard(initialized):
    """What the client's `upsert(ignore_duplicates=True)` asks PostgreSQL to do."""
    engine, _ = initialized
    event_ = make_event()

    assert insert(engine, event_, on_conflict="ON CONFLICT (id) DO NOTHING") == 1
    assert insert(engine, event_, on_conflict="ON CONFLICT (id) DO NOTHING") == 0
    assert query(engine, "SELECT count(*) FROM telemetry_events") == [(1,)]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE telemetry_events SET event_type = 'section_viewed'",
        "DELETE FROM telemetry_events",
        "TRUNCATE telemetry_events",
    ],
)
def test_recorded_events_are_immutable(initialized, statement):
    engine, _ = initialized
    insert(engine, make_event())

    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
        connection.execute(text(statement))

    assert query(engine, "SELECT event_type FROM telemetry_events") == [("outbound_order_created",)]


@pytest.mark.parametrize("payload", ['{"value": NaN}', json.dumps({"value": "lo\u0000gin"})])
def test_jsonb_really_refuses_what_validation_filters(initialized, payload):
    """Why `_fits_jsonb` exists: unfiltered, one such event fails the whole insert."""
    engine, _ = initialized
    healthy = to_row(TelemetryEvent.model_validate(make_event()))
    poisoned = {**to_row(TelemetryEvent.model_validate(make_event())), "payload": payload}
    params = [
        {**healthy, "payload": json.dumps(healthy["payload"]), "tags": "{}"},
        {**poisoned, "tags": "{}"},
    ]

    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(INSERT, params)
    assert query(engine, "SELECT count(*) FROM telemetry_events") == [(0,)]


def latency_event(timestamp, **overrides):
    properties = {
        "route_template": "/auth/login",
        "method": "POST",
        "status_class": "2xx",
        "duration_ms": 120.5,
        "sample_rate": 1,
    }
    return make_event(
        timestamp=timestamp, event_type="api_latency_recorded", properties=properties, **overrides
    )


def test_the_report_loader_filters_the_window_and_types_in_postgres(initialized):
    """The SQL behind every report metric: `[start, end)`, the types asked for, the session tag."""
    engine, _ = initialized
    session = str(uuid4())
    insert(
        engine,
        latency_event("2026-10-18T00:00:00Z", sessionId=session),
        make_event(timestamp="2026-10-18T12:00:00Z", event_type="section_viewed"),
        latency_event("2026-10-17T23:59:59.999Z"),
        latency_event("2026-10-19T00:00:00Z"),
    )
    start = datetime(2026, 10, 18, tzinfo=timezone.utc)
    end = datetime(2026, 10, 19, tzinfo=timezone.utc)

    calls = analysis.load_events(start, end, ["api_latency_recorded"], payload=True)
    everything = analysis.load_events(start, end)

    assert list(calls.columns) == ["timestamp", "event_type", "session_id", "payload"]
    assert calls["session_id"].tolist() == [session]
    assert calls.loc[0, "payload"]["duration_ms"] == 120.5
    assert list(everything.columns) == ["timestamp", "event_type", "session_id"]
    assert sorted(everything["event_type"]) == ["api_latency_recorded", "section_viewed"]
    assert analysis.events_per_day(start, end)[0]["events"] == 2
    assert analysis.latency_by_route(start, end)[0]["p50_ms"] == 120.5
