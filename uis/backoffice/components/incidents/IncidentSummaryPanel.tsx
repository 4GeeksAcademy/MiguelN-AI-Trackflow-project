"use client";

import { StateMessage } from "@/components/common/StateMessage";
import { INCIDENT_ERROR_COPY, describeError } from "@/lib/friendly-error";
import {
  formatBranchLabel,
  formatCategoryLabel,
  formatOriginLabel,
  formatStatusLabel,
} from "@/lib/incident";
import { fetchIncidentSummary } from "@/services/incidents-service";
import {
  INCIDENT_BRANCHES,
  INCIDENT_CATEGORIES,
  INCIDENT_ORIGINS,
  INCIDENT_STATUSES,
  type IncidentStatus,
  type IncidentSummary,
} from "@/types/incident";
import type { AsyncStatus } from "@/types/async-state";
import { useCallback, useEffect, useState } from "react";

type IncidentSummaryPanelProps = {
  /**
   * Bumped by the page whenever something changed the totals, so the panel
   * refetches. It is a number rather than a callback because the panel owns its
   * own request and its own states - the page only says "this is stale now".
   */
  reloadToken?: number;
};

/*
 * Form, chosen before colour:
 *
 * - `total` is the one number the panel leads with, so it is a hero figure and
 *   the only one on the page.
 * - The four breakdowns compare magnitude across named classes. Category has
 *   nine of them, past the point where more colours help, so all four are a
 *   table with bars: one hue, the label and the count always written out.
 *   Nothing is reachable only by hovering, so there is no tooltip to add - the
 *   value is already on the row.
 * - Status is the exception that earns colour, because its classes are states
 *   rather than identities: unattended, being handled, closed, dropped. Those
 *   use the reserved status roles, each with an icon and a label, so the state
 *   never rides on hue alone.
 */

/** Bars are read against the largest value in their own group, not the total. */
function barWidth(value: number, groupMax: number): string {
  if (groupMax <= 0 || value <= 0) {
    return "0%";
  }

  // A non-zero count always shows a sliver, so "1" never looks like "0".
  return `${Math.max((value / groupMax) * 100, 2)}%`;
}

type CountRow = {
  key: string;
  label: string;
  value: number;
};

function CountBreakdown({ title, rows }: { title: string; rows: CountRow[] }) {
  const groupMax = Math.max(0, ...rows.map((row) => row.value));

  return (
    <section>
      <h3 className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">{title}</h3>
      {/* Single hue, so the heading names what is plotted and no legend is needed. */}
      <dl className="mt-3 space-y-2">
        {rows.map((row) => (
          <div
            key={row.key}
            className="grid grid-cols-[minmax(7rem,1fr)_auto] items-center gap-x-3 gap-y-1 rounded-lg px-1 py-0.5 transition hover:bg-[color:var(--brand-primary)]/5"
          >
            <dt className={`text-sm ${row.value > 0 ? "text-slate-700" : "text-slate-400"}`}>
              {row.label}
            </dt>
            <dd
              className={`text-sm font-semibold tabular-nums ${
                row.value > 0 ? "text-[color:var(--foreground)]" : "text-slate-400"
              }`}
            >
              {row.value}
            </dd>
            {/*
              The bar restates the number it sits under, so it is hidden from
              assistive tech rather than announced twice.
            */}
            <div
              aria-hidden="true"
              className="col-span-2 h-2 overflow-hidden rounded-full bg-[color:var(--brand-primary)]/10"
            >
              <div
                className="h-full rounded-r-full bg-[color:var(--brand-primary)]"
                style={{ width: barWidth(row.value, groupMax) }}
              />
            </div>
          </div>
        ))}
      </dl>
    </section>
  );
}

/** Icon plus label, so a status is never carried by its colour alone. */
const STATUS_MARKS: Record<IncidentStatus, { token: string; glyph: string; meaning: string }> = {
  open: { token: "--status-critical", glyph: "!", meaning: "Awaiting assignment" },
  in_progress: { token: "--status-warning", glyph: "→", meaning: "Being handled" },
  resolved: { token: "--status-good", glyph: "✓", meaning: "Closed out" },
  discarded: { token: "--status-muted", glyph: "×", meaning: "Not actionable" },
};

