"""Fixtures for the MCP server: real auth stack, no network.

- An RSA key pair stands in for Auth0: `issue_token` signs access tokens with
  it, and the server's JWKS client serves its public half without a request.
- The authorization server's metadata is built directly (`fetch_server_config`
  is forbidden), so no OIDC discovery leaves the machine.
- The Incidents Manager is the real FastAPI app through the `client` fixture
  (an `httpx.Client`, with its own TinyDB file). The inventory is a fake behind
  `httpx.MockTransport`, because the real one needs PostgreSQL; the contract
  drift tests compare its rows with the API's own models.
- `McpHarness.session(token)` talks to the full ASGI app - CORS, metadata,
  MCP Auth, the bridge, FastMCP - over Streamable HTTP in process.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import UUID

import httpx
import jwt
import pytest
from asgi_lifespan import LifespanManager
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKClient
from jwt.algorithms import RSAAlgorithm
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
from mcpauth.config import AuthorizationServerMetadata, AuthServerConfig, AuthServerType
from starlette.types import ASGIApp

from mcps.trackflow_tools import auth as mcp_auth_module
from mcps.trackflow_tools import config as mcp_config
from mcps.trackflow_tools.app import build_app
from mcps.trackflow_tools.audit import MemorySink
from mcps.trackflow_tools.config import McpServerSettings
from mcps.trackflow_tools.upstream import (
    IncidentsApi,
    ReadOnlyInventoryApi,
    ServiceAccount,
    Upstream,
)

ISSUER = "https://trackflow-test.eu.auth0.com/"
AUDIENCE = "https://mcp.trackflow.test/mcp"
BASE_URL = "http://testserver"
RESOURCE = f"{BASE_URL}/mcp"
KEY_ID = "test-key-1"
ALL = ["incidents:read", "incidents:create", "incidents:update", "inventory:read"]

T = TypeVar("T")

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
# A key the server does not know: tokens signed with it must be refused.
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    public = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    public.update(kid=kid, use="sig", alg="RS256")
    return public


def issue_token(
    scopes: list[str] | None = None,
    *,
    audience: str | list[str] = AUDIENCE,
    issuer: str = ISSUER,
    client_id: str = "agent-client",
    subject: str | None = None,
    expires_in: int = 600,
    key: rsa.RSAPrivateKey = _PRIVATE_KEY,
    kid: str = KEY_ID,
) -> str:
    """An Auth0-shaped client-credentials access token."""
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": subject or f"{client_id}@clients",
        "azp": client_id,
        "gty": "client-credentials",
        "iat": now,
        "exp": now + expires_in,
        "scope": " ".join(scopes or []),
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def isolated_mcp_settings(tmp_path, monkeypatch):
    """Pinned MCP settings whatever `.env` holds, and no real OIDC discovery."""
    values = {
        "MCP_RESOURCE_URL": RESOURCE,
        "MCP_AUTH_ISSUER": ISSUER,
        "MCP_AUTH_AUDIENCE": AUDIENCE,
        "MCP_AUDIT_LOG_PATH": str(tmp_path / "audit.jsonl"),
        "MCP_INVENTORY_EMAIL": "",
        "MCP_INVENTORY_PASSWORD": "",
        "MCP_CORS_ORIGINS": "",
        "MCP_ALLOWED_HOSTS": "",
        "MCP_TESTER_CLIENT_ID": "",
        "MCP_TESTER_CLIENT_SECRET": "",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    mcp_config.get_mcp_settings.cache_clear()

    def forbid_discovery(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Unit tests must not fetch the real authorization server")

    monkeypatch.setattr(mcp_auth_module, "fetch_server_config", forbid_discovery)
    yield
    mcp_config.get_mcp_settings.cache_clear()


@pytest.fixture
def settings() -> McpServerSettings:
    return mcp_config.get_mcp_settings()


@pytest.fixture
def authorization_server() -> AuthServerConfig:
    return AuthServerConfig(
        type=AuthServerType.OIDC,
        metadata=AuthorizationServerMetadata(
            issuer=ISSUER,
            authorization_endpoint=f"{ISSUER}authorize",
            token_endpoint=f"{ISSUER}oauth/token",
            jwks_uri=f"{ISSUER}.well-known/jwks.json",
            response_types_supported=["code"],
        ),
    )


class CountingJWKS(PyJWKClient):
    """Serves the test key set and counts how often it is fetched."""

    def __init__(self, uri: str) -> None:
        super().__init__(uri, cache_keys=True)
        self.fetches = 0

    def fetch_data(self) -> Any:
        self.fetches += 1
        return {"keys": [_jwk(_PRIVATE_KEY, KEY_ID)]}


@pytest.fixture
def jwks(authorization_server) -> CountingJWKS:
    return CountingJWKS(authorization_server.metadata.jwks_uri)


# --- A fake inventory ----------------------------------------------------------------

PRODUCTS = [
    {
        "id": "7b0e7a5e-9f8a-4a43-9a3e-7d6f0c0b1a01",
        "name": "Black running shoes",
        "sku": "SHOE-BLK-42",
        "warehouse": "Monterrey",
        "current_stock": 45,
    },
    {
        "id": "7b0e7a5e-9f8a-4a43-9a3e-7d6f0c0b1a02",
        "name": "Floral perfume 50ml",
        "sku": "PERF-FLR-50",
        "warehouse": "Zaragoza",
        "current_stock": 120,
    },
    {
        "id": "7b0e7a5e-9f8a-4a43-9a3e-7d6f0c0b1a03",
        "name": "Dell laptop 14",
        "sku": "LAPTOP-DELL-14",
        "warehouse": "Zaragoza",
        "current_stock": 8,
    },
]
ORDERS = [
    {
        "id": "a1a1a1a1-0000-4000-8000-000000000001",
        "product_id": PRODUCTS[2]["id"],
        "quantity": 9,
        "created_at": "2026-10-04T10:00:00Z",
        "user_uuid": "c0c0c0c0-0000-4000-8000-000000000001",
        "product_name": "Dell laptop 14",
        "product_sku": "LAPTOP-DELL-14",
        "warehouse": "Zaragoza",
        "direction": "inbound",
    },
    {
        "id": "a1a1a1a1-0000-4000-8000-000000000002",
        "product_id": PRODUCTS[2]["id"],
        "quantity": 1,
        "created_at": "2026-10-05T10:00:00Z",
        "user_uuid": "c0c0c0c0-0000-4000-8000-000000000001",
        "product_name": "Dell laptop 14",
        "product_sku": "LAPTOP-DELL-14",
        "warehouse": "Zaragoza",
        "direction": "outbound",
    },
]
SERVICE_TOKEN = jwt.encode(
    {"sub": "mcp-inventory", "exp": int(time.time()) + 3600}, "secret", algorithm="HS256"
)


@dataclass
class FakeInventory:
    requests: list[httpx.Request] = field(default_factory=list)
    down: bool = False
    reject_logins: bool = False
    # Refuse the next data request's token once, as an expired token would be.
    refuse_next_token: bool = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("inventory down", request=request)
        path = request.url.path
        if request.method == "POST" and path == "/auth/login":
            if self.reject_logins:
                return httpx.Response(401, json={"detail": "Incorrect email or password"})
            return httpx.Response(200, json={"access_token": SERVICE_TOKEN, "token_type": "bearer"})
        if self.refuse_next_token or (
            request.headers.get("authorization") != f"Bearer {SERVICE_TOKEN}"
        ):
            self.refuse_next_token = False
            return httpx.Response(401, json={"detail": "Not authenticated"})
        if request.method != "GET":
            return httpx.Response(201, json={"written": True})
        params = request.url.params
        if path == "/inventory/products":
            rows = [
                row
                for row in PRODUCTS
                if params.get("sku") in (None, row["sku"])
                and params.get("warehouse") in (None, row["warehouse"])
            ]
            return httpx.Response(200, json=rows)
        if path.startswith("/inventory/products/"):
            wanted = path.rsplit("/", 1)[-1]
            for row in PRODUCTS:
                if row["id"] == wanted:
                    return httpx.Response(
                        200, json={"id": row["id"], "current_stock": row["current_stock"]}
                    )
            return httpx.Response(
                404, json={"detail": {"field": "product_id", "message": "Product not found."}}
            )
        if path == "/inventory/orders":
            rows = [
                row
                for row in ORDERS
                if params.get("product_id") in (None, row["product_id"])
                and params.get("warehouse") in (None, row["warehouse"])
            ]
            return httpx.Response(200, json=rows)
        return httpx.Response(404, json={"detail": "Not Found"})

    def data_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != "/auth/login"]


@pytest.fixture
def fake_inventory() -> FakeInventory:
    return FakeInventory()


@pytest.fixture
def inventory_api(fake_inventory) -> ReadOnlyInventoryApi:
    http = httpx.Client(transport=httpx.MockTransport(fake_inventory.handler))
    account = ServiceAccount(
        "http://inventory.test", "mcp-inventory@trackflow.com", "pw", timeout=4, client=http
    )
    return ReadOnlyInventoryApi("http://inventory.test", timeout=4, account=account, client=http)


@pytest.fixture
def upstream(client, inventory_api) -> Upstream:
    """The real Incidents Manager (TestClient) and the fake inventory."""
    return Upstream(
        incidents=IncidentsApi(BASE_URL, timeout=4, client=client), inventory=inventory_api
    )


# --- The app over Streamable HTTP ---------------------------------------------------


@dataclass
class McpHarness:
    app: ASGIApp
    sink: MemorySink
    jwks: CountingJWKS

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=BASE_URL)

    @asynccontextmanager
    async def session(self, token: str | None) -> AsyncIterator[ClientSession]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=BASE_URL, headers=headers
        )
        async with (
            http,
            streamable_http_client(RESOURCE, http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session

    async def call(self, token: str, tool: str, arguments: dict[str, Any]) -> CallToolResult:
        async with self.session(token) as session:
            return await session.call_tool(tool, arguments)

    def run(self, scenario: Callable[["McpHarness"], Awaitable[T]]) -> T:
        async def with_lifespan() -> T:
            async with LifespanManager(self.app) as manager:
                self.app = manager.app
                return await scenario(self)

        return asyncio.run(with_lifespan())

    def call_sync(self, token: str, tool: str, arguments: dict[str, Any]) -> CallToolResult:
        return self.run(lambda harness: harness.call(token, tool, arguments))


@pytest.fixture
def harness(settings, authorization_server, jwks, upstream) -> Iterator[McpHarness]:
    sink = MemorySink()
    app = build_app(
        settings,
        authorization_server=authorization_server,
        jwks=jwks,
        upstream=upstream,
        sink=sink,
    )
    yield McpHarness(app=app, sink=sink, jwks=jwks)


def error_of(result: CallToolResult) -> dict[str, Any]:
    """The JSON payload of a tool error."""
    assert result.isError, result
    text = result.content[0].text  # type: ignore[union-attr]
    return json.loads(text)


def structured(result: CallToolResult) -> dict[str, Any]:
    assert not result.isError, result.content
    assert result.structuredContent is not None
    return result.structuredContent


def is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True
