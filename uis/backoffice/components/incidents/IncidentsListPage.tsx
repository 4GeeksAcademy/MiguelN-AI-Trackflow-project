"use client";

import { StateMessage } from "@/components/common/StateMessage";
import { IncidentStatusControl } from "@/components/incidents/IncidentStatusControl";
import { IncidentSummaryPanel } from "@/components/incidents/IncidentSummaryPanel";
import { INCIDENT_ERROR_COPY, describeError } from "@/lib/friendly-error";
import {
  countActiveFilters,
  formatBranchLabel,
  formatCategoryLabel,
  formatOriginLabel,
  formatStatusLabel,
  formatTimestamp,
  readIncidentFilters,
} from "@/lib/incident";
import { fetchIncidents } from "@/services/incidents-service";
import type { AsyncStatus } from "@/types/async-state";
import {
  INCIDENT_BRANCHES,
  INCIDENT_ORIGINS,
  INCIDENT_STATUSES,
  type Incident,
  type IncidentFilters,
  type IncidentStatus,
} from "@/types/incident";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

const selectClassName =
  "w-full min-h-12 rounded-xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] px-3 py-2 text-sm text-[color:var(--foreground)] outline-none transition focus:border-[color:var(--brand-primary)]";

/**
 * `useSearchParams` suspends while the query string is resolving, so the page
 * that reads it has to sit behind a boundary of its own - same arrangement as
 * the supplier directory.
 */
export default function IncidentsListPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto w-full max-w-6xl px-6 py-10 md:px-10">
          <StateMessage tone="info">Loading incidents...</StateMessage>
        </main>
      }
    >
      <IncidentsListContent />
    </Suspense>
  );
}

