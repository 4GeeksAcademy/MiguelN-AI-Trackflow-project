from datetime import datetime
from enum import Enum

from pydantic import BaseModel, EmailStr, Field, model_validator


class Country(str, Enum):
    USA = "USA"
    SPAIN = "Spain"


class Currency(str, Enum):
    USD = "USD"
    EUR = "EUR"


class SupplierStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class Category(str, Enum):
    CARRIER_LAST_MILE = "carrier_last_mile"
    CARRIER_INTERNATIONAL = "carrier_international"
    WAREHOUSE_SUPPLIES = "warehouse_supplies"
    PACKAGING_MATERIALS = "packaging_materials"
    REVERSE_LOGISTICS = "reverse_logistics"
    FLEET_MAINTENANCE = "fleet_maintenance"
    IT_AND_WMS_SOFTWARE = "it_and_wms_software"
    CLEANING_AND_FACILITIES = "cleaning_and_facilities"


CURRENCY_BY_COUNTRY = {
    Country.USA: Currency.USD,
    Country.SPAIN: Currency.EUR,
}


class SupplierBase(BaseModel):
    name: str = Field(min_length=1)
    country: Country
    categories: list[Category] = Field(min_length=1)
    rate_per_shipment: float = Field(gt=0)
    currency: Currency
    status: SupplierStatus
    service_zone: str | None = None
    contact_email: EmailStr | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def currency_matches_country(self) -> "SupplierBase":
        expected = CURRENCY_BY_COUNTRY[self.country]
        if self.currency is not expected:
            raise ValueError(
                f"{self.country.value} suppliers must use {expected.value}, got {self.currency.value}"
            )
        return self


class SupplierCreate(SupplierBase):
    """Client payload: updated_at is system-generated."""


class SupplierInDB(SupplierBase):
    updated_at: datetime


class SupplierResponse(SupplierInDB):
    id: int


class SupplierListItem(BaseModel):
    id: int
    name: str
    country: Country
    categories: list[Category]
    rate_per_shipment: float
    currency: Currency
    status: SupplierStatus
    service_zone: str | None = None


class RateUpdate(BaseModel):
    rate_per_shipment: float = Field(gt=0)


class StatusUpdate(BaseModel):
    status: SupplierStatus
