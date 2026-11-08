"use client";

import { StateMessage } from "@/components/common/StateMessage";
import { IncidentField, controlClassName } from "@/components/incidents/IncidentField";
import { useFlowTelemetry } from "@/hooks/useFlowTelemetry";
import { INCIDENT_ERROR_COPY, describeError } from "@/lib/friendly-error";
import {
  buildIncidentPayload,
  describeOrigin,
  emptyIncidentFormValues,
  formatBranchLabel,
  formatCategoryLabel,
  formatOriginLabel,
  formatStatusLabel,
  hasFieldErrors,
  isIncidentBranch,
  isIncidentCategory,
  isIncidentOrigin,
  isIncidentStatus,
  validateIncidentForm,
} from "@/lib/incident";
import { createIncident } from "@/services/incidents-service";
import {
  DESCRIPTION_MAX_LENGTH,
  INCIDENT_BRANCHES,
  INCIDENT_CATEGORIES,
  INCIDENT_ORIGINS,
  INCIDENT_STATUSES,
  TITLE_MAX_LENGTH,
  type IncidentCreated,
  type IncidentFieldErrors,
  type IncidentFormField,
  type IncidentFormValues,
} from "@/types/incident";
import Link from "next/link";
import { useState, type FormEvent } from "react";

type IncidentFormProps = {
  /** Called after a successful insert, with the row the API returned. */
  onCreated?: (incident: IncidentCreated) => void;
};

/** The API's `field` values are the form's own field names, so no alias map is needed. */
function toIncidentFieldErrors(fieldErrors: Record<string, string>): IncidentFieldErrors {
  const known: IncidentFormField[] = ["title", "description", "category", "status", "origin", "branch"];
  const mapped: IncidentFieldErrors = {};

  for (const field of known) {
    const message = fieldErrors[field];
    if (message) {
      mapped[field] = message;
    }
  }

  return mapped;
}

