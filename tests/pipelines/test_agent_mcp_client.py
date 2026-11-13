"""The agent's MCP client: contracts, lookups mapped onto MCP tools, OAuth, failures.

Nothing here reaches a real server. The lookups are tested against a fake
`mcp_client.call_tool`; the client's own failure handling against a fake
`_invoke`; the token cache against `httpx.MockTransport`. The end-to-end runs
(agent -> real MCP server -> real Incidents Manager) are in
`tests/mcps/test_agent_over_mcp.py`.
"""

import ast
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from jose import jwt
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData
from pydantic import ValidationError

from mcps.trackflow_tools import contracts as mcp_contracts
from services.agent import config as agent_config
from services.agent.tools import mcp_client, mcp_lookups, oauth, passages
from services.agent.tools.contracts import (
    MAX_PRODUCTS,
    MAX_TICKETS,
    ProductStock,
    StockLookupInput,
    StockLookupResult,
    TicketLookupInput,
    TicketLookupResult,
    TicketRecord,
)
from services.incidents.models import IncidentResponse
from services.inventory.schemas import ProductResponse

ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = ROOT / "services" / "agent"
REAL_MAKE_HTTP_CLIENT = oauth.make_http_client

TICKET = {
    "id": "6c7f3b0e-5a1d-4a8e-9a43-0f5f0e7d2a11",
    "reference": "TRF-000012",
    "title": "Wrong postcode assigned during label generation",
    "description": "The label shows 28001 instead of 28010.",
    "category": "delivery_failure",
    "origin": "customer",
    "branch": "central",
    "status": "in_progress",
    "created_at": "2026-10-20T04:21:51Z",
    "updated_at": "2026-10-21T09:00:00Z",
}
SHOES = {
    "id": "395dfc8e-bb1e-5f97-b0d5-ea06f9af7b0e",
    "name": "Black Running Shoes - Size 42",
    "sku": "SHOE-BLK-42",
    "warehouse": "Monterrey",
    "current_stock": 45,
}


def settings_env(monkeypatch, **values: str) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    agent_config.get_agent_settings.cache_clear()
    oauth.reset_credentials()


def configure_oauth(monkeypatch, **extra: str) -> None:
    settings_env(
        monkeypatch,
        AGENT_OAUTH_TOKEN_URL="https://auth.test/oauth/token",
        AGENT_OAUTH_CLIENT_ID="trackflow-agent",
        AGENT_OAUTH_CLIENT_SECRET="s3cret",
        AGENT_OAUTH_AUDIENCE="https://mcp.trackflow.test/mcp",
        **extra,
    )


class FakeCalls:
    """Stands in for `mcp_client.call_tool`, answering from a queue of results."""

    def __init__(self, *answers: dict[str, Any]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, tool: str, arguments: dict[str, Any]) -> mcp_client.McpCall:
        self.calls.append((tool, dict(arguments)))
        answer = self.answers.pop(0)
        return mcp_client.McpCall(tool, dict(arguments), duration_ms=7.0, **answer)


def ok(content: dict[str, Any]) -> dict[str, Any]:
    return {"outcome": "ok", "content": content}


def tool_error(code: str) -> dict[str, Any]:
    return {"outcome": "tool_error", "error": {"error": code, "message": "..."}}


def failed(label: str, status: int | None = None) -> dict[str, Any]:
    return {"outcome": "failed", "failure": label, "http_status": status}


@pytest.fixture
def fake_calls(monkeypatch):
    def install(*answers: dict[str, Any]) -> FakeCalls:
        fake = FakeCalls(*answers)
        monkeypatch.setattr(mcp_client, "call_tool", fake)
        return fake

    return install


# --- Contracts ---------------------------------------------------------------------


def test_the_ticket_record_mirrors_the_incident_api_and_the_mcp_tool():
    assert set(TicketRecord.model_fields) == set(IncidentResponse.model_fields)
    assert set(TicketRecord.model_fields) == set(mcp_contracts.Incident.model_fields)


def test_the_stock_record_mirrors_the_inventory_api_and_the_mcp_tool():
    assert set(ProductStock.model_fields) == set(ProductResponse.model_fields)
    assert set(ProductStock.model_fields) == set(mcp_contracts.Product.model_fields)


