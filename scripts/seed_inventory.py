"""Seed approved sample products and demo movements from coding-fundamentals.md."""

import argparse
import sys
from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from services.core.config import ConfigurationError
from services.core.errors import ValidationFailed
from services.inventory import service
from services.inventory.database import dispose_engine, get_engine, initialize_schema
from services.inventory.models import InboundOrder, OutboundOrder, Product, Warehouse
from services.inventory.schemas import InboundOrderCreate, OutboundOrderCreate, ProductCreate
from services.users import service as users_service


@dataclass(frozen=True)
class SeedProduct:
    sku: str
    name: str
    warehouse: Warehouse
    client_id: str
    target_stock: int
    outbound_quantity: int = 0

    def record_id(self, kind: str) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            f"trackflow://inventory/seed/v1/{kind}/{self.warehouse.value}/{self.sku}",
        )


# Owners are fictional brand clients, one per product, approved by the developer
# for the business performance pipeline (PIPELINE_DESIGN.md, open question Q1).
SEED_PRODUCTS = (
    SeedProduct(
        "SHOE-BLK-42", "Black Running Shoes - Size 42", Warehouse.MONTERREY, "stride-athletics", 45
    ),
    SeedProduct(
        "LAPTOP-DELL-15", "Dell Laptop 15 inch", Warehouse.ZARAGOZA, "nexa-electronics", 8, 1
    ),
    SeedProduct("PERFUME-COCO-50", "Coco Perfume 50ml", Warehouse.MONTERREY, "coco-beauty", 120),
)


@dataclass
class SeedReport:
    inserted_products: int = 0
    inserted_inbound: int = 0
    inserted_outbound: int = 0
    assigned_clients: int = 0
    skipped_existing: int = 0
    current_stock: dict[str, int] = field(default_factory=dict)


class SeedError(RuntimeError):
    """The seed was refused or rolled back without overwriting existing data."""


def _seed_product(session: Session, item: SeedProduct, report: SeedReport) -> None:
    payload = ProductCreate(name=item.name, sku=item.sku, warehouse=item.warehouse)
    product_id = item.record_id("product")
    existing = session.exec(
        select(Product)
        .where(Product.sku == item.sku, Product.warehouse == item.warehouse)
        .with_for_update()
    ).first()
    if existing is not None:
        if existing.id != product_id or existing.name != item.name:
            raise SeedError(
                f"Seed conflicts with an existing product for {item.sku}; no changes committed."
            )
        if existing.client_id is None:
            # Seeded before products had owners: claim it, once.
            existing.client_id = item.client_id
            session.add(existing)
            session.flush()
            report.assigned_clients += 1
        elif existing.client_id != item.client_id:
            raise SeedError(f"{item.sku} is owned by another client; no changes committed.")
        report.skipped_existing += 1
        return
    if session.get(Product, product_id) is not None:
        raise SeedError("A seed product UUID is already in use; no changes committed.")
    session.add(Product(id=product_id, client_id=item.client_id, **payload.model_dump()))
    session.flush()
    report.inserted_products += 1


def _seed_movement(
    session: Session,
    item: SeedProduct,
    user_uuid: UUID,
    model: type[InboundOrder] | type[OutboundOrder],
    report: SeedReport,
) -> None:
    inbound = model is InboundOrder
    quantity = item.target_stock + item.outbound_quantity if inbound else item.outbound_quantity
    if quantity == 0:
        return
    order_id = item.record_id("inbound" if inbound else "outbound")
    product_id = item.record_id("product")
    existing = (
        session.get(InboundOrder, order_id) if inbound else session.get(OutboundOrder, order_id)
    )
    if existing is not None:
        if (
            existing.product_id != product_id
            or existing.quantity != quantity
            or existing.user_uuid != user_uuid
        ):
            raise SeedError(
                f"Seed movement for {item.sku} differs from stored data; no changes committed."
            )
        report.skipped_existing += 1
        return
    payload_type = InboundOrderCreate if inbound else OutboundOrderCreate
    service.record_order(
        session,
        payload_type(product_id=product_id, quantity=quantity),
        user_uuid,
        model,
        order_id=order_id,
    )
    if inbound:
        report.inserted_inbound += 1
    else:
        report.inserted_outbound += 1


def seed_inventory(engine: Engine, user_uuid: UUID) -> SeedReport:
    user = users_service.get_user(str(user_uuid))
    if user is None or not user.is_active:
        raise SeedError("Seed actor must be an existing active TinyDB user; no changes committed.")
    report = SeedReport()
    try:
        with Session(engine) as session, session.begin():
            for item in SEED_PRODUCTS:
                _seed_product(session, item, report)
            for item in SEED_PRODUCTS:
                _seed_movement(session, item, user_uuid, InboundOrder, report)
            for item in SEED_PRODUCTS:
                _seed_movement(session, item, user_uuid, OutboundOrder, report)
            for item in SEED_PRODUCTS:
                report.current_stock[item.sku] = service.get_product(
                    session, item.record_id("product")
                ).current_stock
        return report
    except SQLAlchemyError:
        raise SeedError("Inventory seed failed; database changes were rolled back.") from None
    except ValidationFailed as error:
        raise SeedError(f"Inventory seed refused: {error.message} No changes committed.") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user-uuid", type=UUID, required=True, help="Existing active TinyDB user's UUID"
    )
    arguments = parser.parse_args(argv)
    try:
        initialize_schema()
        report = seed_inventory(get_engine(), arguments.user_uuid)
        print(
            f"Inserted: {report.inserted_products} products, {report.inserted_inbound} inbound, "
            f"{report.inserted_outbound} outbound. Assigned owners: {report.assigned_clients}. "
            f"Skipped existing: {report.skipped_existing}."
        )
        for item in SEED_PRODUCTS:
            print(f"{item.sku} ({item.warehouse.value}): {report.current_stock[item.sku]} units")
        return 0
    except (SeedError, ConfigurationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        dispose_engine()


if __name__ == "__main__":
    sys.exit(main())
