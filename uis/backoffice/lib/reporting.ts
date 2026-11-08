/**
 * The Weekly Warehouse & Client Performance report, as the dashboard reads it.
 *
 * Every call goes to `services/reporting/` with the session token. Responses
 * are checked before use: a shape the dashboard cannot read is an error, never
 * a page of wrong numbers. The KPI names and what each one measures are
 * BRIEFING section 2's ("KPIs to Measure"), worded for the CEO and the Head of
 * Warehouse Operations rather than for engineers.
 */

import {
  ApiError,
  parseResponseJson,
  requestAuthenticatedApi,
} from "@/lib/api-client";
import { describeError, type ErrorCopy } from "@/lib/friendly-error";
import type {
  PipelineRunStatus,
  ReportingWarehouse,
  WeeklyPerformance,
  WeeklyPerformanceEntry,
} from "@/types/reporting";

export const REPORTING_PATH = "/reporting";
const WEEKLY_PERFORMANCE_API = "/reporting/weekly-warehouse-client-performance";
const PIPELINE_STATUS_API = "/reporting/pipeline-runs/latest";

export const REPORT_TITLE = "Weekly Warehouse & Client Performance";

export type KpiKey =
  | "inbound_units_count"
  | "outbound_orders_count"
  | "stockout_events_count"
  | "discrepancy_rate";

export type KpiDefinition = {
  key: KpiKey;
  /** Exactly the name BRIEFING section 2 gives the KPI. */
  name: string;
  /** BRIEFING's "What it measures", in plain words. */
  measures: string;
};

/** The four KPIs, in BRIEFING's order. */
export const KPIS: readonly KpiDefinition[] = [
  {
    key: "inbound_units_count",
    name: "Inbound Volume",
    measures: "How many units of each client's goods the warehouse received during the week.",
  },
  {
    key: "outbound_orders_count",
    name: "Outbound Throughput",
    measures: "How many orders the warehouse picked and dispatched for each client during the week.",
  },
  {
    key: "stockout_events_count",
    name: "Stockout Frequency",
    measures:
      "How many times during the week one of a client's products in the warehouse fell below its minimum stock level.",
  },
  {
    key: "discrepancy_rate",
    name: "Discrepancy Rate",
    measures:
      "The share of the week's outbound orders linked to a detected inventory discrepancy: stock that did not match the count.",
  },
];

export const REPORTING_ERROR_COPY: ErrorCopy = {
  offline: "Could not reach the reporting service. Check your connection and try again.",
  server:
    "The reporting service is having trouble right now. Please try again in a moment, and contact TrackFlow Tech if it keeps happening.",
};

const WEEK_NOT_CALCULATED =
  "The figures for this week have not been calculated yet. Each week is published on the Monday morning after it ends.";

class ReportingResponseError extends Error {}

// --- Response checks -------------------------------------------------------------

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const WAREHOUSES: readonly ReportingWarehouse[] = ["Monterrey", "Zaragoza"];

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isDate(value: unknown): value is string {
  return typeof value === "string" && ISO_DATE.test(value) && Number.isFinite(Date.parse(value));
}

function isEntry(value: unknown): value is WeeklyPerformanceEntry {
  return (
    record(value) &&
    WAREHOUSES.includes(value.warehouse as ReportingWarehouse) &&
    typeof value.client_id === "string" &&
    value.client_id.trim().length > 0 &&
    isCount(value.inbound_units_count) &&
    isCount(value.outbound_orders_count) &&
    isCount(value.stockout_events_count) &&
    isCount(value.discrepancy_events_count) &&
    typeof value.discrepancy_rate === "number" &&
    Number.isFinite(value.discrepancy_rate) &&
    value.discrepancy_rate >= 0
  );
}

export function isWeeklyPerformance(value: unknown): value is WeeklyPerformance {
  return (
    record(value) &&
    isDate(value.week_start) &&
    Array.isArray(value.entries) &&
    value.entries.every(isEntry)
  );
}

