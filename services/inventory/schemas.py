from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from services.inventory.models import Warehouse

ProductName = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)
]
ProductSKU = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)
]
Quantity = Annotated[int, Field(strict=True, gt=0, le=2_147_483_647)]


class InventorySchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ProductCreate(InventorySchema):
    name: ProductName
    sku: ProductSKU
    warehouse: Warehouse


class ProductSummary(ProductCreate):
    id: UUID


class ProductResponse(ProductSummary):
    current_stock: int = Field(ge=0)


class ProductAvailability(InventorySchema):
    id: UUID
    current_stock: int = Field(ge=0)


class InboundOrderCreate(InventorySchema):
    product_id: UUID
    quantity: Quantity


class OutboundOrderCreate(InventorySchema):
    product_id: UUID
    quantity: Quantity


class OrderFields(InventorySchema):
    id: UUID
    product_id: UUID
    quantity: Quantity
    created_at: datetime
    user_uuid: UUID
    product: ProductSummary

    @field_validator("created_at")
    @classmethod
    def as_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class InboundOrderResponse(OrderFields):
    direction: Literal["inbound"] = "inbound"


class OutboundOrderResponse(OrderFields):
    direction: Literal["outbound"] = "outbound"


OrderResponse = Annotated[
    InboundOrderResponse | OutboundOrderResponse,
    Field(discriminator="direction"),
]


class MovementConfirmation(InventorySchema):
    quantity: Quantity
    product_name: ProductName
    warehouse: Warehouse


class InboundOrderConfirmation(MovementConfirmation):
    direction: Literal["inbound"] = "inbound"


class OutboundOrderConfirmation(MovementConfirmation):
    direction: Literal["outbound"] = "outbound"


class OrderHistoryItem(InventorySchema):
    id: UUID
    product_id: UUID
    quantity: Quantity
    created_at: datetime
    user_uuid: UUID
    product_name: ProductName
    product_sku: ProductSKU
    warehouse: Warehouse
    direction: Literal["inbound", "outbound"]

    @field_validator("created_at")
    @classmethod
    def as_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
