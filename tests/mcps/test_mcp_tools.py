"""The tools against the real Incidents Manager (and a fake inventory), and contract drift."""

import ast
from pathlib import Path

import httpx
import pytest
from trackflow_shared.incidents import IncidentStatus

from mcps.trackflow_tools import contracts
from mcps.trackflow_tools.upstream import IncidentsApi, Upstream
from services.incidents import service as incident_service
from services.incidents.models import (
    DESCRIPTION_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    IncidentBase,
    IncidentCreate,
    IncidentCreatedResponse,
    IncidentResponse,
)
from services.inventory.models import Warehouse as ApiWarehouse
from services.inventory.schemas import OrderHistoryItem, ProductAvailability, ProductResponse
from tests.conftest import VALID_INCIDENT
from tests.mcps.conftest import (
    ALL,
    PRODUCTS,
    McpHarness,
    error_of,
    is_uuid,
    issue_token,
    structured,
)

ROOT = Path(__file__).resolve().parents[2]
EVERYTHING = issue_token(ALL, client_id="ops-tester")


def seed(reference: str, **overrides):
    return incident_service.create_incident(
        IncidentCreate(**{**VALID_INCIDENT, **overrides}), source_incident_id=reference
    )


# --- Incidents ---------------------------------------------------------------------


def test_create_update_and_check_an_incident_through_the_lifecycle_endpoint(harness):
    async def scenario(h: McpHarness):
        created = structured(await h.call(EVERYTHING, "create_incident", VALID_INCIDENT))
        moved = structured(
            await h.call(
                EVERYTHING,
                "update_incident_status",
                {"incident_id": created["id"], "status": "in_progress"},
            )
        )
        checked = structured(
            await h.call(EVERYTHING, "get_incident", {"incident_id": created["id"]})
        )
        return created, moved, checked

    created, moved, checked = harness.run(scenario)

    assert is_uuid(created["id"])
    assert created["status"] == "open"
    assert created["title"] == VALID_INCIDENT["title"]
    assert moved["status"] == checked["status"] == "in_progress"
    assert checked["category"] == "lost_parcel"
    update = next(r for r in harness.sink.records if r["tool"] == "update_incident_status")
    # The status change went through the Incidents Manager's lifecycle endpoint.
    assert update["upstream"] == [
        {"method": "PATCH", "path": f"/api/incidents/{created['id']}/status", "status": 200}
    ]


def test_a_move_the_lifecycle_forbids_is_refused_with_the_services_reason(harness):
    async def scenario(h: McpHarness):
        created = structured(await h.call(EVERYTHING, "create_incident", VALID_INCIDENT))
        return await h.call(
            EVERYTHING,
            "update_incident_status",
            {"incident_id": created["id"], "status": "resolved"},
        )

    payload = error_of(harness.run(scenario))

    assert payload["error"] == "invalid_status_transition"
    assert payload["field"] == "status"
    assert "in_progress" in payload["message"]  # the Incidents Manager names the allowed moves


def test_updating_an_unknown_incident_is_not_found(harness):
    payload = error_of(
        harness.call_sync(
            EVERYTHING,
            "update_incident_status",
            {"incident_id": "no-such-id", "status": "in_progress"},
        )
    )
    assert payload["error"] == "incident_not_found"
    assert "no-such-id" in payload["message"]


@pytest.mark.parametrize("written", ["482", "TRF-482", "trf 000482", "TRF-000482"])
def test_get_incident_by_helpdesk_reference_in_any_form(harness, written):
    seed("TRF-000482", title="Ticket four eight two")

    incident = structured(harness.call_sync(EVERYTHING, "get_incident", {"reference": written}))

    assert incident["reference"] == "TRF-000482"
    assert incident["title"] == "Ticket four eight two"


def test_an_unknown_reference_is_not_found(harness):
    payload = error_of(harness.call_sync(EVERYTHING, "get_incident", {"reference": "999999"}))
    assert payload["error"] == "incident_not_found"


@pytest.mark.parametrize(
    "arguments",
    [{}, {"incident_id": "a", "reference": "1"}, {"reference": "ticket twelve"}],
    ids=["neither", "both", "not-a-reference"],
)
def test_get_incident_needs_exactly_one_valid_identifier(harness, arguments):
    assert (
        error_of(harness.call_sync(EVERYTHING, "get_incident", arguments))["error"]
        == "invalid_arguments"
    )


