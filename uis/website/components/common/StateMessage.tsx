import { ReactNode } from "react";

type StateMessageProps = {
  tone: "info" | "error" | "success";
  children: ReactNode;
  className?: string;
};

const toneClassMap: Record<StateMessageProps["tone"], string> = {
  info: "border-slate-200 bg-slate-100 text-slate-700",
  error: "border-red-200 bg-red-50 text-red-700",
  success: "border-emerald-200 bg-emerald-50 text-emerald-700",
};

export function StateMessage({ tone, children, className = "" }: StateMessageProps) {
  return (
    <div className={`rounded-lg border p-4 text-sm ${toneClassMap[tone]} ${className}`.trim()}>
      {children}
    </div>
  );
}
