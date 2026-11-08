"""The `telemetry_events` table and its startup initialization.

The table's shape is the one created in Supabase: `id`, `timestamp`,
`event_type`, `payload`, `tags`, `created_at`. Rows are written through the
Supabase client (see `service.py`); this module only makes sure the table, its
indexes and its append-only guard exist, which PostgREST cannot do. It shares
the inventory engine: one `DATABASE_URL`, one pool.

The table is append-only - nothing in the API updates or deletes an event, and
the database refuses to as well, so a recorded event cannot be rewritten later.
"""

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    MetaData,
    Table,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import CreateIndex, CreateTable

from services.core.config import ConfigurationError
from services.inventory import database as inventory_database

metadata = MetaData()

telemetry_events = Table(
    "telemetry_events",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("event_type", Text, nullable=False),
    Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("tags", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Named exactly as the indexes created by hand in Supabase. `IF NOT EXISTS`
# matches on the name only, so any other name would build a duplicate index.
Index("telemetry_events_timestamp_idx", telemetry_events.c.timestamp.desc())
Index("telemetry_events_event_type_idx", telemetry_events.c.event_type)
Index("telemetry_events_tags_gin_idx", telemetry_events.c.tags, postgresql_using="gin")

# Row triggers cover UPDATE and DELETE; TRUNCATE only fires statement triggers.
# A future retention purge has to bypass these deliberately, in its own job.
_REJECT_CHANGE_FUNCTION = """
CREATE OR REPLACE FUNCTION telemetry_events_reject_change() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
    RAISE EXCEPTION 'telemetry_events is append-only: % is not allowed', TG_OP;
END;
$$
"""

_REJECT_CHANGE_TRIGGERS = (
    """
    CREATE OR REPLACE TRIGGER telemetry_events_no_update_delete
    BEFORE UPDATE OR DELETE ON telemetry_events
    FOR EACH ROW EXECUTE FUNCTION telemetry_events_reject_change()
    """,
    """
    CREATE OR REPLACE TRIGGER telemetry_events_no_truncate
    BEFORE TRUNCATE ON telemetry_events
    FOR EACH STATEMENT EXECUTE FUNCTION telemetry_events_reject_change()
    """,
)


def initialize_schema() -> None:
    """Add whatever is missing: the table, each index, RLS and the guard.

    Every step is idempotent, so a table created by hand in Supabase gets its
    indexes and guard and is otherwise left as it is. Like the inventory
    tables, RLS is on with no policies: only the secret key and the API's own
    database role can reach the rows.
    """
    try:
        with inventory_database.get_engine().begin() as connection:
            connection.execute(CreateTable(telemetry_events, if_not_exists=True))
            for index in telemetry_events.indexes:
                connection.execute(CreateIndex(index, if_not_exists=True))
            connection.execute(text("ALTER TABLE telemetry_events ENABLE ROW LEVEL SECURITY"))
            connection.execute(text(_REJECT_CHANGE_FUNCTION))
            for statement in _REJECT_CHANGE_TRIGGERS:
                connection.execute(text(statement))
    except SQLAlchemyError:
        raise ConfigurationError(
            "Telemetry schema initialization failed. Check PostgreSQL permissions and availability."
        ) from None
