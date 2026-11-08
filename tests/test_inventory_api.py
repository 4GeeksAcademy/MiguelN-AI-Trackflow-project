from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from services.core import security
from services.inventory.database import get_db
from services.inventory.models import InboundOrder, OutboundOrder
from services.main import app
from services.users import service as users_service
from services.users.models import UserUpdate


@pytest.fixture
def inventory_api(db_path, tmp_path, monkeypatch):
    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'inventory.sqlite'}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)

    def sessions():
        with Session(engine) as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = sessions
    try:
        with TestClient(app) as client:
            credentials = {"email": "inventory@trackflow.com", "password": "inventory-password-42"}
            registered = client.post("/users", json=credentials)
            assert registered.status_code == 201
            token = client.post("/auth/login", json=credentials).json()["access_token"]
            yield client, {"Authorization": f"Bearer {token}"}, registered.json()["id"], engine
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()


def product(client, headers, warehouse="Monterrey", sku="SHOE-42"):
    response = client.post(
        "/inventory/products",
        headers=headers,
        json={
            "name": "Shoes",
            "sku": sku,
            "warehouse": warehouse,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def movement(client, headers, direction, product_id, quantity):
    return client.post(
        f"/inventory/products/{direction}",
        headers=headers,
        json={
            "product_id": product_id,
            "quantity": quantity,
        },
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/inventory/products"),
        ("POST", "/inventory/products"),
        ("GET", f"/inventory/products/{uuid4()}"),
        ("POST", "/inventory/products/inbound"),
        ("POST", "/inventory/products/outbound"),
        ("GET", "/inventory/orders"),
    ],
)
def test_all_six_routes_require_auth(inventory_api, method, path):
    client, _, _, _ = inventory_api
    response = client.request(method, path, json={})
    assert response.status_code == 401


def test_empty_inventory_and_zero_start(inventory_api):
    client, headers, _, engine = inventory_api
    assert client.get("/inventory/products", headers=headers).json() == []
    assert client.get("/inventory/orders", headers=headers).json() == []
    created = product(client, headers)
    assert created["current_stock"] == 0
    assert client.get(f"/inventory/products/{created['id']}", headers=headers).json() == {
        "id": created["id"],
        "current_stock": 0,
    }
    with Session(engine) as session:
        assert session.exec(select(InboundOrder)).all() == []
        assert session.exec(select(OutboundOrder)).all() == []


def test_stock_is_net_history_not_join_multiplication(inventory_api):
    client, headers, user_uuid, _ = inventory_api
    created = product(client, headers)
    for direction, quantity in [("inbound", 10), ("inbound", 5), ("outbound", 2), ("outbound", 3)]:
        response = movement(client, headers, direction, created["id"], quantity)
        assert response.status_code == 201, response.text
        assert response.json() == {
            "quantity": quantity,
            "product_name": "Shoes",
            "warehouse": "Monterrey",
            "direction": direction,
        }
    assert client.get("/inventory/products", headers=headers).json()[0]["current_stock"] == 10
    assert (
        client.get(f"/inventory/products/{created['id']}", headers=headers).json()["current_stock"]
        == 10
    )
    orders = client.get("/inventory/orders", headers=headers).json()
    assert len(orders) == 4
    assert {row["direction"] for row in orders} == {"inbound", "outbound"}
    assert all(
        row["user_uuid"] == user_uuid
        and row["product_id"] == created["id"]
        and row["product_sku"] == "SHOE-42"
        and row["product_name"] == "Shoes"
        for row in orders
    )
    assert [row["created_at"] for row in orders] == sorted(
        (row["created_at"] for row in orders), reverse=True
    )


def test_negative_outbound_is_rejected_without_write_and_exact_depletion_allowed(inventory_api):
    client, headers, _, engine = inventory_api
    created = product(client, headers)
    assert movement(client, headers, "outbound", created["id"], 1).status_code == 400
    assert movement(client, headers, "inbound", created["id"], 5).status_code == 201
    response = movement(client, headers, "outbound", created["id"], 6)
    assert response.status_code == 400
    assert response.json()["detail"] == {
        "field": "quantity",
        "message": "Insufficient stock in Monterrey: 5 units available, 6 requested.",
    }
    with Session(engine) as session:
        assert session.exec(select(OutboundOrder)).all() == []
    assert movement(client, headers, "outbound", created["id"], 5).status_code == 201
    assert (
        client.get(f"/inventory/products/{created['id']}", headers=headers).json()["current_stock"]
        == 0
    )


def test_warehouse_scope_and_duplicate_sku(inventory_api):
    client, headers, _, _ = inventory_api
    monterrey = product(client, headers)
    zaragoza = product(client, headers, warehouse="Zaragoza")
    duplicate = client.post(
        "/inventory/products",
        headers=headers,
        json={
            "name": "Duplicate",
            "sku": "SHOE-42",
            "warehouse": "Monterrey",
        },
    )
    assert duplicate.status_code == 409
    assert movement(client, headers, "inbound", monterrey["id"], 10).status_code == 201
    assert movement(client, headers, "outbound", zaragoza["id"], 1).status_code == 400
    assert client.get("/inventory/products?warehouse=Zaragoza", headers=headers).json() == [
        zaragoza
    ]
    assert client.get("/inventory/products?sku=missing", headers=headers).json() == []
    assert len(client.get("/inventory/products?sku=SHOE-42", headers=headers).json()) == 2
    assert client.get("/inventory/orders?warehouse=Zaragoza", headers=headers).json() == []
    assert (
        len(client.get(f"/inventory/orders?product_id={monterrey['id']}", headers=headers).json())
        == 1
    )


@pytest.mark.parametrize("direction", ["inbound", "outbound"])
def test_unknown_product_is_404(inventory_api, direction):
    client, headers, _, _ = inventory_api
    assert client.get(f"/inventory/products/{uuid4()}", headers=headers).status_code == 404
    assert movement(client, headers, direction, str(uuid4()), 1).status_code == 404


@pytest.mark.parametrize("direction", ["inbound", "outbound"])
@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, "2", 2_147_483_648])
def test_order_quantity_validation_is_422(inventory_api, direction, quantity):
    client, headers, _, _ = inventory_api
    created = product(client, headers)
    assert movement(client, headers, direction, created["id"], quantity).status_code == 422
    assert client.get("/inventory/orders", headers=headers).json() == []


