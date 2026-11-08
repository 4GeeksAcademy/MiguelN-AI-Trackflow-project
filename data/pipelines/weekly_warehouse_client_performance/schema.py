"""The `reporting` schema: the destination table, the run log and their audit tables.

`weekly_warehouse_client_performance` is exactly the table in BRIEFING section
5. The additions (`PIPELINE_DESIGN.md` 2.9, 3.4, 3.6, 3.7) leave its name and
columns unchanged: integrity checks, an index for the endpoint's query, a
revisions trigger that records every change to a figure, and the run log.

`ensure_schema` is idempotent and adds only what is missing, matching objects
by name, because the Supabase project is also edited by hand. Nothing here
touches `public`: the pipeline reads `telemetry_events` and never writes to it.

The schema name is read through SQLAlchemy's `schema_translate_map`, so a test
can point the whole pipeline at a disposable schema with one engine option.
Raw SQL below (functions, triggers) is built with the translated name too.
"""

from typing import Final

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable

SCHEMA: Final = "reporting"
PIPELINE_NAME: Final = "weekly_warehouse_client_performance"

# Transaction-local setting the load sets so the revisions trigger knows which
# run changed a figure. A custom GUC: its prefix is not the schema name.
RUN_ID_SETTING: Final = "reporting.run_id"

metadata = MetaData(schema=SCHEMA)

