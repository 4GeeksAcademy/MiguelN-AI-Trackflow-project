"use client";

import { useId, type InputHTMLAttributes } from "react";

type AuthFieldProps = {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  error?: string;
  hint?: string;
} & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "id">;

const baseFieldClassName =
  "w-full rounded-xl border bg-[color:var(--surface)] px-3 py-2 text-sm text-[color:var(--foreground)] outline-none transition disabled:cursor-not-allowed disabled:opacity-60";

/** Labeled input that renders its own validation message and wires `aria-describedby`. */
export function AuthField({ label, value, onValueChange, error, hint, ...inputProps }: AuthFieldProps) {
  const inputId = useId();
  const errorId = `${inputId}-error`;
  const hintId = `${inputId}-hint`;
  const describedBy = [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(" ");

  return (
    <div className="block text-sm text-slate-600">
      <label htmlFor={inputId} className="mb-1 block font-medium text-[color:var(--foreground)]">
        {label}
      </label>
      <input
        {...inputProps}
        id={inputId}
        value={value}
        onChange={(event) => onValueChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={`${baseFieldClassName} ${
          error
            ? "border-red-300 focus:border-red-500"
            : "border-[color:var(--border-soft)] focus:border-[color:var(--brand-primary)]"
        }`}
      />
      {hint ? (
        <span id={hintId} className="mt-1 block text-xs text-slate-500">
          {hint}
        </span>
      ) : null}
      {error ? (
        <span id={errorId} className="mt-1 block text-xs font-medium text-red-600">
          {error}
        </span>
      ) : null}
    </div>
  );
}
