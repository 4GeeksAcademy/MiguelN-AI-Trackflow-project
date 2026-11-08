"use client";

import { GENERAL_ERROR_COPY, describeError } from "@/lib/friendly-error";
import { parseRateInput } from "@/lib/supplier";
import { updateSupplierRate } from "@/services/suppliers-service";
import type { Supplier, SupplierListItem } from "@/types/supplier";
import { FormEvent, useState } from "react";

type SupplierRateEditorProps = {
  supplier: SupplierListItem;
  onUpdated: (supplier: Supplier) => void;
};

/**
 * Callers key this component on `supplier.rate_per_shipment` so a rate saved here — or refreshed
 * from elsewhere — remounts the field with the stored value instead of syncing it in an effect.
 */
export function SupplierRateEditor({ supplier, onUpdated }: SupplierRateEditorProps) {
  const [rateInput, setRateInput] = useState(String(supplier.rate_per_shipment));
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parsedRate = parseRateInput(rateInput);
  const isUnchanged = parsedRate !== null && parsedRate === supplier.rate_per_shipment;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (parsedRate === null) {
      setError("Rate must be greater than 0.");
      return;
    }

    if (isUnchanged) {
      return;
    }

    setIsSaving(true);
    setError(null);

    try {
      onUpdated(await updateSupplierRate(supplier.id, parsedRate));
    } catch (updateError) {
      const described = describeError(updateError, "Could not update the rate. Try again.", GENERAL_ERROR_COPY);
      setError(described.fieldErrors.rate_per_shipment ?? described.message);
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form className="flex flex-col gap-1" onSubmit={handleSubmit}>
      <div className="flex items-center gap-2">
        <label className="sr-only" htmlFor={`rate-${supplier.id}`}>
          Rate per shipment for {supplier.name}
        </label>
        <span className="font-mono text-xs text-slate-500">{supplier.currency}</span>
        <input
          id={`rate-${supplier.id}`}
          type="number"
          inputMode="decimal"
          min="0.01"
          step="0.01"
          value={rateInput}
          onChange={(event) => {
            setError(null);
            setRateInput(event.target.value);
          }}
          className="w-24 rounded-lg border border-[color:var(--border-soft)] bg-[color:var(--surface)] px-2 py-1 text-sm text-[color:var(--foreground)] outline-none transition focus:border-[color:var(--brand-primary)]"
        />
        <button
          type="submit"
          disabled={isSaving || isUnchanged}
          className="rounded-lg border border-[color:var(--border-soft)] px-2.5 py-1 text-xs font-semibold text-[color:var(--brand-primary)] transition hover:bg-[color:var(--brand-primary)]/10 disabled:cursor-not-allowed disabled:opacity-45"
        >
          {isSaving ? "Saving..." : "Save"}
        </button>
      </div>

      {error ? <p className="text-xs font-medium text-red-600">{error}</p> : null}
    </form>
  );
}
