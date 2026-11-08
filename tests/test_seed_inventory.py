from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from scripts import seed_inventory as seed
from services.core import security
from services.inventory import service
from services.inventory.models import InboundOrder, OutboundOrder, Product, Warehouse
from services.inventory.schemas import OutboundOrderCreate
from services.users import service as users_service
from services.users.models import UserCreate, UserUpdate


@pytest.fixture
def seed_setup(db_path, monkeypatch):
    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)
    user = users_service.create_user(
        UserCreate(email="seed@trackflow.com", password="inventory-password-42")
    )
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    yield engine, UUID(user.id)
    engine.dispose()


def snapshot(engine):
    with Session(engine) as session:
        return [
            row.model_dump(mode="json")
            for model in (Product, InboundOrder, OutboundOrder)
            for row in session.exec(select(model).order_by(model.id)).all()
        ]


def test_seed_matches_sample_products_and_approved_outbound(seed_setup):
    engine, user_uuid = seed_setup
    report = seed.seed_inventory(engine, user_uuid)
    assert (report.inserted_products, report.inserted_inbound, report.inserted_outbound) == (
        3,
        3,
        1,
    )
    assert report.current_stock == {"SHOE-BLK-42": 45, "LAPTOP-DELL-15": 8, "PERFUME-COCO-50": 120}
    with Session(engine) as session:
        products = session.exec(select(Product)).all()
        assert {row.sku: (row.name, row.warehouse.value) for row in products} == {
            "SHOE-BLK-42": ("Black Running Shoes - Size 42", "Monterrey"),
            "LAPTOP-DELL-15": ("Dell Laptop 15 inch", "Zaragoza"),
            "PERFUME-COCO-50": ("Coco Perfume 50ml", "Monterrey"),
        }
        inbound = session.exec(select(InboundOrder)).all()
        outbound = session.exec(select(OutboundOrder)).all()
        assert sorted(row.quantity for row in inbound) == [9, 45, 120]
        assert len(outbound) == 1 and outbound[0].quantity == 1
        assert all(row.user_uuid == user_uuid for row in [*inbound, *outbound])
        for product in products:
            net = sum(row.quantity for row in inbound if row.product_id == product.id) - sum(
                row.quantity for row in outbound if row.product_id == product.id
            )
            assert service.get_product(session, product.id).current_stock == net


def test_second_run_changes_no_records_or_timestamps(seed_setup):
    engine, user_uuid = seed_setup
    seed.seed_inventory(engine, user_uuid)
    before = snapshot(engine)
    second = seed.seed_inventory(engine, user_uuid)
    assert (second.inserted_products, second.inserted_inbound, second.inserted_outbound) == (
        0,
        0,
        0,
    )
    assert second.skipped_existing == 7
    assert snapshot(engine) == before


@pytest.mark.parametrize("inactive", [False, True])
def test_seed_rejects_unknown_or_inactive_actor_without_writes(seed_setup, inactive):
    engine, user_uuid = seed_setup
    if inactive:
        users_service.update_user(str(user_uuid), UserUpdate(is_active=False))
    else:
        user_uuid = uuid4()
    with pytest.raises(seed.SeedError, match="existing active TinyDB user"):
        seed.seed_inventory(engine, user_uuid)
    assert snapshot(engine) == []


def test_product_conflict_rolls_back_earlier_inserts(seed_setup):
    engine, user_uuid = seed_setup
    with Session(engine) as session:
        existing = Product(
            name="Existing laptop", sku="LAPTOP-DELL-15", warehouse=Warehouse.ZARAGOZA
        )
        session.add(existing)
        session.commit()
    before = snapshot(engine)
    with pytest.raises(seed.SeedError, match="conflicts with an existing product"):
        seed.seed_inventory(engine, user_uuid)
    assert snapshot(engine) == before


def test_movement_failure_rolls_back_products_and_receipts(seed_setup, monkeypatch):
    engine, user_uuid = seed_setup
    record = service.record_order

    def fail_outbound(session, payload, actor, model, order_id=None):
        if model is OutboundOrder:
            raise IntegrityError("", {}, Exception("private-database-password"))
        return record(session, payload, actor, model, order_id)

    monkeypatch.setattr(service, "record_order", fail_outbound)
    with pytest.raises(seed.SeedError, match="rolled back") as raised:
        seed.seed_inventory(engine, user_uuid)
    assert "private-database-password" not in str(raised.value)
    assert snapshot(engine) == []


def test_seed_refuses_to_overwrite_changed_seed_movement(seed_setup):
    engine, user_uuid = seed_setup
    seed.seed_inventory(engine, user_uuid)
    with Session(engine) as session:
        row = session.exec(select(OutboundOrder)).one()
        row.quantity = 2
        session.add(row)
        session.commit()
    before = snapshot(engine)
    with pytest.raises(seed.SeedError, match="differs from stored data"):
        seed.seed_inventory(engine, user_uuid)
    assert snapshot(engine) == before


def test_seed_refuses_to_reassign_orders_to_another_user(seed_setup):
    engine, user_uuid = seed_setup
    seed.seed_inventory(engine, user_uuid)
    before = snapshot(engine)
    other = users_service.create_user(
        UserCreate(email="other@trackflow.com", password="inventory-password-42")
    )
    with pytest.raises(seed.SeedError, match="differs from stored data"):
        seed.seed_inventory(engine, UUID(other.id))
    assert snapshot(engine) == before


def test_rerun_does_not_refill_stock_after_real_usage(seed_setup):
    engine, user_uuid = seed_setup
    seed.seed_inventory(engine, user_uuid)
    with Session(engine) as session:
        service.create_outbound(
            session,
            OutboundOrderCreate(
                product_id=seed.SEED_PRODUCTS[0].record_id("product"),
                quantity=1,
            ),
            user_uuid,
        )
    before = snapshot(engine)
    report = seed.seed_inventory(engine, user_uuid)
    assert report.current_stock["SHOE-BLK-42"] == 44
    assert snapshot(engine) == before


def test_seed_cli_reports_reconciled_balances(seed_setup, monkeypatch, capsys):
    engine, user_uuid = seed_setup
    monkeypatch.setattr(seed, "get_engine", lambda: engine)
    monkeypatch.setattr(seed, "initialize_schema", lambda: None)
    assert seed.main(["--user-uuid", str(user_uuid)]) == 0
    output = capsys.readouterr().out
    assert "3 products, 3 inbound, 1 outbound" in output
    assert "LAPTOP-DELL-15 (Zaragoza): 8 units" in output
