import {
  ApiError,
  NetworkError,
  UnauthorizedError,
  extractApiFieldErrors,
  extractApiProblems,
  parseResponseJson,
  requestAuthenticatedApi,
} from "@/lib/api-client";
import type {
  InboundOrder, InventoryOrder, OrderCreate, OutboundOrder,
  Product, ProductAvailability, ProductCreate, ProductSummary, Warehouse,
} from "@/types/inventory";

export const INVENTORY_PATH = "/backoffice/inventory";
export const MAX_QUANTITY = 2_147_483_647;

class InventoryResponseError extends Error {}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nonempty(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isProductSummary(value: unknown): value is ProductSummary {
  return record(value) && nonempty(value.id) && nonempty(value.name) && nonempty(value.sku)
    && (value.warehouse === "Monterrey" || value.warehouse === "Zaragoza");
}

function isProduct(value: unknown): value is Product {
  return isProductSummary(value) && "current_stock" in value
    && typeof value.current_stock === "number"
    && Number.isSafeInteger(value.current_stock) && value.current_stock >= 0;
}

function isProductAvailability(value: unknown): value is ProductAvailability {
  return record(value) && nonempty(value.id)
    && typeof value.current_stock === "number"
    && Number.isSafeInteger(value.current_stock) && value.current_stock >= 0;
}

function isOrder(value: unknown): value is InventoryOrder {
  return record(value) && nonempty(value.id) && nonempty(value.product_id)
    && nonempty(value.user_uuid) && nonempty(value.created_at)
    && Number.isFinite(Date.parse(value.created_at))
    && typeof value.quantity === "number" && Number.isInteger(value.quantity)
    && value.quantity > 0 && value.quantity <= MAX_QUANTITY
    && nonempty(value.product_name) && nonempty(value.product_sku)
    && (value.warehouse === "Monterrey" || value.warehouse === "Zaragoza")
    && (value.direction === "inbound" || value.direction === "outbound");
}

function isMovementConfirmation(value: unknown): value is InboundOrder | OutboundOrder {
  return record(value)
    && typeof value.quantity === "number" && Number.isInteger(value.quantity)
    && value.quantity > 0 && value.quantity <= MAX_QUANTITY
    && nonempty(value.product_name)
    && (value.warehouse === "Monterrey" || value.warehouse === "Zaragoza")
    && (value.direction === "inbound" || value.direction === "outbound");
}

async function inventoryRequest<T>(
  path: string, check: (value: unknown) => value is T, init?: RequestInit,
): Promise<T> {
  const response = await requestAuthenticatedApi(path, { cache: "no-store", ...init });
  const value = await parseResponseJson(response);
  if (!check(value)) {
    throw new InventoryResponseError("The inventory service returned an unreadable response. Please try again.");
  }
  return value;
}

function query(filters: Record<string, string | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  return params.size ? `?${params}` : "";
}

function post(body: ProductCreate | OrderCreate): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export function getProducts(filters: { warehouse?: Warehouse; sku?: string } = {}): Promise<Product[]> {
  return inventoryRequest(`/inventory/products${query(filters)}`,
    (value): value is Product[] => Array.isArray(value) && value.every(isProduct));
}

export function getProduct(id: string): Promise<ProductAvailability> {
  return inventoryRequest(`/inventory/products/${encodeURIComponent(id)}`, isProductAvailability);
}

export function createProduct(payload: ProductCreate): Promise<Product> {
  return inventoryRequest("/inventory/products", isProduct, post(payload));
}

export function createInboundOrder(payload: OrderCreate): Promise<InboundOrder> {
  return inventoryRequest("/inventory/products/inbound",
    (value): value is InboundOrder => isMovementConfirmation(value) && value.direction === "inbound", post(payload));
}

export function createOutboundOrder(payload: OrderCreate): Promise<OutboundOrder> {
  return inventoryRequest("/inventory/products/outbound",
    (value): value is OutboundOrder => isMovementConfirmation(value) && value.direction === "outbound", post(payload));
}

export function getOrders(filters: { warehouse?: Warehouse; product_id?: string } = {}): Promise<InventoryOrder[]> {
  return inventoryRequest(`/inventory/orders${query(filters)}`,
    (value): value is InventoryOrder[] => Array.isArray(value) && value.every(isOrder));
}

export function stockLevel(stock: number): "Out of stock" | "Low stock" | "Healthy stock" {
  // Display-only thresholds: zero is empty, 1-9 units are low, and 10+ are healthy.
  return stock === 0 ? "Out of stock" : stock < 10 ? "Low stock" : "Healthy stock";
}

export function quantityError(value: string): string | null {
  const quantity = Number(value);
  return !value.trim() || !Number.isInteger(quantity) || quantity < 1 || quantity > MAX_QUANTITY
    ? `Enter a whole number from 1 to ${MAX_QUANTITY.toLocaleString("en-US")}.` : null;
}

export function describeInventoryError(error: unknown): { message: string; fieldErrors: Record<string, string> } {
  const fallback = "The inventory service is having trouble. Please try again, and contact TrackFlow Tech if it continues.";
  if (error instanceof UnauthorizedError) {
    return { message: "Your session has expired. Please sign in again.", fieldErrors: {} };
  }
  if (error instanceof NetworkError) {
    return { message: "Could not reach the inventory service. Check your connection and try again.", fieldErrors: {} };
  }
  if (error instanceof InventoryResponseError) {
    return { message: error.message, fieldErrors: {} };
  }
  if (!(error instanceof ApiError)) return { message: fallback, fieldErrors: {} };

  let problems = extractApiProblems(error.payload);
  if (!problems.length) problems = extractApiFieldErrors(error.payload);
  if (!problems.length && record(error.payload) && nonempty(error.payload.detail)) {
    problems = [{ field: "", message: error.payload.detail.trim() }];
  }
  problems = problems.filter(({ message }) => message.length <= 300
    && !/traceback|\bat [\w$.]+ \(|^\w*(Error|Exception):|\.(py|tsx?|js):\d+|<\/?[a-z]+[\s>]/i.test(message));
  const fieldErrors: Record<string, string> = {};
  for (const problem of problems) {
    if (problem.field && !Object.hasOwn(fieldErrors, problem.field)) fieldErrors[problem.field] = problem.message;
  }
  return { message: problems.find(({ field }) => !field)?.message ?? problems[0]?.message ?? fallback, fieldErrors };
}