from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlmodel import SQLModel

from services.inventory import schemas
from services.inventory.models import Product, Warehouse


def test_schema_classes_are_not_orm_models():
    for schema in (
        schemas.ProductCreate,
        schemas.ProductResponse,
        schemas.ProductAvailability,
        schemas.InboundOrderCreate,
        schemas.InboundOrderResponse,
        schemas.OutboundOrderCreate,
        schemas.InboundOrderConfirmation,
        schemas.OutboundOrderConfirmation,
        schemas.OrderHistoryItem,
        schemas.OutboundOrderResponse,
    ):
        assert not issubclass(schema, SQLModel)


def test_product_contract_uses_company_warehouses_and_strips_whitespace():
    product = schemas.ProductCreate(name=" Shoes ", sku=" SHOE-42 ", warehouse="Monterrey")
    assert product.name == "Shoes"
    assert product.sku == "SHOE-42"
    with pytest.raises(ValidationError):
        schemas.ProductCreate(name="Shoes", sku="SHOE-42", warehouse="Los Angeles")


@pytest.mark.parametrize("field", ["name", "sku"])
@pytest.mark.parametrize("value", ["", " \t\n ", 42])
def test_product_rejects_blank_or_nonstring_fields(field, value):
    fields = {"name": "Shoes", "sku": "SHOE-42", "warehouse": "Zaragoza", field: value}
    with pytest.raises(ValidationError):
        schemas.ProductCreate(**fields)


@pytest.mark.parametrize("field", ["id", "current_stock", "stockQuantity"])
def test_product_requests_cannot_assign_system_fields(field):
    with pytest.raises(ValidationError):
        schemas.ProductCreate(name="Shoes", sku="SHOE-42", warehouse="Zaragoza", **{field: 10})


@pytest.mark.parametrize("schema", [schemas.InboundOrderCreate, schemas.OutboundOrderCreate])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, 2.0, "2", 2_147_483_648])
def test_quantity_is_strictly_a_positive_integer(schema, value):
    with pytest.raises(ValidationError):
        schema(product_id=uuid4(), quantity=value)


@pytest.mark.parametrize("schema", [schemas.InboundOrderCreate, schemas.OutboundOrderCreate])
@pytest.mark.parametrize("field", ["id", "user_uuid", "created_at", "warehouse", "current_stock"])
def test_order_requests_cannot_forge_server_metadata(schema, field):
    with pytest.raises(ValidationError):
        schema(product_id=uuid4(), quantity=1, **{field: "forged"})


def test_product_response_requires_computed_stock():
    product = Product(name="Shoes", sku="SHOE-42", warehouse=Warehouse.MONTERREY)
    with pytest.raises(ValidationError):
        schemas.ProductResponse.model_validate(product)
    # The owning client is internal to reporting: the product contract does not carry it.
    response = schemas.ProductResponse(**product.model_dump(exclude={"client_id"}), current_stock=7)
    assert response.current_stock == 7
    assert "current_stock" not in Product.__table__.columns
    assert "client_id" not in schemas.ProductResponse.model_fields


def test_order_response_includes_product_user_and_utc_timestamp():
    product = schemas.ProductSummary(id=uuid4(), name="Shoes", sku="SHOE-42", warehouse="Zaragoza")
    user_uuid = uuid4()
    response = schemas.OutboundOrderResponse(
        id=uuid4(),
        product_id=product.id,
        quantity=1,
        created_at=datetime(2026, 10, 4),
        user_uuid=user_uuid,
        product=product,
    )
    assert response.direction == "outbound"
    assert response.user_uuid == user_uuid
    assert response.created_at.tzinfo is timezone.utc
    assert response.product.warehouse == "Zaragoza"


def test_movement_confirmation_contains_only_fields_the_form_uses():
    response = schemas.InboundOrderConfirmation(
        quantity=2,
        product_name="Shoes",
        warehouse="Zaragoza",
    )
    assert response.model_dump() == {
        "quantity": 2,
        "product_name": "Shoes",
        "warehouse": Warehouse.ZARAGOZA,
        "direction": "inbound",
    }
    with pytest.raises(ValidationError):
        schemas.InboundOrderConfirmation(
            quantity=2,
            product_name="Shoes",
            warehouse="Zaragoza",
            user_uuid=uuid4(),
        )
