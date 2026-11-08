from typing import cast
from uuid import UUID

from sqlalchemy import func, literal, union_all
from sqlalchemy import select as sql_select
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from services.core.errors import ProductAlreadyExists, ProductNotFound, ValidationFailed
from services.inventory.models import InboundOrder, OutboundOrder, Product, Warehouse
from services.inventory.schemas import (
    InboundOrderCreate,
    InboundOrderResponse,
    OutboundOrderCreate,
    OutboundOrderResponse,
    ProductCreate,
    ProductResponse,
    ProductSummary,
)


def _stock_query():
    inbound = (
        select(
            InboundOrder.product_id,
            func.sum(InboundOrder.quantity).label("quantity"),
        )
        .group_by(InboundOrder.product_id)
        .subquery()
    )
    outbound = (
        select(
            OutboundOrder.product_id,
            func.sum(OutboundOrder.quantity).label("quantity"),
        )
        .group_by(OutboundOrder.product_id)
        .subquery()
    )
    return (
        select(
            Product,
            (func.coalesce(inbound.c.quantity, 0) - func.coalesce(outbound.c.quantity, 0)).label(
                "current_stock"
            ),
        )
        .outerjoin(inbound, Product.id == inbound.c.product_id)
        .outerjoin(outbound, Product.id == outbound.c.product_id)
    )


def _response(product: Product, stock: int) -> ProductResponse:
    # `client_id` is internal to reporting and not part of the product contract.
    return ProductResponse(**product.model_dump(exclude={"client_id"}), current_stock=stock)


def list_products(
    session: Session,
    warehouse: Warehouse | None = None,
    sku: str | None = None,
) -> list[ProductResponse]:
    query = _stock_query().order_by(Product.warehouse, Product.sku, Product.id)
    if warehouse is not None:
        query = query.where(Product.warehouse == warehouse)
    if sku is not None:
        query = query.where(Product.sku == sku)
    return [_response(product, int(stock)) for product, stock in session.exec(query).all()]


def get_product(session: Session, product_id: UUID) -> ProductResponse:
    row = session.exec(_stock_query().where(Product.id == product_id)).first()
    if row is None:
        raise ProductNotFound()
    product, stock = row
    return _response(product, int(stock))


def create_product(session: Session, payload: ProductCreate) -> ProductResponse:
    try:
        with session.begin():
            product = Product(**payload.model_dump())
            session.add(product)
            session.flush()
            response = _response(product, 0)
        return response
    except IntegrityError:
        existing = session.exec(
            select(Product).where(
                Product.sku == payload.sku,
                Product.warehouse == payload.warehouse,
            )
        ).first()
        if existing is not None:
            raise ProductAlreadyExists() from None
        raise


def _current_stock(session: Session, product_id: UUID) -> int:
    inbound = session.exec(
        select(func.coalesce(func.sum(InboundOrder.quantity), 0)).where(
            InboundOrder.product_id == product_id
        )
    ).one()
    outbound = session.exec(
        select(func.coalesce(func.sum(OutboundOrder.quantity), 0)).where(
            OutboundOrder.product_id == product_id
        )
    ).one()
    return int(inbound) - int(outbound)


def _create_order(
    session: Session,
    payload: InboundOrderCreate | OutboundOrderCreate,
    user_uuid: UUID,
    model: type[InboundOrder] | type[OutboundOrder],
) -> InboundOrderResponse | OutboundOrderResponse:
    with session.begin():
        return record_order(session, payload, user_uuid, model)


def record_order(
    session: Session,
    payload: InboundOrderCreate | OutboundOrderCreate,
    user_uuid: UUID,
    model: type[InboundOrder] | type[OutboundOrder],
    order_id: UUID | None = None,
) -> InboundOrderResponse | OutboundOrderResponse:
    """Record a locked movement inside a transaction owned by the caller."""
    if not session.in_transaction():
        raise RuntimeError("Inventory movements require an active transaction.")
    product = session.exec(
        select(Product).where(Product.id == payload.product_id).with_for_update()
    ).first()
    if product is None:
        raise ProductNotFound()
    if model is OutboundOrder:
        stock = _current_stock(session, product.id)
        if payload.quantity > stock:
            raise ValidationFailed(
                "quantity",
                f"Insufficient stock in {product.warehouse.value}: "
                f"{stock} units available, {payload.quantity} requested.",
            )
    order = model(product_id=product.id, quantity=payload.quantity, user_uuid=user_uuid)
    if order_id is not None:
        order.id = order_id
    session.add(order)
    session.flush()
    response_type = InboundOrderResponse if model is InboundOrder else OutboundOrderResponse
    return response_type(
        **order.model_dump(),
        product=ProductSummary.model_validate(product),
    )


def create_inbound(
    session: Session,
    payload: InboundOrderCreate,
    user_uuid: UUID,
) -> InboundOrderResponse:
    return cast(InboundOrderResponse, _create_order(session, payload, user_uuid, InboundOrder))


def create_outbound(
    session: Session,
    payload: OutboundOrderCreate,
    user_uuid: UUID,
) -> OutboundOrderResponse:
    return cast(OutboundOrderResponse, _create_order(session, payload, user_uuid, OutboundOrder))


def list_orders(
    session: Session,
    warehouse: Warehouse | None = None,
    product_id: UUID | None = None,
) -> list[InboundOrderResponse | OutboundOrderResponse]:
    orders = union_all(
        sql_select(
            col(InboundOrder.id),
            col(InboundOrder.product_id),
            col(InboundOrder.quantity),
            col(InboundOrder.created_at),
            col(InboundOrder.user_uuid),
            literal("inbound").label("direction"),
        ),
        sql_select(
            col(OutboundOrder.id),
            col(OutboundOrder.product_id),
            col(OutboundOrder.quantity),
            col(OutboundOrder.created_at),
            col(OutboundOrder.user_uuid),
            literal("outbound").label("direction"),
        ),
    ).subquery()
    query = (
        sql_select(
            Product,
            orders.c.id,
            orders.c.quantity,
            orders.c.created_at,
            orders.c.user_uuid,
            orders.c.direction,
        )
        .join(orders, col(Product.id) == orders.c.product_id)
        .order_by(
            orders.c.created_at.desc(),
            orders.c.id.desc(),
            orders.c.direction,
        )
    )
    if warehouse is not None:
        query = query.where(col(Product.warehouse) == warehouse)
    if product_id is not None:
        query = query.where(col(Product.id) == product_id)
    responses = []
    for product, order_id, quantity, created_at, user_uuid, direction in session.execute(
        query
    ).all():
        response_type = InboundOrderResponse if direction == "inbound" else OutboundOrderResponse
        responses.append(
            response_type(
                id=order_id,
                product_id=product.id,
                quantity=quantity,
                created_at=created_at,
                user_uuid=user_uuid,
                product=ProductSummary.model_validate(product),
            )
        )
    return responses
