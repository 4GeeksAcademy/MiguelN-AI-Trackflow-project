"""The Prefect task policies every stage shares: results, timeouts, retries, cache keys.

Retries: every task that talks to PostgreSQL retries transient errors only
(`retry_on_transient_error`). The Supabase pooler has been unreachable before
(2026-10-17 -> 2026-10-19), and a dropped connection usually comes back within
seconds to a couple of minutes. Pure transforms never retry: the same input
fails the same way. Flow-level retries (3 x 15 min, on the main flow) ride out
longer outages.
"""

import hashlib
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pandas as pd
from prefect.context import TaskRunContext
from prefect.states import State
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

DATA_DIR: Final = Path(__file__).resolve().parents[2]

# Intermediate files: the persisted results behind the transform cache. They
# hold counts per warehouse, client and week, and collection stats per
# warehouse and day; never event rows, event ids, payloads or user ids.
# Git-ignored.
RESULTS_DIR: Final = DATA_DIR / "raw" / "prefect-results"

TASK_TIMEOUT_SECONDS: Final = 300

# Each run with the same input computes the same figures, so a result younger
# than this is reused instead of recomputed (see `content_cache_key`).
CACHE_EXPIRATION: Final = timedelta(hours=1)

TRANSIENT_ERRORS: Final = (OperationalError, InterfaceError, ConnectionError, TimeoutError)


def retry_on_transient_error(_task: Any, _task_run: Any, state: State) -> bool:
    """Retry only what a retry can fix: a lost or refused connection, a timeout.

    A `ProgrammingError` (a missing column, a typo in SQL) or an integrity
    violation fails the same way every time, so retrying it only delays the
    alert.
    """
    try:
        error = state.result(raise_on_failure=False)
    except Exception:  # noqa: BLE001 - an unreadable state is not evidence against retrying
        return True
    if isinstance(error, DBAPIError) and error.connection_invalidated:
        return True
    return isinstance(error, TRANSIENT_ERRORS)


# --- Cache keys ----------------------------------------------------------------------


def source_digest(*modules: ModuleType) -> str:
    """The SHA-256 of the modules' source: a code change to a transform invalidates its cache."""
    digest = hashlib.sha256()
    for module in modules:
        digest.update(Path(str(module.__file__)).read_bytes())
    return digest.hexdigest()


def frame_digest(frame: pd.DataFrame) -> str:
    """A content hash of a DataFrame: its column names and every cell, in order."""
    digest = hashlib.sha256("\x1f".join(map(str, frame.columns)).encode())
    digest.update(pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes())
    return digest.hexdigest()


def _key(*parts: str) -> str:
    return hashlib.sha256("\x1e".join(parts).encode()).hexdigest()


def _content(value: Any) -> str:
    if isinstance(value, pd.DataFrame):
        return frame_digest(value)
    if isinstance(value, (tuple, list)):
        return _key(*(_content(item) for item in value))
    if isinstance(value, date):
        return value.isoformat()
    return repr(value)


def content_cache_key(
    name: str, *modules: ModuleType
) -> Callable[[TaskRunContext | None, dict[str, Any]], str]:
    """The cache key of a pure transform task: everything its output depends on.

    A pure function's output is fully determined by (1) the source of the
    `data/process` modules it runs and (2) its parameters, each hashed by
    content (DataFrames cell by cell, weeks by date). Two runs within
    `CACHE_EXPIRATION` (1 hour) that hand a task the same input reuse its stored
    result instead of recomputing it; one new event, a reassigned product or an
    edit to the transform gives a new key. Neither the run id nor the clock is
    part of the key: they do not change the result.
    """
    code = source_digest(*modules)

    def cache_key(_context: TaskRunContext | None, parameters: dict[str, Any]) -> str:
        inputs = (f"{param}={_content(parameters[param])}" for param in sorted(parameters))
        return f"{name}-" + _key(code, *inputs)

    return cache_key
