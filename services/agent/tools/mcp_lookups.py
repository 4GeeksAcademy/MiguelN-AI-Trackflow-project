"""The agent's live lookups, through the MCP server: tickets and stock.

These replace the direct HTTP tools (removed `tools/incidents.py`,
`tools/inventory.py`, `tools/http.py`, `tools/auth.py`): the agent has one path
to the Incidents Manager and the inventory, and it is the MCP server.

Each lookup takes the planner's typed request, calls one MCP read tool and
returns the same result model the graph has always used
(`TicketLookupResult`, `StockLookupResult`), so the routers, the fallback
answers and the passages for generation do not change:

- ticket by helpdesk reference ('482' -> 'TRF-000482') -> `get_incident(reference=...)`;
- ticket by any other reference, taken as an incident id -> `get_incident(incident_id=...)`;
- a search by filters -> `search_incidents(...)`;
- stock by SKU ('SHOE-BLK-42') -> `query_inventory(operation="list_products", sku=...)`,
  by name -> `query_inventory(operation="list_products", name=...)`.

`incident_not_found` (or no product) is `not_found`; every other tool error and
every failed call is `unavailable`, with the reason in `failure`.
"""

import re
from typing import Any, Final

from pydantic import TypeAdapter, ValidationError
from trackflow_shared.incidents import normalize_reference

from services.agent.tools import mcp_client
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

# 'SHOE-BLK-42': upper-case letters and digits in dash-separated groups.
_SKU_SHAPE: Final = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)+")
_TICKETS: Final = TypeAdapter(list[TicketRecord])
_PRODUCTS: Final = TypeAdapter(list[ProductStock])


def sku_in(product: str) -> str | None:
    """The SKU when the request is one ('shoe-blk-42' -> 'SHOE-BLK-42'), else None."""
    candidate = product.strip().upper()
    return candidate if _SKU_SHAPE.fullmatch(candidate) else None


def _failure(call: mcp_client.McpCall) -> dict[str, Any]:
    """The `unavailable` fields for a call that gave no usable answer."""
    return {
        "outcome": "unavailable",
        "failure": call.failure or call.error_code or "unknown",
        "http_status": call.http_status,
    }


def lookup_ticket(request: TicketLookupInput) -> TicketLookupResult:
    """Look the ticket(s) up through the MCP server. Never raises for a service failure."""
    reference = normalize_reference(request.reference) if request.reference else None
    if reference is not None:
        call = mcp_client.call_tool(mcp_client.GET_INCIDENT, {"reference": reference})
    elif request.reference is not None:
        call = mcp_client.call_tool(mcp_client.GET_INCIDENT, {"incident_id": request.reference})
    else:
        call = mcp_client.call_tool(
            mcp_client.SEARCH_INCIDENTS, {**request.filters(), "limit": MAX_TICKETS}
        )

    def result(**fields: Any) -> TicketLookupResult:
        return TicketLookupResult.model_validate(
            {
                "request": request,
                "endpoint": call.label,
                "reference": reference or request.reference,
                "duration_ms": call.duration_ms,
                **fields,
            }
        )

    if call.outcome == "tool_error" and call.error_code == "incident_not_found":
        return result(outcome="not_found")
    if call.outcome != "ok" or call.content is None:
        return result(**_failure(call))
    try:
        if call.tool == mcp_client.GET_INCIDENT:
            tickets, total = [TicketRecord.model_validate(call.content)], 1
        else:
            tickets = _TICKETS.validate_python(call.content.get("incidents"))
            total = int(call.content.get("total", len(tickets)))
    except (ValidationError, TypeError, ValueError):
        return result(outcome="unavailable", failure="invalid_response")
    return result(outcome="ok", tickets=tickets[:MAX_TICKETS], total=total)


def lookup_stock(request: StockLookupInput) -> StockLookupResult:
    """Look the product's stock up through the MCP server. Never raises for a service failure."""
    arguments: dict[str, Any] = {"operation": "list_products", "limit": MAX_PRODUCTS}
    sku = sku_in(request.product)
    if sku is not None:
        arguments["sku"] = sku
    else:
        arguments["name"] = request.product
    if request.warehouse is not None:
        arguments["warehouse"] = request.warehouse.value
    call = mcp_client.call_tool(mcp_client.QUERY_INVENTORY, arguments)

    def result(**fields: Any) -> StockLookupResult:
        return StockLookupResult.model_validate(
            {
                "request": request,
                "endpoint": call.label,
                "duration_ms": call.duration_ms,
                **fields,
            }
        )

    if call.outcome != "ok" or call.content is None:
        return result(**_failure(call))
    try:
        products = _PRODUCTS.validate_python(call.content.get("products") or [])
        total = int(call.content.get("total", len(products)))
    except (ValidationError, TypeError, ValueError):
        return result(outcome="unavailable", failure="invalid_response")
    if not products:
        return result(outcome="not_found")
    return result(outcome="ok", products=products[:MAX_PRODUCTS], total=total)
