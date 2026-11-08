import {
  CURRENCY_BY_COUNTRY,
  SUPPLIER_CATEGORIES,
  SUPPLIER_COUNTRIES,
  SUPPLIER_STATUSES,
  type Supplier,
  type SupplierCategory,
  type SupplierCountry,
  type SupplierCreatePayload,
  type SupplierCurrency,
  type SupplierFormValues,
  type SupplierListItem,
  type SupplierStatus,
} from "@/types/supplier";

const CATEGORY_LABELS: Record<SupplierCategory, string> = {
  carrier_last_mile: "Last-mile carrier",
  carrier_international: "International carrier",
  warehouse_supplies: "Warehouse supplies",
  packaging_materials: "Packaging materials",
  reverse_logistics: "Reverse logistics",
  fleet_maintenance: "Fleet maintenance",
  it_and_wms_software: "IT & WMS software",
  cleaning_and_facilities: "Cleaning & facilities",
};

const STATUS_LABELS: Record<SupplierStatus, string> = {
  active: "Active",
  suspended: "Suspended",
};

export function formatCategoryLabel(category: SupplierCategory): string {
  return CATEGORY_LABELS[category];
}

export function formatCategoriesLabel(categories: SupplierCategory[]): string {
  if (categories.length === 0) {
    return "-";
  }

  return categories.map(formatCategoryLabel).join(", ");
}

export function formatStatusLabel(status: SupplierStatus): string {
  return STATUS_LABELS[status];
}

export function formatRate(rate: number, currency: SupplierCurrency): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(rate);
}

export function formatRatePerShipment(supplier: Supplier): string {
  return `${formatRate(supplier.rate_per_shipment, supplier.currency)} / shipment`;
}

export function formatUpdatedAt(updatedAt: string): string {
  const parsed = new Date(updatedAt);
  if (Number.isNaN(parsed.getTime())) {
    return updatedAt;
  }

  return parsed.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
}

export function currencyForCountry(country: SupplierCountry): SupplierCurrency {
  return CURRENCY_BY_COUNTRY[country];
}

export function isSupplierCountry(value: string): value is SupplierCountry {
  return (SUPPLIER_COUNTRIES as readonly string[]).includes(value);
}

export function isSupplierCategory(value: string): value is SupplierCategory {
  return (SUPPLIER_CATEGORIES as readonly string[]).includes(value);
}

export function isSupplierStatus(value: string): value is SupplierStatus {
  return (SUPPLIER_STATUSES as readonly string[]).includes(value);
}

export function emptySupplierFormValues(): SupplierFormValues {
  return {
    name: "",
    country: "USA",
    categories: [],
    ratePerShipment: "",
    status: "active",
    serviceZone: "",
    contactEmail: "",
    notes: "",
  };
}

export function parseRateInput(value: string): number | null {
  const parsed = Number(value.trim());
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return null;
  }

  return parsed;
}

export function validateSupplierForm(values: SupplierFormValues): string | null {
  if (!values.name.trim()) {
    return "Name is required.";
  }

  if (values.categories.length === 0) {
    return "Select at least one category.";
  }

  if (parseRateInput(values.ratePerShipment) === null) {
    return "Rate per shipment must be a number greater than 0.";
  }

  const email = values.contactEmail.trim();
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return "Contact email is not a valid email address.";
  }

  return null;
}

/** Assumes `validateSupplierForm` already returned null. */
export function buildSupplierPayload(values: SupplierFormValues): SupplierCreatePayload {
  const serviceZone = values.serviceZone.trim();
  const contactEmail = values.contactEmail.trim();
  const notes = values.notes.trim();

  return {
    name: values.name.trim(),
    country: values.country,
    categories: values.categories,
    rate_per_shipment: parseRateInput(values.ratePerShipment) ?? 0,
    currency: currencyForCountry(values.country),
    status: values.status,
    service_zone: serviceZone || null,
    contact_email: contactEmail || null,
    notes: notes || null,
  };
}

function toNullableString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

/** Trusts the verified API contract but guards against a malformed payload reaching the UI. */
export function normalizeSupplier(payload: unknown): Supplier {
  if (typeof payload !== "object" || payload === null) {
    throw new Error("Unexpected supplier payload received from the API");
  }

  const record = payload as Record<string, unknown>;
  const country = typeof record.country === "string" && isSupplierCountry(record.country) ? record.country : "USA";
  const status = typeof record.status === "string" && isSupplierStatus(record.status) ? record.status : "active";
  const categories = Array.isArray(record.categories)
    ? record.categories.filter(
        (item): item is SupplierCategory => typeof item === "string" && isSupplierCategory(item),
      )
    : [];

  return {
    id: typeof record.id === "number" ? record.id : Number(record.id),
    name: typeof record.name === "string" ? record.name : "Unnamed supplier",
    country,
    categories,
    rate_per_shipment: typeof record.rate_per_shipment === "number" ? record.rate_per_shipment : 0,
    currency: record.currency === "USD" || record.currency === "EUR" ? record.currency : currencyForCountry(country),
    status,
    service_zone: toNullableString(record.service_zone),
    contact_email: toNullableString(record.contact_email),
    notes: toNullableString(record.notes),
    updated_at: typeof record.updated_at === "string" ? record.updated_at : "",
  };
}

export function normalizeSupplierListItem(payload: unknown): SupplierListItem {
  const supplier = normalizeSupplier(payload);
  return supplierListItemFromSupplier(supplier);
}

export function supplierListItemFromSupplier(supplier: Supplier): SupplierListItem {
  return {
    id: supplier.id,
    name: supplier.name,
    country: supplier.country,
    categories: supplier.categories,
    rate_per_shipment: supplier.rate_per_shipment,
    currency: supplier.currency,
    status: supplier.status,
    service_zone: supplier.service_zone,
  };
}

export function normalizeSuppliers(payload: unknown): SupplierListItem[] {
  if (!Array.isArray(payload)) {
    return [];
  }

  return payload.map(normalizeSupplierListItem);
}
