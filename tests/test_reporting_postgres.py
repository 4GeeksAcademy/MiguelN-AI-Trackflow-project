"""The reporting pipeline against a real PostgreSQL, opt-in like the inventory tests.

Set `INVENTORY_TEST_DATABASE_URL` to run them. Each test gets two disposable
schemas: one holding copies of the source tables (`telemetry_events`,
`products`, `inbound_orders`, `outbound_orders`), selected with
`SET LOCAL search_path` as the telemetry tests do, and one standing in for
`reporting` through SQLAlchemy's `schema_translate_map`. Nothing touches the
real `public` or `reporting` schemas, and both are dropped afterwards.

These cover PIPELINE_DESIGN.md section 7: idempotency, late events, stale
keys, failure injection, snapshot reproducibility, concurrency, the audit
trail and the read-only guarantee.
"""

import json
import os
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg2
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, InternalError
from sqlalchemy.schema import CreateSchema, CreateTable, DropSchema
from sqlmodel import SQLModel

from data.pipelines import run_log
from data.pipelines.weekly_warehouse_client_performance import extract, load, queries, schema
from data.process import collection_health, weekly_rollup
from services.inventory import models as inventory_models  # noqa: F401 - registers the tables
from services.telemetry.database import telemetry_events

REAL_CONNECT = psycopg2.connect
WEEKS = (date(2026, 10, 5), date(2026, 10, 12))
SHOES = UUID("395dfc8e-bb1e-5f97-b0d5-ea06f9af7b0e")
LAPTOP = UUID("c030461b-a5f8-527f-a05b-8c67186118a5")
USER = UUID("11111111-1111-4111-8111-111111111111")

SCHEDULED = {
    "trigger": "schedule",
    "triggered_by": None,
    "weeks_from": WEEKS[0],
    "weeks_to": WEEKS[1],
    "code_version": "test",
}

# (source schema, reporting schema) of each test's engine.
SCHEMAS: dict[object, tuple[str, str]] = {}


@pytest.fixture
def engine(monkeypatch):
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
    base = create_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_size=3,
        max_overflow=0,
        pool_pre_ping=True,
        isolation_level="READ COMMITTED",
        connect_args={"sslmode": url.query.get("sslmode", "require"), "connect_timeout": 10},
    )
    suffix = uuid4().hex
    source, reporting = f"source_test_{suffix}", f"reporting_test_{suffix}"

    def use_source_schema(connection):
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{source}"')

    test_engine = base.execution_options(schema_translate_map={"reporting": reporting})
    with base.begin() as connection:
        connection.execute(CreateSchema(source))
        connection.execute(CreateSchema(reporting))
    # From here on the schemas exist, so every exit path must drop them.
    try:
        source_engine = base.execution_options(schema_translate_map={None: source})
        with source_engine.begin() as connection:
            for name in ("products", "inbound_orders", "outbound_orders"):
                SQLModel.metadata.tables[name].create(connection)
            connection.execute(CreateTable(telemetry_events))
        event.listen(base, "begin", use_source_schema)
        schema.ensure_schema(test_engine)
        SCHEMAS[test_engine] = (source, reporting)
        yield test_engine
    finally:
        SCHEMAS.pop(test_engine, None)
        if event.contains(base, "begin", use_source_schema):
            event.remove(base, "begin", use_source_schema)
        with base.begin() as connection:
            connection.execute(DropSchema(source, cascade=True, if_exists=True))
            connection.execute(DropSchema(reporting, cascade=True, if_exists=True))
        base.dispose()


def sql(engine, statement, **params):
    with engine.begin() as connection:
        result = connection.execute(text(statement), params)
        return result.all() if result.returns_rows else result.rowcount


def report_sql(engine, statement, **params):
    """Raw SQL against the disposable `reporting` schema (`{r}` is its name)."""
    return sql(engine, statement.format(r=f'"{SCHEMAS[engine][1]}"'), **params)


def add_product(engine, product_id, sku, warehouse, client_id):
    sql(
        engine,
        "INSERT INTO products (id, name, sku, warehouse, client_id) "
        "VALUES (:id, :sku, :sku, :warehouse, :client)",
        id=product_id,
        sku=sku,
        warehouse=warehouse,
        client=client_id,
    )


