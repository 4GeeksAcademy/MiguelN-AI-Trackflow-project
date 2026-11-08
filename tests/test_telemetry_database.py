from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import CreateIndex, CreateTable

from services.core import config
from services.inventory import database as inventory_database
from services.telemetry import database

INITIALIZE_SCHEMA = database.initialize_schema


def index_ddl(name):
    index = next(index for index in database.telemetry_events.indexes if index.name == name)
    return str(CreateIndex(index).compile(dialect=postgresql.dialect()))


def test_timestamp_and_event_type_have_btree_indexes():
    assert "telemetry_events (timestamp DESC)" in index_ddl("telemetry_events_timestamp_idx")
    assert "telemetry_events (event_type)" in index_ddl("telemetry_events_event_type_idx")


def test_tags_have_a_gin_index_for_jsonb_searches():
    assert "USING gin (tags)" in index_ddl("telemetry_events_tags_gin_idx")


def test_index_names_are_the_ones_created_in_supabase():
    """`IF NOT EXISTS` matches on the name, so another name would duplicate the index."""
    assert {index.name for index in database.telemetry_events.indexes} == {
        "telemetry_events_timestamp_idx",
        "telemetry_events_event_type_idx",
        "telemetry_events_tags_gin_idx",
    }


def test_initialization_only_adds_what_is_missing(monkeypatch):
    engine = MagicMock()
    monkeypatch.setattr(inventory_database, "get_engine", lambda: engine)

    INITIALIZE_SCHEMA()

    connection = engine.begin.return_value.__enter__.return_value
    statements = [
        str(call.args[0].compile(dialect=postgresql.dialect()))
        for call in connection.execute.call_args_list
    ]
    assert statements[0].startswith("\nCREATE TABLE IF NOT EXISTS telemetry_events")
    assert sum("CREATE INDEX IF NOT EXISTS" in statement for statement in statements) == 3
    joined = " ".join(statements)
    assert "ALTER TABLE telemetry_events ENABLE ROW LEVEL SECURITY" in joined
    assert "BEFORE UPDATE OR DELETE ON telemetry_events" in joined
    assert "BEFORE TRUNCATE ON telemetry_events" in joined


def test_table_definition_matches_the_supabase_table():
    ddl = str(CreateTable(database.telemetry_events).compile(dialect=postgresql.dialect()))

    for column in [
        "id UUID DEFAULT gen_random_uuid() NOT NULL",
        "timestamp TIMESTAMP WITH TIME ZONE NOT NULL",
        "event_type TEXT NOT NULL",
        "payload JSONB DEFAULT '{}'::jsonb NOT NULL",
        "tags JSONB DEFAULT '{}'::jsonb NOT NULL",
        "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
        "PRIMARY KEY (id)",
    ]:
        assert column in ddl


def test_initialization_error_does_not_expose_credentials(monkeypatch):
    engine = MagicMock()
    engine.begin.side_effect = OperationalError("", {}, Exception("password=secret"))
    monkeypatch.setattr(inventory_database, "get_engine", lambda: engine)

    with pytest.raises(config.ConfigurationError, match="Telemetry schema") as raised:
        INITIALIZE_SCHEMA()

    assert "secret" not in str(raised.value)
    assert raised.value.__suppress_context__
