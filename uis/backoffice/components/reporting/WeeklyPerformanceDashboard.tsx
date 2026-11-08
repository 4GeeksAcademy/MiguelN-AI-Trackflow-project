"use client";

import { useEffect, useState, type ReactNode } from "react";
import { StateMessage } from "@/components/common/StateMessage";
import {
  EMPTY_KPI_NOTE,
  KPIS,
  REPORT_TITLE,
  byWarehouse,
  clientName,
  describeReportingError,
  formatCount,
  formatKpiTotal,
  formatKpiValue,
  formatRate,
  formatUpdatedAt,
  formatWeekPeriod,
  getPipelineStatus,
  getWeeklyPerformance,
  kpiIsEmpty,
  shiftWeek,
  weekTotals,
  type KpiDefinition,
  type WeekTotals,
} from "@/lib/reporting";
import type { PipelineRunStatus, WeeklyPerformance, WeeklyPerformanceEntry } from "@/types/reporting";

/*
 * Form, chosen before colour (the same rules as the incident summary panel):
 *
 * - Each KPI leads with one number: its total for the week, written out with
 *   its unit ("174 units received"), so a reader never has to decode a figure.
 * - The breakdown compares magnitude across clients, so it is a table with
 *   bars: one hue (brand blue, validated against the white card), each row
 *   carrying the client's name and value in text. Nothing is reachable only
 *   by hovering, so there is no tooltip layer to add.
 * - One table under the charts holds every figure of the week: the view that
 *   does not depend on reading bar lengths at all.
 * - Warnings about freshness or completeness use the reserved status roles,
 *   each with an icon and words, never colour alone.
 */

const buttonClass =
  "inline-flex min-h-11 items-center justify-center rounded-lg border border-[color:var(--border-soft)] bg-[color:var(--surface)] px-4 py-2 text-sm font-semibold text-[color:var(--brand-primary)] transition hover:bg-blue-50 focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50";

/** Bars are read against the largest value in their own chart. */
function barWidth(value: number, chartMax: number): string {
  if (chartMax <= 0 || value <= 0) return "0%";
  // A non-zero value always shows a sliver, so "1" never looks like "0".
  return `${Math.max((value / chartMax) * 100, 2)}%`;
}

function barValue(kpi: KpiDefinition, entry: WeeklyPerformanceEntry): number {
  return entry[kpi.key];
}

function KpiBreakdown({ kpi, entries }: { kpi: KpiDefinition; entries: WeeklyPerformanceEntry[] }) {
  const chartMax = Math.max(0, ...entries.map((entry) => barValue(kpi, entry)));

  return (
    <div className="mt-4 space-y-4">
      {byWarehouse(entries).map((group) => (
        <div key={group.warehouse}>
          <h4 className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
            {group.warehouse} warehouse
          </h4>
          {/* Single hue, so the card's heading names what is plotted and no legend is needed. */}
          <dl className="mt-2 space-y-2">
            {group.entries.map((entry) => {
              const value = barValue(kpi, entry);
              return (
                <div
                  key={`${entry.warehouse}-${entry.client_id}`}
                  className="grid grid-cols-[minmax(7rem,1fr)_auto] items-center gap-x-3 gap-y-1 rounded-lg px-1 py-0.5 transition hover:bg-[color:var(--brand-primary)]/5"
                >
                  <dt className={`text-sm ${value > 0 ? "text-slate-700" : "text-slate-500"}`}>
                    {clientName(entry.client_id)}
                  </dt>
                  <dd
                    className={`text-right text-sm font-semibold tabular-nums ${
                      value > 0 ? "text-[color:var(--foreground)]" : "text-slate-500"
                    }`}
                  >
                    {formatKpiValue(kpi.key, entry)}
                  </dd>
                  {/* The bar restates the number beside it, so it is not announced twice. */}
                  <div
                    aria-hidden="true"
                    className="col-span-2 h-2 overflow-hidden rounded-full bg-[color:var(--brand-primary)]/10"
                  >
                    <div
                      className="h-full rounded-r-full bg-[color:var(--brand-primary)]"
                      style={{ width: barWidth(value, chartMax) }}
                    />
                  </div>
                </div>
              );
            })}
          </dl>
        </div>
      ))}
    </div>
  );
}

