"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { StateMessage } from "@/components/common/StateMessage";
import { InventoryPageShell, inventoryButtonClass } from "@/components/inventory/InventoryPageShell";
import { describeInventoryError, getProducts, INVENTORY_PATH, stockLevel } from "@/lib/inventory";
import type { Product } from "@/types/inventory";

const stockClasses = {
  "Out of stock": "bg-red-50 text-red-800 border-red-200",
  "Low stock": "bg-amber-50 text-amber-900 border-amber-300",
  "Healthy stock": "bg-emerald-50 text-emerald-800 border-emerald-200",
};

export function ProductsPage() {
  const [products, setProducts] = useState<Product[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    getProducts().then((items) => {
      if (active) setProducts(items);
    }).catch((failure: unknown) => {
      if (active) setError(describeInventoryError(failure).message);
    });
    return () => { active = false; };
  }, [attempt]);

  function retry() {
    setError(null);
    setProducts(null);
    setAttempt((current) => current + 1);
  }

  return (
    <InventoryPageShell title="Products">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-slate-600" role="status">
          {products ? `${products.length} ${products.length === 1 ? "product" : "products"}` : error ? "Products unavailable" : "Loading products..."}
        </p>
        <Link className={inventoryButtonClass} href={`${INVENTORY_PATH}/orders`}>Orders history</Link>
      </div>
      {error ? <div role="alert"><StateMessage tone="error">{error}<div className="mt-3"><button className={inventoryButtonClass} onClick={retry}>Try again</button></div></StateMessage></div> : null}
      {products?.length === 0 ? <StateMessage tone="info">No products on record.</StateMessage> : null}
      {products && products.length > 0 ? (
        <div className="overflow-x-auto border-y border-[color:var(--border-soft)] bg-white">
          <table className="block w-full text-left text-sm sm:table sm:min-w-[640px] sm:table-fixed">
            <caption className="sr-only">Products and current stock by warehouse</caption>
            <thead className="hidden border-b border-[color:var(--border-soft)] bg-slate-50 text-slate-600 sm:table-header-group">
              <tr>{["Name", "SKU", "Warehouse", "Current stock", "Orders"].map((label) => <th scope="col" key={label} className="px-3 py-3 font-semibold">{label}</th>)}</tr>
            </thead>
            <tbody className="block divide-y divide-slate-100 sm:table-row-group">
              {products.map((product) => {
                const level = stockLevel(product.current_stock);
                return (
                  <tr key={product.id} className="grid grid-cols-2 gap-3 p-4 sm:table-row sm:p-0">
                    <th scope="row" className="col-span-2 min-w-0 break-words font-semibold sm:px-3 sm:py-4">{product.name}</th>
                    <td className="min-w-0 break-all font-mono text-xs sm:px-3 sm:py-4"><span className="mb-1 block font-sans text-xs text-slate-600 sm:hidden">SKU</span>{product.sku}</td>
                    <td className="min-w-0 sm:px-3 sm:py-4"><span className="mb-1 block text-xs text-slate-600 sm:hidden">Warehouse</span>{product.warehouse}</td>
                    <td className="col-span-2 min-w-0 sm:px-3 sm:py-4"><span className="mb-1 block text-xs text-slate-600 sm:hidden">Current stock</span><span className="block font-semibold tabular-nums">{product.current_stock.toLocaleString()}</span><span className={`mt-1 inline-block rounded border px-2 py-1 text-xs font-medium ${stockClasses[level]}`}>{level}</span></td>
                    <td className="col-span-2 min-w-0 sm:px-3 sm:py-4"><div className="flex flex-wrap items-start gap-x-5 gap-y-2 sm:flex-col">
                      <Link className="min-h-11 content-center text-blue-700 underline underline-offset-4" href={`${INVENTORY_PATH}/orders/inbound?product_id=${encodeURIComponent(product.id)}`} aria-label={`Inbound order for ${product.name}, ${product.warehouse}`}>Inbound order</Link>
                      <Link className="min-h-11 content-center text-blue-700 underline underline-offset-4" href={`${INVENTORY_PATH}/orders/outbound?product_id=${encodeURIComponent(product.id)}`} aria-label={`Outbound order for ${product.name}, ${product.warehouse}`}>Outbound order</Link>
                    </div></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </InventoryPageShell>
  );
}