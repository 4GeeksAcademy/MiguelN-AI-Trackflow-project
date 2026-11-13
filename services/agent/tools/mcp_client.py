"""The agent's MCP client: tools loaded from the TrackFlow MCP server.

The agent reads live data only through the MCP server (`mcps/trackflow_tools`),
as any other authorized MCP client would. For each lookup:

1. an access token from the agent's OAuth client (`oauth.py`, read scopes only);
2. a Streamable HTTP session with `langchain-mcp-adapters`
   (`MultiServerMCPClient.session` -> `load_mcp_tools`), the token in the
   `Authorization` header;
3. the tools the server advertises, kept to `AGENT_TOOLS` - the read tools the
   agent is meant to use. A server that no longer offers one is a failure
   (`tool_missing`), never a reason to call something else;
4. one `tool.ainvoke(...)`, whose structured content (the MCP tool's typed
   output) or JSON error comes back as an `McpCall`.

The graph runs synchronously and the adapters' tools are async-only, so each
call runs its own event loop (`asyncio.run`) inside the node's thread; if a
loop is already running there, it moves to a worker thread instead.

Every way a call can fail - no OAuth client configured, the token endpoint or
the MCP server unreachable, too slow, refusing the token, a tool error - ends
as an `McpCall` that says which, so the lookups can answer `unavailable`
honestly. A programming error still raises.
"""

import asyncio
import json
import time
import uuid
from collections.abc import Coroutine, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Final, Literal, TypeVar

import httpx
from langchain_core.messages import ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StreamableHttpConnection
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.shared._httpx_utils import McpHttpClientFactory
from mcp.shared.exceptions import McpError

from services.agent.config import AgentSettings, get_agent_settings
from services.agent.tools import oauth

SERVER_NAME: Final = "trackflow"
GET_INCIDENT: Final = "get_incident"
SEARCH_INCIDENTS: Final = "search_incidents"
QUERY_INVENTORY: Final = "query_inventory"
# The only MCP tools the agent uses: reads. The server offers more (create and
# update incidents); the agent neither loads them nor holds the scopes for them.
AGENT_TOOLS: Final = frozenset({GET_INCIDENT, SEARCH_INCIDENTS, QUERY_INVENTORY})

# Replaced in tests to reach an in-process server; None means a real connection.
http_client_factory: McpHttpClientFactory | None = None

T = TypeVar("T")
CallOutcome = Literal["ok", "tool_error", "failed"]


@dataclass(frozen=True)
class McpCall:
    """One tool call through the MCP server, and how it went."""

    tool: str
    arguments: dict[str, Any]
    outcome: CallOutcome
    duration_ms: float
    # `ok`: the tool's structured content.
    content: dict[str, Any] | None = None
    # `tool_error`: the server's JSON error, `{"error": "<code>", "message": ...}`.
    error: dict[str, Any] = field(default_factory=dict)
    # `failed`: why no answer came back (`mcp_unreachable`, `oauth_rejected`, ...).
    failure: str | None = None
    http_status: int | None = None

    @property
    def error_code(self) -> str | None:
        code = self.error.get("error")
        return code if isinstance(code, str) else None

    @property
    def label(self) -> str:
        """'MCP get_incident(reference=TRF-000482)': what was asked, without host or token."""
        arguments = ", ".join(f"{key}={value}" for key, value in self.arguments.items())
        return f"MCP {self.tool}({arguments})"


