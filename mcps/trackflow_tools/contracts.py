"""The tools' output models: the services' response models, field for field.

The names are the APIs' own (`IncidentResponse`, `IncidentCreatedResponse`,
`ProductResponse`, `ProductAvailability`, `OrderHistoryItem`), so a client that
knows the Incidents Manager or the inventory API reads the same fields here.
They are mirrored rather than imported because this server talks to the
services over HTTP and never imports them; `tests/mcps/test_mcp_tools.py`
fails if either side drifts.

The incident vocabulary (statuses, categories, origins, branches) comes from
`trackflow_shared.incidents`, the package both the API and the tooling already
share. The warehouse names are mirrored from `services.inventory.models`.
"""

from datetime import datetime
from enum import StrEnum
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from trackflow_shared.incidents import (
    IncidentBranch,
    IncidentCategory,
    IncidentOrigin,
    IncidentStatus,
)

# `IncidentBase` limits in `services/incidents/models.py`.
TITLE_MAX_LENGTH: Final = 120
DESCRIPTION_MAX_LENGTH: Final = 4000
# `ProductName` / `ProductSKU` limits in `services/inventory/schemas.py`.
PRODUCT_NAME_MAX_LENGTH: Final = 200
PRODUCT_SKU_MAX_LENGTH: Final = 100

DEFAULT_LIMIT: Final = 10
MAX_LIMIT: Final = 50


class Warehouse(StrEnum):
    MONTERREY = "Monterrey"
    ZARAGOZA = "Zaragoza"


class _Record(BaseModel):
    # A field the API adds later is ignored instead of breaking the tool; the
    # drift test is what notices it.
    model_config = ConfigDict(extra="ignore")


# --- Incidents Manager -----------------------------------------------------------


class Incident(_Record):
    """One incident, as `GET /api/incidents/{id}` returns it (`IncidentResponse`)."""

    id: str = Field(description="The incident's id (UUID), used by update_incident_status.")
    reference: str | None = Field(
        default=None,
        description="The helpdesk reference ('TRF-000123') when the incident came from it.",
    )
    title: str
    description: str
    status: IncidentStatus
    category: IncidentCategory
    origin: IncidentOrigin
    branch: IncidentBranch
    created_at: datetime
    updated_at: datetime


class CreatedIncident(_Record):
    """The new incident, as `POST /api/incidents` returns it (`IncidentCreatedResponse`)."""

    id: str = Field(description="The new incident's id (UUID).")
    title: str
    status: IncidentStatus
    branch: IncidentBranch


class IncidentSearchResult(BaseModel):
    incidents: list[Incident] = Field(
        description="The matching incidents, in the Incidents Manager's order, up to `limit`."
    )
    total: int = Field(ge=0, description="How many incidents matched in all.")


# --- Inventory (read-only) ---------------------------------------------------------


class Product(_Record):
    """One product and its stock, as `GET /inventory/products` returns it (`ProductResponse`)."""

    id: UUID
    name: str
    sku: str
    warehouse: Warehouse
    current_stock: int = Field(ge=0, description="Units in stock: inbound minus outbound.")


class ProductAvailability(_Record):
    """One product's stock, as `GET /inventory/products/{id}` returns it."""

    id: UUID
    current_stock: int = Field(ge=0)


class StockMovement(_Record):
    """One inbound or outbound movement, as `GET /inventory/orders` returns it
    (`OrderHistoryItem`). The API's `user_uuid` (who recorded it) is left out:
    no inventory question needs it."""

    id: UUID
    product_id: UUID
    quantity: int = Field(gt=0)
    created_at: datetime
    product_name: str
    product_sku: str
    warehouse: Warehouse
    direction: Literal["inbound", "outbound"]


InventoryOperation = Literal["list_products", "get_product", "list_orders"]


class InventoryQueryResult(BaseModel):
    operation: InventoryOperation = Field(description="The read that was performed.")
    products: list[Product] | None = Field(
        default=None, description="list_products: the matching products, up to `limit`."
    )
    product: ProductAvailability | None = Field(
        default=None, description="get_product: the product's current stock."
    )
    orders: list[StockMovement] | None = Field(
        default=None, description="list_orders: the matching movements, up to `limit`."
    )
    total: int = Field(ge=0, description="How many records matched in all.")
