"use client";

import { StateMessage } from "@/components/common/StateMessage";
import { SupplierRateEditor } from "@/components/suppliers/SupplierRateEditor";
import { SupplierStatusBadge } from "@/components/suppliers/SupplierStatusBadge";
import { SupplierStatusToggle } from "@/components/suppliers/SupplierStatusToggle";
import { getApiBaseUrl } from "@/lib/api-client";
import { GENERAL_ERROR_COPY, describeError } from "@/lib/friendly-error";
import {
  formatCategoriesLabel,
  formatCategoryLabel,
  isSupplierCategory,
  isSupplierCountry,
  supplierListItemFromSupplier,
} from "@/lib/supplier";
import { fetchSuppliers } from "@/services/suppliers-service";
import dynamic from "next/dynamic";
import type { AsyncStatus } from "@/types/async-state";
import {
  SUPPLIER_CATEGORIES,
  SUPPLIER_COUNTRIES,
  type Supplier,
  type SupplierCategory,
  type SupplierCountry,
  type SupplierListItem,
} from "@/types/supplier";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

const SupplierCreateForm = dynamic(
  () => import("@/components/suppliers/SupplierCreateForm").then((module) => module.SupplierCreateForm),
  {
    loading: () => (
      <div role="status" className="py-4 text-sm text-slate-500">
        Loading supplier form...
      </div>
    ),
  },
);

const apiBaseUrl = getApiBaseUrl();
const suppliersEndpointLabel = apiBaseUrl ? `${apiBaseUrl}/suppliers` : "NEXT_PUBLIC_API_URL/suppliers";

const selectClassName =
  "w-full rounded-xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] px-3 py-2 text-sm text-[color:var(--foreground)] outline-none transition focus:border-[color:var(--brand-primary)]";

export default function SuppliersListPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto w-full max-w-6xl px-6 py-10 md:px-10">
          <StateMessage tone="info">Loading the supplier directory...</StateMessage>
        </main>
      }
    >
      <SuppliersListContent />
    </Suspense>
  );
}

