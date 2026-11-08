import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import ValidationError
from tinydb.table import Document

from services.auth.dependencies import get_current_user
from services.suppliers.db import get_table
from services.suppliers.models import (
    Category,
    Country,
    RateUpdate,
    StatusUpdate,
    SupplierCreate,
    SupplierInDB,
    SupplierListItem,
    SupplierResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/suppliers",
    tags=["suppliers"],
    # Rates, margins and contact emails are commercially sensitive, so every
    # supplier route - read included - requires a valid token.
    dependencies=[Depends(get_current_user)],
)


def to_response(doc: Document) -> SupplierResponse:
    return SupplierResponse(id=doc.doc_id, **doc)


def get_document_or_404(supplier_id: int) -> Document:
    doc = get_table().get(doc_id=supplier_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Supplier {supplier_id} not found",
        )
    return doc


@router.post("", response_model=SupplierListItem, status_code=status.HTTP_201_CREATED)
def create_supplier(payload: SupplierCreate) -> SupplierListItem:
    record = SupplierInDB(**payload.model_dump(), updated_at=datetime.now(timezone.utc))
    doc_id = get_table().insert(record.model_dump(mode="json"))
    supplier = SupplierResponse(id=doc_id, **record.model_dump())
    return SupplierListItem.model_validate(supplier.model_dump())


@router.get("", response_model=list[SupplierListItem])
def list_suppliers(
    country: Country | None = Query(default=None),
    category: Category | None = Query(default=None),
) -> list[SupplierListItem]:
    docs = get_table().all()
    if country is not None:
        docs = [doc for doc in docs if doc.get("country") == country.value]
    if category is not None:
        docs = [doc for doc in docs if category.value in doc.get("categories", [])]

    # A stored row that no longer fits the model is skipped and logged rather
    # than failing the whole directory with a 500.
    suppliers: list[SupplierListItem] = []
    for doc in docs:
        try:
            supplier = to_response(doc)
            suppliers.append(SupplierListItem.model_validate(supplier.model_dump()))
        except ValidationError:
            logger.warning(
                "Skipping stored supplier %s: it no longer matches the model", doc.doc_id
            )
    return suppliers


@router.get("/{supplier_id}", response_model=SupplierResponse)
def get_supplier(supplier_id: int) -> SupplierResponse:
    return to_response(get_document_or_404(supplier_id))


@router.patch("/{supplier_id}/rate", response_model=SupplierResponse)
def update_rate(supplier_id: int, payload: RateUpdate) -> SupplierResponse:
    get_document_or_404(supplier_id)
    get_table().update(
        {
            "rate_per_shipment": payload.rate_per_shipment,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        doc_ids=[supplier_id],
    )
    return to_response(get_document_or_404(supplier_id))


@router.patch("/{supplier_id}/status", response_model=SupplierResponse)
def update_status(supplier_id: int, payload: StatusUpdate) -> SupplierResponse:
    get_document_or_404(supplier_id)
    get_table().update({"status": payload.status.value}, doc_ids=[supplier_id])
    return to_response(get_document_or_404(supplier_id))


@router.delete(
    "/{supplier_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT
)
def delete_supplier(supplier_id: int) -> Response:
    get_document_or_404(supplier_id)
    get_table().remove(doc_ids=[supplier_id])
    return Response(status_code=status.HTTP_204_NO_CONTENT)