def test_a_ticket_lookup_needs_a_reference_or_a_filter():
    with pytest.raises(ValidationError, match="reference or at least one filter"):
        TicketLookupInput()
    assert TicketLookupInput(status="open", category="lost_parcel").filters() == {
        "status": "open",
        "category": "lost_parcel",
    }


@pytest.mark.parametrize(
    "arguments",
    [{"status": "lost"}, {"reference": "12", "priority": "high"}, {"reference": ""}],
)
def test_a_ticket_lookup_rejects_what_the_contract_does_not_define(arguments):
    with pytest.raises(ValidationError):
        TicketLookupInput.model_validate(arguments)


def test_a_stock_lookup_needs_a_product_and_knows_the_warehouses():
    with pytest.raises(ValidationError):
        StockLookupInput.model_validate({})
    with pytest.raises(ValidationError):
        StockLookupInput(product="shoes", warehouse="Los Angeles")


# --- Ticket lookups mapped onto MCP tools ------------------------------------------


@pytest.mark.parametrize("written", ["12", "TRF-12", "trf 000012"])
def test_a_helpdesk_number_becomes_get_incident_by_reference(fake_calls, written):
    calls = fake_calls(ok(TICKET))

    result = mcp_lookups.lookup_ticket(TicketLookupInput(reference=written))

    assert calls.calls == [("get_incident", {"reference": "TRF-000012"})]
    assert result.outcome == "ok"
    assert result.reference == "TRF-000012"
    assert result.tickets[0].status.value == "in_progress"
    assert result.total == 1
    assert result.endpoint == "MCP get_incident(reference=TRF-000012)"
    assert result.duration_ms == 7.0


def test_any_other_reference_is_read_as_an_incident_id(fake_calls):
    calls = fake_calls(ok(TICKET))

    mcp_lookups.lookup_ticket(TicketLookupInput(reference=TICKET["id"]))

    assert calls.calls == [("get_incident", {"incident_id": TICKET["id"]})]


def test_a_search_asks_for_at_most_the_cap_and_keeps_the_full_count(fake_calls):
    calls = fake_calls(ok({"incidents": [TICKET] * 3, "total": 25}))

    result = mcp_lookups.lookup_ticket(TicketLookupInput(status="open", branch="central"))

    assert calls.calls == [
        ("search_incidents", {"status": "open", "branch": "central", "limit": MAX_TICKETS})
    ]
    assert result.outcome == "ok"
    assert len(result.tickets) == 3
    assert result.total == 25


def test_a_search_with_no_match_is_a_real_answer(fake_calls):
    fake_calls(ok({"incidents": [], "total": 0}))
    result = mcp_lookups.lookup_ticket(TicketLookupInput(status="discarded"))
    assert (result.outcome, result.total) == ("ok", 0)


def test_an_unknown_ticket_is_not_found(fake_calls):
    fake_calls(tool_error("incident_not_found"))
    result = mcp_lookups.lookup_ticket(TicketLookupInput(reference="482"))
    assert result.outcome == "not_found"
    assert result.reference == "TRF-000482"


@pytest.mark.parametrize(
    ("answer", "failure", "status"),
    [
        (tool_error("upstream_unavailable"), "upstream_unavailable", None),
        (tool_error("insufficient_scope"), "insufficient_scope", None),
        (failed("mcp_unreachable"), "mcp_unreachable", None),
        (failed("mcp_timeout"), "mcp_timeout", None),
        (failed("mcp_forbidden", 403), "mcp_forbidden", 403),
        (failed("oauth_rejected", 401), "oauth_rejected", 401),
        (failed("not_configured"), "not_configured", None),
        (failed("tool_missing"), "tool_missing", None),
        (ok({"not": "a ticket"}), "invalid_response", None),
    ],
)
def test_every_failure_becomes_an_unavailable_result(fake_calls, answer, failure, status):
    fake_calls(answer)

    result = mcp_lookups.lookup_ticket(TicketLookupInput(reference="12"))

    assert result.outcome == "unavailable"
    assert result.failure == failure
    assert result.http_status == status
    assert result.tickets == []


# --- Stock lookups mapped onto the read-only inventory tool ------------------------


def test_a_sku_is_asked_as_an_exact_sku(fake_calls):
    calls = fake_calls(ok({"operation": "list_products", "products": [SHOES], "total": 1}))

    result = mcp_lookups.lookup_stock(StockLookupInput(product="shoe-blk-42"))

    assert calls.calls == [
        (
            "query_inventory",
            {"operation": "list_products", "limit": MAX_PRODUCTS, "sku": "SHOE-BLK-42"},
        )
    ]
    assert result.outcome == "ok"
    assert result.products[0].current_stock == 45


