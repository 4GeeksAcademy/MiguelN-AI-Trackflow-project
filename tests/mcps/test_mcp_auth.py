"""MCP Auth in front of the endpoint: no valid token, no MCP - not even tools/list."""

import asyncio
import json

import httpx
import pytest

from mcps.trackflow_tools.app import build_app
from mcps.trackflow_tools.audit import MemorySink
from mcps.trackflow_tools.config import McpServerSettings
from tests.mcps.conftest import (
    ALL,
    AUDIENCE,
    BASE_URL,
    ISSUER,
    OTHER_KEY,
    RESOURCE,
    McpHarness,
    error_of,
    issue_token,
    structured,
)

LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
METADATA_URL = f"{BASE_URL}/.well-known/oauth-protected-resource/mcp"


def post_mcp(harness: McpHarness, headers: dict[str, str]) -> httpx.Response:
    async def scenario(h: McpHarness) -> httpx.Response:
        async with h.http() as http:
            return await http.post("/mcp", json=LIST_TOOLS, headers={**MCP_HEADERS, **headers})

    return harness.run(scenario)


def test_protected_resource_metadata_names_the_authorization_server_and_scopes(harness):
    async def scenario(h: McpHarness) -> httpx.Response:
        async with h.http() as http:
            return await http.get("/.well-known/oauth-protected-resource/mcp")

    response = harness.run(scenario)

    assert response.status_code == 200
    body = response.json()
    assert body["resource"] == RESOURCE
    assert body["authorization_servers"] == [ISSUER]
    assert body["scopes_supported"] == ALL
    assert body["bearer_methods_supported"] == ["header"]


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({}, "missing_auth_header"),
        ({"authorization": "Basic abc"}, "invalid_auth_header_format"),
        ({"authorization": "Bearer not-a-jwt"}, "invalid_token"),
        ({"authorization": f"Bearer {issue_token(ALL, expires_in=-3600)}"}, "invalid_token"),
        ({"authorization": f"Bearer {issue_token(ALL, key=OTHER_KEY)}"}, "invalid_token"),
        (
            {"authorization": f"Bearer {issue_token(ALL, audience='https://other-api')}"},
            "invalid_audience",
        ),
        (
            {"authorization": f"Bearer {issue_token(ALL, issuer='https://evil.example/')}"},
            "invalid_issuer",
        ),
    ],
    ids=[
        "no-header",
        "not-bearer",
        "malformed",
        "expired",
        "unknown-key",
        "wrong-audience",
        "wrong-issuer",
    ],
)
def test_no_mcp_method_runs_without_a_valid_token(harness, headers, code):
    response = post_mcp(harness, headers)

    assert response.status_code == 401
    assert response.json()["error"] == code
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert f'error="{code}"' in challenge
    assert f'resource_metadata="{METADATA_URL}"' in challenge
    # The refusal is recorded, without the token.
    rejected = [r for r in harness.sink.records if r["event"] == "auth_rejected"]
    assert rejected[-1]["status"] == 401
    assert rejected[-1]["error"] == code
    assert "authorization" not in json.dumps(rejected).lower()


def test_a_valid_token_lists_the_tools(harness):
    response = post_mcp(harness, {"authorization": f"Bearer {issue_token(['incidents:read'])}"})

    assert response.status_code == 200
    assert "get_incident" in response.text


def test_a_token_without_any_scope_still_discovers_every_tool_but_cannot_call_them(harness):
    token = issue_token([])

    async def scenario(h: McpHarness):
        async with h.session(token) as session:
            listed = await session.list_tools()
            called = await session.call_tool("get_incident", {"reference": "1"})
            return listed, called

    listed, called = harness.run(scenario)

    assert {tool.name for tool in listed.tools} == {
        "create_incident",
        "update_incident_status",
        "get_incident",
        "search_incidents",
        "query_inventory",
    }
    assert error_of(called)["error"] == "insufficient_scope"


