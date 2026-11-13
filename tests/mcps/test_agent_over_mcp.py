"""End to end: the agent's MCP client nodes -> the real MCP server -> the real Incidents Manager.

The MCP server runs under uvicorn on a free local port, so the agent connects
exactly as in production: Streamable HTTP, `langchain-mcp-adapters`, a bearer
token validated by MCP Auth. Only the token endpoint is a fake: it signs tokens
with the test key, for the scopes the agent asks for.
"""

import socket
import threading
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest
import uvicorn
from langgraph.checkpoint.memory import InMemorySaver

from data.pipelines import rag
from mcps.trackflow_tools.app import build_app
from mcps.trackflow_tools.audit import MemorySink
from services.agent import config as agent_config
from services.agent import planner
from services.agent.graph import build_agent_graph
from services.agent.runner import run_agent
from services.agent.state import (
    ANSWER_LOOKUP_FAILED,
    GENERATE_ANSWER,
    LOOKUP_STOCK,
    LOOKUP_TICKET,
    PLAN_SOURCES,
    RECEIVE_QUESTION,
    TICKET_UNAVAILABLE_ANSWER,
)
from services.agent.tools import mcp_client, mcp_lookups, oauth
from services.agent.tools.contracts import StockLookupInput, TicketLookupInput
from services.agent.tracing import TraceStore
from services.incidents import service as incident_service
from services.incidents.models import IncidentCreate, IncidentStatus
from tests.conftest import VALID_INCIDENT
from tests.mcps.conftest import AUDIENCE, OTHER_KEY, issue_token


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def mcp_server(settings, authorization_server, jwks, upstream) -> Iterator[tuple[str, MemorySink]]:
    """The MCP server on a real socket; yields its endpoint URL and its audit sink."""
    sink = MemorySink()
    app = build_app(
        settings, authorization_server=authorization_server, jwks=jwks, upstream=upstream, sink=sink
    )
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "the MCP server did not start"
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}/mcp", sink
    server.should_exit = True
    thread.join(timeout=10)


class TokenEndpoint:
    def __init__(self) -> None:
        self.forms: list[dict[str, str]] = []
        self.key = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = dict(parse_qsl(request.content.decode()))
        self.forms.append(form)
        extra: dict[str, Any] = {"key": self.key} if self.key is not None else {}
        token = issue_token(
            form.get("scope", "").split(),
            audience=form["audience"],
            client_id=form["client_id"],
            **extra,
        )
        return httpx.Response(200, json={"access_token": token, "expires_in": 86400})


@pytest.fixture
def agent(mcp_server, monkeypatch) -> tuple[TokenEndpoint, MemorySink]:
    """The agent configured as in production, pointed at the live MCP server."""
    url, sink = mcp_server
    endpoint = TokenEndpoint()
    monkeypatch.setattr(
        oauth,
        "make_http_client",
        lambda timeout: httpx.Client(transport=httpx.MockTransport(endpoint)),
    )
    # A real connection to the local server.
    monkeypatch.setattr(mcp_client, "http_client_factory", None)
    for name, value in {
        "AGENT_MCP_URL": url,
        "AGENT_OAUTH_TOKEN_URL": "https://trackflow-test.eu.auth0.com/oauth/token",
        "AGENT_OAUTH_CLIENT_ID": "trackflow-agent",
        "AGENT_OAUTH_CLIENT_SECRET": "s3cret",
        "AGENT_OAUTH_AUDIENCE": AUDIENCE,
    }.items():
        monkeypatch.setenv(name, value)
    agent_config.get_agent_settings.cache_clear()
    oauth.reset_credentials()
    return endpoint, sink


def seed(reference: str, **overrides: Any):
    return incident_service.create_incident(
        IncidentCreate(**{**VALID_INCIDENT, **overrides}), source_incident_id=reference
    )


def test_a_ticket_is_read_through_the_mcp_server(agent):
    _, sink = agent
    seeded = seed("TRF-000012", title="Wrong postcode on the label")
    incident_service.update_status(seeded.id, IncidentStatus.IN_PROGRESS)

    result = mcp_lookups.lookup_ticket(TicketLookupInput(reference="12"))

    assert result.outcome == "ok"
    assert result.tickets[0].title == "Wrong postcode on the label"
    assert result.tickets[0].status.value == "in_progress"
    assert result.endpoint == "MCP get_incident(reference=TRF-000012)"
    (call,) = [r for r in sink.records if r["event"] == "tool_call"]
    assert call["tool"] == "get_incident"
    assert call["client_id"] == "trackflow-agent"
    assert call["scopes"] == ["incidents:read", "inventory:read"]
    assert call["upstream"] == [{"method": "GET", "path": "/api/incidents", "status": 200}]


def test_the_agent_asks_for_read_scopes_and_reuses_its_token(agent):
    endpoint, _ = agent
    mcp_lookups.lookup_ticket(TicketLookupInput(reference="12"))
    mcp_lookups.lookup_ticket(TicketLookupInput(status="open"))

    (form,) = endpoint.forms
    assert form["scope"] == "incidents:read inventory:read"
    assert form["grant_type"] == "client_credentials"


def test_a_search_and_an_unknown_ticket(agent):
    for number in range(3):
        seed(f"TRF-00030{number}", category="carrier_issue")

    found = mcp_lookups.lookup_ticket(TicketLookupInput(category="carrier_issue"))
    missing = mcp_lookups.lookup_ticket(TicketLookupInput(reference="482"))

    assert (found.outcome, found.total) == ("ok", 3)
    assert missing.outcome == "not_found"


