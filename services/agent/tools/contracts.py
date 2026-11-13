"""Typed contracts of the agent's tools: what each one takes and what it returns.

The input models are also the JSON schemas the planner shows the model
(`planner.tool_definitions`), so their field descriptions are written for it.
The record models are what the MCP server's read tools return, which in turn
mirror the services' own response models - `TicketRecord` is `IncidentResponse`,
`ProductStock` is `ProductResponse` - and `tests/pipelines/test_agent_mcp_client.py`
fails if either side drifts.

A result always says how the lookup went (`outcome`), which MCP call was made
(`endpoint`, e.g. `MCP get_incident(reference=TRF-000482)`, without host or
token) and how long it took, so the run trace shows exactly what the tool did.
`http_status` is only set when the MCP server or the token endpoint answered
with an HTTP error.
"""

from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from trackflow_shared.incidents import (
    IncidentBranch,
    IncidentCategory,
    IncidentOrigin,
    IncidentStatus,
)

from services.inventory.models import Warehouse

# How a lookup went:
# - `ok`: the service answered; a search with no match is still `ok` (total 0).
# - `not_found`: the ticket or product asked for does not exist.
# - `unavailable`: no usable answer - timeout, unreachable, an error status, an
#   unreadable body, or the tool is not configured. `failure` says which.
LookupOutcome = Literal["ok", "not_found", "unavailable"]

# At most this many records go into a result, newest tickets first.
MAX_TICKETS: Final = 10
MAX_PRODUCTS: Final = 10


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- Ticket lookup (incident manager) -------------------------------------------


class TicketLookupInput(_Contract):
    """Read support tickets (incidents) from the incident manager: one ticket by
    its reference, or a search by status, category, origin and branch."""

    reference: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description=(
            "The ticket the user asks about, exactly as they wrote it: a helpdesk "
            "reference such as '482' or 'TRF-000482', or an incident id. Leave it out "
            "to search with the filters instead."
        ),
    )
    status: IncidentStatus | None = Field(
        default=None, description="Only tickets in this status (search without a reference)."
    )
    category: IncidentCategory | None = Field(
        default=None, description="Only tickets of this category (search without a reference)."
    )
    origin: IncidentOrigin | None = Field(
        default=None, description="Only tickets reported by this origin (search only)."
    )
    branch: IncidentBranch | None = Field(
        default=None, description="Only tickets of this branch (search without a reference)."
    )

    @model_validator(mode="after")
    def needs_a_reference_or_a_filter(self) -> "TicketLookupInput":
        if self.reference is None and not self.filters():
            raise ValueError("give a ticket reference or at least one filter")
        return self

    def filters(self) -> dict[str, str]:
        """The search filters given, as query parameters."""
        values = {
            "status": self.status,
            "category": self.category,
            "origin": self.origin,
            "branch": self.branch,
        }
        return {name: value.value for name, value in values.items() if value is not None}


class TicketRecord(BaseModel):
    """One incident as `GET /api/incidents` returns it (`IncidentResponse`)."""

    # A field the API adds later is ignored rather than breaking the tool; the
    # drift test is what notices it.
    model_config = ConfigDict(extra="ignore")

    id: str
    reference: str | None = None
    title: str
    description: str
    status: IncidentStatus
    category: IncidentCategory
    origin: IncidentOrigin
    branch: IncidentBranch
    created_at: datetime
    updated_at: datetime


class TicketLookupResult(BaseModel):
    request: TicketLookupInput
    outcome: LookupOutcome
    endpoint: str
    # The canonical helpdesk reference looked up (`TRF-000482`), when there was one.
    reference: str | None = None
    tickets: list[TicketRecord] = Field(default_factory=list)
    total: int = 0
    failure: str | None = None
    http_status: int | None = None
    duration_ms: float


# --- Stock lookup (inventory manager) -------------------------------------------


class StockLookupInput(_Contract):
    """Read the current stock of a product from the inventory manager."""

    product: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "The product the user asks about, as they wrote it: its name (e.g. 'black "
            "running shoes') or its SKU (e.g. 'SHOE-BLK-42')."
        ),
    )
    warehouse: Warehouse | None = Field(
        default=None,
        description="Only stock in this warehouse, when the user names one.",
    )


class ProductStock(BaseModel):
    """One product as `GET /inventory/products` returns it (`ProductResponse`)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    sku: str
    warehouse: Warehouse
    current_stock: int = Field(ge=0)


class StockLookupResult(BaseModel):
    request: StockLookupInput
    outcome: LookupOutcome
    endpoint: str
    products: list[ProductStock] = Field(default_factory=list)
    total: int = 0
    failure: str | None = None
    http_status: int | None = None
    duration_ms: float