function KpiCard({
  kpi,
  totals,
  entries,
}: {
  kpi: KpiDefinition;
  totals: WeekTotals;
  entries: WeeklyPerformanceEntry[];
}) {
  const headingId = `kpi-${kpi.key}`;

  return (
    <section
      aria-labelledby={headingId}
      className="rounded-2xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-5 shadow-sm"
    >
      <h3 id={headingId} className="text-lg font-semibold text-[color:var(--foreground)]">
        {kpi.name}
      </h3>
      <p className="mt-1 text-sm text-slate-600">{kpi.measures}</p>
      <p className="mt-4 text-2xl font-semibold text-[color:var(--foreground)]">
        {formatKpiTotal(kpi.key, totals)}
      </p>
      {kpi.key === "discrepancy_rate" ? (
        <p className="text-sm text-slate-600">
          {formatCount(totals.discrepancy_events_count)}{" "}
          {totals.discrepancy_events_count === 1 ? "discrepancy" : "discrepancies"} across{" "}
          {formatCount(totals.outbound_orders_count)}{" "}
          {totals.outbound_orders_count === 1 ? "order" : "orders"} dispatched
        </p>
      ) : null}
      {kpiIsEmpty(kpi.key, totals) ? (
        <p className="mt-2 text-sm text-slate-600">{EMPTY_KPI_NOTE[kpi.key]}</p>
      ) : null}
      <KpiBreakdown kpi={kpi} entries={entries} />
    </section>
  );
}