export function isPipelineRunStatus(value: unknown): value is PipelineRunStatus {
  return (
    record(value) &&
    ["running", "completed", "failed", "skipped"].includes(value.status as string) &&
    (value.finished_at === null || typeof value.finished_at === "string") &&
    isDate(value.weeks_from) &&
    isDate(value.weeks_to) &&
    isDate(value.last_closed_week) &&
    typeof value.stale === "boolean" &&
    (value.quality_status === null || value.quality_status === "ok" || value.quality_status === "degraded")
  );
}

// --- Requests ----------------------------------------------------------------------

async function reportingRequest<T>(path: string, check: (value: unknown) => value is T): Promise<T> {
  const response = await requestAuthenticatedApi(path, { cache: "no-store" });
  const value = await parseResponseJson(response);
  if (!check(value)) {
    throw new ReportingResponseError(
      "The reporting service returned figures this page cannot read. Please try again, and contact TrackFlow Tech if it keeps happening.",
    );
  }
  return value;
}

/** One week's KPIs. Without `weekStart`, the most recent week that has been calculated. */
export function getWeeklyPerformance(weekStart?: string): Promise<WeeklyPerformance> {
  const query = weekStart ? `?week_start=${encodeURIComponent(weekStart)}` : "";
  return reportingRequest(`${WEEKLY_PERFORMANCE_API}${query}`, isWeeklyPerformance);
}