def test_search_filters_and_counts(harness):
    for number in range(3):
        seed(f"TRF-00010{number}", category="lost_parcel", branch="zaragoza_warehouse")
    seed("TRF-000200", category="carrier_issue")

    result = structured(
        harness.call_sync(
            EVERYTHING,
            "search_incidents",
            {"category": "lost_parcel", "branch": "zaragoza_warehouse", "limit": 2},
        )
    )

    assert result["total"] == 3
    assert len(result["incidents"]) == 2
    assert {i["category"] for i in result["incidents"]} == {"lost_parcel"}


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({**VALID_INCIDENT, "title": ""}, "title"),
        ({**VALID_INCIDENT, "category": "meteor_strike"}, "category"),
        ({**VALID_INCIDENT, "status": "resolved"}, "status"),
        ({key: value for key, value in VALID_INCIDENT.items() if key != "branch"}, "branch"),
    ],
    ids=["empty-title", "unknown-category", "status-not-settable", "missing-branch"],
)
def test_invalid_arguments_name_the_field(harness, arguments, field):
    payload = error_of(harness.call_sync(EVERYTHING, "create_incident", arguments))

    assert payload["error"] == "invalid_arguments"
    assert field in {problem["field"] for problem in payload["problems"]}


def test_a_refusal_by_the_service_comes_back_as_upstream_rejected(harness):
    payload = error_of(
        harness.call_sync(EVERYTHING, "create_incident", {**VALID_INCIDENT, "title": "   "})
    )
    assert payload["error"] == "upstream_rejected"
    assert payload["field"] == "title"


@pytest.mark.parametrize(
    ("failure", "label"),
    [(httpx.ConnectError("refused"), "unreachable"), (httpx.ReadTimeout("slow"), "timeout")],
)
def test_an_unavailable_incident_manager_is_reported_not_hidden(
    settings, authorization_server, jwks, inventory_api, failure, label
):
    def broken(request: httpx.Request) -> httpx.Response:
        raise failure

    upstream = Upstream(
        incidents=IncidentsApi(
            "http://incidents.test",
            timeout=4,
            client=httpx.Client(transport=httpx.MockTransport(broken)),
        ),
        inventory=inventory_api,
    )
    from mcps.trackflow_tools.app import build_app
    from mcps.trackflow_tools.audit import MemorySink

    app = build_app(
        settings,
        authorization_server=authorization_server,
        jwks=jwks,
        upstream=upstream,
        sink=MemorySink(),
    )
    payload = error_of(
        McpHarness(app, MemorySink(), jwks).call_sync(
            EVERYTHING, "get_incident", {"reference": "1"}
        )
    )

    assert payload["error"] == "upstream_unavailable"
    assert payload["failure"] == label


# --- Inventory -----------------------------------------------------------------------

READER = issue_token(["inventory:read"], client_id="stock-reader")


def query(harness: McpHarness, **arguments):
    return harness.call_sync(READER, "query_inventory", arguments)


def test_list_products_with_their_stock(harness):
    result = structured(query(harness, operation="list_products"))
    assert result["total"] == 3
    assert {p["sku"]: p["current_stock"] for p in result["products"]} == {
        "SHOE-BLK-42": 45,
        "PERF-FLR-50": 120,
        "LAPTOP-DELL-14": 8,
    }


def test_list_products_by_exact_sku_uses_the_apis_filter(harness, fake_inventory):
    result = structured(query(harness, operation="list_products", sku="LAPTOP-DELL-14"))

    assert [p["name"] for p in result["products"]] == ["Dell laptop 14"]
    assert fake_inventory.data_requests()[-1].url.params["sku"] == "LAPTOP-DELL-14"


@pytest.mark.parametrize(
    ("name", "found"),
    [
        ("black running shoes", ["Black running shoes"]),
        ("dell laptops", ["Dell laptop 14"]),
        ("perfume", ["Floral perfume 50ml"]),
        ("purple umbrella", []),
    ],
)
def test_list_products_by_name_words(harness, name, found):
    result = structured(query(harness, operation="list_products", name=name))
    assert [p["name"] for p in result["products"]] == found
    assert result["total"] == len(found)


def test_get_product_returns_its_stock(harness):
    result = structured(query(harness, operation="get_product", product_id=PRODUCTS[2]["id"]))
    assert result["product"] == {"id": PRODUCTS[2]["id"], "current_stock": 8}


def test_an_unknown_product_is_not_found(harness):
    payload = error_of(
        query(harness, operation="get_product", product_id="00000000-0000-4000-8000-000000000000")
    )
    assert payload["error"] == "product_not_found"