function StatusBreakdown({ summary }: { summary: IncidentSummary }) {
  return (
    <section>
      <h3 className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">By status</h3>
      <dl className="mt-3 grid gap-2 sm:grid-cols-2">
        {INCIDENT_STATUSES.map((status) => {
          const mark = STATUS_MARKS[status];
          const count = summary.by_status[status];

          return (
            <div
              key={status}
              className="flex items-center gap-3 rounded-xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] px-3 py-2.5"
            >
              <span
                aria-hidden="true"
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-bold text-white"
                style={{ backgroundColor: `var(${mark.token})` }}
              >
                {mark.glyph}
              </span>
              <div className="min-w-0 flex-1">
                <dt className="truncate text-sm font-semibold text-[color:var(--foreground)]">
                  {formatStatusLabel(status)}
                </dt>
                <p className="truncate text-xs text-slate-500">{mark.meaning}</p>
              </div>
              <dd className="text-xl font-semibold tabular-nums text-[color:var(--foreground)]">
                {count}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}

export function IncidentSummaryPanel({ reloadToken = 0 }: IncidentSummaryPanelProps) {
  const [summary, setSummary] = useState<IncidentSummary | null>(null);
  const [status, setStatus] = useState<AsyncStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const retry = useCallback(() => setAttempt((current) => current + 1), []);

  useEffect(() => {
    let active = true;

    async function load(): Promise<void> {
      // A refresh keeps the numbers already on screen and only marks itself
      // busy, so the panel does not collapse and reflow the page under the
      // reader every time a status changes.
      setStatus("loading");
      setError(null);

      try {
        const data = await fetchIncidentSummary();
        if (!active) {
          return;
        }

        setSummary(data);
        setStatus("success");
      } catch (loadError) {
        if (!active) {
          return;
        }

        setError(describeError(loadError, "Could not load the incident metrics.", INCIDENT_ERROR_COPY).message);
        setStatus("error");
      }
    }

    void load();

    return () => {
      active = false;
    };
  }, [reloadToken, attempt]);

  const isRefreshing = status === "loading" && summary !== null;

  return (
    <section
      aria-labelledby="incident-summary-heading"
      className="rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-6 shadow-[0_16px_38px_-28px_rgba(37,99,235,0.35)] md:p-8"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2
            id="incident-summary-heading"
            className="text-2xl font-semibold text-[color:var(--foreground)]"
          >
            Summary
          </h2>
          <p className="mt-1 text-sm text-slate-600">
            Every incident on record, whatever the filters below are showing.
          </p>
        </div>

        <span aria-live="polite" className="text-xs text-slate-500">
          {isRefreshing ? "Refreshing..." : null}
        </span>
      </header>

      {/* First load: no numbers yet, so the panel says so rather than showing an empty grid. */}
      {status === "loading" && summary === null ? (
        <div className="mt-6">
          <StateMessage tone="info">Loading the incident metrics...</StateMessage>
        </div>
      ) : null}

      {/*
        An error keeps whatever was last loaded on screen underneath, so a failed
        refresh degrades to stale-but-labelled instead of wiping the panel - and
        the list and the form on this page are untouched either way.
      */}
      {status === "error" && error ? (
        <div className="mt-6">
          <StateMessage tone="error">
            <p>{error}</p>
            <button
              type="button"
              onClick={retry}
              className="mt-3 inline-flex min-h-10 items-center rounded-xl border border-red-300 bg-white px-4 py-2 text-sm font-semibold text-red-700 transition hover:bg-red-100"
            >
              Retry
            </button>
            {summary ? <p className="mt-3 text-xs">Showing the last figures that loaded.</p> : null}
          </StateMessage>
        </div>
      ) : null}

      {summary ? (
        <div className={status === "error" ? "mt-6 opacity-60" : "mt-6"}>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
            Incidents on record
          </p>
          {/* The hero figure: the one number this view leads with. */}
          <p className="mt-1 text-5xl font-semibold leading-none text-[color:var(--foreground)]">
            {summary.total}
          </p>

          <div className="mt-8 space-y-8">
            <StatusBreakdown summary={summary} />

            <p className="text-xs text-slate-500">
              In the three breakdowns below, each bar is drawn against the largest value in its own
              group.
            </p>

            <div className="grid gap-8 lg:grid-cols-3">
              <CountBreakdown
                title="By category"
                rows={INCIDENT_CATEGORIES.map((category) => ({
                  key: category,
                  label: formatCategoryLabel(category),
                  value: summary.by_category[category],
                }))}
              />
              <CountBreakdown
                title="By origin"
                rows={INCIDENT_ORIGINS.map((origin) => ({
                  key: origin,
                  label: formatOriginLabel(origin),
                  value: summary.by_origin[origin],
                }))}
              />
              <CountBreakdown
                title="By branch"
                rows={INCIDENT_BRANCHES.map((branch) => ({
                  key: branch,
                  label: formatBranchLabel(branch),
                  value: summary.by_branch[branch],
                }))}
              />
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
