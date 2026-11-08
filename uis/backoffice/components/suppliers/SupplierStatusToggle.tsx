"use client";

import { GENERAL_ERROR_COPY, describeError } from "@/lib/friendly-error";
import { formatStatusLabel } from "@/lib/supplier";
import { updateSupplierStatus } from "@/services/suppliers-service";
import { track } from "@/services/telemetry-service";
import type { Supplier, SupplierListItem, SupplierStatus } from "@/types/supplier";
import { useState } from "react";

type SupplierStatusToggleProps = {
  supplier: SupplierListItem;
  onUpdated: (supplier: Supplier) => void;
};

function nextStatus(status: SupplierStatus): SupplierStatus {
  return status === "active" ? "suspended" : "active";
}

export function SupplierStatusToggle({ supplier, onUpdated }: SupplierStatusToggleProps) {
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const targetStatus = nextStatus(supplier.status);

  async function handleToggle() {
    setIsSaving(true);
    setError(null);

    try {
      onUpdated(await updateSupplierStatus(supplier.id, targetStatus));
      track("supplier_status_changed", { supplier_id: supplier.id, from_status: supplier.status, to_status: targetStatus });
    } catch (updateError) {
      const described = describeError(updateError, "Could not change the status. Try again.", GENERAL_ERROR_COPY);
      setError(described.fieldErrors.status ?? described.message);
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        onClick={handleToggle}
        disabled={isSaving}
        className={`rounded-lg border px-2.5 py-1 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-45 ${
          targetStatus === "suspended"
            ? "border-amber-200 text-amber-700 hover:bg-amber-50"
            : "border-emerald-200 text-emerald-700 hover:bg-emerald-50"
        }`}
      >
        {isSaving ? "Saving..." : `Set ${formatStatusLabel(targetStatus)}`}
      </button>

      {error ? <p className="text-xs font-medium text-red-600">{error}</p> : null}
    </div>
  );
}