def test_a_product_in_words_is_asked_by_name_and_warehouse(fake_calls):
    calls = fake_calls(ok({"operation": "list_products", "products": [SHOES], "total": 1}))

    mcp_lookups.lookup_stock(StockLookupInput(product="black running shoes", warehouse="Monterrey"))

    assert calls.calls[0][1] == {
        "operation": "list_products",
        "limit": MAX_PRODUCTS,
        "name": "black running shoes",
        "warehouse": "Monterrey",
    }


def test_no_matching_product_is_not_found(fake_calls):
    fake_calls(ok({"operation": "list_products", "products": [], "total": 0}))
    assert (
        mcp_lookups.lookup_stock(StockLookupInput(product="purple umbrella")).outcome == "not_found"
    )


def test_an_inventory_failure_is_unavailable(fake_calls):
    fake_calls(tool_error("upstream_auth_failed"))
    result = mcp_lookups.lookup_stock(StockLookupInput(product="shoes"))
    assert (result.outcome, result.failure) == ("unavailable", "upstream_auth_failed")


def test_only_a_sku_shaped_request_is_sent_as_a_sku():
    assert mcp_lookups.sku_in("shoe-blk-42") == "SHOE-BLK-42"
    assert mcp_lookups.sku_in("black running shoes") is None
    assert mcp_lookups.sku_in("laptop") is None


# --- The MCP client ----------------------------------------------------------------


def test_the_client_refuses_tools_outside_its_allow_list():
    for tool in ("create_incident", "update_incident_status", "delete_everything"):
        with pytest.raises(ValueError, match="not one of the agent's MCP tools"):
            mcp_client.call_tool(tool, {})


def test_without_an_oauth_client_no_request_is_made(monkeypatch):
    def no_session(*args, **kwargs):
        raise AssertionError("no MCP session without credentials")

    monkeypatch.setattr(mcp_client, "_invoke", no_session)

    call = mcp_client.call_tool("get_incident", {"reference": "TRF-000012"})

    assert (call.outcome, call.failure) == ("failed", "not_configured")


class TokenEndpoint:
    """A MockTransport token endpoint that issues numbered tokens and records the forms."""

    def __init__(self, status: int = 200, expires_in: int = 86400) -> None:
        self.status = status
        self.expires_in = expires_in
        self.forms: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        from urllib.parse import parse_qsl

        self.forms.append(dict(parse_qsl(request.content.decode())))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "access_denied"})
        return httpx.Response(
            200,
            json={
                "access_token": f"token-{len(self.forms)}",
                "token_type": "Bearer",
                "expires_in": self.expires_in,
            },
        )


@pytest.fixture
def token_endpoint(monkeypatch):
    endpoint = TokenEndpoint()
    monkeypatch.setattr(
        oauth,
        "make_http_client",
        lambda timeout: httpx.Client(transport=httpx.MockTransport(endpoint)),
    )
    configure_oauth(monkeypatch)
    return endpoint


def test_the_agent_asks_for_its_read_scopes_only(token_endpoint, monkeypatch):
    async def answer(settings, token, tool, arguments):
        return "ok", TICKET, {}, None

    monkeypatch.setattr(mcp_client, "_invoke", answer)

    mcp_client.call_tool("get_incident", {"reference": "TRF-000012"})

    (form,) = token_endpoint.forms
    assert form == {
        "grant_type": "client_credentials",
        "client_id": "trackflow-agent",
        "client_secret": "s3cret",
        "audience": "https://mcp.trackflow.test/mcp",
        "scope": "incidents:read inventory:read",
    }


def test_the_token_is_reused_until_it_nears_expiry(token_endpoint, monkeypatch):
    tokens: list[str] = []

    async def answer(settings, token, tool, arguments):
        tokens.append(token)
        return "ok", TICKET, {}, None

    monkeypatch.setattr(mcp_client, "_invoke", answer)

    for _ in range(3):
        mcp_client.call_tool("get_incident", {"reference": "TRF-000012"})
    assert tokens == ["token-1"] * 3

    token_endpoint.expires_in = 30  # inside the 60 s margin: renewed on every call
    oauth.reset_credentials()
    mcp_client.call_tool("get_incident", {"reference": "TRF-000012"})
    mcp_client.call_tool("get_incident", {"reference": "TRF-000012"})
    assert tokens[-2:] == ["token-2", "token-3"]


