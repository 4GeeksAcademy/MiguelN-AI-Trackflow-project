"""The one way the lookups talk HTTP: a GET with a numeric timeout.

`get_json` is the only request the lookup modules make, so they cannot create,
change or delete anything. Every way a call can go wrong ends as a
`ServiceFailure` naming it; the lookups turn that into an `unavailable` result
instead of letting it break the run.
"""

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from services.agent.config import get_agent_settings

# Statuses a lookup reads itself; any other non-200 status is a failure.
# 401: the stock tool signs in again once. 404: "no such ticket or product".
READABLE_ERROR_STATUSES = frozenset({401, 404})


class ServiceFailure(Exception):
    """The service gave no usable answer. `failure` is a short, safe label."""

    def __init__(self, failure: str, http_status: int | None = None) -> None:
        super().__init__(failure)
        self.failure = failure
        self.http_status = http_status


@dataclass(frozen=True)
class Reply:
    status: int
    # The parsed JSON body of a 200; None for 401/404.
    body: Any


def make_client() -> httpx.Client:
    """A client with the configured timeout, no retries and no redirects."""
    timeout = get_agent_settings().agent_tool_timeout_seconds
    return httpx.Client(timeout=httpx.Timeout(timeout), follow_redirects=False)


@contextmanager
def client_for_call(client: httpx.Client | None) -> Iterator[httpx.Client]:
    """The injected client as is, or a fresh one closed after the call."""
    if client is not None:
        yield client
        return
    with make_client() as fresh:
        yield fresh


def endpoint_label(path: str, params: Mapping[str, str] | None = None) -> str:
    """'GET /api/incidents?reference=TRF-000012': the request, without host or secrets."""
    query = f"?{urlencode(params)}" if params else ""
    return f"GET {path}{query}"


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
) -> Reply:
    """GET `url`. Returns 200 with its JSON body, or 401/404 without one.

    Raises ServiceFailure for a timeout (`timeout`), a connection problem
    (`unreachable`), any other status (`http_<code>`) or a 200 whose body is not
    JSON (`invalid_response`).
    """
    try:
        response = client.get(url, params=params, headers=headers)
    except httpx.TimeoutException as error:
        raise ServiceFailure("timeout") from error
    except httpx.TransportError as error:
        raise ServiceFailure("unreachable") from error

    if response.status_code in READABLE_ERROR_STATUSES:
        return Reply(status=response.status_code, body=None)
    if response.status_code != 200:
        raise ServiceFailure(f"http_{response.status_code}", response.status_code)
    try:
        return Reply(status=200, body=response.json())
    except ValueError as error:
        raise ServiceFailure("invalid_response", 200) from error


class Stopwatch:
    """Milliseconds since it was started, for a result's `duration_ms`."""

    def __init__(self) -> None:
        self._started = time.perf_counter()

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._started) * 1000, 1)
