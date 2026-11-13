"""Least privilege per tool, and an inventory that is read-only by design."""

import httpx
import pytest

from mcps.trackflow_tools.permissions import (
    ALL_SCOPES,
    POLICIES,
    Access,
    Domain,
    PolicyViolation,
    Scope,
    ToolPolicy,
    inventory_write_attempt,
    is_inventory_write_tool_name,
    validate_policies,
)
from mcps.trackflow_tools.upstream import ReadOnlyInventoryApi, ReadOnlyViolation
from tests.mcps.conftest import ALL, error_of, issue_token, structured

READ_INCIDENT = {"reference": "1"}
CALLS = {
    "create_incident": {
        "title": "Parcel lost",
        "description": "Customer says the parcel never arrived.",
        "category": "lost_parcel",
        "origin": "customer",
        "branch": "central",
    },
    "update_incident_status": {"incident_id": "no-such-id", "status": "in_progress"},
    "get_incident": READ_INCIDENT,
    "search_incidents": {"limit": 1},
    "query_inventory": {"operation": "list_products"},
}


def test_every_tool_requires_exactly_its_own_scope():
    assert {tool: policy.scope_names for tool, policy in POLICIES.items()} == {
        "create_incident": ["incidents:create"],
        "update_incident_status": ["incidents:update"],
        "get_incident": ["incidents:read"],
        "search_incidents": ["incidents:read"],
        "query_inventory": ["inventory:read"],
    }
    assert tuple(ALL) == ALL_SCOPES
    assert not any("write" in scope for scope in ALL_SCOPES if scope.startswith("inventory"))


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_a_token_with_every_other_scope_is_refused(harness, tool):
    required = POLICIES[tool].scope_names
    others = [scope for scope in ALL if scope not in required]

    payload = error_of(harness.call_sync(issue_token(others), tool, CALLS[tool]))

    assert payload["error"] == "insufficient_scope"
    assert payload["required_scopes"] == required
    assert payload["granted_scopes"] == sorted(others)


def test_the_agents_read_scopes_cannot_create_or_update_incidents(harness):
    agent = issue_token(["incidents:read", "inventory:read"])

    async def scenario(h):
        return [
            await h.call(agent, tool, CALLS[tool])
            for tool in ("create_incident", "update_incident_status")
        ]

    refused = harness.run(scenario)

    assert [error_of(result)["error"] for result in refused] == ["insufficient_scope"] * 2
    # Nothing reached the Incidents Manager.
    calls = [r for r in harness.sink.records if r["event"] == "tool_call"]
    assert all(record["upstream"] == [] for record in calls)


# --- The read-only inventory, layer by layer ------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("query_inventory", {"operation": "record_inbound", "product_id": "x", "quantity": 5}),
        ("query_inventory", {"operation": "record_outbound"}),
        ("query_inventory", {"operation": "create_product", "name": "New", "sku": "NEW-1"}),
        ("query_inventory", {"operation": "Update_Stock"}),
        ("query_inventory", {"operation": "list_products", "quantity": 3}),
        ("create_product", {"name": "New", "sku": "NEW-1", "warehouse": "Zaragoza"}),
        ("record_inbound", {"product_id": "x", "quantity": 5}),
        ("recordOutboundOrder", {"product_id": "x", "quantity": 1}),
        ("update_stock", {}),
    ],
    ids=[
        "inbound-op",
        "outbound-op",
        "create-op",
        "update-op",
        "quantity-field",
        "create-tool",
        "inbound-tool",
        "camel-tool",
        "update-tool",
    ],
)
def test_every_inventory_write_attempt_is_explicitly_refused(
    harness, fake_inventory, tool, arguments
):
    payload = error_of(harness.call_sync(issue_token(ALL), tool, arguments))

    assert payload["error"] == "inventory_read_only"
    assert "read-only" in payload["message"]
    # Refused before anything reached the inventory, sign-in included.
    assert fake_inventory.requests == []