def _status_error(status: int) -> BaseExceptionGroup:
    request = httpx.Request("POST", "http://mcp.test/mcp")
    error = httpx.HTTPStatusError(
        "refused", request=request, response=httpx.Response(status, request=request)
    )
    return BaseExceptionGroup("unhandled errors in a TaskGroup", [error])


def test_a_refused_token_is_replaced_once(token_endpoint, monkeypatch):
    tokens: list[str] = []

    async def answer(settings, token, tool, arguments):
        tokens.append(token)
        if token == "token-1":
            raise _status_error(401)
        return "ok", TICKET, {}, None

    monkeypatch.setattr(mcp_client, "_invoke", answer)

    call = mcp_client.call_tool("get_incident", {"reference": "TRF-000012"})

    assert call.outcome == "ok"
    assert tokens == ["token-1", "token-2"]


def test_a_second_refusal_is_a_failure_not_a_loop(token_endpoint, monkeypatch):
    async def refuse(settings, token, tool, arguments):
        raise _status_error(401)

    monkeypatch.setattr(mcp_client, "_invoke", refuse)

    call = mcp_client.call_tool("get_incident", {"reference": "TRF-000012"})

    assert (call.outcome, call.failure, call.http_status) == ("failed", "mcp_unauthorized", 401)
    assert len(token_endpoint.forms) == 2


@pytest.mark.parametrize(
    ("raised", "label", "status"),
    [
        (_status_error(403), "mcp_forbidden", 403),
        (_status_error(502), "mcp_http_502", 502),
        (BaseExceptionGroup("g", [httpx.ConnectError("refused")]), "mcp_unreachable", None),
        (BaseExceptionGroup("g", [httpx.ReadTimeout("slow")]), "mcp_timeout", None),
        (TimeoutError(), "mcp_timeout", None),
        (
            BaseExceptionGroup("g", [McpError(ErrorData(code=-32600, message="bad"))]),
            "mcp_protocol_error",
            None,
        ),
    ],
)
def test_transport_failures_are_labelled(token_endpoint, monkeypatch, raised, label, status):
    async def fail(settings, token, tool, arguments):
        raise raised

    monkeypatch.setattr(mcp_client, "_invoke", fail)

    call = mcp_client.call_tool("search_incidents", {"status": "open"})

    assert (call.outcome, call.failure, call.http_status) == ("failed", label, status)


def test_a_programming_error_is_not_hidden(token_endpoint, monkeypatch):
    async def bug(settings, token, tool, arguments):
        raise TypeError("a bug, not a service failure")

    monkeypatch.setattr(mcp_client, "_invoke", bug)

    with pytest.raises(TypeError):
        mcp_client.call_tool("get_incident", {"reference": "TRF-000012"})


@pytest.mark.parametrize(
    ("status", "label"), [(401, "oauth_rejected"), (403, "oauth_rejected"), (500, "oauth_http_500")]
)
def test_a_refused_client_is_reported_without_calling_the_server(
    token_endpoint, monkeypatch, status, label
):
    token_endpoint.status = status
    monkeypatch.setattr(
        mcp_client, "_invoke", lambda *a: pytest.fail("no MCP call without a token")
    )

    call = mcp_client.call_tool("get_incident", {"reference": "TRF-000012"})

    assert (call.outcome, call.failure) == ("failed", label)


def test_an_unreachable_token_endpoint_is_reported(monkeypatch):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(
        oauth, "make_http_client", lambda timeout: httpx.Client(transport=httpx.MockTransport(down))
    )
    configure_oauth(monkeypatch)

    call = mcp_client.call_tool("get_incident", {"reference": "TRF-000012"})

    assert (call.outcome, call.failure) == ("failed", "oauth_unreachable")


def test_a_jwt_without_expires_in_uses_its_exp():
    now = time.time()
    token = jwt.encode({"exp": int(now) + 3600}, "k", algorithm="HS256")
    assert oauth._expiry(token, None) == pytest.approx(now + 3600, abs=2)
    assert oauth._expiry("opaque", None) == pytest.approx(
        now + oauth.FALLBACK_LIFETIME_SECONDS, abs=2
    )