pipeline_runs = Table(
    "pipeline_runs",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("pipeline_name", Text, nullable=False),
    Column("flow_run_id", Uuid),
    Column("trigger", Text, nullable=False),
    Column("triggered_by", Uuid),
    Column("status", Text, nullable=False),
    Column("blocked_by_run_id", Uuid, ForeignKey(f"{SCHEMA}.pipeline_runs.id")),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("weeks_from", Date, nullable=False),
    Column("weeks_to", Date, nullable=False),
    Column("arrival_cutoff", DateTime(timezone=True), nullable=False),
    Column("source_watermark", DateTime(timezone=True)),
    Column("events_read", Integer),
    Column("events_excluded", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("rows_inserted", Integer),
    Column("rows_updated", Integer),
    Column("rows_unchanged", Integer),
    Column("rows_deleted", Integer),
    Column("weeks_revised", ARRAY(Date), nullable=False, server_default=text("'{}'")),
    Column("late_events_outside_window", Integer),
    Column("telemetry_dropped", Integer),
    Column("min_capture_coverage", Numeric),
    Column("quality_status", Text),
    Column("error_stage", Text),
    Column("error_class", Text),
    Column("error_message", Text),
    Column("code_version", Text),
    # A retried flow keeps its flow run id and resumes its own row (3.3).
    UniqueConstraint("flow_run_id", name="pipeline_runs_flow_run_id_key"),
    CheckConstraint(
        "trigger in ('schedule', 'manual', 'backfill')", name="pipeline_runs_trigger_ck"
    ),
    CheckConstraint(
        "status in ('running', 'completed', 'failed', 'skipped')", name="pipeline_runs_status_ck"
    ),
    CheckConstraint("quality_status in ('ok', 'degraded')", name="pipeline_runs_quality_status_ck"),
    CheckConstraint(
        "error_stage in ('extract', 'transform', 'validate', 'load')",
        name="pipeline_runs_error_stage_ck",
    ),
    CheckConstraint(
        "status = 'running' or finished_at is not null", name="pipeline_runs_finished_ck"
    ),
    CheckConstraint(
        "status not in ('failed', 'skipped') or error_class is not null",
        name="pipeline_runs_error_class_ck",
    ),
)
# One `running` row per pipeline: the database half of the concurrency guard (3.3).
Index(
    "pipeline_runs_one_running_idx",
    pipeline_runs.c.pipeline_name,
    unique=True,
    postgresql_where=text("status = 'running'"),
)
Index("pipeline_runs_latest_idx", pipeline_runs.c.pipeline_name, pipeline_runs.c.started_at.desc())

weekly_warehouse_client_performance = Table(
    "weekly_warehouse_client_performance",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("warehouse", Text, nullable=False),
    Column("client_id", Text, nullable=False),
    Column("week_start", Date, nullable=False),
    Column("inbound_units_count", Integer, nullable=False, server_default=text("0")),
    Column("outbound_orders_count", Integer, nullable=False, server_default=text("0")),
    Column("stockout_events_count", Integer, nullable=False, server_default=text("0")),
    Column("discrepancy_events_count", Integer, nullable=False, server_default=text("0")),
    Column("discrepancy_rate", Numeric, nullable=False, server_default=text("0")),
    Column("computed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # The key the load upserts on. A table created by hand from the BRIEFING SQL
    # has it under PostgreSQL's generated name; `ensure_schema` matches unique
    # constraints by their columns, so that one counts too.
    UniqueConstraint("warehouse", "client_id", "week_start", name="wwcp_warehouse_client_week_key"),
    CheckConstraint("warehouse in ('Monterrey', 'Zaragoza')", name="wwcp_warehouse_ck"),
    CheckConstraint("extract(isodow from week_start) = 1", name="wwcp_week_start_ck"),
    CheckConstraint(
        "inbound_units_count >= 0 and outbound_orders_count >= 0 "
        "and stockout_events_count >= 0 and discrepancy_events_count >= 0 "
        "and discrepancy_rate >= 0",
        name="wwcp_counts_ck",
    ),
)
# "All combinations for one week" is the endpoint's query, and week_start is the
# last column of the unique index, so it gets its own.
Index("wwcp_week_start_idx", weekly_warehouse_client_performance.c.week_start)

revisions = Table(
    "weekly_warehouse_client_performance_revisions",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("run_id", Uuid, ForeignKey(f"{SCHEMA}.pipeline_runs.id"), nullable=False),
    Column("operation", Text, nullable=False),
    Column("warehouse", Text, nullable=False),
    Column("client_id", Text, nullable=False),
    Column("week_start", Date, nullable=False),
    Column("old_values", JSONB),  # the five figures before; null on insert
    Column("new_values", JSONB),  # the five figures after; null on delete
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("operation in ('insert', 'update', 'delete')", name="wwcp_revisions_op_ck"),
)
Index(
    "wwcp_revisions_key_idx",
    revisions.c.week_start,
    revisions.c.warehouse,
    revisions.c.client_id,
)

collection_stats = Table(
    "pipeline_run_collection_stats",
    metadata,
    Column("run_id", Uuid, ForeignKey(f"{SCHEMA}.pipeline_runs.id"), nullable=False),
    Column("warehouse", Text, nullable=False),
    Column("day", Date, nullable=False),  # UTC
    Column("event_type", Text, nullable=False),
    Column("telemetry_events", Integer, nullable=False),  # as extracted, before dedupe
    Column("counted_events", Integer, nullable=False),  # after exclusions and dedupe
    Column("inventory_orders", Integer),  # null for the two non-order event types
    Column("lag_p95_seconds", Numeric),  # created_at - timestamp
    Column("max_gap_minutes", Numeric),  # longest silence between consecutive events
    Column("peak_events_per_minute", Integer),
    Column("future_dated", Integer),  # timestamp later than created_at
    PrimaryKeyConstraint("run_id", "warehouse", "day", "event_type"),
)

exclusions = Table(
    "pipeline_run_exclusions",
    metadata,
    Column("run_id", Uuid, ForeignKey(f"{SCHEMA}.pipeline_runs.id"), nullable=False),
    Column("event_id", Uuid, nullable=False),  # telemetry_events.id
    Column("reason", Text, nullable=False),  # same keys as pipeline_runs.events_excluded
    PrimaryKeyConstraint("run_id", "event_id"),
)

FIGURE_COLUMNS: Final = (
    "inbound_units_count",
    "outbound_orders_count",
    "stockout_events_count",
    "discrepancy_events_count",
    "discrepancy_rate",
)


def reporting_schema(connection: Connection) -> str:
    """The schema the `reporting` tables resolve to on this connection."""
    translate = connection.get_execution_options().get("schema_translate_map") or {}
    return translate.get(SCHEMA, SCHEMA)


def advisory_key(connection: Connection, purpose: str) -> str:
    """A lock key scoped to the effective schema, so test schemas never block production."""
    return f"{reporting_schema(connection)}:{PIPELINE_NAME}:{purpose}"


def _figures_json(row: str) -> str:
    return (
        "jsonb_build_object("
        + ", ".join(f"'{name}', {row}.{name}" for name in FIGURE_COLUMNS)
        + ")"
    )


def _trigger_sql(schema: str) -> tuple[str, ...]:
    """Functions and triggers, with every name qualified by `schema`.

    The revisions trigger raises when `reporting.run_id` is unset, so no write -
    a manual SQL fix included - can change a published figure without a record.
    Unchanged rows are never updated (the load's upsert skips them), so the
    history holds what changed, not one copy per run.
    """
    table = f'"{schema}".weekly_warehouse_client_performance'
    history = f'"{schema}".weekly_warehouse_client_performance_revisions'
    return (
        f"""
        CREATE OR REPLACE FUNCTION "{schema}".wwcp_record_revision() RETURNS trigger
        LANGUAGE plpgsql SET search_path = '' AS $$
        DECLARE
            run_id uuid := nullif(current_setting('{RUN_ID_SETTING}', true), '')::uuid;
        BEGIN
            IF run_id IS NULL THEN
                RAISE EXCEPTION 'weekly_warehouse_client_performance: set {RUN_ID_SETTING} '
                    'before changing a published figure';
            END IF;
            IF TG_OP = 'INSERT' THEN
                INSERT INTO {history}
                    (run_id, operation, warehouse, client_id, week_start, old_values, new_values)
                VALUES (run_id, 'insert', NEW.warehouse, NEW.client_id, NEW.week_start,
                        NULL, {_figures_json("NEW")});
                RETURN NEW;
            ELSIF TG_OP = 'UPDATE' THEN
                INSERT INTO {history}
                    (run_id, operation, warehouse, client_id, week_start, old_values, new_values)
                VALUES (run_id, 'update', NEW.warehouse, NEW.client_id, NEW.week_start,
                        {_figures_json("OLD")}, {_figures_json("NEW")});
                RETURN NEW;
            END IF;
            INSERT INTO {history}
                (run_id, operation, warehouse, client_id, week_start, old_values, new_values)
            VALUES (run_id, 'delete', OLD.warehouse, OLD.client_id, OLD.week_start,
                    {_figures_json("OLD")}, NULL);
            RETURN OLD;
        END;
        $$
        """,
        f"""
        CREATE OR REPLACE TRIGGER wwcp_record_revision
        AFTER INSERT OR UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION "{schema}".wwcp_record_revision()
        """,
        # TRUNCATE fires no row trigger, so it would erase figures without a record.
        f"""
        CREATE OR REPLACE FUNCTION "{schema}".reporting_reject_change() RETURNS trigger
        LANGUAGE plpgsql SET search_path = '' AS $$
        BEGIN
            RAISE EXCEPTION '%.% does not allow %', TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
        END;
        $$
        """,
        f"""
        CREATE OR REPLACE TRIGGER wwcp_no_truncate
        BEFORE TRUNCATE ON {table}
        FOR EACH STATEMENT EXECUTE FUNCTION "{schema}".reporting_reject_change()
        """,
        # The audit trail is append-only, like telemetry_events.
        f"""
        CREATE OR REPLACE TRIGGER wwcp_revisions_no_update_delete
        BEFORE UPDATE OR DELETE ON {history}
        FOR EACH ROW EXECUTE FUNCTION "{schema}".reporting_reject_change()
        """,
        f"""
        CREATE OR REPLACE TRIGGER wwcp_revisions_no_truncate
        BEFORE TRUNCATE ON {history}
        FOR EACH STATEMENT EXECUTE FUNCTION "{schema}".reporting_reject_change()
        """,
    )


def _existing_constraints(
    connection: Connection, schema: str, table: Table
) -> tuple[set[str], set[tuple[str, ...]]]:
    """The table's constraint names, and the column sets of its unique constraints."""
    rows = connection.execute(
        text(
            "SELECT c.conname, c.contype, "
            "       array(SELECT a.attname FROM pg_attribute a "
            "             WHERE a.attrelid = t.oid AND a.attnum = ANY (c.conkey) "
            "             ORDER BY a.attname) AS columns "
            "FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = :schema AND t.relname = :table"
        ),
        {"schema": schema, "table": table.name},
    ).all()
    names = {row.conname for row in rows}
    unique_columns = {tuple(row.columns) for row in rows if row.contype == "u"}
    return names, unique_columns


def _is_missing(constraint: object, names: set[str], unique_columns: set[tuple[str, ...]]) -> bool:
    if isinstance(constraint, UniqueConstraint):
        return tuple(sorted(constraint.columns.keys())) not in unique_columns
    return isinstance(constraint, CheckConstraint) and constraint.name not in names


def ensure_schema(engine: Engine) -> None:
    """Create whatever is missing in the `reporting` schema. Safe to run every time.

    One transaction under an advisory lock, so two runs starting together do
    not race on `CREATE ... IF NOT EXISTS`. A table that already exists - for
    example one created by hand from the BRIEFING SQL - keeps its data and
    gets the named constraints, indexes, RLS and triggers it lacks.
    """
    with engine.begin() as connection:
        schema = reporting_schema(connection)
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": advisory_key(connection, "ddl")},
        )
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        for table in metadata.sorted_tables:
            connection.execute(CreateTable(table, if_not_exists=True))
            names, unique_columns = _existing_constraints(connection, schema, table)
            for constraint in table.constraints:
                if _is_missing(constraint, names, unique_columns):
                    connection.execute(AddConstraint(constraint))
            for index in table.indexes:
                connection.execute(CreateIndex(index, if_not_exists=True))
            # Same posture as every other table: RLS on, no policies.
            connection.execute(
                text(f'ALTER TABLE "{schema}".{table.name} ENABLE ROW LEVEL SECURITY')
            )
        for statement in _trigger_sql(schema):
            connection.execute(text(statement))