def test_list_orders_leaves_out_who_recorded_them(harness):
    result = structured(query(harness, operation="list_orders", product_id=PRODUCTS[2]["id"]))

    assert [o["direction"] for o in result["orders"]] == ["inbound", "outbound"]
    assert all("user_uuid" not in order for order in result["orders"])


@pytest.mark.parametrize(
    "arguments",
    [
        {"operation": "get_product"},
        {"operation": "list_products", "product_id": PRODUCTS[0]["id"]},
        {"operation": "get_product", "product_id": PRODUCTS[0]["id"], "sku": "X"},
        {"operation": "describe_products"},
    ],
    ids=["missing-id", "unused-id", "unused-sku", "unknown-operation"],
)
def test_inventory_arguments_are_checked_per_operation(harness, arguments):
    assert error_of(query(harness, **arguments))["error"] == "invalid_arguments"


def test_the_service_account_signs_in_once_and_again_after_a_401(harness, fake_inventory):
    def logins() -> int:
        return sum(1 for r in fake_inventory.requests if r.url.path == "/auth/login")

    query(harness, operation="list_products")
    query(harness, operation="list_products")
    assert logins() == 1

    fake_inventory.refuse_next_token = True
    result = structured(query(harness, operation="list_products"))

    assert result["total"] == 3
    assert logins() == 2


def test_a_refused_service_account_is_upstream_auth_failed(harness, fake_inventory):
    fake_inventory.reject_logins = True
    assert error_of(query(harness, operation="list_products"))["error"] == "upstream_auth_failed"


def test_a_server_without_an_inventory_account_says_so(
    settings, authorization_server, jwks, client
):
    from mcps.trackflow_tools.app import build_app
    from mcps.trackflow_tools.audit import MemorySink

    app = build_app(
        settings,
        authorization_server=authorization_server,
        jwks=jwks,
        upstream=Upstream.from_settings(settings),
        sink=MemorySink(),
    )
    payload = error_of(
        McpHarness(app, MemorySink(), jwks).call_sync(
            READER, "query_inventory", {"operation": "list_products"}
        )
    )

    assert payload["error"] == "upstream_auth_failed"
    assert payload["failure"] == "not_configured"


def test_an_unreachable_inventory_is_upstream_unavailable(harness, fake_inventory):
    fake_inventory.down = True
    assert error_of(query(harness, operation="list_products"))["error"] == "upstream_unavailable"


# --- The contracts match the APIs ------------------------------------------------------


def _fields(model) -> set[str]:
    return set(model.model_fields)


@pytest.mark.parametrize(
    ("ours", "theirs", "left_out"),
    [
        (contracts.Incident, IncidentResponse, set()),
        (contracts.CreatedIncident, IncidentCreatedResponse, set()),
        (contracts.Product, ProductResponse, set()),
        (contracts.ProductAvailability, ProductAvailability, set()),
        (contracts.StockMovement, OrderHistoryItem, {"user_uuid"}),
    ],
)
def test_output_fields_are_the_apis_own(ours, theirs, left_out):
    assert _fields(ours) == _fields(theirs) - left_out


def test_create_incident_takes_the_apis_fields_and_limits(harness):
    async def scenario(h: McpHarness):
        async with h.session(EVERYTHING) as session:
            return {tool.name: tool for tool in (await session.list_tools()).tools}

    tools = harness.run(scenario)
    create = tools["create_incident"].inputSchema["properties"]

    assert set(create) == _fields(IncidentBase)
    assert create["title"]["maxLength"] == TITLE_MAX_LENGTH == contracts.TITLE_MAX_LENGTH
    assert create["description"]["maxLength"] == DESCRIPTION_MAX_LENGTH
    assert set(tools["update_incident_status"].inputSchema["properties"]) == {
        "incident_id",
        "status",
    }
    assert tools["update_incident_status"].inputSchema["properties"]["status"]["enum"] == [
        status.value for status in IncidentStatus
    ]
    search = set(tools["search_incidents"].inputSchema["properties"])
    assert search - {"limit"} == {"status", "category", "origin", "branch"}


def test_warehouses_match_the_inventory():
    assert [w.value for w in contracts.Warehouse] == [w.value for w in ApiWarehouse]


def test_the_mcp_server_never_imports_the_backend():
    for path in (ROOT / "mcps").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for name in names:
                assert name.split(".")[0] not in {"services", "data", "scripts"}, (path, name)
