import { ApiError } from "@/lib/api-client";
import type { OrderDirection, Product } from "@/types/inventory";
import { track } from "@/services/telemetry-service";

type Band = "out_of_stock" | "low" | "healthy";

function band(stock: number): Band {
  // Same display thresholds as `stockLevel` in lib/inventory.ts.
  return stock === 0 ? "out_of_stock" : stock < 10 ? "low" : "healthy";
}

type RejectionReason = "insufficient_stock" | "product_not_found" | "invalid_quantity" | "malformed_payload";

export function trackOrderCreated(
  direction: OrderDirection,
  product: Product,
  quantity: number,
  stockBefore: number | undefined,
): void {
  const base = { product_id: product.id, sku: product.sku, warehouse: product.warehouse, quantity };

  if (direction === "inbound") {
    track("inbound_order_created", { ...base, stock_after: product.current_stock + quantity });
    return;
  }

  if (stockBefore === undefined) return;

  const stockAfter = Math.max(0, stockBefore - quantity);
  track("outbound_order_created", {
    ...base,
    stock_before: stockBefore,
    stock_after: stockAfter,
    drained_to_zero: stockAfter === 0,
  });

  const previousBand = band(stockBefore);
  const newBand = band(stockAfter);
  if (newBand !== "healthy" && previousBand !== newBand && previousBand !== "out_of_stock") {
    track("stock_threshold_triggered", {
      product_id: product.id,
      sku: product.sku,
      warehouse: product.warehouse,
      stock_level_after: stockAfter,
      threshold_band: newBand,
      threshold_max_units: newBand === "out_of_stock" ? 0 : 9,
      previous_band: previousBand,
    });
  }
}

export function trackOrderRejected(
  direction: OrderDirection,
  product: Product | null,
  reason: RejectionReason,
  field: "quantity" | "product_id" | "other",
  requested: number | null,
  available: number | null,
): void {
  track("order_validation_failed", {
    order_direction: direction,
    rejection_reason: reason,
    field,
    product_id: product?.id ?? null,
    sku: product?.sku ?? null,
    warehouse: product?.warehouse ?? null,
    requested_quantity: requested !== null && Number.isInteger(requested) ? requested : null,
    available_stock: available,
    shortfall: reason === "insufficient_stock" && requested !== null && available !== null && requested > available ? requested - available : null,
  });
}

/** Maps a refused order request to the schema's rejection vocabulary. */
export function trackServerRejection(
  direction: OrderDirection,
  failure: unknown,
  product: Product | null,
  requested: number,
  available: number | null,
): void {
  if (!(failure instanceof ApiError)) return;

  if (failure.status === 404) {
    trackOrderRejected(direction, product, "product_not_found", "product_id", requested, null);
  } else if (failure.status === 400) {
    trackOrderRejected(direction, product, "insufficient_stock", "quantity", requested, available);
  } else if (failure.status === 422) {
    trackOrderRejected(direction, product, "malformed_payload", "other", requested, available);
  }
}

export function trackStockAvailability(
  context: "outbound_form" | "product_list",
  product: Pick<Product, "id" | "warehouse"> | null,
  stock: number | null,
  startedAt: number,
): void {
  if (!product) return;

  track("stock_availability_checked", {
    product_id: product.id,
    warehouse: product.warehouse,
    context,
    outcome: stock === null ? "error" : "ok",
    stock_returned: stock,
    latency_ms: Math.max(0, performance.now() - startedAt),
  });
}