def add_event(engine, event_type, at, created_at=None, **payload):
    event_id = uuid4()
    sql(
        engine,
        "INSERT INTO telemetry_events (id, timestamp, event_type, payload, tags, created_at) "
        "VALUES (:id, :ts, :type, CAST(:payload AS jsonb), '{}'::jsonb, "
        "        coalesce(CAST(:created AS timestamptz), now()))",
        id=event_id,
        ts=at,
        type=event_type,
        payload=json.dumps(payload),
        created=created_at,
    )
    return event_id


def add_order(engine, kind, product_id, quantity, at):
    sql(
        engine,
        f"INSERT INTO {kind}_orders (id, product_id, quantity, created_at, user_uuid) "
        "VALUES (:id, :product, :quantity, :at, :user)",
        id=uuid4(),
        product=product_id,
        quantity=quantity,
        at=at,
        user=USER,
    )


def at(day, hour=10):
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


@pytest.fixture
def seeded(engine):
    add_product(engine, SHOES, "SHOE-BLK-42", "Monterrey", "stride-athletics")
    add_product(engine, LAPTOP, "LAPTOP-DELL-15", "Zaragoza", "nexa-electronics")
    for day, quantity in ((6, 20), (13, 10)):
        add_order(engine, "inbound", SHOES, quantity, at(day))
        add_event(
            engine,
            weekly_rollup.INBOUND,
            at(day),
            at(day) + timedelta(seconds=5),
            product_id=str(SHOES),
            warehouse="Monterrey",
            quantity=quantity,
        )
    add_order(engine, "outbound", LAPTOP, 1, at(14))
    add_event(
        engine,
        weekly_rollup.OUTBOUND,
        at(14),
        at(14) + timedelta(seconds=5),
        product_id=str(LAPTOP),
        warehouse="Zaragoza",
        quantity=1,
    )
    return engine


def run_once(engine, weeks=WEEKS, flow_run_id=None, *, fail_after_upsert=False, monkeypatch=None):
    """The flow's stages in order, without Prefect: what each task calls."""
    run = run_log.open_run(
        engine,
        flow_run_id=flow_run_id or uuid4(),
        trigger="manual",
        triggered_by=USER,
        weeks_from=min(weeks),
        weeks_to=max(weeks),
        code_version="test",
    )
    if run.status != "running":
        return run, None
    start, end = weekly_rollup.window_bounds(weeks)
    source = extract.read_source_events(
        engine,
        window_start=start,
        window_end=end,
        arrival_cutoff=run.arrival_cutoff,
        previous_cutoff=run.previous_cutoff,
    )
    products = extract.read_product_clients(engine)
    orders = extract.read_inventory_order_counts(engine, window_start=start, window_end=end)
    rollup = weekly_rollup.build_rollup(source.events, products, weeks)
    health = collection_health.compute(source.events, rollup.exclusions, orders, weeks)
    rows = weekly_rollup.validate(rollup.rows, weeks, max(weeks))
    loaded = load.load_weekly_rows(
        engine,
        run_id=run.run_id,
        arrival_cutoff=run.arrival_cutoff,
        weeks=weeks,
        rows=rows,
        exclusions=rollup.exclusions,
        health=health,
        run_fields={
            "events_read": rollup.events_read,
            "events_excluded": rollup.events_excluded,
            "source_watermark": source.source_watermark,
            "late_events_outside_window": source.late_events_outside_window,
            "telemetry_dropped": source.telemetry_dropped,
        },
    )
    return run, loaded


def table(engine):
    return report_sql(engine, "SELECT * FROM {r}.weekly_warehouse_client_performance ORDER BY id")


def figures(engine, warehouse, client_id, week):
    (row,) = report_sql(
        engine,
        "SELECT inbound_units_count, outbound_orders_count, stockout_events_count, "
        "discrepancy_events_count, discrepancy_rate "
        "FROM {r}.weekly_warehouse_client_performance "
        "WHERE warehouse = :w AND client_id = :c AND week_start = :week",
        w=warehouse,
        c=client_id,
        week=week,
    )
    return tuple(row)


def run_row(engine, run_id):
    (row,) = report_sql(engine, "SELECT * FROM {r}.pipeline_runs WHERE id = :id", id=run_id)
    return row._mapping