def test_a_token_claiming_an_inventory_write_scope_gains_nothing(harness, fake_inventory):
    token = issue_token([*ALL, "inventory:write", "inventory:admin"])

    payload = error_of(
        harness.call_sync(token, "query_inventory", {"operation": "record_inbound", "quantity": 1})
    )

    assert payload["error"] == "inventory_read_only"
    assert fake_inventory.requests == []


def test_the_write_attempt_is_refused_even_without_the_read_scope(harness):
    payload = error_of(
        harness.call_sync(
            issue_token(["incidents:read"]), "query_inventory", {"operation": "create_product"}
        )
    )
    assert payload["error"] == "inventory_read_only"


def test_the_refusal_names_the_route_that_would_have_written(harness):
    payload = error_of(
        harness.call_sync(issue_token(ALL), "query_inventory", {"operation": "record_inbound"})
    )
    assert "POST /inventory/products/inbound" in payload["message"]


def test_a_read_still_works_next_to_the_refusals(harness, fake_inventory):
    result = structured(
        harness.call_sync(
            issue_token(["inventory:read"]), "query_inventory", {"operation": "list_products"}
        )
    )
    assert result["total"] == 3
    assert {r.method for r in fake_inventory.data_requests()} == {"GET"}


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_the_inventory_client_cannot_send_anything_but_get(method):
    sent: list[httpx.Request] = []
    http = httpx.Client(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200))
    )
    api = ReadOnlyInventoryApi("http://inventory.test", timeout=1, account=None, client=http)

    with pytest.raises(ReadOnlyViolation):
        api._send(method, "/inventory/products/inbound")
    with pytest.raises(ReadOnlyViolation):
        api._send("GET", "/inventory/products", json={"quantity": 1})
    assert sent == []


def test_a_write_reaching_the_client_is_still_refused_as_read_only(
    harness, inventory_api, monkeypatch
):
    """The last layer: even if a bug made a tool try to write, the call fails as read-only."""

    def buggy_list_products(**kwargs):
        return inventory_api._send("POST", "/inventory/products", json={"name": "x"})

    monkeypatch.setattr(inventory_api, "list_products", buggy_list_products)

    payload = error_of(
        harness.call_sync(issue_token(ALL), "query_inventory", {"operation": "list_products"})
    )
    assert payload["error"] == "inventory_read_only"


# --- The policy check that refuses to start a wrong server ------------------------------


def test_the_shipped_policies_are_valid():
    validate_policies(POLICIES)


@pytest.mark.parametrize(
    "policy",
    [
        ToolPolicy("adjust_inventory", Domain.INVENTORY, Access.WRITE, (Scope.INVENTORY_READ,)),
        ToolPolicy("query_inventory", Domain.INVENTORY, Access.READ, (Scope.INCIDENTS_READ,)),
        ToolPolicy("query_inventory", Domain.INVENTORY, Access.READ, ()),
    ],
    ids=["write-access", "foreign-scope", "no-scope"],
)
def test_a_server_breaking_the_read_only_design_refuses_to_start(policy):
    policies = {**POLICIES, policy.tool: policy}
    with pytest.raises(PolicyViolation):
        validate_policies(policies, policies)


def test_a_tool_without_a_policy_refuses_to_start():
    with pytest.raises(PolicyViolation, match="no permission policy"):
        validate_policies([*POLICIES, "export_everything"])


@pytest.mark.parametrize(
    ("name", "write"),
    [
        ("create_product", True),
        ("recordInboundOrder", True),
        ("delete-stock", True),
        ("query_inventory", False),
        ("update_incident_status", False),
        ("list_orders", False),
        ("create_incident", False),
    ],
)
def test_inventory_write_names_are_recognised(name, write):
    assert is_inventory_write_tool_name(name) is write


def test_reads_are_not_write_attempts():
    for operation in ("list_products", "get_product", "list_orders"):
        assert inventory_write_attempt({"operation": operation}) is None