function SuppliersListContent() {
  const [suppliers, setSuppliers] = useState<SupplierListItem[]>([]);
  const [listStatus, setListStatus] = useState<AsyncStatus>("loading");
  const [listError, setListError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [isCreateFormOpen, setIsCreateFormOpen] = useState(false);

  const retry = useCallback(() => setAttempt((current) => current + 1), []);

  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const rawCountry = searchParams.get("country") ?? "";
  const rawCategory = searchParams.get("category") ?? "";
  const countryFilter: SupplierCountry | "" = isSupplierCountry(rawCountry) ? rawCountry : "";
  const categoryFilter: SupplierCategory | "" = isSupplierCategory(rawCategory) ? rawCategory : "";

  // The API does the filtering, so every filter change is a fresh request.
  useEffect(() => {
    let active = true;

    async function loadSuppliers() {
      setListStatus("loading");
      setListError(null);

      try {
        const data = await fetchSuppliers({ country: countryFilter, category: categoryFilter });
        if (!active) {
          return;
        }

        setSuppliers(data);
        setListStatus("success");
      } catch (loadError) {
        if (!active) {
          return;
        }

        setListError(describeError(loadError, "Could not load the supplier directory.", GENERAL_ERROR_COPY).message);
        setListStatus("error");
      }
    }

    void loadSuppliers();

    return () => {
      active = false;
    };
  }, [countryFilter, categoryFilter, attempt]);

  const suspendedCount = useMemo(
    () => suppliers.filter((supplier) => supplier.status === "suspended").length,
    [suppliers],
  );

  function updateQueryFilter(key: "country" | "category", value: string): void {
    const params = new URLSearchParams(searchParams.toString());

    if (!value) {
      params.delete(key);
    } else {
      params.set(key, value);
    }

    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }

  function matchesActiveFilters(supplier: SupplierListItem): boolean {
    const matchesCountry = !countryFilter || supplier.country === countryFilter;
    const matchesCategory = !categoryFilter || supplier.categories.includes(categoryFilter);

    return matchesCountry && matchesCategory;
  }

  /** Swaps the single row the API just returned instead of refetching the whole list. */
  function handleSupplierUpdated(updated: Supplier): void {
    const listItem = supplierListItemFromSupplier(updated);
    setSuppliers((currentSuppliers) =>
      currentSuppliers.map((supplier) => (supplier.id === updated.id ? listItem : supplier)),
    );
  }

  function handleSupplierCreated(created: SupplierListItem): void {
    if (!matchesActiveFilters(created)) {
      return;
    }

    setSuppliers((currentSuppliers) => [...currentSuppliers, created]);
  }

  return (
    <main className="mx-auto w-full max-w-6xl px-6 py-10 md:px-10">
      <div className="space-y-6">
        <section className="rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)]/90 p-6 shadow-[0_24px_90px_rgba(37,99,235,0.12)] backdrop-blur md:p-8">
          <p className="font-mono text-xs uppercase tracking-[0.22em] text-[color:var(--brand-primary)]">
            Supplier Directory
          </p>
          <h1 className="mt-3 text-3xl font-semibold leading-tight text-[color:var(--foreground)] md:text-4xl">
            Every supplier behind TrackFlow USA and Spain.
          </h1>
          <p className="mt-3 max-w-3xl text-sm text-slate-600 md:text-base">
            Filter the directory by country and category, adjust negotiated rates, and suspend or reactivate suppliers
            without leaving the table.
          </p>

          <div className="mt-6 grid gap-3 sm:grid-cols-3">
            <article className="rounded-2xl border border-[color:var(--border-soft)] bg-blue-50/80 p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-blue-700">Suppliers listed</p>
              <p className="mt-2 text-3xl font-semibold text-blue-900">{suppliers.length}</p>
            </article>
            <article className="rounded-2xl border border-[color:var(--border-soft)] bg-amber-50/80 p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-amber-700">Suspended</p>
              <p className="mt-2 text-3xl font-semibold text-amber-900">{suspendedCount}</p>
            </article>
            <article className="rounded-2xl border border-[color:var(--border-soft)] bg-slate-50 p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-slate-700">Source</p>
              <p className="mt-2 break-all font-mono text-xs text-slate-700">{suppliersEndpointLabel}</p>
            </article>
          </div>
        </section>

        <section className="rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-6 shadow-[0_16px_38px_-28px_rgba(37,99,235,0.35)] md:p-8">
          <header className="mb-6 flex flex-col gap-4 border-b border-[color:var(--border-soft)] pb-5 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h2 className="text-2xl font-semibold text-[color:var(--foreground)]">Directory</h2>
              <p className="mt-1 text-sm text-slate-600">
                {listStatus === "loading" ? "Loading suppliers..." : `${suppliers.length} suppliers match the filters`}
              </p>
            </div>

            <button
              type="button"
              onClick={() => setIsCreateFormOpen((isOpen) => !isOpen)}
              aria-expanded={isCreateFormOpen}
              className="rounded-xl bg-[color:var(--brand-primary)] px-4 py-2 text-sm font-semibold text-white transition hover:bg-[color:var(--brand-secondary)]"
            >
              {isCreateFormOpen ? "Hide form" : "Register supplier"}
            </button>
          </header>

          {isCreateFormOpen ? (
            <div className="mb-6 rounded-2xl border border-dashed border-[color:var(--border-soft)] bg-[color:var(--background)] p-5 md:p-6">
              <h3 className="text-lg font-semibold text-[color:var(--foreground)]">Register a new supplier</h3>
              <p className="mt-1 mb-5 text-sm text-slate-600">
                Currency follows the selected country. The API rejects any other combination.
              </p>
              <SupplierCreateForm
                onCreated={handleSupplierCreated}
                onCancel={() => setIsCreateFormOpen(false)}
              />
            </div>
          ) : null}

          <div className="mb-6 grid gap-3 sm:grid-cols-2">
            <label className="block text-sm text-slate-600">
              <span className="mb-1 block font-medium text-[color:var(--foreground)]">Country</span>
              <select
                value={countryFilter}
                onChange={(event) => updateQueryFilter("country", event.target.value)}
                className={selectClassName}
              >
                <option value="">All countries</option>
                {SUPPLIER_COUNTRIES.map((country) => (
                  <option key={country} value={country}>
                    {country}
                  </option>
                ))}
              </select>
            </label>

            <label className="block text-sm text-slate-600">
              <span className="mb-1 block font-medium text-[color:var(--foreground)]">Category</span>
              <select
                value={categoryFilter}
                onChange={(event) => updateQueryFilter("category", event.target.value)}
                className={selectClassName}
              >
                <option value="">All categories</option>
                {SUPPLIER_CATEGORIES.map((category) => (
                  <option key={category} value={category}>
                    {formatCategoryLabel(category)}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {listStatus === "loading" ? (
            <StateMessage tone="info">Loading suppliers...</StateMessage>
          ) : null}

          {listStatus === "error" && listError ? (
            <StateMessage tone="error">
              <p>{listError}</p>
              <button
                type="button"
                onClick={retry}
                className="mt-3 inline-flex min-h-10 items-center rounded-xl border border-red-300 bg-white px-4 py-2 text-sm font-semibold text-red-700 transition hover:bg-red-100"
              >
                Try again
              </button>
            </StateMessage>
          ) : null}

          {listStatus === "success" && suppliers.length === 0 ? (
            <StateMessage tone="info">No suppliers match the current filters.</StateMessage>
          ) : null}

          {listStatus === "success" && suppliers.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[54rem] border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-[color:var(--border-soft)] text-xs uppercase tracking-[0.14em] text-slate-500">
                    <th scope="col" className="py-3 pr-4 font-semibold">Name</th>
                    <th scope="col" className="py-3 pr-4 font-semibold">Country</th>
                    <th scope="col" className="py-3 pr-4 font-semibold">Categories</th>
                    <th scope="col" className="py-3 pr-4 font-semibold">Rate</th>
                    <th scope="col" className="py-3 pr-4 font-semibold">Status</th>
                    <th scope="col" className="py-3 font-semibold">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {suppliers.map((supplier) => {
                    const isSuspended = supplier.status === "suspended";

                    return (
                      <tr
                        key={supplier.id}
                        className={`border-b border-[color:var(--border-soft)]/60 align-top ${
                          isSuspended ? "bg-slate-50/80 text-slate-400" : "text-slate-700"
                        }`}
                      >
                        <td className="py-4 pr-4">
                          <Link
                            href={`/suppliers/${supplier.id}`}
                            className={`font-semibold underline-offset-4 transition hover:underline ${
                              isSuspended ? "text-slate-500" : "text-[color:var(--brand-primary)]"
                            }`}
                          >
                            {supplier.name}
                          </Link>
                          {supplier.service_zone ? (
                            <p className="mt-1 text-xs text-slate-500">{supplier.service_zone}</p>
                          ) : null}
                        </td>
                        <td className="py-4 pr-4">{supplier.country}</td>
                        <td className="py-4 pr-4 text-xs leading-relaxed">
                          {formatCategoriesLabel(supplier.categories)}
                        </td>
                        <td className="py-4 pr-4">
                          <SupplierRateEditor
                            key={supplier.rate_per_shipment}
                            supplier={supplier}
                            onUpdated={handleSupplierUpdated}
                          />
                        </td>
                        <td className="py-4 pr-4">
                          <SupplierStatusBadge status={supplier.status} />
                        </td>
                        <td className="py-4">
                          <SupplierStatusToggle supplier={supplier} onUpdated={handleSupplierUpdated} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      </div>
    </main>
  );
}
