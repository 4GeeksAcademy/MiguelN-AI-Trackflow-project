"""The pipeline's own database engine, for runs outside the API process.

The API passes its shared engine (`services/inventory/database.py::get_engine`)
to the functions in this package. The flow builds one here, from the first of:

1. `REPORTING_DATABASE_URL`, for a one-off run against another database.
2. The Prefect Secret block `trackflow-reporting-database-url` (design 4.5),
   meant to hold the URL of a dedicated `reporting_pipeline` role.
3. `DATABASE_URL`, from the environment or the repository's `.env`, the same
   connection the API uses.

The URL is never logged or put in flow parameters. Nothing in `data/` imports
`services/`, so the settings are read here rather than through `get_settings`.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Final

from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url

ROOT_DIR: Final = Path(__file__).resolve().parents[2]
SECRET_BLOCK: Final = "trackflow-reporting-database-url"


class PipelineConfigurationError(RuntimeError):
    """No database URL is configured. The message names the settings, never a value."""


def _from_secret_block() -> str | None:
    try:
        from prefect.blocks.system import Secret

        # `_sync=True`: `load` is async-dispatched; this caller is always synchronous.
        block = Secret.load(SECRET_BLOCK, _sync=True)  # type: ignore[call-arg]
        return str(block.get())  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 - a missing block or an unreachable API both mean "not set"
        return None


def resolve_database_url() -> str:
    url = (
        os.environ.get("REPORTING_DATABASE_URL")
        or _from_secret_block()
        or os.environ.get("DATABASE_URL")
        or dotenv_values(ROOT_DIR / ".env").get("DATABASE_URL")
    )
    if not url:
        raise PipelineConfigurationError(
            "No database configured: set REPORTING_DATABASE_URL, the Prefect Secret block "
            f"'{SECRET_BLOCK}', or DATABASE_URL"
        )
    return url


@lru_cache(maxsize=1)
def get_pipeline_engine() -> Engine:
    """One pooled engine per process, with the API engine's safety settings.

    SSL is required unless the URL says otherwise, the transaction pooler
    (port 6543) is supported because the pipeline uses only transaction-scoped
    locks and settings, and bound parameters are hidden from error messages.
    """
    url = make_url(resolve_database_url()).set(drivername="postgresql+psycopg2")
    return create_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
        pool_timeout=10,
        isolation_level="READ COMMITTED",
        connect_args={"sslmode": url.query.get("sslmode", "require"), "connect_timeout": 10},
    )