/** The latest pipeline run, or null when the pipeline has never run. */
export async function getPipelineStatus(): Promise<PipelineRunStatus | null> {
  try {
    return await reportingRequest(PIPELINE_STATUS_API, isPipelineRunStatus);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export function describeReportingError(error: unknown): string {
  if (error instanceof ReportingResponseError) return error.message;
  // The API's 404 sentences ("That week has not been computed") are written
  // for developers; leadership gets the same fact in its own words.
  if (error instanceof ApiError && error.status === 404) return WEEK_NOT_CALCULATED;
  return describeError(error, REPORTING_ERROR_COPY.server, REPORTING_ERROR_COPY).message;
}

// --- Weeks -------------------------------------------------------------------------

const DAY_MS = 24 * 60 * 60 * 1000;

function utcDate(isoDate: string): Date {
  return new Date(`${isoDate}T00:00:00Z`);
}

/** The Monday `weeks` weeks away from `weekStart`, as `YYYY-MM-DD`. */
export function shiftWeek(weekStart: string, weeks: number): string {
  return new Date(utcDate(weekStart).getTime() + weeks * 7 * DAY_MS).toISOString().slice(0, 10);
}

const DAY_FORMAT = new Intl.DateTimeFormat("en-GB", {
  weekday: "long",
  day: "numeric",
  month: "long",
  year: "numeric",
  timeZone: "UTC",
});

/** "Monday, 12 October 2026 to Sunday, 18 October 2026": the days the week covers. */
export function formatWeekPeriod(weekStart: string): string {
  const monday = utcDate(weekStart);
  const sunday = new Date(monday.getTime() + 6 * DAY_MS);
  return `${DAY_FORMAT.format(monday)} to ${DAY_FORMAT.format(sunday)}`;
}

const MOMENT_FORMAT = new Intl.DateTimeFormat("en-GB", {
  weekday: "long",
  day: "numeric",
  month: "long",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
  timeZoneName: "short",
});

export function formatUpdatedAt(timestamp: string): string | null {
  const moment = new Date(timestamp);
  return Number.isFinite(moment.getTime()) ? MOMENT_FORMAT.format(moment) : null;
}

// --- Figures -----------------------------------------------------------------------

/** `stride-athletics` -> "Stride Athletics": the client as people say it. */
export function clientName(clientId: string): string {
  return clientId
    .split("-")
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

const COUNT_FORMAT = new Intl.NumberFormat("en-US");
const RATE_FORMAT = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 2 });

export function formatCount(value: number): string {
  return COUNT_FORMAT.format(value);
}

/** The pipeline stores the rate as a fraction (0.375); people read a percentage (37.5%). */
export function formatRate(rate: number): string {
  return RATE_FORMAT.format(rate);
}

export type WeekTotals = {
  inbound_units_count: number;
  outbound_orders_count: number;
  stockout_events_count: number;
  discrepancy_events_count: number;
  /** BRIEFING's definition over the whole week: all discrepancies / all outbound orders, 0 without orders. */
  discrepancy_rate: number;
};

export function weekTotals(entries: readonly WeeklyPerformanceEntry[]): WeekTotals {
  const sum = (key: keyof Omit<WeekTotals, "discrepancy_rate">) =>
    entries.reduce((total, entry) => total + entry[key], 0);
  const orders = sum("outbound_orders_count");
  const discrepancies = sum("discrepancy_events_count");
  return {
    inbound_units_count: sum("inbound_units_count"),
    outbound_orders_count: orders,
    stockout_events_count: sum("stockout_events_count"),
    discrepancy_events_count: discrepancies,
    discrepancy_rate: orders > 0 ? discrepancies / orders : 0,
  };
}

/** The headline of one KPI for the whole week, written out. */
export function formatKpiTotal(kpi: KpiKey, totals: WeekTotals): string {
  switch (kpi) {
    case "inbound_units_count":
      return `${formatCount(totals.inbound_units_count)} ${totals.inbound_units_count === 1 ? "unit" : "units"} received`;
    case "outbound_orders_count":
      return `${formatCount(totals.outbound_orders_count)} ${totals.outbound_orders_count === 1 ? "order" : "orders"} dispatched`;
    case "stockout_events_count":
      return `${formatCount(totals.stockout_events_count)} ${totals.stockout_events_count === 1 ? "time" : "times"} below minimum stock`;
    case "discrepancy_rate":
      return `${formatRate(totals.discrepancy_rate)} of orders`;
  }
}

/** One client's value of a KPI, written out (the rate with the counts behind it). */
export function formatKpiValue(kpi: KpiKey, entry: WeeklyPerformanceEntry): string {
  if (kpi === "discrepancy_rate") {
    const orders = entry.outbound_orders_count;
    // BRIEFING: the rate is 0 when nothing was shipped; "0 of 0" would read as a glitch.
    if (orders === 0) return `${formatRate(entry.discrepancy_rate)} (no orders dispatched)`;
    return `${formatRate(entry.discrepancy_rate)} (${formatCount(entry.discrepancy_events_count)} of ${formatCount(orders)} ${orders === 1 ? "order" : "orders"})`;
  }
  return formatCount(entry[kpi]);
}

/** The week has nothing to show for this KPI: no units, orders, crossings or discrepancies. */
export function kpiIsEmpty(kpi: KpiKey, totals: WeekTotals): boolean {
  return kpi === "discrepancy_rate" ? totals.discrepancy_events_count === 0 : totals[kpi] === 0;
}

export const EMPTY_KPI_NOTE: Record<KpiKey, string> = {
  inbound_units_count: "No goods were received this week.",
  outbound_orders_count: "No orders were dispatched this week.",
  stockout_events_count: "No product fell below its minimum stock this week.",
  discrepancy_rate: "No inventory discrepancy was detected this week.",
};

/** Entries grouped by warehouse, in the API's order (warehouse, then client). */
export function byWarehouse(
  entries: readonly WeeklyPerformanceEntry[],
): Array<{ warehouse: ReportingWarehouse; entries: WeeklyPerformanceEntry[] }> {
  return WAREHOUSES.map((warehouse) => ({
    warehouse,
    entries: entries.filter((entry) => entry.warehouse === warehouse),
  })).filter((group) => group.entries.length > 0);
}