function FiguresTable({ report }: { report: WeeklyPerformance }) {
  return (
    <section aria-labelledby="all-figures" className="mt-8">
      <h3 id="all-figures" className="text-lg font-semibold">
        All figures for the week
      </h3>
      <div className="mt-3 overflow-x-auto border-y border-[color:var(--border-soft)] bg-white">
        <table className="w-full min-w-[720px] text-left text-sm">
          <caption className="sr-only">
            {REPORT_TITLE}, {formatWeekPeriod(report.week_start)}, by warehouse and client
          </caption>
          <thead className="border-b border-[color:var(--border-soft)] bg-slate-50 text-slate-600">
            <tr>
              <th scope="col" className="px-3 py-3 font-semibold">Warehouse</th>
              <th scope="col" className="px-3 py-3 font-semibold">Client</th>
              <th scope="col" className="px-3 py-3 text-right font-semibold">Inbound Volume<span className="block text-xs font-normal">units received</span></th>
              <th scope="col" className="px-3 py-3 text-right font-semibold">Outbound Throughput<span className="block text-xs font-normal">orders dispatched</span></th>
              <th scope="col" className="px-3 py-3 text-right font-semibold">Stockout Frequency<span className="block text-xs font-normal">times below minimum</span></th>
              <th scope="col" className="px-3 py-3 text-right font-semibold">Discrepancy Rate<span className="block text-xs font-normal">discrepancies / orders</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {report.entries.map((entry) => (
              <tr key={`${entry.warehouse}-${entry.client_id}`}>
                <td className="px-3 py-3">{entry.warehouse}</td>
                <th scope="row" className="px-3 py-3 font-semibold">{clientName(entry.client_id)}</th>
                <td className="px-3 py-3 text-right tabular-nums">{formatCount(entry.inbound_units_count)}</td>
                <td className="px-3 py-3 text-right tabular-nums">{formatCount(entry.outbound_orders_count)}</td>
                <td className="px-3 py-3 text-right tabular-nums">{formatCount(entry.stockout_events_count)}</td>
                <td className="px-3 py-3 text-right tabular-nums">
                  {formatRate(entry.discrepancy_rate)}
                  <span className="block text-xs text-slate-500">
                    {entry.outbound_orders_count > 0
                      ? `${formatCount(entry.discrepancy_events_count)} of ${formatCount(entry.outbound_orders_count)}`
                      : "no orders"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** Icon plus words, so a warning is never carried by its colour alone. */
function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-4 text-sm text-slate-700">
      <span
        aria-hidden="true"
        className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[color:var(--status-warning)] text-xs font-bold text-[color:var(--foreground)]"
      >
        !
      </span>
      <p>
        <span className="font-semibold">Please note: </span>
        {children}
      </p>
    </div>
  );
}

function RunNotices({ status, week }: { status: PipelineRunStatus | null; week: string }) {
  if (!status) return null;
  const coversWeek = status.weeks_from <= week && week <= status.weeks_to;
  const updatedAt = status.status === "completed" && status.finished_at ? formatUpdatedAt(status.finished_at) : null;

  return (
    <div className="mt-4 space-y-3">
      {status.stale ? (
        <Notice>
          the week of {formatWeekPeriod(status.last_closed_week)} has not been calculated yet. The figures
          shown are the most recent available.
        </Notice>
      ) : null}
      {coversWeek && status.status === "completed" && status.quality_status === "degraded" ? (
        <Notice>
          some activity may be missing from this week&apos;s figures. The system could not confirm that every
          warehouse order was recorded.
        </Notice>
      ) : null}
      {status.status === "running" ? (
        <p className="text-sm text-slate-600" role="status">The figures are being recalculated right now.</p>
      ) : null}
      {updatedAt && coversWeek ? <p className="text-sm text-slate-600">Last updated {updatedAt}.</p> : null}
    </div>
  );
}

export function WeeklyPerformanceDashboard() {
  // undefined asks the API for the most recent calculated week.
  const [requestedWeek, setRequestedWeek] = useState<string | undefined>(undefined);
  const [attempt, setAttempt] = useState(0);
  const [report, setReport] = useState<WeeklyPerformance | null>(null);
  const [latestWeek, setLatestWeek] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<PipelineRunStatus | null>(null);

  useEffect(() => {
    let active = true;
    getWeeklyPerformance(requestedWeek)
      .then((week) => {
        if (!active) return;
        setReport(week);
        setError(null);
        if (requestedWeek === undefined) setLatestWeek(week.week_start);
      })
      .catch((failure: unknown) => {
        if (active) setError(describeReportingError(failure));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [requestedWeek, attempt]);

  useEffect(() => {
    let active = true;
    // Freshness is supplementary: if it cannot be read, the figures still show.
    getPipelineStatus()
      .then((run) => {
        if (active) setStatus(run);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  const shownWeek = requestedWeek ?? report?.week_start ?? null;
  const canGoForward = shownWeek !== null && latestWeek !== null && shownWeek < latestWeek;

  function showWeek(week: string) {
    setLoading(true);
    setError(null);
    setRequestedWeek(week === latestWeek ? undefined : week);
  }

  function retry() {
    setLoading(true);
    setError(null);
    setAttempt((current) => current + 1);
  }

  // While another week loads, the figures on screen stay (dimmed) instead of
  // flashing away; once it has answered, only that week's figures are shown.
  const visibleReport = report && (loading || report.week_start === shownWeek) ? report : null;
  const totals = visibleReport ? weekTotals(visibleReport.entries) : null;

  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 md:px-10">
      <header className="mb-6 border-b border-[color:var(--border-soft)] pb-5">
        <p className="mb-2 text-sm font-medium text-slate-600">Executive reporting</p>
        <h1 className="text-2xl font-semibold leading-tight">{REPORT_TITLE}</h1>
        <p className="mt-2 max-w-3xl text-sm text-slate-600">
          How each warehouse performed for each client: goods received, orders dispatched, how often stock ran
          low, and how accurate the inventory was.
        </p>
      </header>

      {/* One row above everything it scopes: the week every figure below belongs to. */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className={buttonClass}
          disabled={shownWeek === null || loading}
          onClick={() => shownWeek && showWeek(shiftWeek(shownWeek, -1))}
        >
          ← Previous week
        </button>
        {/* On a phone the period gets its own line, above the two buttons. */}
        <div className="order-first w-full min-w-0 sm:order-none sm:w-auto sm:flex-1">
          <h2 className="text-base font-semibold" aria-live="polite">
            {shownWeek ? `Week of ${formatWeekPeriod(shownWeek)}` : "Most recent week"}
          </h2>
          <p className="text-xs text-slate-500">Weeks run Monday to Sunday, in UTC.</p>
        </div>
        <button
          type="button"
          className={buttonClass}
          disabled={!canGoForward || loading}
          onClick={() => shownWeek && showWeek(shiftWeek(shownWeek, 1))}
        >
          Next week →
        </button>
      </div>

      {visibleReport ? <RunNotices status={status} week={visibleReport.week_start} /> : null}

      {error ? (
        <div role="alert" className="mt-6">
          <StateMessage tone="error">
            {error}
            <div className="mt-3 flex flex-wrap gap-3">
              <button type="button" className={buttonClass} onClick={retry}>
                Try again
              </button>
              {latestWeek && shownWeek !== latestWeek ? (
                <button type="button" className={buttonClass} onClick={() => showWeek(latestWeek)}>
                  Show the most recent week
                </button>
              ) : null}
            </div>
          </StateMessage>
        </div>
      ) : null}

      {!report && !error ? (
        <p className="mt-6 text-sm text-slate-600" role="status">
          Loading the weekly figures...
        </p>
      ) : null}

      {visibleReport && totals ? (
        <div aria-busy={loading} className={`mt-6 transition-opacity ${loading ? "opacity-60" : ""}`}>
          {visibleReport.entries.length === 0 ? (
            <StateMessage tone="info">
              No client has products in a warehouse yet, so there is nothing to report for this week.
            </StateMessage>
          ) : (
            <>
              <div className="grid gap-5 md:grid-cols-2">
                {KPIS.map((kpi) => (
                  <KpiCard key={kpi.key} kpi={kpi} totals={totals} entries={visibleReport.entries} />
                ))}
              </div>
              <FiguresTable report={visibleReport} />
            </>
          )}
        </div>
      ) : null}
    </main>
  );
}