# --- Idempotency ---------------------------------------------------------------------


def test_a_second_run_over_the_same_data_changes_nothing(seeded):
    first_run, first = run_once(seeded)
    assert (first.rows_inserted, first.rows_updated, first.rows_deleted) == (4, 0, 0)
    before = table(seeded)
    revisions = report_sql(
        seeded, "SELECT count(*) FROM {r}.weekly_warehouse_client_performance_revisions"
    )

    second_run, second = run_once(seeded)

    assert (second.rows_inserted, second.rows_updated, second.rows_deleted) == (0, 0, 0)
    assert second.rows_unchanged == 4
    assert table(seeded) == before  # byte-identical, computed_at included
    assert (
        report_sql(seeded, "SELECT count(*) FROM {r}.weekly_warehouse_client_performance_revisions")
        == revisions
    )
    assert figures(seeded, "Monterrey", "stride-athletics", WEEKS[1])[:2] == (10, 0)
    row = run_row(seeded, second_run.run_id)
    assert (row["status"], row["rows_unchanged"], row["quality_status"]) == ("completed", 4, "ok")
    assert row["weeks_revised"] == []


def test_a_late_event_updates_exactly_its_row_and_is_audited(seeded):
    first_run, _ = run_once(seeded)
    add_event(
        seeded, weekly_rollup.OUTBOUND, at(15), product_id=str(SHOES), warehouse="Monterrey"
    )  # arrives now, for a week already published

    second_run, second = run_once(seeded)

    assert (second.rows_inserted, second.rows_updated, second.rows_deleted) == (0, 1, 0)
    assert second.weeks_revised == (WEEKS[1],)
    assert figures(seeded, "Monterrey", "stride-athletics", WEEKS[1])[:2] == (10, 1)
    (revision,) = report_sql(
        seeded,
        "SELECT run_id, operation, old_values, new_values "
        "FROM {r}.weekly_warehouse_client_performance_revisions WHERE operation = 'update'",
    )
    assert revision.run_id == second_run.run_id
    assert revision.old_values["outbound_orders_count"] == 0
    assert revision.new_values["outbound_orders_count"] == 1
    assert run_row(seeded, second_run.run_id)["weeks_revised"] == [WEEKS[1]]


def test_reassigning_a_product_deletes_the_old_clients_rows(seeded):
    run_once(seeded)
    sql(seeded, "UPDATE products SET client_id = 'coco-beauty' WHERE id = :id", id=SHOES)

    _, second = run_once(seeded)

    assert (second.rows_inserted, second.rows_deleted) == (2, 2)
    clients = {
        row.client_id
        for row in report_sql(
            seeded, "SELECT DISTINCT client_id FROM {r}.weekly_warehouse_client_performance"
        )
    }
    assert clients == {"coco-beauty", "nexa-electronics"}


def test_a_failure_inside_the_load_leaves_the_table_untouched(seeded, monkeypatch):
    first_run, _ = run_once(seeded)
    before = table(seeded)
    add_event(seeded, weekly_rollup.OUTBOUND, at(15), product_id=str(SHOES), warehouse="Monterrey")

    def explode(*args):
        raise RuntimeError("connection lost after the upsert")

    monkeypatch.setattr(load, "_delete_stale_keys", explode)
    flow_run_id = uuid4()
    with pytest.raises(RuntimeError):
        run_once(seeded, flow_run_id=flow_run_id)

    assert table(seeded) == before  # the upsert was rolled back with everything else
    outcome = run_log.fail_run(
        seeded,
        flow_run_id=flow_run_id,
        error_class="RuntimeError",
        error_message="Publishing the weekly rows failed",
        error_stage="load",
        events_read=4,
    )
    assert outcome == "failed"
    monkeypatch.undo()

    _, rerun = run_once(seeded)
    assert rerun.rows_updated == 1
    assert figures(seeded, "Monterrey", "stride-athletics", WEEKS[1])[:2] == (10, 1)


