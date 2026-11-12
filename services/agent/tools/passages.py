"""Successful lookups as context passages for `rag.generate_answer`.

`rag.build_messages` formats every passage as "[n] {source_document} - {section}"
followed by its `text`, so a live record takes the same shape as a knowledge-base
chunk and the generation prompt needs no change. Only lookups that succeeded
become passages: the model never sees a failed one, so it cannot dress a
failure up as a status.
"""

from typing import Any, Final

from trackflow_shared.incidents import BRANCH_LABELS

from services.agent.tools.contracts import (
    ProductStock,
    StockLookupResult,
    TicketLookupResult,
    TicketRecord,
)

INCIDENT_MANAGER_DOCUMENT: Final = "incident-manager"
INVENTORY_MANAGER_DOCUMENT: Final = "inventory-manager"
_TIMESTAMP: Final = "%Y-%m-%d %H:%M UTC"


def _label(value: str) -> str:
    """'in_progress' -> 'in progress': the words a person would use."""
    return value.replace("_", " ")


def _ticket_name(ticket: TicketRecord) -> str:
    return ticket.reference or f"with id {ticket.id} (no helpdesk reference)"


def _ticket_lines(ticket: TicketRecord) -> list[str]:
    branch = BRANCH_LABELS["en"].get(ticket.branch, _label(ticket.branch.value))
    return [
        f"Ticket {_ticket_name(ticket)}: {ticket.title}",
        f"Status: {_label(ticket.status.value)}",
        f"Category: {_label(ticket.category.value)}",
        f"Reported by: {_label(ticket.origin.value)}",
        f"Branch: {branch}",
        f"Opened: {ticket.created_at.strftime(_TIMESTAMP)}",
        f"Last updated: {ticket.updated_at.strftime(_TIMESTAMP)}",
        f"Description: {ticket.description}",
    ]


def ticket_passages(result: TicketLookupResult) -> list[dict[str, Any]]:
    """One passage per ticket looked up, or one for a whole search."""
    if result.outcome != "ok":
        return []
    if result.request.reference is not None:
        return [
            {
                "source_document": INCIDENT_MANAGER_DOCUMENT,
                "section": f"ticket {_ticket_name(ticket)}",
                "text": "\n".join(_ticket_lines(ticket)),
            }
            for ticket in result.tickets
        ]
    filters = ", ".join(
        f"{name} {_label(value)}" for name, value in result.request.filters().items()
    )
    lines = [f"Search of the incident manager ({filters}): {result.total} matching ticket(s)."]
    if result.total > len(result.tickets):
        lines.append(f"The {len(result.tickets)} most recent:")
    for ticket in result.tickets:
        lines.append("")
        lines.extend(_ticket_lines(ticket))
    return [
        {
            "source_document": INCIDENT_MANAGER_DOCUMENT,
            "section": f"search {filters}",
            "text": "\n".join(lines),
        }
    ]


def _product_lines(product: ProductStock) -> list[str]:
    return [
        f"Product: {product.name}",
        f"SKU: {product.sku}",
        f"Warehouse: {product.warehouse.value}",
        f"Current stock: {product.current_stock} units",
    ]


def stock_passages(result: StockLookupResult) -> list[dict[str, Any]]:
    """One passage per product found."""
    if result.outcome != "ok":
        return []
    return [
        {
            "source_document": INVENTORY_MANAGER_DOCUMENT,
            "section": f"stock {product.sku} ({product.warehouse.value})",
            "text": "\n".join(_product_lines(product)),
        }
        for product in result.products
    ]
