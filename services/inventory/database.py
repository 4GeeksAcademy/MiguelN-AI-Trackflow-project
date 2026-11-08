from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, SQLModel, create_engine, text
from tinydb import TinyDB

from services.core.config import ConfigurationError, get_settings
from services.core.db import get_db as get_tinydb
from services.inventory import models


def get_identity_db() -> TinyDB:
    return get_tinydb()


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = make_url(get_settings().database_url.get_secret_value()).set(
        drivername="postgresql+psycopg2"
    )
    return create_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=10,
        isolation_level="READ COMMITTED",
        connect_args={"sslmode": url.query.get("sslmode", "require"), "connect_timeout": 10},
    )


def get_db() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def check_connection() -> None:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise ConfigurationError(
            "PostgreSQL connection failed. Check DATABASE_URL and Supabase availability."
        ) from None


def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()


def reset_engine_after_fork() -> None:
    """Forget an engine inherited from a parent process, without closing its connections.

    A Celery worker child is a fork: any pooled connection it inherited shares
    its socket with the parent. `dispose_engine` would close those sockets
    under the parent; `dispose(close=False)` only drops this process's
    references, and the next `get_engine()` builds a fresh pool.
    """
    if get_engine.cache_info().currsize:
        get_engine().dispose(close=False)
    get_engine.cache_clear()


# `products.client_id` arrived after the table existed, and `create_all` never
# alters a table, so the column and its slug check are added here when missing.
_CLIENT_ID_COLUMN = "ALTER TABLE products ADD COLUMN IF NOT EXISTS client_id VARCHAR(64)"
_CLIENT_ID_CHECK = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'products'::regclass AND conname = 'ck_product_client_id'
    ) THEN
        ALTER TABLE products ADD CONSTRAINT ck_product_client_id
            CHECK (client_id ~ '^[a-z0-9]+(-[a-z0-9]+)*$');
    END IF;
END
$$
"""


def initialize_schema() -> None:
    try:
        tables = [
            SQLModel.metadata.tables[models.Product.__tablename__],
            SQLModel.metadata.tables[models.InboundOrder.__tablename__],
            SQLModel.metadata.tables[models.OutboundOrder.__tablename__],
        ]
        with get_engine().begin() as connection:
            SQLModel.metadata.create_all(connection, tables=tables)
            if connection.dialect.name == "postgresql":
                connection.execute(text(_CLIENT_ID_COLUMN))
                connection.execute(text(_CLIENT_ID_CHECK))
                quote = connection.dialect.identifier_preparer.quote
                for table in tables:
                    connection.execute(
                        text(f"ALTER TABLE {quote(table.name)} ENABLE ROW LEVEL SECURITY")
                    )
    except SQLAlchemyError:
        raise ConfigurationError(
            "Inventory schema initialization failed. Check PostgreSQL permissions and availability."
        ) from None
