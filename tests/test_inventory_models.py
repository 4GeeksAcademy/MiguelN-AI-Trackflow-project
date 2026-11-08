from uuid import uuid4

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from services.inventory import database
from services.inventory.models import InboundOrder, OutboundOrder, Product, Warehouse

INITIALIZE_SCHEMA = database.initialize_schema


@pytest.fixture
def inventory_engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_inventory_tables_have_no_stock_or_identity_columns():
    assert set(SQLModel.metadata.tables) == {"products", "inbound_orders", "outbound_orders"}
    # `client_id` is the owning brand client, read by the reporting pipeline.
    assert set(Product.__table__.columns.keys()) == {"id", "name", "sku", "warehouse", "client_id"}
    assert Product.__table__.columns.client_id.nullable
    for model in (InboundOrder, OutboundOrder):
        assert set(model.__table__.columns.keys()) == {
            "id",
            "product_id",
            "quantity",
            "created_at",
            "user_uuid",
        }
        assert [key.target_fullname for key in model.__table__.foreign_keys] == ["products.id"]
        assert model.__table__.columns.created_at.type.timezone


def test_product_is_unique_within_warehouse(inventory_engine):
    with Session(inventory_engine) as session:
        session.add(Product(name="Shoes", sku="SHOE-42", warehouse=Warehouse.MONTERREY))
        session.add(Product(name="Shoes", sku="SHOE-42", warehouse=Warehouse.ZARAGOZA))
        session.commit()
        session.add(Product(name="Duplicate", sku="SHOE-42", warehouse=Warehouse.MONTERREY))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert len(session.exec(select(Product)).all()) == 2


@pytest.mark.parametrize("model", [InboundOrder, OutboundOrder])
def test_orders_require_existing_product_and_positive_quantity(inventory_engine, model):
    with Session(inventory_engine) as session:
        user_uuid = uuid4()
        session.add(model(product_id=uuid4(), quantity=1, user_uuid=user_uuid))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        product = Product(name="Shoes", sku="SHOE-42", warehouse=Warehouse.MONTERREY)
        session.add(product)
        session.commit()
        product_id = product.id
        for quantity in (0, -1):
            session.add(model(product_id=product_id, quantity=quantity, user_uuid=user_uuid))
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
        order = model(product_id=product_id, quantity=2, user_uuid=user_uuid)
        assert order.created_at.utcoffset().total_seconds() == 0
        session.add(order)
        session.commit()
        assert len(session.exec(select(model)).all()) == 1


def test_schema_initialization_is_idempotent(inventory_engine, monkeypatch):
    monkeypatch.setattr(database, "get_engine", lambda: inventory_engine)
    INITIALIZE_SCHEMA()
    INITIALIZE_SCHEMA()
    assert set(inspect(inventory_engine).get_table_names()) == {
        "products",
        "inbound_orders",
        "outbound_orders",
    }
