import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from time import monotonic
from uuid import UUID, uuid4

import psycopg2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlmodel import Session, SQLModel, create_engine, select

from scripts.seed_inventory import seed_inventory
from services.core import security
from services.core.errors import ValidationFailed
from services.inventory import service
from services.inventory.database import get_db
from services.inventory.models import InboundOrder, OutboundOrder, Product, Warehouse
from services.inventory.schemas import InboundOrderCreate, OutboundOrderCreate, ProductCreate
from services.main import app
from services.users import service as users_service
from services.users.models import UserCreate
from tests.test_inventory_api import movement, product
from tests.test_seed_inventory import snapshot

REAL_CONNECT = psycopg2.connect


@pytest.fixture
def postgres_inventory_engine(monkeypatch):
    value = os.getenv("INVENTORY_TEST_DATABASE_URL")
    if not value:
        pytest.skip(
            "Set INVENTORY_TEST_DATABASE_URL explicitly for disposable-schema PostgreSQL tests"
        )
    url = make_url(value)
    if url.get_backend_name() != "postgresql":
        pytest.fail("PostgreSQL integration tests require a PostgreSQL URL")
    url = url.set(drivername="postgresql+psycopg2")
    monkeypatch.setattr(psycopg2, "connect", REAL_CONNECT)
    base_engine = create_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_size=4,
        max_overflow=0,
        pool_pre_ping=True,
        pool_timeout=10,
        isolation_level="READ COMMITTED",
        connect_args={"sslmode": "require", "connect_timeout": 10},
    )
    schema = f"inventory_test_{uuid4().hex}"
    created = False
    try:
        with base_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        engine = base_engine.execution_options(schema_translate_map={None: schema})
        with engine.begin() as connection:
            SQLModel.metadata.create_all(connection)
        yield engine
    finally:
        if created:
            with base_engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        base_engine.dispose()


def test_postgres_constraints_and_warehouse_partition(postgres_inventory_engine):
    with Session(postgres_inventory_engine) as session:
        first = service.create_product(
            session, ProductCreate(name="Shoes", sku="SHOE-42", warehouse="Monterrey")
        )
        second = service.create_product(
            session, ProductCreate(name="Shoes", sku="SHOE-42", warehouse="Zaragoza")
        )
        assert first.id != second.id
        user_uuid = uuid4()
        for model in (InboundOrder, OutboundOrder):
            for product_id, quantity in [(uuid4(), 1), (first.id, 0), (first.id, -1)]:
                session.add(model(product_id=product_id, quantity=quantity, user_uuid=user_uuid))
                with pytest.raises(IntegrityError):
                    session.commit()
                session.rollback()
        session.add(Product(name="Duplicate", sku="SHOE-42", warehouse=Warehouse.MONTERREY))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert len(session.exec(select(Product)).all()) == 2


def test_postgres_concurrent_outbound_cannot_oversell(postgres_inventory_engine, monkeypatch):
    user_uuid = uuid4()
    with Session(postgres_inventory_engine) as session:
        created = service.create_product(
            session, ProductCreate(name="Shoes", sku="SHOE-42", warehouse="Monterrey")
        )
        service.create_inbound(
            session, InboundOrderCreate(product_id=created.id, quantity=10), user_uuid
        )
    first_checked = Event()
    second_lock_attempted = Event()
    release_first = Event()
    count_lock = Lock()
    checks = 0
    lock_queries = 0
    second_pid = None
    current_stock = service._current_stock

    def controlled_stock(session, product_id):
        nonlocal checks
        stock = current_stock(session, product_id)
        with count_lock:
            checks += 1
            position = checks
        if position == 1:
            first_checked.set()
            assert release_first.wait(15), "Timed out releasing the first withdrawal"
        return stock

    @event.listens_for(postgres_inventory_engine, "before_cursor_execute")
    def observe_lock(connection, cursor, statement, parameters, context, executemany):
        nonlocal lock_queries, second_pid
        if "FOR UPDATE" in statement:
            with count_lock:
                lock_queries += 1
                position = lock_queries
            if position == 2:
                second_pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
                second_lock_attempted.set()

    monkeypatch.setattr(service, "_current_stock", controlled_stock)

    def withdraw():
        with Session(postgres_inventory_engine) as session:
            try:
                service.create_outbound(
                    session, OutboundOrderCreate(product_id=created.id, quantity=7), user_uuid
                )
                return "created"
            except ValidationFailed:
                assert not session.in_transaction()
                return "rejected"

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(withdraw)
        second = None
        try:
            assert first_checked.wait(10)
            second = workers.submit(withdraw)
            assert second_lock_attempted.wait(10)
            blocked = False
            deadline = monotonic() + 5
            with postgres_inventory_engine.connect() as connection:
                while monotonic() < deadline:
                    if connection.execute(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid}
                    ).scalar_one():
                        blocked = True
                        break
            assert blocked, "The competing withdrawal did not wait on the product row lock"
        finally:
            release_first.set()
        assert first.result(timeout=10) == "created"
        assert second is not None and second.result(timeout=10) == "rejected"
    with Session(postgres_inventory_engine) as session:
        assert service.get_product(session, created.id).current_stock == 3
        assert len(session.exec(select(OutboundOrder)).all()) == 1


def test_postgres_api_uses_tinydb_uuid_and_computed_stock(
    postgres_inventory_engine, db_path, monkeypatch
):
    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)
    user = users_service.create_user(
        UserCreate(email="postgres-test@trackflow.com", password="inventory-password-42")
    )
    token = security.create_access_token(user_id=user.id, role=user.role.value)
    headers = {"Authorization": f"Bearer {token}"}

    def sessions():
        with Session(postgres_inventory_engine) as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = sessions
    try:
        with TestClient(app) as client:
            created = product(client, headers)
            assert movement(client, headers, "inbound", created["id"], 10).status_code == 201
            outbound = movement(client, headers, "outbound", created["id"], 3)
            assert outbound.status_code == 201
            assert movement(client, headers, "outbound", created["id"], 8).status_code == 400
            assert (
                client.get("/inventory/products", headers=headers).json()[0]["current_stock"] == 7
            )
            orders = client.get("/inventory/orders", headers=headers).json()
            assert len(orders) == 2
            # Movement confirmations no longer echo the actor (serialization audit,
            # 2026-10-12); the order history is where attribution is read back.
            assert all(
                row["user_uuid"] == user.id and row["product_sku"] == "SHOE-42" for row in orders
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_postgres_seed_is_reconciled_and_idempotent(
    postgres_inventory_engine, db_path, monkeypatch
):
    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)
    user = users_service.create_user(
        UserCreate(email="postgres-seed@trackflow.com", password="inventory-password-42")
    )
    report = seed_inventory(postgres_inventory_engine, UUID(user.id))
    assert (report.inserted_products, report.inserted_inbound, report.inserted_outbound) == (
        3,
        3,
        1,
    )
    assert report.current_stock == {
        "SHOE-BLK-42": 45,
        "LAPTOP-DELL-15": 8,
        "PERFUME-COCO-50": 120,
    }
    before = snapshot(postgres_inventory_engine)
    repeated = seed_inventory(postgres_inventory_engine, UUID(user.id))
    assert repeated.skipped_existing == 7
    assert (repeated.inserted_products, repeated.inserted_inbound, repeated.inserted_outbound) == (
        0,
        0,
        0,
    )
    assert snapshot(postgres_inventory_engine) == before