def test_stock_is_read_through_the_read_only_inventory_tool(agent):
    by_name = mcp_lookups.lookup_stock(
        StockLookupInput(product="dell laptop", warehouse="Zaragoza")
    )
    by_sku = mcp_lookups.lookup_stock(StockLookupInput(product="shoe-blk-42"))

    assert [(p.name, p.current_stock) for p in by_name.products] == [("Dell laptop 14", 8)]
    assert [(p.sku, p.current_stock) for p in by_sku.products] == [("SHOE-BLK-42", 45)]
    assert by_name.endpoint.startswith("MCP query_inventory(operation=list_products")


def test_without_the_scope_the_server_refuses_and_the_lookup_says_so(agent, monkeypatch):
    monkeypatch.setenv("AGENT_OAUTH_SCOPES", "inventory:read")
    agent_config.get_agent_settings.cache_clear()
    oauth.reset_credentials()

    result = mcp_lookups.lookup_ticket(TicketLookupInput(reference="12"))

    assert (result.outcome, result.failure) == ("unavailable", "insufficient_scope")


def test_a_token_the_server_cannot_verify_is_retried_once_then_reported(agent):
    endpoint, sink = agent
    endpoint.key = OTHER_KEY

    result = mcp_lookups.lookup_ticket(TicketLookupInput(reference="12"))

    assert (result.outcome, result.failure, result.http_status) == (
        "unavailable",
        "mcp_unauthorized",
        401,
    )
    assert len(endpoint.forms) == 2
    rejected = [r for r in sink.records if r["event"] == "auth_rejected"]
    assert [r["error"] for r in rejected] == ["invalid_token", "invalid_token"]


def test_a_down_mcp_server_is_unavailable_not_an_error(agent, monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    monkeypatch.setenv("AGENT_MCP_URL", f"http://127.0.0.1:{closed}/mcp")
    agent_config.get_agent_settings.cache_clear()

    result = mcp_lookups.lookup_ticket(TicketLookupInput(reference="12"))

    assert (result.outcome, result.failure) == ("unavailable", "mcp_unreachable")


# --- The whole graph, with the MCP client node in place -----------------------------


@pytest.fixture
def offline_models(monkeypatch):
    """The planner, retrieval and generation are fakes; the lookups are real MCP calls."""
    seen: dict[str, Any] = {}

    def choose_sources(question: str) -> dict[str, Any]:
        steps = []
        if "ticket" in question:
            steps.append(
                {
                    "source": "incident_manager",
                    "tool": "lookup_ticket",
                    "arguments": {"reference": "12"},
                }
            )
        if "stock" in question:
            steps.append(
                {
                    "source": "inventory_manager",
                    "tool": "lookup_stock",
                    "arguments": {"product": "black running shoes"},
                }
            )
        return {"decided_by": "model", "steps": steps, "ignored": []}

    def generate_answer(question: str, passages: list[dict[str, Any]]) -> str:
        seen["passages"] = passages
        return "generated from the live records"

    monkeypatch.setattr(planner, "choose_sources", choose_sources)
    monkeypatch.setattr(rag, "retrieve", lambda question: [])
    monkeypatch.setattr(rag, "generate_answer", generate_answer)
    monkeypatch.setattr(rag, "query", lambda question: pytest.fail("never rag.query()"))
    return seen


def test_the_graph_answers_from_mcp_with_the_same_routing(agent, offline_models, tmp_path):
    seeded = seed("TRF-000012", title="Wrong postcode on the label")
    incident_service.update_status(seeded.id, IncidentStatus.IN_PROGRESS)
    store = TraceStore(tmp_path / "runs.sqlite")

    result = run_agent(
        "What's the status of ticket 12 and the stock of black running shoes?",
        graph=build_agent_graph(InMemorySaver()),
        store=store,
    )

    trace = store.get_trace(result.run_id)
    assert result.outcome == "answered"
    assert trace.node_names() == [
        RECEIVE_QUESTION,
        PLAN_SOURCES,
        LOOKUP_TICKET,
        LOOKUP_STOCK,
        GENERATE_ANSWER,
    ]
    assert [(e.name, e.output["decision"]) for e in trace.edge_events()] == [
        ("route_question", "plan"),
        ("route_after_plan", "incident_manager"),
        ("route_after_ticket", "inventory_manager"),
        ("route_after_stock", "generate"),
    ]
    assert trace.sources() == ["incident_manager", "inventory_manager"]
    texts = " ".join(p["text"] for p in offline_models["passages"])
    assert "Status: in progress" in texts
    assert "Current stock: 45 units" in texts


def test_the_graph_falls_back_honestly_when_the_mcp_server_is_down(
    agent, offline_models, tmp_path, monkeypatch
):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    monkeypatch.setenv("AGENT_MCP_URL", f"http://127.0.0.1:{closed}/mcp")
    agent_config.get_agent_settings.cache_clear()
    store = TraceStore(tmp_path / "runs.sqlite")

    result = run_agent(
        "What's the status of ticket 12?", graph=build_agent_graph(InMemorySaver()), store=store
    )

    assert result.outcome == "no_information"
    assert result.answer == TICKET_UNAVAILABLE_ANSWER.format(reference="TRF-000012")
    nodes = store.get_trace(result.run_id).node_names()
    assert nodes[-2:] == [LOOKUP_TICKET, ANSWER_LOOKUP_FAILED]
    assert "generate_answer" not in nodes
