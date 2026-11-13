"""FastMCP middleware: what happens around every tool call, outermost first.

1. `AuditMiddleware` - one audit record per call, with the final outcome.
2. `ErrorShapingMiddleware` - every failure leaves as a `ToolFailure` with a
   code: argument validation -> `invalid_arguments`, an unknown tool ->
   `unknown_tool`, anything unexpected -> `internal_error` (details logged,
   never sent).
3. `ReadOnlyInventoryGuard` - refuses any inventory modification attempt with
   `inventory_read_only`, before arguments are validated and whatever the
   token's scopes.
4. `ScopeGuard` - per-tool least privilege: the token (validated by MCP Auth
   before any MCP method runs) must carry every scope the tool's policy
   requires, else `insufficient_scope`.

`tools/list` is not filtered by scope: any valid token can discover every tool,
its required scopes and its errors; each call is then enforced.
"""

import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

import mcp.types as mt
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcpauth.types import AuthInfo
from pydantic import ValidationError as PydanticValidationError

from mcps.trackflow_tools.audit import AuditSink, loggable_arguments, now_iso
from mcps.trackflow_tools.errors import ErrorCode, ToolFailure
from mcps.trackflow_tools.permissions import (
    POLICIES,
    QUERY_INVENTORY,
    inventory_write_attempt,
    is_inventory_write_tool_name,
    missing_scopes,
)
from mcps.trackflow_tools.upstream import ReadOnlyViolation, recording_upstream_calls

logger = logging.getLogger("trackflow.mcp")

AuthResolver = Callable[[], AuthInfo | None]
ToolCallContext = MiddlewareContext[mt.CallToolRequestParams]


def _arguments(context: ToolCallContext) -> dict[str, Any]:
    return dict(context.message.arguments or {})


class AuditMiddleware(Middleware):
    def __init__(self, sink: AuditSink, auth: AuthResolver) -> None:
        self._sink = sink
        self._auth = auth

    async def on_call_tool(
        self, context: ToolCallContext, call_next: CallNext[mt.CallToolRequestParams, ToolResult]
    ) -> ToolResult:
        started = time.perf_counter()
        info = self._auth()
        record: dict[str, Any] = {
            "event": "tool_call",
            "ts": now_iso(),
            "tool": context.message.name,
            "client_id": info.client_id if info else None,
            "subject": info.subject if info else None,
            "scopes": sorted(info.scopes) if info else [],
            "arguments": loggable_arguments(_arguments(context)),
        }
        fastmcp_context = context.fastmcp_context
        if fastmcp_context is not None:
            with contextlib.suppress(RuntimeError):  # no request context (in-process calls)
                record["request_id"] = fastmcp_context.request_id
        with recording_upstream_calls() as calls:
            try:
                result = await call_next(context)
            except ToolFailure as failure:
                record.update(outcome="error", error=failure.code.value)
                raise
            except Exception:
                record.update(outcome="error", error=ErrorCode.INTERNAL_ERROR.value)
                raise
            else:
                record.update(outcome="ok", error=None)
                return result
            finally:
                record["upstream"] = list(calls)
                record["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
                self._sink.write(record)


ErrorT = TypeVar("ErrorT", bound=BaseException)


def _cause_of_type(error: BaseException, kind: type[ErrorT]) -> ErrorT | None:
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, kind):
            return seen
        seen = seen.__cause__ or seen.__context__
    return None


def _problems(error: BaseException) -> list[dict[str, str]]:
    """Field-level problems from the pydantic error behind a validation failure."""
    validation = _cause_of_type(error, PydanticValidationError)
    if validation is None:
        return [{"field": "arguments", "message": str(error).splitlines()[0][:200]}]
    return [
        {
            "field": ".".join(str(part) for part in item["loc"]) or "arguments",
            "message": item["msg"],
        }
        for item in validation.errors(include_url=False)
    ]


class ErrorShapingMiddleware(Middleware):
    async def on_call_tool(
        self, context: ToolCallContext, call_next: CallNext[mt.CallToolRequestParams, ToolResult]
    ) -> ToolResult:
        name = context.message.name
        try:
            return await call_next(context)
        except ToolFailure:
            raise
        except (FastMCPValidationError, PydanticValidationError) as error:
            raise ToolFailure(
                ErrorCode.INVALID_ARGUMENTS,
                f"The arguments for '{name}' do not match its input schema.",
                problems=_problems(error),
            ) from error
        except NotFoundError as error:
            raise ToolFailure(
                ErrorCode.UNKNOWN_TOOL, f"No tool named '{name}'. Call tools/list."
            ) from error
        except Exception as error:
            # FastMCP masks an exception raised inside a tool as a generic
            # ToolError; the original is kept as its cause.
            violation = _cause_of_type(error, ReadOnlyViolation)
            if violation is not None:
                # The last line of defence fired: something tried to send a write
                # to the inventory. Refuse like the guard does, and log it as a bug.
                logger.error("inventory write blocked at the client: %s", violation)
                raise ToolFailure(
                    ErrorCode.INVENTORY_READ_ONLY, "The inventory is read-only on this server."
                ) from error
            if isinstance(error, ToolError):
                logger.error("tool '%s' failed: %s", name, error)
            else:
                logger.exception("tool '%s' failed", name)
            raise ToolFailure(ErrorCode.INTERNAL_ERROR, f"'{name}' failed unexpectedly.") from error


class ReadOnlyInventoryGuard(Middleware):
    async def on_call_tool(
        self, context: ToolCallContext, call_next: CallNext[mt.CallToolRequestParams, ToolResult]
    ) -> ToolResult:
        name = context.message.name
        if name not in POLICIES and is_inventory_write_tool_name(name):
            raise ToolFailure(
                ErrorCode.INVENTORY_READ_ONLY,
                f"'{name}' would modify inventory data, and the inventory is read-only on "
                f"this server. Use {QUERY_INVENTORY} to read it.",
                tool=name,
            )
        if name == QUERY_INVENTORY:
            reason = inventory_write_attempt(_arguments(context))
            if reason is not None:
                raise ToolFailure(
                    ErrorCode.INVENTORY_READ_ONLY,
                    f"The inventory is read-only on this server: {reason}.",
                    tool=name,
                )
        return await call_next(context)


class ScopeGuard(Middleware):
    def __init__(self, auth: AuthResolver) -> None:
        self._auth = auth

    async def on_call_tool(
        self, context: ToolCallContext, call_next: CallNext[mt.CallToolRequestParams, ToolResult]
    ) -> ToolResult:
        policy = POLICIES.get(context.message.name)
        if policy is None:
            # Not a tool of ours: FastMCP answers unknown_tool.
            return await call_next(context)
        info = self._auth()
        granted = info.scopes if info is not None else []
        missing = missing_scopes(policy, granted)
        if missing:
            raise ToolFailure(
                ErrorCode.INSUFFICIENT_SCOPE,
                f"'{policy.tool}' requires the scope(s) {', '.join(missing)}, which this "
                "access token does not carry.",
                required_scopes=policy.scope_names,
                granted_scopes=sorted(granted),
            )
        return await call_next(context)
