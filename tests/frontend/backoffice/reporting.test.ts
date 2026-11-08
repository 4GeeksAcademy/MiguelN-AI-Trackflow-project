import {
  KPIS,
  clientName,
  describeReportingError,
  formatKpiTotal,
  formatKpiValue,
  formatRate,
  formatWeekPeriod,
  getPipelineStatus,
  getWeeklyPerformance,
  kpiIsEmpty,
  shiftWeek,
  weekTotals,
} from "@/lib/reporting";
import { ApiError, NetworkError } from "@/lib/api-client";
import { clearToken, storeToken } from "@/lib/auth-storage";
import type { WeeklyPerformanceEntry } from "@/types/reporting";
import { installBrowser, removeBrowser } from "../shared/support/browser";
import { API, jsonResponse, scriptFetch, textResponse } from "../shared/support/http";

const stride: WeeklyPerformanceEntry = {
  warehouse: "Monterrey",
  client_id: "stride-athletics",
  inbound_units_count: 174,
  outbound_orders_count: 8,
  stockout_events_count: 2,
  discrepancy_events_count: 3,
  discrepancy_rate: 0.375,
};
const coco: WeeklyPerformanceEntry = {
  warehouse: "Monterrey",
  client_id: "coco-beauty",
  inbound_units_count: 30,
  outbound_orders_count: 3,
  stockout_events_count: 0,
  discrepancy_events_count: 1,
  discrepancy_rate: 0.3333,
};
const week = { week_start: "2026-10-12", entries: [coco, stride] };
const run = {
  pipeline_name: "weekly_warehouse_client_performance",
  status: "completed",
  trigger: "schedule",
  started_at: "2026-10-19T12:00:03Z",
  finished_at: "2026-10-19T12:00:41Z",
  weeks_from: "2026-10-05",
  weeks_to: "2026-10-12",
  last_closed_week: "2026-10-12",
  stale: false,
  quality_status: "ok",
};

beforeEach(() => { installBrowser(); storeToken("test-session"); });
afterEach(() => { clearToken(); removeBrowser(); });

it("reads the KPI endpoint with the session token, for the latest or a chosen week", async () => {
  const requests = scriptFetch(jsonResponse(200, week), jsonResponse(200, week));

  expect(await getWeeklyPerformance()).toEqual(week);
  await getWeeklyPerformance("2026-10-12");

  expect(requests.map(({ url }) => url)).toEqual([
    `${API}/reporting/weekly-warehouse-client-performance`,
    `${API}/reporting/weekly-warehouse-client-performance?week_start=2026-10-12`,
  ]);
  expect(requests.every(({ headers }) => headers.Authorization === "Bearer test-session")).toBe(true);
});

it("accepts a calculated week with no clients", async () => {
  scriptFetch(jsonResponse(200, { week_start: "2026-10-12", entries: [] }));
  expect((await getWeeklyPerformance()).entries).toEqual([]);
});

it.each([
  null,
  { entries: [] },
  { week_start: "12/10/2026", entries: [] },
  { ...week, entries: [{ ...stride, inbound_units_count: -1 }] },
  { ...week, entries: [{ ...stride, outbound_orders_count: 2.5 }] },
  { ...week, entries: [{ ...stride, discrepancy_rate: "0.375" }] },
  { ...week, entries: [{ ...stride, warehouse: "los_angeles" }] },
  { ...week, entries: [{ ...stride, client_id: "" }] },
])("refuses figures it cannot read rather than showing wrong numbers: %j", async (body) => {
  scriptFetch(jsonResponse(200, body));
  await expect(getWeeklyPerformance()).rejects.toThrow("cannot read");
});

it("refuses a successful HTML response", async () => {
  scriptFetch(textResponse(200, "<html>Login</html>"));
  await expect(getWeeklyPerformance()).rejects.toThrow("cannot read");
});

it("reads the pipeline status, and treats a pipeline that never ran as no status", async () => {
  const requests = scriptFetch(jsonResponse(200, run), jsonResponse(404, { detail: "The pipeline has never run" }));

  expect(await getPipelineStatus()).toMatchObject({ stale: false, quality_status: "ok" });
  expect(await getPipelineStatus()).toBeNull();
  expect(requests[0].url).toBe(`${API}/reporting/pipeline-runs/latest`);
});

it("explains a week that was never calculated in leadership's words, not the API's", () => {
  const message = describeReportingError(new ApiError(404, { detail: "That week has not been computed" }));
  expect(message).toContain("have not been calculated");
  expect(message).not.toContain("computed");
  expect(describeReportingError(new NetworkError(new TypeError("Failed to fetch")))).toContain(
    "Could not reach the reporting service",
  );
  expect(describeReportingError(new ApiError(503, { detail: "Reporting storage unavailable" }))).toContain(
    "having trouble",
  );
});

it("names the four KPIs exactly as BRIEFING section 2 does, in its order", () => {
  expect(KPIS.map((kpi) => kpi.name)).toEqual([
    "Inbound Volume",
    "Outbound Throughput",
    "Stockout Frequency",
    "Discrepancy Rate",
  ]);
});

it("shows the period a week covers, Monday to Sunday", () => {
  expect(formatWeekPeriod("2026-10-12")).toBe("Monday, 12 October 2026 to Sunday, 18 October 2026");
  expect(formatWeekPeriod("2025-12-29")).toBe("Monday, 29 December 2025 to Sunday, 4 January 2026");
  expect(shiftWeek("2026-10-12", -1)).toBe("2026-10-05");
  expect(shiftWeek("2025-12-29", 1)).toBe("2026-01-05");
});

it("totals the week with BRIEFING's discrepancy rate: all discrepancies over all orders", () => {
  const totals = weekTotals([stride, coco]);

  // (3 + 1) / (8 + 3) = 0.3636..., not the average of 0.375 and 0.3333.
  expect(totals).toEqual({
    inbound_units_count: 204,
    outbound_orders_count: 11,
    stockout_events_count: 2,
    discrepancy_events_count: 4,
    discrepancy_rate: 4 / 11,
  });
  expect(formatKpiTotal("inbound_units_count", totals)).toBe("204 units received");
  expect(formatKpiTotal("outbound_orders_count", totals)).toBe("11 orders dispatched");
  expect(formatKpiTotal("stockout_events_count", totals)).toBe("2 times below minimum stock");
  expect(formatKpiTotal("discrepancy_rate", totals)).toBe("36.36% of orders");
});

it("writes each value as people read it", () => {
  expect(clientName("stride-athletics")).toBe("Stride Athletics");
  expect(formatRate(0.375)).toBe("37.5%");
  expect(formatRate(0.002)).toBe("0.2%");
  expect(formatKpiValue("discrepancy_rate", stride)).toBe("37.5% (3 of 8 orders)");
  expect(formatKpiValue("inbound_units_count", { ...stride, inbound_units_count: 4200 })).toBe("4,200");
});

it("treats a week without orders as a 0% rate, not a division by zero", () => {
  const quiet = { ...stride, outbound_orders_count: 0, discrepancy_events_count: 0, discrepancy_rate: 0 };
  const totals = weekTotals([quiet]);

  expect(totals.discrepancy_rate).toBe(0);
  expect(formatKpiTotal("discrepancy_rate", totals)).toBe("0% of orders");
  expect(formatKpiValue("discrepancy_rate", quiet)).toBe("0% (no orders dispatched)");
  expect(kpiIsEmpty("discrepancy_rate", totals)).toBe(true);
  expect(kpiIsEmpty("inbound_units_count", totals)).toBe(false);
});
