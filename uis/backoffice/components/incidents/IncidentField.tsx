"use client";

import { useId, type ReactNode } from "react";

type ControlProps = {
  id: string;
  "aria-invalid": true | undefined;
  "aria-describedby": string | undefined;
};

type IncidentFieldProps = {
  label: string;
  /** Shown under the control, before any error. */
  hint?: string;
  /** Message from client validation or from the API's `field` on a 400. */
  error?: string;
  required?: boolean;
  /**
   * Emphasis for a field that is correct but needs attention - the branch
   * field once the origin is `branch`. The note is read out with the control,
   * so the emphasis is never carried by colour alone.
   */
  emphasis?: string;
  className?: string;
  /**
   * The control itself. A render prop because these fields are inputs, selects
   * and textareas, and the point of this component is that the label, the
   * error and the `aria-*` wiring are written once rather than per control.
   */
  children: (props: ControlProps) => ReactNode;
};

/**
 * One labelled field, with its own validation message.
 *
 * Sized for the warehouse floor: `min-h-12` gives a 48px touch target, which is
 * the smallest the terminals are comfortable with, and `text-base` keeps the
 * value legible at arm's length.
 */
export function IncidentField({
  label,
  hint,
  error,
  required = false,
  emphasis,
  className = "",
  children,
}: IncidentFieldProps) {
  const id = useId();
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;
  const emphasisId = `${id}-emphasis`;

  const describedBy =
    [error ? errorId : null, emphasis ? emphasisId : null, hint ? hintId : null]
      .filter(Boolean)
      .join(" ") || undefined;

  return (
    <div
      className={`rounded-2xl transition ${
        emphasis
          ? "bg-[color:var(--brand-accent)]/10 ring-2 ring-[color:var(--brand-accent)] p-4 -m-0.5"
          : ""
      } ${className}`.trim()}
    >
      <label htmlFor={id} className="mb-1.5 block text-sm font-semibold text-[color:var(--foreground)]">
        {label}
        {required ? (
          <span className="ml-1 text-[color:var(--brand-primary)]" aria-hidden="true">
            *
          </span>
        ) : null}
        {required ? <span className="sr-only"> (required)</span> : null}
      </label>

      {children({
        id,
        "aria-invalid": error ? true : undefined,
        "aria-describedby": describedBy,
      })}

      {emphasis ? (
        <p id={emphasisId} className="mt-2 text-xs font-semibold text-[color:var(--foreground)]">
          {emphasis}
        </p>
      ) : null}

      {hint ? (
        <p id={hintId} className="mt-1.5 text-xs text-slate-500">
          {hint}
        </p>
      ) : null}

      {error ? (
        <p id={errorId} className="mt-1.5 text-xs font-semibold text-red-600">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Shared control styling. Exported so the form and its selects stay identical. */
export function controlClassName(hasError: boolean): string {
  const base =
    "w-full min-h-12 rounded-xl border bg-[color:var(--surface)] px-4 py-3 text-base text-[color:var(--foreground)] outline-none transition disabled:cursor-not-allowed disabled:opacity-60";

  return `${base} ${
    hasError
      ? "border-red-300 focus:border-red-500"
      : "border-[color:var(--border-soft)] focus:border-[color:var(--brand-primary)]"
  }`;
}