export function IncidentForm({ onCreated }: IncidentFormProps) {
  const [values, setValues] = useState<IncidentFormValues>(emptyIncidentFormValues());
  const [fieldErrors, setFieldErrors] = useState<IncidentFieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<IncidentCreated | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const flow = useFlowTelemetry("incident_report", "incident_new");

  // The reminder the CONTEXT asks for: a report filed *from* a facility needs
  // to name that facility, not fall back to Central.
  const emphasiseBranch = values.origin === "branch";

  function updateValues(patch: Partial<IncidentFormValues>): void {
    flow.fieldsEdited(patch);
    setValues((current) => ({ ...current, ...patch }));

    // Clear only the messages for the fields being edited. Leaving the others
    // in place means a form with three problems does not lose two of them the
    // moment the first is touched.
    setFieldErrors((current) => {
      const next = { ...current };
      for (const key of Object.keys(patch) as IncidentFormField[]) {
        delete next[key];
      }
      return next;
    });
    setFormError(null);
    setConfirmation(null);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();

    // Guards a second submit from a double tap or an Enter key while the first
    // request is still open, which `disabled` alone does not cover.
    if (isSubmitting) {
      return;
    }

    const validation = validateIncidentForm(values);
    if (hasFieldErrors(validation)) {
      flow.validationFailedFields(validation);
      setFieldErrors(validation);
      setFormError("Please fix the highlighted fields and submit again.");
      setConfirmation(null);
      return;
    }

    setIsSubmitting(true);
    setFieldErrors({});
    setFormError(null);
    setConfirmation(null);
    flow.submitAttempted();

    try {
      const created = await createIncident(buildIncidentPayload(values));
      flow.completed();

      setValues(emptyIncidentFormValues());
      setConfirmation(created);
      onCreated?.(created);
    } catch (submitError) {
      flow.submitFailed();
      const described = describeError(
        submitError,
        "Please fix the highlighted fields and submit again.",
        INCIDENT_ERROR_COPY,
      );

      setFieldErrors(toIncidentFieldErrors(described.fieldErrors));
      setFormError(described.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <form className="grid gap-5 md:grid-cols-2" onSubmit={handleSubmit} noValidate>
      <IncidentField
        label="Title"
        required
        error={fieldErrors.title}
        hint={`A short summary. Up to ${TITLE_MAX_LENGTH} characters.`}
        className="md:col-span-2"
      >
        {(control) => (
          <input
            {...control}
            type="text"
            value={values.title}
            onChange={(event) => updateValues({ title: event.target.value })}
            maxLength={TITLE_MAX_LENGTH}
            autoComplete="off"
            placeholder="Pallet left the dock two units short"
            className={controlClassName(Boolean(fieldErrors.title))}
          />
        )}
      </IncidentField>

      <IncidentField
        label="Description"
        required
        error={fieldErrors.description}
        hint="What happened, where, and anything the assigned team will need."
        className="md:col-span-2"
      >
        {(control) => (
          <textarea
            {...control}
            value={values.description}
            onChange={(event) => updateValues({ description: event.target.value })}
            maxLength={DESCRIPTION_MAX_LENGTH}
            rows={4}
            placeholder="Pallet 42 left the dock two units short. Both units are unscanned and not on the shelf."
            className={controlClassName(Boolean(fieldErrors.description))}
          />
        )}
      </IncidentField>

      <IncidentField label="Category" required error={fieldErrors.category}>
        {(control) => (
          <select
            {...control}
            value={values.category}
            onChange={(event) => {
              const next = event.target.value;
              updateValues({ category: next === "" || isIncidentCategory(next) ? next : "" });
            }}
            className={controlClassName(Boolean(fieldErrors.category))}
          >
            <option value="">Select a category...</option>
            {INCIDENT_CATEGORIES.map((category) => (
              <option key={category} value={category}>
                {formatCategoryLabel(category)}
              </option>
            ))}
          </select>
        )}
      </IncidentField>

      <IncidentField
        label="Origin"
        required
        error={fieldErrors.origin}
        hint={values.origin ? describeOrigin(values.origin) : "Who noticed this."}
      >
        {(control) => (
          <select
            {...control}
            value={values.origin}
            onChange={(event) => {
              const next = event.target.value;
              updateValues({ origin: next === "" || isIncidentOrigin(next) ? next : "" });
            }}
            className={controlClassName(Boolean(fieldErrors.origin))}
          >
            <option value="">Select an origin...</option>
            {INCIDENT_ORIGINS.map((origin) => (
              <option key={origin} value={origin}>
                {formatOriginLabel(origin)}
              </option>
            ))}
          </select>
        )}
      </IncidentField>

      {/*
        Always rendered and always required, whatever the origin. The emphasis
        appears once the origin is `branch`; it never gates the field.
      */}
      <IncidentField
        label="Branch"
        required
        error={fieldErrors.branch}
        emphasis={
          emphasiseBranch
            ? "You are reporting from a specific location - pick the facility this came from."
            : undefined
        }
        hint="Use Central when the incident belongs to no single facility."
        className="md:col-span-2"
      >
        {(control) => (
          <select
            {...control}
            value={values.branch}
            onChange={(event) => {
              const next = event.target.value;
              updateValues({ branch: next === "" || isIncidentBranch(next) ? next : "" });
            }}
            className={controlClassName(Boolean(fieldErrors.branch))}
          >
            <option value="">Select a branch...</option>
            {INCIDENT_BRANCHES.map((branch) => (
              <option key={branch} value={branch}>
                {formatBranchLabel(branch)}
              </option>
            ))}
          </select>
        )}
      </IncidentField>

      <IncidentField
        label="Status"
        required
        error={fieldErrors.status}
        hint="New reports are Open. Change this only if the incident is already being handled or is already closed."
        className="md:col-span-2"
      >
        {(control) => (
          <select
            {...control}
            value={values.status}
            onChange={(event) => {
              const next = event.target.value;
              if (isIncidentStatus(next)) {
                updateValues({ status: next });
              }
            }}
            className={controlClassName(Boolean(fieldErrors.status))}
          >
            {INCIDENT_STATUSES.map((status) => (
              <option key={status} value={status}>
                {formatStatusLabel(status)}
              </option>
            ))}
          </select>
        )}
      </IncidentField>

      <p className="text-xs text-slate-500 md:col-span-2">
        The reference number and the created and updated timestamps are generated when you submit.
      </p>

      {/*
        `aria-live` so a screen reader announces the outcome without the focus
        having to move: on a phone or a terminal the message can be off-screen.
      */}
      <div aria-live="polite" className="md:col-span-2">
        {formError ? <StateMessage tone="error">{formError}</StateMessage> : null}

        {confirmation ? (
          <StateMessage tone="success">
            <p className="font-semibold">Incident registered.</p>
            <p className="mt-1">
              &ldquo;{confirmation.title}&rdquo; was filed as{" "}
              <strong>{formatStatusLabel(confirmation.status)}</strong> at{" "}
              <strong>{formatBranchLabel(confirmation.branch)}</strong>.
            </p>
            <p className="mt-1 font-mono text-xs break-all">Reference: {confirmation.id}</p>
            <p className="mt-2">
              The form is cleared and ready for the next report, or{" "}
              <Link href="/incidents" className="font-semibold underline underline-offset-4">
                open the incident list
              </Link>
              .
            </p>
          </StateMessage>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center justify-end gap-3 md:col-span-2">
        <Link
          href="/incidents"
          className="rounded-xl border border-[color:var(--border-soft)] px-5 py-3 text-sm font-semibold text-slate-600 transition hover:bg-[color:var(--brand-primary)]/5"
        >
          Back to list
        </Link>
        <button
          type="submit"
          disabled={isSubmitting}
          aria-busy={isSubmitting}
          className="inline-flex min-h-12 items-center gap-2 rounded-xl bg-[color:var(--brand-primary)] px-6 py-3 text-base font-semibold text-white transition hover:bg-[color:var(--brand-secondary)] disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isSubmitting ? (
            <>
              <span
                aria-hidden="true"
                className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white"
              />
              Registering...
            </>
          ) : (
            "Register incident"
          )}
        </button>
      </div>
    </form>
  );
}
