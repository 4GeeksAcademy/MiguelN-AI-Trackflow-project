import type { ReactNode } from "react";

export const inventoryButtonClass = "inline-flex min-h-11 items-center justify-center rounded-lg border border-[color:var(--border-soft)] px-4 py-2 text-sm font-semibold text-[color:var(--brand-primary)] transition hover:bg-blue-50 focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
export const inventoryControlClass = "min-h-12 w-full rounded-lg border border-[color:var(--border-soft)] bg-[color:var(--surface)] px-3 py-2 text-sm outline-none focus:border-[color:var(--brand-primary)] focus:ring-2 focus:ring-blue-100 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";

export function InventoryPageShell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 md:px-10">
      <header className="mb-6 border-b border-[color:var(--border-soft)] pb-5">
        <p className="mb-2 text-sm font-medium text-slate-600">Warehouse Operations / Inventory</p>
        <h1 className="text-2xl font-semibold leading-tight">{title}</h1>
      </header>
      {children}
    </main>
  );
}