def test_the_timeout_setting_is_a_number_of_at_most_ten_seconds(monkeypatch):
    for bad in ("0", "11", "soon"):
        monkeypatch.setenv("AGENT_TOOL_TIMEOUT_SECONDS", bad)
        with pytest.raises(ValidationError):
            agent_config.AgentSettings()
    monkeypatch.setenv("AGENT_TOOL_TIMEOUT_SECONDS", "7.5")
    assert agent_config.AgentSettings().agent_tool_timeout_seconds == 7.5


def test_the_token_endpoint_client_has_a_numeric_timeout_and_no_redirects():
    with REAL_MAKE_HTTP_CLIENT(3.0) as client:
        assert client.timeout.read == 3.0
        assert client.follow_redirects is False


# --- One path to live data ------------------------------------------------------------


def test_the_direct_tools_are_gone():
    tools = AGENT_DIR / "tools"
    for removed in ("incidents.py", "inventory.py", "http.py", "auth.py"):
        assert not (tools / removed).exists(), removed


def test_the_agent_has_no_path_to_the_services_but_the_mcp_server():
    forbidden_text = (
        "/api/incidents",
        "/inventory/",
        "/auth/login",
        "INCIDENTS_API_URL",
        "INVENTORY_API_URL",
    )
    for path in AGENT_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # The strings the code uses - docstrings are prose and may name the routes.
        docstrings = {
            id(owner.body[0].value)
            for owner in ast.walk(tree)
            if isinstance(owner, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and owner.body
            and isinstance(owner.body[0], ast.Expr)
            and isinstance(owner.body[0].value, ast.Constant)
        }
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]
        code_only = "\n".join(literals)
        for marker in forbidden_text:
            assert marker not in code_only, (path.name, marker)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert "mcps" not in imported, (
            f"{path.name}: the agent is a client; it must not import the server"
        )
        if "httpx" in imported:
            # Only the OAuth token request and the MCP client's error handling use httpx.
            assert path.name in {"oauth.py", "mcp_client.py"}, path.name


def test_the_lookup_nodes_only_use_the_mcp_lookups():
    from services.agent import nodes

    tree = ast.parse(Path(nodes.__file__).read_text(encoding="utf-8"))
    for function in (
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("lookup_")
    ):
        called = {
            f"{node.func.value.id}.{node.func.attr}"
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        }
        assert {c for c in called if c.startswith("mcp_lookups.")}, function.name


# --- Passages for generation -------------------------------------------------------


def ticket_result(outcome: str = "ok", *, reference: str | None = "12") -> TicketLookupResult:
    request = (
        TicketLookupInput(reference=reference) if reference else TicketLookupInput(status="open")
    )
    found = outcome == "ok"
    return TicketLookupResult.model_validate(
        {
            "request": request,
            "outcome": outcome,
            "endpoint": "MCP get_incident(reference=TRF-000012)",
            "reference": "TRF-000012" if reference else None,
            "tickets": [TICKET] if found else [],
            "total": 1 if found else 0,
            "duration_ms": 3.0,
        }
    )


def test_a_found_ticket_becomes_a_passage_with_its_live_fields():
    (passage,) = passages.ticket_passages(ticket_result())

    assert passage["source_document"] == "incident-manager"
    assert passage["section"] == "ticket TRF-000012"
    assert "Status: in progress" in passage["text"]
    assert "Branch: Central" in passage["text"]


def test_a_search_becomes_one_passage_with_its_count():
    (passage,) = passages.ticket_passages(ticket_result(reference=None))
    assert passage["section"] == "search status open"
    assert "1 matching ticket(s)" in passage["text"]


def test_a_failed_lookup_never_becomes_a_passage():
    assert passages.ticket_passages(ticket_result("not_found")) == []
    assert passages.ticket_passages(ticket_result("unavailable")) == []


def test_found_products_become_stock_passages():
    result = StockLookupResult.model_validate(
        {
            "request": StockLookupInput(product="shoes"),
            "outcome": "ok",
            "endpoint": "MCP query_inventory(operation=list_products, name=shoes)",
            "products": [SHOES],
            "total": 1,
            "duration_ms": 4.0,
        }
    )

    (passage,) = passages.stock_passages(result)

    assert passage["source_document"] == "inventory-manager"
    assert passage["section"] == "stock SHOE-BLK-42 (Monterrey)"
    assert "Current stock: 45 units" in passage["text"]
