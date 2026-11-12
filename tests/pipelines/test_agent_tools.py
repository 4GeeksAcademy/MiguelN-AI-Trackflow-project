"""The agent's tools: typed contracts, live reads, timeouts and failure results.

Nothing here reaches a real service. HTTP goes through `httpx.MockTransport`,
through the real incident router in-process (`TestClient`), or to a socket on
127.0.0.1 that this test opens itself.
"""

import ast
import json
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
from jose import jwt
from pydantic import ValidationError

from services.agent import config as agent_config
from services.agent.tools import auth, incidents, inventory, passages
from services.agent.tools import http as agent_http
from services.agent.tools.contracts import (
    MAX_TICKETS,
    ProductStock,
    StockLookupInput,
    TicketLookupInput,
    TicketRecord,
)
from services.incidents import service as incident_service
from services.incidents.models import IncidentCreate, IncidentResponse
from services.inventory.schemas import ProductResponse
from tests.conftest import VALID_INCIDENT

# Captured at import, before the autouse guard replaces it.
REAL_MAKE_CLIENT = agent_http.make_client
TOOLS_DIR = Path(incidents.__file__).parent

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
LAPTOP = {
    "id": "c030461b-a5f8-527f-a05b-8c67186118a5",
    "name": "Dell Laptop 15 inch",
    "sku": "LAPTOP-DELL-15",
    "warehouse": "Zaragoza",
    "current_stock": 8,
}


def settings_env(monkeypatch, **values):
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    agent_config.get_agent_settings.cache_clear()


class Recorder:
    """A MockTransport handler that answers from a function and remembers every request."""

    def __init__(self, respond):
        self.respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self), timeout=1.0)


def answer(status=200, body=None, *, raw=None):
    def respond(request):
        if raw is not None:
            return httpx.Response(status, content=raw)
        return httpx.Response(status, json=body)

    return respond


def raising(error_class):
    def respond(request):
        raise error_class("simulated transport failure", request=request)

    return respond


# --- Contracts ---------------------------------------------------------------------


def test_the_ticket_record_mirrors_the_incident_api_response():
    assert set(TicketRecord.model_fields) == set(IncidentResponse.model_fields)


def test_the_stock_record_mirrors_the_inventory_api_response():
    assert set(ProductStock.model_fields) == set(ProductResponse.model_fields)


def test_a_ticket_lookup_needs_a_reference_or_a_filter():
    with pytest.raises(ValidationError, match="reference or at least one filter"):
        TicketLookupInput()

    assert TicketLookupInput(reference="482").reference == "482"
    assert TicketLookupInput(status="open", category="lost_parcel").filters() == {
        "status": "open",
        "category": "lost_parcel",
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {"status": "lost"},
        {"reference": "12", "priority": "high"},
        {"reference": ""},
        {"reference": "x" * 65},
    ],
)
def test_a_ticket_lookup_rejects_what_the_contract_does_not_define(arguments):
    with pytest.raises(ValidationError):
        TicketLookupInput.model_validate(arguments)


def test_a_stock_lookup_needs_a_product_and_knows_the_warehouses():
    with pytest.raises(ValidationError):
        StockLookupInput.model_validate({})
    with pytest.raises(ValidationError):
        StockLookupInput(product="shoes", warehouse="Los Angeles")
    assert StockLookupInput(product=" shoes ", warehouse="Zaragoza").product == "shoes"


# --- Ticket lookup -----------------------------------------------------------------


def test_a_helpdesk_number_is_looked_up_by_its_canonical_reference():
    service = Recorder(answer(body=[TICKET]))

    result = incidents.lookup_ticket(TicketLookupInput(reference="12"), client=service.client())

    (request,) = service.requests
    assert request.method == "GET"
    assert str(request.url) == "http://incidents.invalid/api/incidents?reference=TRF-000012"
    assert "authorization" not in request.headers, "the incident routes need no credentials"
    assert result.outcome == "ok"
    assert result.reference == "TRF-000012"
    assert result.endpoint == "GET /api/incidents?reference=TRF-000012"
    assert [t.status.value for t in result.tickets] == ["in_progress"]
    assert result.total == 1
    assert result.http_status == 200
    assert result.duration_ms >= 0


