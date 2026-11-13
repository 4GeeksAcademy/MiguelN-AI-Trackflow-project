"""Traceability: one JSON record per tool invocation and per refused request.

Records go to the logger `trackflow.mcp.audit` and, appended as JSON lines, to
`MCP_AUDIT_LOG_PATH` (default `logs/mcp_tool_calls.jsonl`, git-ignored).

A `tool_call` record says which tool, which client (the token's `client_id` /
`azp`, its `sub` and scopes), with which arguments, what came of it (`ok` or
the error code), which upstream requests it made and how long it took.
Arguments are logged by value only when they identify or filter (ids,
references, statuses, operations); free text - an incident's title or
description, a product name - is logged as its length. Tokens are never logged.

An `auth_rejected` record is written for every 401/403 MCP Auth answers on the
endpoint, with MCP Auth's error code (the request never reached a tool, so
there is no client to name).
"""

import json
import logging
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("trackflow.mcp.audit")

# Logged by value: they identify a record or filter a search.
LOGGED_BY_VALUE: Final = frozenset(
    {"incident_id", "reference", "status", "category", "origin", "branch", "operation",
     "product_id", "sku", "warehouse", "limit"}
)  # fmt: skip


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def loggable_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    logged: dict[str, Any] = {}
    for name, value in sorted(arguments.items()):
        if name in LOGGED_BY_VALUE and isinstance(value, str | int | float | bool | None):
            logged[name] = value
        elif isinstance(value, str):
            logged[name] = f"<{len(value)} chars>"
        else:
            logged[name] = f"<{type(value).__name__}>"
    return logged


class AuditSink:
    """Writes audit records to the logger and, when a path is set, to a JSONL file."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._lock = threading.Lock()

    def write(self, record: Mapping[str, Any]) -> None:
        line = json.dumps(record, default=str, sort_keys=True)
        logger.info(line)
        if self._path is None:
            return
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            # The record is in the log already; a full disk must not fail the call.
            logger.warning("could not append to the audit file %s", self._path)


class MemorySink(AuditSink):
    """Keeps records in memory (tests)."""

    def __init__(self) -> None:
        super().__init__(None)
        self.records: list[dict[str, Any]] = []

    def write(self, record: Mapping[str, Any]) -> None:
        self.records.append(dict(record))


def _www_authenticate_error(value: str | None) -> str | None:
    if not value:
        return None
    marker = 'error="'
    start = value.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = value.find('"', start)
    return value[start:end] if end != -1 else None


class AuthRejectionLog:
    """ASGI middleware outside MCP Auth's: records every 401/403 it answers."""

    def __init__(self, app: ASGIApp, sink_factory: Callable[[], AuditSink]) -> None:
        self.app = app
        self._sink_factory = sink_factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def observe(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] in {401, 403}:
                headers = Headers(raw=message.get("headers", []))
                client = scope.get("client")
                self._sink_factory().write(
                    {
                        "event": "auth_rejected",
                        "ts": now_iso(),
                        "status": message["status"],
                        "error": _www_authenticate_error(headers.get("www-authenticate")),
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                        "remote": client[0] if client else None,
                    }
                )
            await send(message)

        await self.app(scope, receive, observe)