def test_the_same_cutoff_always_reads_the_same_snapshot(seeded):
    start, end = weekly_rollup.window_bounds(WEEKS)
    cutoff = sql(seeded, "SELECT now()")[0][0]

    def read(cutoff_at):
        return extract.read_source_events(
            seeded,
            window_start=start,
            window_end=end,
            arrival_cutoff=cutoff_at,
            previous_cutoff=None,
        ).events

    original = read(cutoff)
    add_event(
        seeded,
        weekly_rollup.INBOUND,
        at(13),
        created_at=cutoff + timedelta(seconds=5),
        product_id=str(SHOES),
        warehouse="Monterrey",
        quantity=1,
    )

    assert read(cutoff)["id"].tolist() == original["id"].tolist()
    assert len(read(cutoff + timedelta(minutes=1))) == len(original) + 1


def test_a_clock_running_ahead_is_read_for_the_week_it_arrived_in(seeded):
    add_event(
        seeded,
        weekly_rollup.INBOUND,
        datetime(2026, 10, 19, 0, 3, tzinfo=timezone.utc),
        created_at=datetime(2026, 10, 18, 23, 59, tzinfo=timezone.utc),
        product_id=str(SHOES),
        warehouse="Monterrey",
        quantity=4,
    )
    run_once(seeded)
    assert figures(seeded, "Monterrey", "stride-athletics", WEEKS[1])[0] == 14


# --- Concurrency and recovery --------------------------------------------------------


def test_open_run_resumes_its_own_row_and_skips_anyone_else(engine):
    flow_run_id = uuid4()
    kwargs = SCHEDULED
    first = run_log.open_run(engine, flow_run_id=flow_run_id, **kwargs)
    retried = run_log.open_run(engine, flow_run_id=flow_run_id, **kwargs)
    assert (retried.run_id, retried.arrival_cutoff, retried.resumed) == (
        first.run_id,
        first.arrival_cutoff,
        True,
    )
    assert run_log.is_running(engine)

    other = run_log.open_run(engine, flow_run_id=uuid4(), **kwargs)
    assert (other.status, other.blocked_by_run_id) == ("skipped", first.run_id)
    row = run_row(engine, other.run_id)
    assert (row["status"], row["error_class"]) == ("skipped", "ConcurrentRun")


def test_a_dead_run_is_swept_before_a_new_one_opens(engine):
    kwargs = SCHEDULED
    crashed = run_log.open_run(engine, flow_run_id=uuid4(), **kwargs)

    alive = run_log.open_run(
        engine, flow_run_id=uuid4(), flow_run_is_terminal=lambda _: False, **kwargs
    )
    assert alive.status == "skipped"

    fresh = run_log.open_run(
        engine, flow_run_id=uuid4(), flow_run_is_terminal=lambda _: True, **kwargs
    )
    assert fresh.status == "running"
    assert run_row(engine, crashed.run_id)["error_class"] == "Abandoned"


def test_a_failed_run_keeps_its_metadata_and_reports_what_it_did(engine):
    flow_run_id = uuid4()
    opened = run_log.open_run(engine, flow_run_id=flow_run_id, **SCHEDULED)
    failure = {"error_class": "OperationalError", "error_message": "Publishing failed"}

    assert (
        run_log.fail_run(
            engine, flow_run_id=flow_run_id, error_stage="load", events_read=15, **failure
        )
        == "failed"
    )
    row = run_row(engine, opened.run_id)
    assert row["started_at"] is not None and row["finished_at"] is not None
    assert (row["status"], row["events_read"]) == ("failed", 15)
    assert (row["error_stage"], row["error_class"]) == ("load", "OperationalError")

    # Already final: nothing changes, and the caller knows a row exists.
    assert run_log.fail_run(engine, flow_run_id=flow_run_id, **failure) == "unchanged"
    assert run_row(engine, opened.run_id)["events_read"] == 15
    # A flow run that never opened a row has to be recorded elsewhere.
    assert run_log.fail_run(engine, flow_run_id=uuid4(), **failure) == "missing"
    report_sql(engine, "DROP TABLE {r}.pipeline_runs CASCADE")
    assert run_log.fail_run(engine, flow_run_id=flow_run_id, **failure) == "missing"


def test_an_expired_running_row_is_swept_even_if_prefect_cannot_answer(engine):
    kwargs = SCHEDULED
    stuck = run_log.open_run(engine, flow_run_id=uuid4(), **kwargs)
    report_sql(
        engine,
        "UPDATE {r}.pipeline_runs SET started_at = now() - interval '4 hours' WHERE id = :id",
        id=stuck.run_id,
    )
    assert run_log.open_run(engine, flow_run_id=uuid4(), **kwargs).status == "running"


