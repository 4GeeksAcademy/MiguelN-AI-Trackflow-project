import { formatStatusLabel } from "@/lib/supplier";
import type { SupplierStatus } from "@/types/supplier";

type SupplierStatusBadgeProps = {
  status: SupplierStatus;
};

const statusClassMap: Record<SupplierStatus, string> = {
  active: "border-emerald-200 bg-emerald-50 text-emerald-700",
  suspended: "border-amber-200 bg-amber-50 text-amber-700",
};

export function SupplierStatusBadge({ status }: SupplierStatusBadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${statusClassMap[status]}`}
    >
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full ${status === "active" ? "bg-emerald-500" : "bg-amber-500"}`}
      />
      {formatStatusLabel(status)}
    </span>
  );
}
