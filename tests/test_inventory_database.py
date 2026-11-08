from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, create_engine, text

from services.core import config
from services.core.db import get_db as get_tinydb
from services.inventory import database
from services.main import app

CHECK_CONNECTION = database.check_connection


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://test:test@localhost/postgres",
        "postgresql+psycopg2://test:test@localhost:6543/postgres?sslmode=verify-full",
        "postgresql://test:p%40ss%23word@localhost/postgres",
    ],
)
def test_valid_url_is_redacted(monkeypatch, value):
    monkeypatch.setenv("DATABASE_URL", value)
    settings = config.get_settings()
    assert settings.database_url.get_secret_value() == value
    assert value not in repr(settings)
    assert settings.model_dump(mode="json")["database_url"] == "**********"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-url-secret",
        "sqlite:///inventory.db",
        "postgresql+asyncpg://user:secret@localhost/postgres",
        "postgresql://user:secret@localhost:not-a-port/postgres",
        "postgresql://user:secret@localhost:65536/postgres",
        "postgresql://user:secret@localhost/postgres?sslmode=disable",
        "postgresql://user:secret@localhost/postgres?sslmode=prefer",
        "postgresql://user@localhost/postgres",
    ],
)
def test_invalid_url_has_safe_error(monkeypatch, value):
    monkeypatch.setenv("DATABASE_URL", value)
    with pytest.raises(config.ConfigurationError, match="DATABASE_URL") as raised:
        config.get_settings()
    assert "secret" not in str(raised.value)
    assert "postgresql://" not in str(raised.value)
    assert raised.value.__suppress_context__


def test_ssl_disabled_url_is_only_allowed_in_development(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:secret@db:5432/trackflow?sslmode=disable",
    )
    config.get_settings.cache_clear()

    settings = config.get_settings()

    assert settings.database_url.get_secret_value().endswith("sslmode=disable")


def test_missing_url_is_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(config.ValidationError) as raised:
        config.Settings(_env_file=None)
    assert any(error["loc"] == ("database_url",) for error in raised.value.errors())


def test_engine_is_cached_with_safe_pool_settings():
    engine = database.get_engine()
    assert database.get_engine() is engine
    assert engine.url.drivername == "postgresql+psycopg2"
    assert engine.pool.size() == 5
    assert engine.pool.timeout() == 10
    assert engine.echo is False
    assert engine.hide_parameters is True


def test_engine_passes_ssl_timeout_and_isolation(monkeypatch):
    create = MagicMock()
    monkeypatch.setattr(database, "create_engine", create)
    database.get_engine()
    options = create.call_args.kwargs
    assert options["connect_args"] == {"sslmode": "require", "connect_timeout": 10}
    assert options["isolation_level"] == "READ COMMITTED"
    assert options["pool_pre_ping"] is True


def test_identity_uses_existing_handle(db_path):
    assert database.get_identity_db() is get_tinydb()


def test_dependency_yields_distinct_sessions_and_closes(monkeypatch):
    engine = create_engine("sqlite://")
    monkeypatch.setattr(database, "get_engine", lambda: engine)
    factory = MagicMock(wraps=Session)
    monkeypatch.setattr(database, "Session", factory)
    first = database.get_db()
    second = database.get_db()
    first_session = next(first)
    second_session = next(second)
    assert first_session is not second_session
    first_session.execute(text("SELECT 1"))
    assert first_session.in_transaction()
    first.close()
    assert not first_session.in_transaction()
    second.close()
    engine.dispose()


def test_dependency_rolls_back_on_exception(monkeypatch):
    engine = create_engine("sqlite://")
    monkeypatch.setattr(database, "get_engine", lambda: engine)
    dependency = database.get_db()
    session = next(dependency)
    session.execute(text("SELECT 1"))
    with pytest.raises(RuntimeError, match="failed request"):
        dependency.throw(RuntimeError("failed request"))
    assert not session.in_transaction()
    engine.dispose()


def test_connection_probe_only_selects_and_closes(monkeypatch):
    engine = MagicMock()
    monkeypatch.setattr(database, "get_engine", lambda: engine)
    CHECK_CONNECTION()
    connection = engine.connect.return_value.__enter__.return_value
    assert str(connection.execute.call_args.args[0]) == "SELECT 1"
    engine.connect.return_value.__exit__.assert_called_once()


def test_connection_error_does_not_expose_credentials(monkeypatch):
    engine = MagicMock()
    engine.connect.side_effect = OperationalError("", {}, Exception("password=secret"))
    monkeypatch.setattr(database, "get_engine", lambda: engine)
    with pytest.raises(config.ConfigurationError, match="PostgreSQL connection failed") as raised:
        CHECK_CONNECTION()
    assert "secret" not in str(raised.value)
    assert raised.value.__suppress_context__


def test_disposal_clears_cached_engine(monkeypatch):
    engine = database.get_engine()
    dispose = MagicMock()
    monkeypatch.setattr(engine, "dispose", dispose)
    database.dispose_engine()
    dispose.assert_called_once()
    assert database.get_engine.cache_info().currsize == 0


def test_unused_engine_is_not_created_by_disposal(monkeypatch):
    create = MagicMock()
    monkeypatch.setattr(database, "create_engine", create)
    database.dispose_engine()
    create.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_lifespan_checks_connection_and_disposes(db_path, monkeypatch, fails):
    probe = MagicMock()
    if fails:
        probe.side_effect = config.ConfigurationError("PostgreSQL unavailable")
    cleanup = MagicMock()
    monkeypatch.setattr(database, "check_connection", probe)
    monkeypatch.setattr(database, "dispose_engine", cleanup)
    if fails:
        with pytest.raises(config.ConfigurationError, match="PostgreSQL unavailable"):
            with TestClient(app):
                pass
    else:
        with TestClient(app):
            assert get_tinydb() is database.get_identity_db()
    probe.assert_called_once()
    cleanup.assert_called_once()
