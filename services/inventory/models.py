from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, Enum, UniqueConstraint, func
from sqlmodel import Field, SQLModel


class Warehouse(StrEnum):
    MONTERREY = "Monterrey"
    ZARAGOZA = "Zaragoza"


class Product(SQLModel, table=True):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("sku", "warehouse", name="uq_product_sku_warehouse"),
        CheckConstraint("length(trim(name)) > 0", name="ck_product_name"),
        CheckConstraint("length(trim(sku)) > 0", name="ck_product_sku"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(max_length=200, nullable=False)
    sku: str = Field(max_length=100, nullable=False, index=True)
    warehouse: Warehouse = Field(
        sa_column=Column(
            Enum(
                Warehouse,
                values_callable=lambda members: [member.value for member in members],
                native_enum=False,
                create_constraint=True,
                name="warehouse_location",
            ),
            nullable=False,
        )
    )
    # The brand client that owns the goods: in a 3PL warehouse every SKU belongs
    # to one client. A lowercase slug assigned by Commercial and Client Relations
    # (PIPELINE_DESIGN.md 2.4). Nullable until every product has an owner; the
    # slug format is a PostgreSQL check added by `initialize_schema`. Internal:
    # product responses do not carry it.
    client_id: str | None = Field(default=None, max_length=64, nullable=True)


class InboundOrder(SQLModel, table=True):
    __tablename__ = "inbound_orders"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_inbound_quantity"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    product_id: UUID = Field(foreign_key="products.id", nullable=False, index=True)
    quantity: int = Field(gt=0, nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
        ),
    )
    user_uuid: UUID = Field(nullable=False)


class OutboundOrder(SQLModel, table=True):
    __tablename__ = "outbound_orders"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_outbound_quantity"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    product_id: UUID = Field(foreign_key="products.id", nullable=False, index=True)
    quantity: int = Field(gt=0, nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
        ),
    )
    user_uuid: UUID = Field(nullable=False)
