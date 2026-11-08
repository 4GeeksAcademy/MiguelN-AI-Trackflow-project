"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { StateMessage } from "@/components/common/StateMessage";
import { InventoryPageShell, inventoryButtonClass } from "@/components/inventory/InventoryPageShell";
import { describeInventoryError, getOrders, INVENTORY_PATH } from "@/lib/inventory";
import type { InventoryOrder } from "@/types/inventory";

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short",
  }).format(new Date(value));
}

export function OrdersPage() {
  const [orders, setOrders] = useState<InventoryOrder[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    getOrders().then((items) => {
      if (active) setOrders(items);
    }).catch((failure: unknown) => {
      if (active) setError(describeInventoryError(failure).message);
    });
    return () => { active = false; };
  }, [attempt]);

  function retry() {
    setError(null);
    setOrders(null);
    setAttempt((current) => current + 1);
  }

  return (
    <InventoryPageShell title="Orders history">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-slate-600" role="status">{orders ? `${orders.length} ${orders.length === 1 ? "order" : "orders"}` : error ? "Orders unavailable" : "Loading orders..."}</p>
        <Link href={`${INVENTORY_PATH}/products`} className={inventoryButtonClass}>Products</Link>
      </div>
      {error ? <div role="alert"><StateMessage tone="error">{error}<div className="mt-3"><button className={inventoryButtonClass} onClick={retry}>Try again</button></div></StateMessage></div> : null}
      {orders?.length === 0 ? <StateMessage tone="info">No orders on record.</StateMessage> : null}
      {orders && orders.length > 0 ? (
        <div className="overflow-x-auto border-y border-[color:var(--border-soft)] bg-white">
          <table className="block w-full text-left text-sm sm:table sm:min-w-[740px] sm:table-fixed">
            <caption className="sr-only">Inbound and outbound orders, newest first</caption>
            <thead className="hidden border-b border-[color:var(--border-soft)] bg-slate-50 text-slate-600 sm:table-header-group">
              <tr>{["Product name", "Quantity", "Order type", "Creation date", "User UUID"].map((label) => <th scope="col" key={label} className="px-3 py-3 font-semibold">{label}</th>)}</tr>
            </thead>
            <tbody className="block divide-y divide-slate-100 sm:table-row-group">
              {orders.map((order) => (
                <tr key={`${order.direction}-${order.id}`} className="grid grid-cols-2 gap-3 p-4 sm:table-row sm:p-0">
                  <th scope="row" className="col-span-2 min-w-0 break-words font-semibold sm:px-3 sm:py-4">{order.product_name}<span className="mt-1 block break-all font-mono text-xs font-normal text-slate-600">{order.product_sku}</span><span className="mt-1 block font-normal text-slate-600">{order.warehouse}</span></th>
                  <td className="min-w-0 tabular-nums sm:px-3 sm:py-4"><span className="mb-1 block text-xs text-slate-600 sm:hidden">Quantity</span>{order.quantity.toLocaleString()}</td>
                  <td className="min-w-0 sm:px-3 sm:py-4"><span className="mb-1 block text-xs text-slate-600 sm:hidden">Order type</span><span className={`inline-block rounded border px-2 py-1 text-xs font-semibold ${order.direction === "inbound" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-blue-200 bg-blue-50 text-blue-800"}`}>{order.direction === "inbound" ? "Inbound" : "Outbound"}</span></td>
                  <td className="col-span-2 min-w-0 sm:px-3 sm:py-4"><span className="mb-1 block text-xs text-slate-600 sm:hidden">Creation date</span><time dateTime={order.created_at}>{formatDate(order.created_at)}</time></td>
                  <td className="col-span-2 min-w-0 break-all font-mono text-xs sm:px-3 sm:py-4"><span className="mb-1 block font-sans text-xs text-slate-600 sm:hidden">User UUID</span>{order.user_uuid}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </InventoryPageShell>
  );
}