function IncidentsListContent() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [listStatus, setListStatus] = useState<AsyncStatus>("loading");
  const [listError, setListError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  // Bumped on every confirmed status change so the metrics panel refetches.
  const [summaryToken, setSummaryToken] = useState(0);

  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const filters: IncidentFilters = readIncidentFilters(new URLSearchParams(searchParams.toString()));
  const activeFilterCount = countActiveFilters(filters);

  const retry = useCallback(() => setAttempt((current) => current + 1), []);

  // The API does the filtering, so every filter change is a fresh request. The
  // three values are read out of the object rather than passed as one so a
  // re-render with an equal-but-new object does not refetch.
  const { status: statusFilter, origin: originFilter, branch: branchFilter } = filters;

  useEffect(() => {
    let active = true;

    async function load(): Promise<void> {
      setListStatus("loading");
      setListError(null);

      try {
        const data = await fetchIncidents({
          status: statusFilter,
          origin: originFilter,
          branch: branchFilter,
        });
        if (!active) {
          return;
        }

        setIncidents(data);
        setListStatus("success");
      } catch (loadError) {
        if (!active) {
          return;
        }

        setListError(describeError(loadError, "Could not load the incident list.", INCIDENT_ERROR_COPY).message);
        setListStatus("error");
      }
    }

    void load();

    return () => {
      active = false;
    };
  }, [statusFilter, originFilter, branchFilter, attempt]);

  function updateQueryFilter(key: keyof IncidentFilters, value: string): void {
    const params = new URLSearchParams(searchParams.toString());

    if (!value) {
      params.delete(key);
    } else {
      params.set(key, value);
    }

    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }

  function clearFilters(): void {
    router.replace(pathname, { scroll: false });
  }

  /**
   * Applies a status to one row. Used twice by the status control: once to show
   * the change immediately, and again with the old value if the request fails.
   */
  function applyStatus(id: string, status: IncidentStatus): void {
    setIncidents((current) =>
      current.map((incident) => (incident.id === id ? { ...incident, status } : incident)),
    );
  }

  /**
   * Swaps in the confirmed row from the API, which also refreshes `updated_at`.
   *
   * The row stays in the list even when the new status no longer matches an
   * active filter. Dropping it would make the row the reader just acted on
   * vanish under the pointer; it disappears on the next load instead, which is
   * the same thing the supplier directory does.
   */
  function replaceIncident(updated: Incident): void {
    setIncidents((current) =>
      current.map((incident) => (incident.id === updated.id ? updated : incident)),
    );
    setSummaryToken((current) => current + 1);
  }

  const isFirstLoad = listStatus === "loading" && incidents.length === 0;

  return (
    <main className="mx-auto w-full max-w-6xl px-6 py-10 md:px-10">
      <div className="space-y-6">
        <section className="rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)]/90 p-6 shadow-[0_24px_90px_rgba(37,99,235,0.12)] backdrop-blur md:p-8">
          <p className="font-mono text-xs uppercase tracking-[0.22em] text-[color:var(--brand-primary)]">
            Incident Manager
          </p>
          <h1 className="mt-3 text-3xl font-semibold leading-tight text-[color:var(--foreground)] md:text-4xl">
            Every operational incident, in one place.
          </h1>
          <p className="mt-3 max-w-3xl text-sm text-slate-600 md:text-base">
            Lost parcels, carrier failures, inventory discrepancies and customer complaints - logged
            from the warehouse floor or from headquarters, filtered by state, origin and branch, and
            moved along their lifecycle without leaving the table.
          </p>

          <Link
            href="/incidents/new"
            className="mt-6 inline-flex min-h-12 items-center rounded-xl bg-[color:var(--brand-primary)] px-6 py-3 text-base font-semibold text-white transition hover:bg-[color:var(--brand-secondary)]"
          >
            Report an incident
          </Link>
        </section>

        {/* Its own request and its own states, so a failure here leaves the list below working. */}
        <IncidentSummaryPanel reloadToken={summaryToken} />

        <section className="rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-6 shadow-[0_16px_38px_-28px_rgba(37,99,235,0.35)] md:p-8">
          <header className="mb-6 flex flex-col gap-4 border-b border-[color:var(--border-soft)] pb-5 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h2 className="text-2xl font-semibold text-[color:var(--foreground)]">Incidents</h2>
              <p className="mt-1 text-sm text-slate-600" aria-live="polite">
                {listStatus === "loading"
                  ? "Loading incidents..."
                  : `${incidents.length} ${incidents.length === 1 ? "incident" : "incidents"}${
                      activeFilterCount > 0 ? " match the filters" : " on record"
                    }`}
              </p>
            </div>

            {activeFilterCount > 0 ? (
              <button
                type="button"
                onClick={clearFilters}
                className="self-start rounded-xl border border-[color:var(--border-soft)] px-4 py-2 text-sm font-semibold text-slate-600 transition hover:bg-[color:var(--brand-primary)]/5"
              >
                Clear {activeFilterCount === 1 ? "filter" : "filters"}
              </button>
            ) : null}
          </header>

          <div className="mb-6 grid gap-3 sm:grid-cols-3">
            <label className="block text-sm text-slate-600">
              <span className="mb-1 block font-medium text-[color:var(--foreground)]">Status</span>
              <select
                value={filters.status}
                onChange={(event) => updateQueryFilter("status", event.target.value)}
                className={selectClassName}
              >
                <option value="">All statuses</option>
                {INCIDENT_STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {formatStatusLabel(status)}
                  </option>
                ))}
              </select>
            </label>

            <label className="block text-sm text-slate-600">
              <span className="mb-1 block font-medium text-[color:var(--foreground)]">Origin</span>
              <select
                value={filters.origin}
                onChange={(event) => updateQueryFilter("origin", event.target.value)}
                className={selectClassName}
              >
                <option value="">All origins</option>
                {INCIDENT_ORIGINS.map((origin) => (
                  <option key={origin} value={origin}>
                    {formatOriginLabel(origin)}
                  </option>
                ))}
              </select>
            </label>

            <label className="block text-sm text-slate-600">
              <span className="mb-1 block font-medium text-[color:var(--foreground)]">Branch</span>
              <select
                value={filters.branch}
                onChange={(event) => updateQueryFilter("branch", event.target.value)}
                className={selectClassName}
              >
                <option value="">All branches</option>
                {INCIDENT_BRANCHES.map((branch) => (
                  <option key={branch} value={branch}>
                    {formatBranchLabel(branch)}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {isFirstLoad ? <StateMessage tone="info">Loading incidents...</StateMessage> : null}

          {/*
            A failed load keeps whatever was already listed and offers a retry, so
            the page never goes blank on a dropped request.
          */}
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
              {incidents.length > 0 ? (
                <p className="mt-3 text-xs">The incidents below are from the last successful load.</p>
              ) : null}
            </StateMessage>
          ) : null}

          {/* Never an empty table: an empty result says why it is empty. */}
          {listStatus === "success" && incidents.length === 0 ? (
            <StateMessage tone="info">
              {activeFilterCount > 0 ? (
                <>
                  <p className="font-semibold">No incidents match these filters.</p>
                  <p className="mt-1">
                    Nothing on record is{" "}
                    {[
                      filters.status ? `"${formatStatusLabel(filters.status)}"` : null,
                      filters.origin ? `from "${formatOriginLabel(filters.origin)}"` : null,
                      filters.branch ? `at "${formatBranchLabel(filters.branch)}"` : null,
                    ]
                      .filter(Boolean)
                      .join(", ")}
                    . Try widening the filters or clearing them.
                  </p>
                  <button
                    type="button"
                    onClick={clearFilters}
                    className="mt-3 inline-flex min-h-10 items-center rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-100"
                  >
                    Clear filters
                  </button>
                </>
              ) : (
                <>
                  <p className="font-semibold">No incidents have been registered yet.</p>
                  <p className="mt-1">
                    When something goes wrong in a warehouse, with a carrier or with a customer
                    order, report it here and it will show up in this table and in the metrics above.
                  </p>
                  <Link
                    href="/incidents/new"
                    className="mt-3 inline-flex min-h-10 items-center rounded-xl bg-[color:var(--brand-primary)] px-4 py-2 text-sm font-semibold text-white transition hover:bg-[color:var(--brand-secondary)]"
                  >
                    Report the first incident
                  </Link>
                </>
              )}
            </StateMessage>
          ) : null}

          {incidents.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[56rem] border-collapse text-left text-sm">
                <caption className="sr-only">
                  Registered incidents, with the status of each one editable in the last column.
                </caption>
                <thead>
                  <tr className="border-b border-[color:var(--border-soft)] text-xs uppercase tracking-[0.14em] text-slate-500">
                    <th scope="col" className="py-3 pr-4 font-semibold">
                      Incident
                    </th>
                    <th scope="col" className="py-3 pr-4 font-semibold">
                      Category
                    </th>
                    <th scope="col" className="py-3 pr-4 font-semibold">
                      Origin
                    </th>
                    <th scope="col" className="py-3 pr-4 font-semibold">
                      Branch
                    </th>
                    <th scope="col" className="py-3 pr-4 font-semibold">
                      Reported
                    </th>
                    <th scope="col" className="py-3 font-semibold">
                      Status
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {incidents.map((incident) => (
                    <tr
                      key={incident.id}
                      className="border-b border-[color:var(--border-soft)]/60 align-top text-slate-700"
                    >
                      <td className="py-4 pr-4">
                        <p className="font-semibold text-[color:var(--foreground)]">{incident.title}</p>
                        {/*
                          Seeded incidents have no title of their own - the CSV
                          had only a description, so short ones became both.
                          Printing the same sentence twice reads as a rendering
                          fault, so the description only appears when it says
                          something the title did not.
                        */}
                        {incident.description !== incident.title ? (
                          <p className="mt-1 max-w-md text-xs leading-relaxed text-slate-500">
                            {incident.description}
                          </p>
                        ) : null}
                      </td>
                      <td className="py-4 pr-4">{formatCategoryLabel(incident.category)}</td>
                      <td className="py-4 pr-4">{formatOriginLabel(incident.origin)}</td>
                      <td className="py-4 pr-4">{formatBranchLabel(incident.branch)}</td>
                      <td className="py-4 pr-4 text-xs text-slate-500">
                        <p>{formatTimestamp(incident.created_at)}</p>
                        {incident.updated_at !== incident.created_at ? (
                          <p className="mt-1">Updated {formatTimestamp(incident.updated_at)}</p>
                        ) : null}
                      </td>
                      <td className="py-4">
                        <IncidentStatusControl
                          incident={incident}
                          onStatusChange={applyStatus}
                          onConfirmed={replaceIncident}
                          onRefresh={retry}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      </div>
    </main>
  );
}