def test_a_reference_the_incident_manager_does_not_have_is_not_found():
    service = Recorder(answer(body=[]))

    result = incidents.lookup_ticket(TicketLookupInput(reference="482"), client=service.client())

    assert result.outcome == "not_found"
    assert result.reference == "TRF-000482"
    assert result.tickets == []


def test_any_other_reference_is_read_as_an_incident_id():
    found = Recorder(answer(body={**TICKET, "reference": None}))
    missing = Recorder(answer(404, {"detail": {"field": "id", "message": "No incident"}}))

    ok = incidents.lookup_ticket(TicketLookupInput(reference=TICKET["id"]), client=found.client())
    gone = incidents.lookup_ticket(
        TicketLookupInput(reference="no such id"), client=missing.client()
    )

    assert found.requests[0].url.path == f"/api/incidents/{TICKET['id']}"
    assert ok.outcome == "ok" and ok.total == 1
    assert missing.requests[0].url.raw_path == b"/api/incidents/no%20such%20id"
    assert gone.outcome == "not_found"
    assert gone.http_status == 404


def test_a_search_returns_the_newest_tickets_and_the_full_count():
    many = [{**TICKET, "id": f"id-{n}", "reference": f"TRF-{n:06d}"} for n in range(1, 13)]
    service = Recorder(answer(body=many))

    result = incidents.lookup_ticket(
        TicketLookupInput(status="open", category="lost_parcel"), client=service.client()
    )

    assert dict(service.requests[0].url.params) == {"status": "open", "category": "lost_parcel"}
    assert result.outcome == "ok"
    assert result.total == 12
    assert len(result.tickets) == MAX_TICKETS
    assert result.reference is None


def test_a_search_with_no_match_is_a_real_answer_not_a_failure():
    result = incidents.lookup_ticket(
        TicketLookupInput(status="in_progress"), client=Recorder(answer(body=[])).client()
    )

    assert result.outcome == "ok"
    assert result.total == 0


@pytest.mark.parametrize(
    ("respond", "failure", "status"),
    [
        (answer(500, {"detail": "boom"}), "http_500", 500),
        (answer(503, raw=b"Service Unavailable"), "http_503", 503),
        (answer(401, {"detail": "Not authenticated"}), "http_401", 401),
        (answer(404, {"detail": "Not Found"}), "http_404", 404),
        (answer(200, raw=b"<html>proxy page</html>"), "invalid_response", 200),
        (answer(200, {"unexpected": "shape"}), "invalid_response", 200),
        (answer(200, [{"id": "only an id"}]), "invalid_response", 200),
        (raising(httpx.ReadTimeout), "timeout", None),
        (raising(httpx.ConnectTimeout), "timeout", None),
        (raising(httpx.ConnectError), "unreachable", None),
        (raising(httpx.RemoteProtocolError), "unreachable", None),
    ],
)
def test_every_service_failure_becomes_an_unavailable_result(respond, failure, status):
    result = incidents.lookup_ticket(
        TicketLookupInput(reference="TRF-000012"), client=Recorder(respond).client()
    )

    assert result.outcome == "unavailable"
    assert result.failure == failure
    assert result.http_status == status
    assert result.tickets == []


def test_the_service_url_comes_from_configuration(monkeypatch):
    settings_env(monkeypatch, INCIDENTS_API_URL="https://incidents.example.test/")
    service = Recorder(answer(body=[TICKET]))

    incidents.lookup_ticket(TicketLookupInput(reference="12"), client=service.client())

    assert str(service.requests[0].url).startswith("https://incidents.example.test/api/incidents?")


# --- Timeouts against a real socket ------------------------------------------------