def test_an_older_snapshot_never_overwrites_a_newer_one(seeded):
    slow = run_log.open_run(
        seeded,
        flow_run_id=uuid4(),
        trigger="manual",
        triggered_by=None,
        weeks_from=WEEKS[0],
        weeks_to=WEEKS[1],
        code_version="test",
    )
    # Meanwhile another run, holding a later snapshot, completed the same weeks.
    newer = uuid4()
    report_sql(
        seeded,
        "INSERT INTO {r}.pipeline_runs (id, pipeline_name, trigger, status, finished_at, "
        "weeks_from, weeks_to, arrival_cutoff) VALUES (:id, :p, 'manual', 'completed', now(), "
        ":f, :t, now() + interval '1 minute')",
        id=newer,
        p=schema.PIPELINE_NAME,
        f=WEEKS[0],
        t=WEEKS[1],
    )
    rows = weekly_rollup.build_rollup(
        weekly_rollup.empty_events(), extract.read_product_clients(seeded), WEEKS
    ).rows

    result = load.load_weekly_rows(
        seeded,
        run_id=slow.run_id,
        arrival_cutoff=slow.arrival_cutoff,
        weeks=WEEKS,
        rows=rows,
        exclusions=weekly_rollup.empty_exclusions(),
        health=None,
        run_fields={},
    )

    assert (result.status, result.blocked_by_run_id) == ("skipped", newer)
    assert table(seeded) == []
    row = run_row(seeded, slow.run_id)
    assert (row["status"], row["error_class"]) == ("skipped", "StaleSnapshot")


# --- Audit trail and guards ----------------------------------------------------------


def test_no_figure_changes_without_a_run_id(seeded):
    run_once(seeded)
    with pytest.raises(InternalError, match="reporting.run_id"):
        report_sql(
            seeded, "UPDATE {r}.weekly_warehouse_client_performance SET inbound_units_count = 1"
        )
    with pytest.raises(DBAPIError, match="does not allow TRUNCATE"):
        report_sql(seeded, "TRUNCATE {r}.weekly_warehouse_client_performance")
    with pytest.raises(DBAPIError, match="does not allow"):
        report_sql(seeded, "DELETE FROM {r}.weekly_warehouse_client_performance_revisions")


def test_constraints_repeat_the_validation_rules(seeded):
    run_id = run_once(seeded)[0].run_id
    statement = (
        "SELECT set_config('reporting.run_id', :run, true); "
        "INSERT INTO {r}.weekly_warehouse_client_performance "
        "(warehouse, client_id, week_start) VALUES (:w, 'x', :week)"
    )
    for warehouse, week in (("los_angeles", WEEKS[0]), ("Monterrey", date(2026, 10, 6))):
        with pytest.raises(DBAPIError, match="wwcp_"):
            report_sql(seeded, statement, run=str(run_id), w=warehouse, week=week)


def test_extraction_runs_read_only(seeded):
    with seeded.connect() as connection, connection.begin():
        extract._read_only(connection)
        with pytest.raises(DBAPIError, match="read-only transaction"):
            connection.execute(
                text("INSERT INTO telemetry_events (timestamp, event_type) VALUES (now(), 'x')")
            )
    count = sql(seeded, "SELECT count(*) FROM telemetry_events")[0][0]
    run_once(seeded)
    assert sql(seeded, "SELECT count(*) FROM telemetry_events")[0][0] == count


