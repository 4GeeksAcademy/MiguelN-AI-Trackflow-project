import { parseResponseJson, requestAuthenticatedApi } from "@/lib/api-client";
import { track } from "@/services/telemetry-service";
import { normalizeSupplier, normalizeSupplierListItem, normalizeSuppliers } from "@/lib/supplier";
import type {
  Supplier,
  SupplierCreatePayload,
  SupplierFilters,
  SupplierListItem,
  SupplierStatus,
} from "@/types/supplier";

const JSON_HEADERS = { "Content-Type": "application/json" };

function buildSuppliersQuery(filters?: Partial<SupplierFilters>): string {
  const params = new URLSearchParams();

  if (filters?.country) {
    params.set("country", filters.country);
  }
  if (filters?.category) {
    params.set("category", filters.category);
  }

  const query = params.toString();
  return query ? `?${query}` : "";
}

export async function fetchSuppliers(filters?: Partial<SupplierFilters>): Promise<SupplierListItem[]> {
  const response = await requestAuthenticatedApi(`/suppliers${buildSuppliersQuery(filters)}`);
  const suppliers = normalizeSuppliers(await parseResponseJson(response));

  // The schema has no `country` filter key, so it is reported as `other`.
  const filterKeys = [filters?.country ? "other" : null, filters?.category ? "category" : null].filter(Boolean);
  if (filterKeys.length > 0) {
    track("list_filter_applied", { view: "suppliers_list", filter_keys: filterKeys, result_count: suppliers.length });
  }

  return suppliers;
}

export async function fetchSupplierById(id: string): Promise<Supplier> {
  const response = await requestAuthenticatedApi(`/suppliers/${encodeURIComponent(id)}`);

  return normalizeSupplier(await parseResponseJson(response));
}

export async function createSupplier(payload: SupplierCreatePayload): Promise<SupplierListItem> {
  const response = await requestAuthenticatedApi("/suppliers", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  });

  const created = normalizeSupplierListItem(await parseResponseJson(response));
  track("supplier_created", { supplier_id: created.id });

  return created;
}

export async function updateSupplierRate(id: number, ratePerShipment: number): Promise<Supplier> {
  const response = await requestAuthenticatedApi(`/suppliers/${id}/rate`, {
    method: "PATCH",
    headers: JSON_HEADERS,
    body: JSON.stringify({ rate_per_shipment: ratePerShipment }),
  });

  return normalizeSupplier(await parseResponseJson(response));
}

export async function updateSupplierStatus(id: number, status: SupplierStatus): Promise<Supplier> {
  const response = await requestAuthenticatedApi(`/suppliers/${id}/status`, {
    method: "PATCH",
    headers: JSON_HEADERS,
    body: JSON.stringify({ status }),
  });

  return normalizeSupplier(await parseResponseJson(response));
}
