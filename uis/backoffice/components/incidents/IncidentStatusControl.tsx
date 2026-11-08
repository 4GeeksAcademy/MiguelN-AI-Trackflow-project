"use client";

import { ApiError } from "@/lib/api-client";
import { INCIDENT_ERROR_COPY, describeError } from "@/lib/friendly-error";
import { allowedTransitionsFrom, formatStatusLabel, isFinalStatus, isIncidentStatus } from "@/lib/incident";
import { updateIncidentStatus } from "@/services/incidents-service";
import { track } from "@/services/telemetry-service";
import type { Incident, IncidentStatus } from "@/types/incident";
import { useState } from "react";

type IncidentStatusControlProps = {
  incident: Incident;
  /** Applies a status to this row in the parent's list. Used to move and to undo. */
  onStatusChange: (id: string, status: IncidentStatus) => void;
  /** Replaces the whole row with what the API returned, once it is confirmed. */
  onConfirmed: (incident: Incident) => void;
  /** Reloads the list. Offered when the incident turns out to no longer exist. */
  onRefresh: () => void;
};

/**
 * Moves one incident along its lifecycle from inside the list.
 *
 * The change is applied optimistically so the table responds to the tap on a
 * warehouse terminal rather than to the round trip. If the request fails the
 * row is put back to the status it had and the reason is shown next to the
 * control - which is why `previousStatus` is captured before anything is
 * touched, instead of being re-read afterwards from a row that has since
 * changed.
 *
 * Only the statuses the lifecycle allows are offered. The API re-checks the
 * transition regardless, so a stale tab that offers a move which is no longer
 * legal gets a 400 with the reason, not a silent write.
 */
export function IncidentStatusControl({
  incident,
  onStatusChange,
  onConfirmed,
  onRefresh,
}: IncidentStatusControlProps) {
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A 404 means the row is stale, so picking another status cannot help; the
  // way forward is reloading the list.
  const [isGone, setIsGone] = useState(false);

  const transitions = allowedTransitionsFrom(incident.status);

  if (isFinalStatus(incident.status)) {
    return (
      <div className="text-xs text-slate-500">
        <span className="font-semibold">{formatStatusLabel(incident.status)}</span>
        <p className="mt-0.5">Final state</p>
      </div>
    );
  }

  async function handleChange(nextStatus: IncidentStatus): Promise<void> {
    const previousStatus = incident.status;

    setIsSaving(true);
    setError(null);
    setIsGone(false);
    onStatusChange(incident.id, nextStatus);

    try {
      onConfirmed(await updateIncidentStatus(incident.id, nextStatus));
      track("incident_status_changed", {
        incident_id: incident.id,
        from_status: previousStatus,
        to_status: nextStatus,
        age_hours: Math.max(0, (Date.now() - Date.parse(incident.created_at)) / 3_600_000) || 0,
      });
    } catch (updateError) {
      onStatusChange(incident.id, previousStatus);

      const described = describeError(
        updateError,
        `Could not move this incident to ${formatStatusLabel(nextStatus)}.`,
        INCIDENT_ERROR_COPY,
      );
      const notFound = updateError instanceof ApiError && updateError.status === 404;

      // A refused transition comes back attached to the `status` field, and
      // this control *is* that field - there is no separate input to hang the
      // message on. Preferring it keeps the API's explanation of why the move
      // was refused instead of replacing it with the generic fallback.
      if (notFound) {
        setIsGone(true);
        setError(INCIDENT_ERROR_COPY.notFound ?? described.message);
      } else {
        const reason = described.fieldErrors.status ?? described.message;
        setError(`${reason} Left as ${formatStatusLabel(previousStatus)}.`);
      }
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <label className="sr-only" htmlFor={`status-${incident.id}`}>
        Status for {incident.title}
      </label>
      <select
        id={`status-${incident.id}`}
        value={incident.status}
        disabled={isSaving}
        aria-busy={isSaving}
        onChange={(event) => {
          const next = event.target.value;
          if (isIncidentStatus(next) && next !== incident.status) {
            void handleChange(next);
          }
        }}
        className={`min-h-10 rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
          error
            ? "border-red-300 text-red-700"
            : "border-[color:var(--border-soft)] text-[color:var(--foreground)]"
        }`}
      >
        {/* The current status, so the select reads as the row's state and not as an empty command. */}
        <option value={incident.status}>{formatStatusLabel(incident.status)}</option>
        {transitions.map((status) => (
          <option key={status} value={status}>
            Move to {formatStatusLabel(status)}
          </option>
        ))}
      </select>

      <span aria-live="polite" className="block">
        {isSaving ? <span className="text-xs text-slate-500">Saving...</span> : null}
        {!isSaving && error ? <span className="text-xs font-semibold text-red-600">{error}</span> : null}
      </span>

      {!isSaving && isGone ? (
        <button
          type="button"
          onClick={onRefresh}
          className="self-start rounded-lg border border-red-300 bg-white px-2.5 py-1 text-xs font-semibold text-red-700 transition hover:bg-red-100"
        >
          Refresh list
        </button>
      ) : null}
    </div>
  );
}