def test_ensure_schema_completes_a_table_made_by_hand_from_the_briefing(engine):
    _, reporting = SCHEMAS[engine]
    report_sql(engine, "DROP TABLE {r}.weekly_warehouse_client_performance CASCADE")
    report_sql(
        engine,
        "CREATE TABLE {r}.weekly_warehouse_client_performance ("
        " id uuid primary key default gen_random_uuid(), warehouse text not null,"
        " client_id text not null, week_start date not null,"
        " inbound_units_count integer not null default 0,"
        " outbound_orders_count integer not null default 0,"
        " stockout_events_count integer not null default 0,"
        " discrepancy_events_count integer not null default 0,"
        " discrepancy_rate numeric not null default 0,"
        " computed_at timestamptz not null default now(),"
        " unique (warehouse, client_id, week_start))",
    )
    schema.ensure_schema(engine)
    schema.ensure_schema(engine)
    names = {
        row[0]
        for row in sql(
            engine,
            "SELECT conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = :s AND t.relname = 'weekly_warehouse_client_performance'",
            s=reporting,
        )
    }
    assert {"wwcp_warehouse_ck", "wwcp_week_start_ck", "wwcp_counts_ck"} <= names
    assert "wwcp_warehouse_client_week_key" not in names  # the hand-made unique key is reused
    triggers = {
        row[0]
        for row in sql(
            engine,
            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid = "
            "CAST(:t AS regclass)",
            t=f'"{reporting}".weekly_warehouse_client_performance',
        )
    }
    assert {"wwcp_record_revision", "wwcp_no_truncate"} <= triggers


# --- Readers -------------------------------------------------------------------------


def test_readers_distinguish_never_computed_from_empty(seeded):
    assert run_log.get_latest_run(seeded) is None
    assert queries.get_pipeline_status(seeded, datetime.now(timezone.utc)) is None
    assert run_log.latest_completed_week(seeded) is None
    assert not run_log.is_week_computed(seeded, WEEKS[1])

    run_once(seeded)

    latest = run_log.get_latest_run(seeded)
    assert latest["status"] == "completed" and "flow_run_id" not in latest
    assert run_log.latest_completed_week(seeded) == WEEKS[1]
    assert run_log.is_week_computed(seeded, WEEKS[0])
    assert not run_log.is_week_computed(seeded, date(2026, 10, 19))
    entries = queries.get_weekly_performance(seeded, WEEKS[1])
    assert [(e["warehouse"], e["client_id"]) for e in entries] == [
        ("Monterrey", "stride-athletics"),
        ("Zaragoza", "nexa-electronics"),
    ]
    assert entries[1]["outbound_orders_count"] == 1
    assert isinstance(entries[1]["discrepancy_rate"], float)

    on_the_monday_after = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)
    status = queries.get_pipeline_status(seeded, on_the_monday_after)
    assert (status["last_closed_week"], status["stale"]) == (WEEKS[1], False)
    assert status["weeks_revised"] == [] and set(status["events_excluded"]) == set(
        weekly_rollup.EXCLUSION_REASONS
    )
    a_week_later = datetime(2026, 10, 27, 9, tzinfo=timezone.utc)
    assert queries.get_pipeline_status(seeded, a_week_later)["stale"] is True


def test_readers_answer_none_before_the_schema_exists(engine):
    report_sql(engine, "DROP TABLE {r}.pipeline_runs CASCADE")
    report_sql(engine, "DROP TABLE {r}.weekly_warehouse_client_performance CASCADE")
    assert run_log.get_latest_run(engine) is None
    assert not run_log.is_running(engine)
    assert queries.get_weekly_performance(engine, WEEKS[0]) == []


# --- The real flow, end to end -------------------------------------------------------


def test_the_prefect_flow_runs_end_to_end(seeded, monkeypatch, tmp_path):
    from prefect.testing.utilities import prefect_test_harness

    from data.pipelines import pipeline
    from tests.pipelines.prefect_support import redirect_cached_results

    monkeypatch.setattr(pipeline.reporting_db, "get_pipeline_engine", lambda: seeded)
    monkeypatch.setattr(pipeline, "EVAL_DIR", tmp_path / "eval")
    redirect_cached_results(monkeypatch, tmp_path / "results")
    flow = pipeline.weekly_warehouse_client_performance.with_options(retries=0)

    with prefect_test_harness(server_startup_timeout=120):
        first = flow(week_start=WEEKS[1], trigger="manual", return_state=True)
        second = flow(week_start=WEEKS[1], trigger="manual", return_state=True)

    assert first.is_completed() and second.is_completed()
    assert first.result()["rows_inserted"] == 2
    assert (second.result()["rows_inserted"], second.result()["rows_updated"]) == (0, 0)
    assert len(list((tmp_path / "eval").glob("*.json"))) == 2
    stats = report_sql(seeded, "SELECT count(*) FROM {r}.pipeline_run_collection_stats")[0][0]
    assert stats > 0