def test_system_fields_cannot_be_forged(inventory_api):
    client, headers, _, _ = inventory_api
    response = client.post(
        "/inventory/products",
        headers=headers,
        json={
            "name": "Shoes",
            "sku": "SHOE-42",
            "warehouse": "Monterrey",
            "current_stock": 100,
        },
    )
    assert response.status_code == 422
    created = product(client, headers)
    for direction in ("inbound", "outbound"):
        response = client.post(
            f"/inventory/products/{direction}",
            headers=headers,
            json={
                "product_id": created["id"],
                "quantity": 1,
                "user_uuid": str(uuid4()),
            },
        )
        assert response.status_code == 422
    assert client.get("/inventory/orders", headers=headers).json() == []


def test_inactive_user_is_refused(inventory_api):
    client, headers, user_uuid, _ = inventory_api
    users_service.update_user(user_uuid, UserUpdate(is_active=False))
    assert client.get("/inventory/products", headers=headers).status_code == 403


def test_openapi_exposes_pydantic_contracts_not_tables(inventory_api):
    client, _, _, _ = inventory_api
    specification = client.get("/openapi.json").json()
    paths = {
        path: set(specification["paths"][path])
        for path in specification["paths"]
        if path.startswith("/inventory")
    }
    assert paths == {
        "/inventory/products": {"get", "post"},
        "/inventory/products/{id}": {"get"},
        "/inventory/products/inbound": {"post"},
        "/inventory/products/outbound": {"post"},
        "/inventory/orders": {"get"},
    }
    contracts = specification["components"]["schemas"]
    assert "current_stock" in contracts["ProductResponse"]["properties"]
    assert "current_stock" not in contracts["ProductCreate"]["properties"]
    assert set(contracts["ProductAvailability"]["properties"]) == {"id", "current_stock"}
    assert set(contracts["InboundOrderConfirmation"]["properties"]) == {
        "quantity", "product_name", "warehouse", "direction"
    }
    assert "product" not in contracts["OrderHistoryItem"]["properties"]
    assert "user_uuid" in contracts["OrderHistoryItem"]["properties"]
    assert "Product" not in contracts
