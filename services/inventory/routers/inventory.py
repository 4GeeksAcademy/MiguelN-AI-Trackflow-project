from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from services.auth.dependencies import CurrentUser
from services.inventory import service
from services.inventory.database import get_db
from services.inventory.models import Warehouse
from services.inventory.schemas import (
    InboundOrderConfirmation,
    InboundOrderCreate,
    OrderHistoryItem,
    OutboundOrderConfirmation,
    OutboundOrderCreate,
    ProductAvailability,
    ProductCreate,
    ProductResponse,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])
InventorySession = Annotated[Session, Depends(get_db)]


@router.get("/products", response_model=list[ProductResponse])
def list_products(
    user: CurrentUser,
    session: InventorySession,
    warehouse: Warehouse | None = None,
    sku: str | None = None,
) -> list[ProductResponse]:
    return service.list_products(session, warehouse=warehouse, sku=sku)


@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate,
    user: CurrentUser,
    session: InventorySession,
) -> ProductResponse:
    return service.create_product(session, payload)


@router.post(
    "/products/inbound",
    response_model=InboundOrderConfirmation,
    status_code=status.HTTP_201_CREATED,
)
def create_inbound(
    payload: InboundOrderCreate,
    user: CurrentUser,
    session: InventorySession,
) -> InboundOrderConfirmation:
    order = service.create_inbound(session, payload, UUID(user.id))
    return InboundOrderConfirmation(
        quantity=order.quantity,
        product_name=order.product.name,
        warehouse=order.product.warehouse,
    )


@router.post(
    "/products/outbound",
    response_model=OutboundOrderConfirmation,
    status_code=status.HTTP_201_CREATED,
)
def create_outbound(
    payload: OutboundOrderCreate,
    user: CurrentUser,
    session: InventorySession,
) -> OutboundOrderConfirmation:
    order = service.create_outbound(session, payload, UUID(user.id))
    return OutboundOrderConfirmation(
        quantity=order.quantity,
        product_name=order.product.name,
        warehouse=order.product.warehouse,
    )


@router.get("/products/{id}", response_model=ProductAvailability)
def get_product(
    id: UUID, user: CurrentUser, session: InventorySession
) -> ProductAvailability:
    product = service.get_product(session, id)
    return ProductAvailability(id=product.id, current_stock=product.current_stock)


@router.get("/orders", response_model=list[OrderHistoryItem])
def list_orders(
    user: CurrentUser,
    session: InventorySession,
    warehouse: Warehouse | None = None,
    product_id: UUID | None = None,
) -> list[OrderHistoryItem]:
    orders = service.list_orders(session, warehouse=warehouse, product_id=product_id)
    return [
        OrderHistoryItem(
            id=order.id,
            product_id=order.product_id,
            quantity=order.quantity,
            created_at=order.created_at,
            user_uuid=order.user_uuid,
            product_name=order.product.name,
            product_sku=order.product.sku,
            warehouse=order.product.warehouse,
            direction=order.direction,
        )
        for order in orders
    ]
