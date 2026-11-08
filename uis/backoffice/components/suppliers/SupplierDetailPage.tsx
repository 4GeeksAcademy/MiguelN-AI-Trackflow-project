"use client";

import { StateMessage } from "@/components/common/StateMessage";
import { SupplierRateEditor } from "@/components/suppliers/SupplierRateEditor";
import { SupplierStatusBadge } from "@/components/suppliers/SupplierStatusBadge";
import { SupplierStatusToggle } from "@/components/suppliers/SupplierStatusToggle";
import { GENERAL_ERROR_COPY, describeError } from "@/lib/friendly-error";
import { formatCategoryLabel, formatRatePerShipment, formatUpdatedAt } from "@/lib/supplier";
import { fetchSupplierById } from "@/services/suppliers-service";
import type { AsyncStatus } from "@/types/async-state";
import type { Supplier } from "@/types/supplier";
import Link from "next/link";
import { ReactNode, useCallback, useEffect, useState } from "react";

type SupplierDetailPageProps = {
  supplierId: string;
};

export default function SupplierDetailPage({ supplierId }: SupplierDetailPageProps) {
  const [supplier, setSupplier] = useState<Supplier | null>(null);
  const [detailStatus, setDetailStatus] = useState<AsyncStatus>("loading");
  const [detailError, setDetailError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const retry = useCallback(() => setAttempt((current) => current + 1), []);

  useEffect(() => {
    let active = true;

    async function loadSupplier() {
      setDetailStatus("loading");
      setDetailError(null);

      try {
        const data = await fetchSupplierById(supplierId);
        if (!active) {
          return;
        }

        setSupplier(data);
        setDetailStatus("success");
      } catch (loadError) {
        if (!active) {
          return;
        }

        setDetailError(
          describeError(
            loadError,
            "Could not load this supplier. Check the address, or go back to the directory.",
            GENERAL_ERROR_COPY,
          ).message,
        );
        setDetailStatus("error");
      }
    }

    void loadSupplier();

    return () => {
      active = false;
    };
  }, [supplierId, attempt]);

  return (
    <main className="mx-auto w-full max-w-4xl px-6 py-10 md:px-10">
      <Link
        href="/suppliers"
        className="text-sm font-semibold text-[color:var(--brand-primary)] underline-offset-4 transition hover:underline"
      >
        &larr; Back to the directory
      </Link>

      <div className="mt-5 space-y-6">
        {detailStatus === "loading" ? <StateMessage tone="info">Loading supplier...</StateMessage> : null}

        {detailStatus === "error" && detailError ? (
          <StateMessage tone="error">
            <p>{detailError}</p>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={retry}
                className="inline-flex min-h-10 items-center rounded-xl border border-red-300 bg-white px-4 py-2 text-sm font-semibold text-red-700 transition hover:bg-red-100"
              >
                Try again
              </button>
              <Link href="/suppliers" className="text-sm font-semibold text-red-700 underline-offset-4 hover:underline">
                Back to the directory
              </Link>
            </div>
          </StateMessage>
        ) : null}

        {detailStatus === "success" && supplier ? (
          <>
            <section className="rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)]/90 p-6 shadow-[0_24px_90px_rgba(37,99,235,0.12)] backdrop-blur md:p-8">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <p className="font-mono text-xs uppercase tracking-[0.22em] text-[color:var(--brand-primary)]">
                    Supplier #{supplier.id}
                  </p>
                  <h1 className="mt-2 text-3xl font-semibold leading-tight text-[color:var(--foreground)] md:text-4xl">
                    {supplier.name}
                  </h1>
                  <p className="mt-2 text-sm text-slate-600">
                    {supplier.country}
                    {supplier.service_zone ? ` · ${supplier.service_zone}` : ""}
                  </p>
                </div>

                <div className="flex flex-col items-start gap-3 sm:items-end">
                  <SupplierStatusBadge status={supplier.status} />
                  <SupplierStatusToggle supplier={supplier} onUpdated={setSupplier} />
                </div>
              </div>
            </section>

            <section className="rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-6 shadow-[0_16px_38px_-28px_rgba(37,99,235,0.35)] md:p-8">
              <h2 className="text-lg font-semibold text-[color:var(--foreground)]">Commercial terms</h2>

              <dl className="mt-5 grid gap-5 sm:grid-cols-2">
                <DetailField label="Rate per shipment">
                  <p className="text-xl font-semibold text-[color:var(--foreground)]">
                    {formatRatePerShipment(supplier)}
                  </p>
                  <div className="mt-2">
                    <SupplierRateEditor key={supplier.rate_per_shipment} supplier={supplier} onUpdated={setSupplier} />
                  </div>
                </DetailField>

                <DetailField label="Currency">{supplier.currency}</DetailField>

                <DetailField label="Categories">
                  <ul className="flex flex-wrap gap-2">
                    {supplier.categories.map((category) => (
                      <li
                        key={category}
                        className="rounded-full border border-[color:var(--border-soft)] bg-[color:var(--background)] px-2.5 py-1 text-xs font-medium text-slate-600"
                      >
                        {formatCategoryLabel(category)}
                      </li>
                    ))}
                  </ul>
                </DetailField>

                <DetailField label="Contact email">
                  {supplier.contact_email ? (
                    <a
                      href={`mailto:${supplier.contact_email}`}
                      className="text-[color:var(--brand-primary)] underline-offset-4 transition hover:underline"
                    >
                      {supplier.contact_email}
                    </a>
                  ) : (
                    "-"
                  )}
                </DetailField>

                <DetailField label="Service zone">{supplier.service_zone ?? "-"}</DetailField>

                <DetailField label="Last updated">{formatUpdatedAt(supplier.updated_at)}</DetailField>

                <DetailField label="Notes" className="sm:col-span-2">
                  {supplier.notes ?? "No notes recorded for this supplier."}
                </DetailField>
              </dl>
            </section>
          </>
        ) : null}
      </div>
    </main>
  );
}

type DetailFieldProps = {
  label: string;
  children: ReactNode;
  className?: string;
};

function DetailField({ label, children, className = "" }: DetailFieldProps) {
  return (
    <div className={className}>
      <dt className="text-xs uppercase tracking-[0.16em] text-slate-500">{label}</dt>
      <dd className="mt-2 text-sm leading-relaxed text-slate-700">{children}</dd>
    </div>
  );
}