def test_the_health_check_needs_no_token_and_says_nothing_else(harness):
    async def scenario(h: McpHarness) -> httpx.Response:
        async with h.http() as http:
            return await http.get("/healthz")

    response = harness.run(scenario)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_an_unknown_host_is_refused_before_auth(harness):
    response = post_mcp(
        harness,
        {"host": "attacker.example", "authorization": f"Bearer {issue_token(ALL)}"},
    )

    assert response.status_code == 421


def test_scopes_are_checked_per_request_not_per_connection(harness):
    """Two clients in flight at once each get exactly their own token's rights."""
    reader = issue_token(["incidents:read"], client_id="reader")
    nobody = issue_token([], client_id="nobody")

    async def scenario(h: McpHarness):
        return await asyncio.gather(
            h.call(reader, "search_incidents", {"limit": 1}),
            h.call(nobody, "search_incidents", {"limit": 1}),
            h.call(reader, "search_incidents", {"limit": 1}),
        )

    first, refused, second = harness.run(scenario)

    assert structured(first)["total"] >= 0
    assert error_of(refused)["error"] == "insufficient_scope"
    assert structured(second)["total"] >= 0
    clients = [
        (r["client_id"], r["outcome"]) for r in harness.sink.records if r["event"] == "tool_call"
    ]
    assert sorted(clients) == [("nobody", "error"), ("reader", "ok"), ("reader", "ok")]


def test_the_signing_keys_are_fetched_once_not_per_request(harness):
    token = issue_token(["incidents:read"])

    async def scenario(h: McpHarness):
        for _ in range(3):
            await h.call(token, "search_incidents", {"limit": 1})

    harness.run(scenario)

    assert harness.jwks.fetches == 1


def test_cors_preflight_is_answered_without_a_token(
    authorization_server, jwks, upstream, monkeypatch
):
    monkeypatch.setenv("MCP_CORS_ORIGINS", "https://playground.example")
    settings = McpServerSettings()  # type: ignore[call-arg]
    app = build_app(
        settings,
        authorization_server=authorization_server,
        jwks=jwks,
        upstream=upstream,
        sink=MemorySink(),
    )
    harness = McpHarness(app=app, sink=MemorySink(), jwks=jwks)

    async def scenario(h: McpHarness):
        async with h.http() as http:
            preflight = await http.options(
                "/mcp",
                headers={
                    "origin": "https://playground.example",
                    "access-control-request-method": "POST",
                    "access-control-request-headers": "authorization,content-type",
                },
            )
            refused = await http.post(
                "/mcp",
                json=LIST_TOOLS,
                headers={**MCP_HEADERS, "origin": "https://playground.example"},
            )
            return preflight, refused

    preflight, refused = harness.run(scenario)

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://playground.example"
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()
    # The browser can read the challenge, so it can start the OAuth flow.
    assert refused.status_code == 401
    assert "www-authenticate" in refused.headers["access-control-expose-headers"].lower()


def test_settings_require_the_resource_and_the_issuer(monkeypatch):
    monkeypatch.delenv("MCP_RESOURCE_URL")
    monkeypatch.delenv("MCP_AUTH_ISSUER")
    with pytest.raises(ValueError, match="resource_url"):
        McpServerSettings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "resource", ["http://host", "http://host/", "ftp://host/mcp", "https://host/mcp?x=1"]
)
def test_the_resource_url_must_be_the_endpoint(monkeypatch, resource):
    monkeypatch.setenv("MCP_RESOURCE_URL", resource)
    with pytest.raises(ValueError):
        McpServerSettings()  # type: ignore[call-arg]


def test_the_audience_defaults_to_the_resource(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_AUDIENCE", "")
    settings = McpServerSettings()  # type: ignore[call-arg]
    assert settings.expected_audience == RESOURCE
    monkeypatch.setenv("MCP_AUTH_AUDIENCE", AUDIENCE)
    assert McpServerSettings().expected_audience == AUDIENCE  # type: ignore[call-arg]