@pytest.fixture
def silent_server():
    """A TCP server on 127.0.0.1 that accepts connections and never answers."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    held: list[socket.socket] = []
    stop = threading.Event()

    def accept():
        listener.settimeout(0.1)
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except OSError:
                continue
            held.append(connection)

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    stop.set()
    thread.join(timeout=1)
    for connection in held:
        connection.close()
    listener.close()


def test_a_service_that_never_answers_ends_in_a_timeout_not_a_hang(monkeypatch, silent_server):
    monkeypatch.setattr(agent_http, "make_client", REAL_MAKE_CLIENT)
    settings_env(monkeypatch, INCIDENTS_API_URL=silent_server, AGENT_TOOL_TIMEOUT_SECONDS="0.3")

    started = time.perf_counter()
    result = incidents.lookup_ticket(TicketLookupInput(reference="12"))
    elapsed = time.perf_counter() - started

    assert result.outcome == "unavailable"
    assert result.failure == "timeout"
    assert elapsed < 1.5, f"the lookup took {elapsed:.2f}s with a 0.3s timeout"


def test_a_closed_port_is_unreachable(monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    monkeypatch.setattr(agent_http, "make_client", REAL_MAKE_CLIENT)
    settings_env(monkeypatch, INCIDENTS_API_URL=f"http://127.0.0.1:{closed_port}")

    result = incidents.lookup_ticket(TicketLookupInput(reference="12"))

    assert result.outcome == "unavailable"
    assert result.failure == "unreachable"


def test_the_client_uses_the_configured_numeric_timeout_without_redirects(monkeypatch):
    settings_env(monkeypatch, AGENT_TOOL_TIMEOUT_SECONDS="3.5")

    with REAL_MAKE_CLIENT() as client:
        assert client.timeout == httpx.Timeout(3.5)
        assert client.follow_redirects is False


@pytest.mark.parametrize("timeout", ["0", "-1", "5.5", "four"])
def test_the_timeout_setting_must_be_a_number_of_at_most_five_seconds(monkeypatch, timeout):
    settings_env(monkeypatch, AGENT_TOOL_TIMEOUT_SECONDS=timeout)

    with pytest.raises(ValidationError):
        agent_config.get_agent_settings()


# --- Read-only by construction -----------------------------------------------------

_WRITE_VERBS = {"post", "put", "patch", "delete", "request", "stream", "send"}


def _called_attributes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


@pytest.mark.parametrize("module", ["incidents.py", "inventory.py", "http.py", "passages.py"])
def test_the_lookup_modules_never_call_a_write_method(module):
    assert not _called_attributes(TOOLS_DIR / module) & _WRITE_VERBS


def test_the_only_post_in_the_tools_is_the_sign_in():
    posts = [
        node
        for node in ast.walk(ast.parse((TOOLS_DIR / "auth.py").read_text()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _WRITE_VERBS
    ]
    assert len(posts) == 1
    assert posts[0].func.attr == "post"
    assert "LOGIN_PATH" in ast.unparse(posts[0].args[0])


def test_every_request_a_lookup_makes_is_a_get(monkeypatch):
    settings_env(
        monkeypatch, AGENT_INVENTORY_EMAIL="agent@example.com", AGENT_INVENTORY_PASSWORD="pw"
    )
    service = InventoryService(products=[SHOES])
    tickets = Recorder(answer(body=[TICKET]))

    incidents.lookup_ticket(TicketLookupInput(reference="12"), client=tickets.client())
    incidents.lookup_ticket(TicketLookupInput(status="open"), client=tickets.client())
    inventory.lookup_stock(StockLookupInput(product="shoes"), client=service.client())

    assert {request.method for request in tickets.requests} == {"GET"}
    product_calls = [r for r in service.requests if r.url.path != auth.LOGIN_PATH]
    assert {request.method for request in product_calls} == {"GET"}


# --- Against the real incident router ----------------------------------------------


def test_the_tool_reads_the_live_incident_manager(client, monkeypatch):
    settings_env(monkeypatch, INCIDENTS_API_URL="http://testserver")
    seeded = incident_service.create_incident(
        IncidentCreate(**VALID_INCIDENT), source_incident_id="TRF-000012"
    )

    before = incidents.lookup_ticket(TicketLookupInput(reference="12"), client=client)
    client.patch(f"/api/incidents/{seeded.id}/status", json={"status": "in_progress"})
    after = incidents.lookup_ticket(TicketLookupInput(reference="TRF-12"), client=client)
    missing = incidents.lookup_ticket(TicketLookupInput(reference="482"), client=client)
    search = incidents.lookup_ticket(TicketLookupInput(status="in_progress"), client=client)

    assert before.outcome == "ok" and before.tickets[0].status.value == "open"
    assert after.tickets[0].status.value == "in_progress", "a status change shows at once"
    assert after.tickets[0].id == seeded.id
    assert missing.outcome == "not_found"
    assert [t.reference for t in search.tickets] == ["TRF-000012"]


# --- Stock lookup ------------------------------------------------------------------


def _token(lifetime: float = 3600) -> str:
    return jwt.encode({"sub": "agent", "exp": int(time.time() + lifetime)}, "x" * 32)


class InventoryService(Recorder):
    """Sign-in plus the products route, with the products and statuses a test needs."""

    def __init__(self, products, *, product_statuses=(), login_status=200):
        self.products = products
        self.product_statuses = list(product_statuses)
        self.login_status = login_status
        self.logins = 0
        super().__init__(self._respond)

    def _respond(self, request):
        if request.url.path == auth.LOGIN_PATH:
            self.logins += 1
            assert json.loads(request.content) == {"email": "agent@example.com", "password": "pw"}
            if self.login_status != 200:
                return httpx.Response(self.login_status, json={"detail": "Invalid credentials"})
            return httpx.Response(200, json={"access_token": _token(), "token_type": "bearer"})
        assert request.url.path == inventory.PRODUCTS_PATH
        assert request.headers["authorization"].startswith("Bearer ")
        if self.product_statuses:
            status = self.product_statuses.pop(0)
            if status != 200:
                return httpx.Response(status, json={"detail": "Not authenticated"})
        rows = [
            row
            for row in self.products
            if request.url.params.get("sku") in (None, row["sku"])
            and request.url.params.get("warehouse") in (None, row["warehouse"])
        ]
        return httpx.Response(200, json=rows)


@pytest.fixture
def account(monkeypatch):
    settings_env(
        monkeypatch,
        INVENTORY_API_URL="http://inventory.invalid",
        AGENT_INVENTORY_EMAIL="agent@example.com",
        AGENT_INVENTORY_PASSWORD="pw",
    )


def test_without_an_account_the_stock_tool_makes_no_request():
    service = InventoryService(products=[SHOES])

    result = inventory.lookup_stock(StockLookupInput(product="shoes"), client=service.client())

    assert service.requests == []
    assert result.outcome == "unavailable"
    assert result.failure == "not_configured"


def test_a_product_named_in_words_is_matched_on_the_live_rows(account):
    service = InventoryService(products=[SHOES, LAPTOP])

    result = inventory.lookup_stock(
        StockLookupInput(product="the black running shoes", warehouse="Monterrey"),
        client=service.client(),
    )

    products_request = service.requests[-1]
    assert dict(products_request.url.params) == {"warehouse": "Monterrey"}
    assert result.endpoint == "GET /inventory/products?warehouse=Monterrey"
    assert result.outcome == "ok"
    assert [(p.sku, p.current_stock) for p in result.products] == [("SHOE-BLK-42", 45)]


def test_a_sku_is_filtered_by_the_inventory_manager_itself(account):
    service = InventoryService(products=[SHOES, LAPTOP])

    result = inventory.lookup_stock(
        StockLookupInput(product="laptop-dell-15"), client=service.client()
    )

    assert dict(service.requests[-1].url.params) == {"sku": "LAPTOP-DELL-15"}
    assert [p.sku for p in result.products] == ["LAPTOP-DELL-15"]


def test_a_product_the_inventory_manager_does_not_have_is_not_found(account):
    result = inventory.lookup_stock(
        StockLookupInput(product="red umbrella"),
        client=InventoryService(products=[SHOES, LAPTOP]).client(),
    )

    assert result.outcome == "not_found"
    assert result.products == []


def test_the_token_is_reused_until_the_service_refuses_it(account):
    service = InventoryService(products=[SHOES], product_statuses=[200, 200, 401, 200])

    for _ in range(3):
        result = inventory.lookup_stock(StockLookupInput(product="shoes"), client=service.client())
        assert result.outcome == "ok"

    assert service.logins == 2, "one sign-in, then one more after the 401"


def test_a_second_refusal_is_a_failure_not_a_loop(account):
    service = InventoryService(products=[SHOES], product_statuses=[401, 401])

    result = inventory.lookup_stock(StockLookupInput(product="shoes"), client=service.client())

    assert result.outcome == "unavailable"
    assert result.failure == "http_401"
    assert service.logins == 2


def test_a_rejected_sign_in_is_reported_as_such(account):
    service = InventoryService(products=[SHOES], login_status=401)

    result = inventory.lookup_stock(StockLookupInput(product="shoes"), client=service.client())

    assert result.outcome == "unavailable"
    assert result.failure == "login_rejected"
    assert [r.url.path for r in service.requests] == [auth.LOGIN_PATH]


def test_an_inventory_outage_becomes_an_unavailable_result(account):
    result = inventory.lookup_stock(
        StockLookupInput(product="shoes"), client=Recorder(raising(httpx.ReadTimeout)).client()
    )

    assert result.outcome == "unavailable"
    assert result.failure == "timeout"


def test_a_token_is_signed_in_again_shortly_before_it_expires():
    credentials = auth.InventoryCredentials("http://inventory.invalid", "a@b.com", "pw")
    tokens = iter([_token(lifetime=30), _token(lifetime=3600)])
    service = Recorder(lambda request: httpx.Response(200, json={"access_token": next(tokens)}))

    first = credentials.token(service.client())
    second = credentials.token(service.client())

    assert first != second, "a token with less than the margin left is replaced"
    assert credentials.token(service.client()) == second


@pytest.mark.parametrize(
    ("product", "row", "matches"),
    [
        ("black running shoes", SHOES, True),
        ("Black Running Shoe", SHOES, True),
        ("shoes size 42", SHOES, True),
        ("SHOE-BLK-42", SHOES, True),
        ("laptops", LAPTOP, True),
        ("el portátil dell", LAPTOP, False),
        ("dell laptop", LAPTOP, True),
        ("red shoes", SHOES, False),
        ("stock", SHOES, False),
    ],
)
def test_name_matching_needs_every_meaningful_word(product, row, matches):
    assert inventory.matches_name(product, ProductStock.model_validate(row)) is matches


def test_only_a_sku_shaped_request_is_sent_as_a_sku():
    assert inventory.sku_in("shoe-blk-42") == "SHOE-BLK-42"
    assert inventory.sku_in("black running shoes") is None
    assert inventory.sku_in("laptop") is None


# --- Passages for generation -------------------------------------------------------


def test_a_found_ticket_becomes_a_passage_with_its_live_fields():
    result = incidents.lookup_ticket(
        TicketLookupInput(reference="12"), client=Recorder(answer(body=[TICKET])).client()
    )

    (passage,) = passages.ticket_passages(result)

    assert passage["source_document"] == "incident-manager"
    assert passage["section"] == "ticket TRF-000012"
    assert "Status: in progress" in passage["text"]
    assert "Opened: 2026-10-20 04:21 UTC" in passage["text"]
    assert "Branch: Central" in passage["text"]


def test_a_search_becomes_one_passage_with_its_count():
    result = incidents.lookup_ticket(
        TicketLookupInput(status="open"), client=Recorder(answer(body=[TICKET])).client()
    )

    (passage,) = passages.ticket_passages(result)

    assert passage["section"] == "search status open"
    assert "1 matching ticket(s)" in passage["text"]


def test_a_failed_lookup_never_becomes_a_passage():
    gone = incidents.lookup_ticket(
        TicketLookupInput(reference="482"), client=Recorder(answer(body=[])).client()
    )
    down = incidents.lookup_ticket(
        TicketLookupInput(reference="12"), client=Recorder(raising(httpx.ConnectError)).client()
    )

    assert passages.ticket_passages(gone) == []
    assert passages.ticket_passages(down) == []


def test_found_products_become_stock_passages(account):
    result = inventory.lookup_stock(
        StockLookupInput(product="shoes"), client=InventoryService(products=[SHOES]).client()
    )

    (passage,) = passages.stock_passages(result)

    assert passage["source_document"] == "inventory-manager"
    assert passage["section"] == "stock SHOE-BLK-42 (Monterrey)"
    assert "Current stock: 45 units" in passage["text"]