def _run(coroutine: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def _leaves(error: BaseException) -> Iterator[BaseException]:
    """The real exceptions inside the task-group exception groups the client raises."""
    if isinstance(error, BaseExceptionGroup):
        for inner in error.exceptions:
            yield from _leaves(inner)
    else:
        yield error


def _classify(error: BaseException) -> tuple[str, int | None] | None:
    """(failure label, HTTP status) for a transport failure; None for anything else."""
    for leaf in _leaves(error):
        if isinstance(leaf, httpx.HTTPStatusError):
            status = leaf.response.status_code
            if status == 401:
                return "mcp_unauthorized", 401
            if status == 403:
                return "mcp_forbidden", 403
            return f"mcp_http_{status}", status
        if isinstance(leaf, httpx.TimeoutException | TimeoutError):
            return "mcp_timeout", None
        if isinstance(leaf, httpx.TransportError | OSError):
            return "mcp_unreachable", None
        if isinstance(leaf, McpError):
            return "mcp_protocol_error", None
    return None


def _tool_error(message: ToolMessage) -> dict[str, Any]:
    texts = [
        block.get("text", "") if isinstance(block, dict) else str(block)
        for block in (message.content if isinstance(message.content, list) else [message.content])
    ]
    for text in texts:
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            return payload
    return {"error": "unreadable_error", "message": " ".join(texts)[:300]}


def _connection(settings: AgentSettings, token: str) -> StreamableHttpConnection:
    deadline = settings.agent_tool_timeout_seconds
    connection: StreamableHttpConnection = {
        "transport": "streamable_http",
        "url": settings.agent_mcp_url,
        "headers": {"Authorization": f"Bearer {token}"},
        "timeout": deadline,
        "sse_read_timeout": deadline,
    }
    if http_client_factory is not None:
        connection["httpx_client_factory"] = http_client_factory
    return connection


async def _invoke(
    settings: AgentSettings, token: str, tool: str, arguments: dict[str, Any]
) -> tuple[CallOutcome, dict[str, Any] | None, dict[str, Any], str | None]:
    client = MultiServerMCPClient({SERVER_NAME: _connection(settings, token)})
    async with asyncio.timeout(settings.agent_tool_timeout_seconds):
        async with client.session(SERVER_NAME) as session:
            loaded = {t.name: t for t in await load_mcp_tools(session) if t.name in AGENT_TOOLS}
            if tool not in loaded:
                return "failed", None, {}, "tool_missing"
            message = await loaded[tool].ainvoke(
                {"type": "tool_call", "id": uuid.uuid4().hex, "name": tool, "args": arguments}
            )
    if not isinstance(message, ToolMessage):
        return "failed", None, {}, "invalid_response"
    if message.status == "error":
        return "tool_error", None, _tool_error(message), None
    artifact = message.artifact if isinstance(message.artifact, dict) else {}
    content = artifact.get("structured_content")
    if not isinstance(content, dict):
        return "failed", None, {}, "invalid_response"
    return "ok", content, {}, None


def call_tool(tool: str, arguments: dict[str, Any]) -> McpCall:
    """Call one of `AGENT_TOOLS` on the MCP server. Never raises for a failing service."""
    if tool not in AGENT_TOOLS:
        raise ValueError(f"'{tool}' is not one of the agent's MCP tools {sorted(AGENT_TOOLS)}")
    settings = get_agent_settings()
    started = time.perf_counter()

    def result(outcome: CallOutcome, **fields: Any) -> McpCall:
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        return McpCall(tool, dict(arguments), outcome, elapsed, **fields)

    credentials = oauth.credentials(settings)
    if credentials is None:
        return result("failed", failure="not_configured")

    for attempt in range(2):
        try:
            token = credentials.token()
        except oauth.TokenFailure as failure:
            return result("failed", failure=failure.failure, http_status=failure.http_status)
        try:
            outcome, content, error, failure_label = _run(_invoke(settings, token, tool, arguments))
        except Exception as raised:
            classified = _classify(raised)
            if classified is None:
                raise  # a programming error, not a service failure
            label, status = classified
            if status == 401:
                # The token was refused (expired, revoked): get a new one, once.
                credentials.invalidate()
                if attempt == 0:
                    continue
            return result("failed", failure=label, http_status=status)
        return result(outcome, content=content, error=error, failure=failure_label)
    raise AssertionError("unreachable")  # pragma: no cover
