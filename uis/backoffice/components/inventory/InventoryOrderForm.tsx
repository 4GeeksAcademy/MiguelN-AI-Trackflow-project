"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { StateMessage } from "@/components/common/StateMessage";
import { InventoryPageShell, inventoryButtonClass, inventoryControlClass } from "@/components/inventory/InventoryPageShell";
import { useLocationSearch } from "@/hooks/useLocationSearch";
import { useFlowTelemetry } from "@/hooks/useFlowTelemetry";
import { ApiError } from "@/lib/api-client";
import { createInboundOrder, createOutboundOrder, describeInventoryError, getProduct, getProducts, INVENTORY_PATH, MAX_QUANTITY, quantityError } from "@/lib/inventory";
import { trackOrderCreated, trackOrderRejected, trackServerRejection, trackStockAvailability } from "@/lib/inventory-telemetry";
import type { OrderDirection, Product } from "@/types/inventory";

export function InventoryOrderForm({ direction }: { direction: OrderDirection }) {
  const outbound = direction === "outbound";
  const label = outbound ? "Outbound" : "Inbound";
  const flow = useFlowTelemetry(outbound ? "outbound_order" : "inbound_order", outbound ? "inventory_outbound_order" : "inventory_inbound_order");
  const search = useLocationSearch();
  const initialProduct = new URLSearchParams(search ?? "").get("product_id") ?? "";
  const [products, setProducts] = useState<Product[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [productId, setProductId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [stockAttempt, setStockAttempt] = useState(0);
  const [stock, setStock] = useState<{ productId: string; attempt: number; value?: number; error?: string } | null>(null);
  const selectionTouched = useRef(false);
  const submitting = useRef(false);
  const currentStock = stock?.productId === productId && stock.attempt === stockAttempt ? stock : null;
  const available = currentStock?.value;
  const exceedsStock = outbound && available !== undefined && Number(quantity) > available;
  const quantityMessage = fieldErrors.quantity || (exceedsStock ? `Only ${available?.toLocaleString()} units are available. Reduce the quantity.` : null);
  const productsRef = useRef<Product[] | null>(null);

  useEffect(() => {
    productsRef.current = products;
  }, [products]);

  useEffect(() => {
    if (!outbound || !productId) return;
    let active = true;
    const startedAt = performance.now();
    const product = productsRef.current?.find(({ id }) => id === productId) ?? null;
    getProduct(productId).then((fetched) => {
      trackStockAvailability("outbound_form", product, fetched.current_stock, startedAt);
      if (active) setStock({ productId, attempt: stockAttempt, value: fetched.current_stock });
    }).catch((failure: unknown) => {
      trackStockAvailability("outbound_form", product, null, startedAt);
      if (active) setStock({ productId, attempt: stockAttempt, error: describeInventoryError(failure).message });
    });
    return () => { active = false; };
  }, [outbound, productId, stockAttempt]);

  useEffect(() => {
    let active = true;
    getProducts().then((items) => {
      if (!active) return;
      setProducts(items);
      if (!selectionTouched.current && initialProduct) {
        if (items.some(({ id }) => id === initialProduct)) setProductId(initialProduct);
        else setFieldErrors({ product_id: "That product is not available. Select a product from the list." });
      }
    }).catch((failure: unknown) => {
      if (active) setListError(describeInventoryError(failure).message);
    });
    return () => { active = false; };
  }, [initialProduct, attempt]);

  function retryProducts() {
    setProducts(null);
    setListError(null);
    setAttempt((current) => current + 1);
  }

  function selectProduct(value: string) {
    selectionTouched.current = true;
    flow.fieldEdited("product_id");
    setProductId(value);
    setQuantity("");
    setStock(null);
    setFieldErrors({});
    setFormError(null);
    setConfirmation(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    const errors: Record<string, string> = {};
    if (!products?.some(({ id }) => id === productId)) errors.product_id = "Select a product.";
    const invalidQuantity = quantityError(quantity);
    if (invalidQuantity) errors.quantity = invalidQuantity;
    if (outbound && available === undefined) errors.quantity = "Wait for current stock to load before submitting.";
    if (exceedsStock) errors.quantity = `Only ${available?.toLocaleString()} units are available. Reduce the quantity.`;
    setFieldErrors(errors);
    setFormError(null);
    setConfirmation(null);
    if (Object.keys(errors).length) {
      const selected = products?.find(({ id }) => id === productId) ?? null;
      const requested = Number(quantity);
      if (errors.product_id) {
        trackOrderRejected(direction, null, "malformed_payload", "product_id", null, null);
        flow.validationFailed("product_id", "required");
      }
      if (errors.quantity) {
        const reason = exceedsStock ? "insufficient_stock" : "invalid_quantity";
        trackOrderRejected(direction, selected, reason, "quantity", Number.isFinite(requested) ? requested : null, available ?? null);
        flow.validationFailed("quantity", exceedsStock ? "exceeds_available_stock" : invalidQuantity && !quantity.trim() ? "required" : "out_of_range");
      }
      flow.submitFailed();
      return;
    }

    submitting.current = true;
    setIsSubmitting(true);
    flow.submitAttempted();
    const selected = products?.find(({ id }) => id === productId) ?? null;
    try {
      const createOrder = outbound ? createOutboundOrder : createInboundOrder;
      const created = await createOrder({ product_id: productId, quantity: Number(quantity) });
      if (selected) trackOrderCreated(direction, selected, Number(quantity), available);
      flow.completed();
      selectionTouched.current = true;
      setProductId("");
      setQuantity("");
      setStock(null);
      setFieldErrors({});
      setConfirmation(`${label} order registered: ${created.quantity.toLocaleString()} units of ${created.product_name} (${created.warehouse}).`);
    } catch (failure) {
      flow.submitFailed();
      trackServerRejection(direction, failure, selected, Number(quantity), available ?? null);
      const described = describeInventoryError(failure);
      if (outbound && failure instanceof ApiError && failure.status === 400) {
        described.fieldErrors.quantity = described.fieldErrors.quantity || described.message;
        setStockAttempt((current) => current + 1);
      }
      setFieldErrors(described.fieldErrors);
      setFormError(described.fieldErrors.quantity || described.fieldErrors.product_id ? null : described.message);
    } finally {
      submitting.current = false;
      setIsSubmitting(false);
    }
  }

  return (
    <InventoryPageShell title={`${label} order`}>
      <div className="mb-6 flex flex-wrap gap-3">
        <Link className={inventoryButtonClass} href={`${INVENTORY_PATH}/products`}>Products</Link>
        <Link className={inventoryButtonClass} href={`${INVENTORY_PATH}/orders`}>Orders history</Link>
      </div>
      <div className="max-w-xl">
        {listError ? <div className="mb-5" role="alert"><StateMessage tone="error">{listError}<div className="mt-3"><button className={inventoryButtonClass} onClick={retryProducts}>Try again</button></div></StateMessage></div> : null}
        {!products && !listError ? <p className="mb-5 text-sm text-slate-600" role="status">Loading products...</p> : null}
        {products?.length === 0 ? <StateMessage tone="info">No products are available for an order.</StateMessage> : null}
        {confirmation ? <div className="mb-5" role="status"><StateMessage tone="success">{confirmation}</StateMessage></div> : null}
        {formError ? <div className="mb-5" role="alert"><StateMessage tone="error">{formError}</StateMessage></div> : null}
        <form className="grid gap-5" onSubmit={submit} noValidate aria-busy={isSubmitting}>
          <div>
            <label htmlFor="product" className="mb-2 block text-sm font-semibold">Product</label>
            <select id="product" value={productId} onChange={(event) => selectProduct(event.target.value)} required disabled={isSubmitting || !products?.length} className={inventoryControlClass} aria-invalid={Boolean(fieldErrors.product_id)} aria-describedby={fieldErrors.product_id ? "product-error" : undefined}>
              <option value="">Select a product</option>
              {products?.map((product) => <option key={product.id} value={product.id}>{product.name} / {product.sku} / {product.warehouse}</option>)}
            </select>
            {fieldErrors.product_id ? <p id="product-error" role="alert" className="mt-2 text-sm text-red-700">{fieldErrors.product_id}</p> : null}
          </div>
          {outbound ? (
            <div className="border-y border-[color:var(--border-soft)] py-4" aria-live="polite">
              <p className="text-sm font-semibold">Current stock</p>
              {!productId ? <p className="mt-1 text-sm text-slate-600">Select a product.</p>
                : currentStock?.error ? <div role="alert"><p className="mt-1 text-sm text-red-700">{currentStock.error}</p><button type="button" className={`${inventoryButtonClass} mt-3`} onClick={() => setStockAttempt((current) => current + 1)}>Try again</button></div>
                : available === undefined ? <p className="mt-1 text-sm text-slate-600">Loading current stock...</p>
                : <p className="mt-1 text-lg font-semibold tabular-nums">{available.toLocaleString()} <span className="text-sm font-normal text-slate-600">units available</span>{available === 0 ? <span className="mt-1 block text-sm text-red-700">Out of stock</span> : null}</p>}
            </div>
          ) : null}
          <div>
            <label htmlFor="quantity" className="mb-2 block text-sm font-semibold">Quantity</label>
            <input id="quantity" type="number" inputMode="numeric" min={1} max={MAX_QUANTITY} step={1} required value={quantity} disabled={isSubmitting || !products?.length || (outbound && available === undefined)} onChange={(event) => {
              flow.fieldEdited("quantity");
              setQuantity(event.target.value);
              setFieldErrors((current) => ({ ...current, quantity: "" }));
              setFormError(null);
              setConfirmation(null);
            }} className={inventoryControlClass} aria-invalid={Boolean(quantityMessage)} aria-describedby={quantityMessage ? "quantity-error" : undefined} />
            {quantityMessage ? <p id="quantity-error" role="alert" className="mt-2 text-sm text-red-700">{quantityMessage}</p> : null}
          </div>
          <button type="submit" disabled={isSubmitting || !products?.length || exceedsStock || (outbound && (available === undefined || available === 0))} className={`${inventoryButtonClass} justify-self-start border-transparent bg-[color:var(--brand-primary)] text-white hover:bg-blue-700`}>
            {isSubmitting ? "Registering..." : `Register ${direction} order`}
          </button>
        </form>
      </div>
    </InventoryPageShell>
  );
}