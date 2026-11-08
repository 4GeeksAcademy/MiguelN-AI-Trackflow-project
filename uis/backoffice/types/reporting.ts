/**
 * Contracts of the business reporting API (`services/reporting/models.py`).
 *
 * `WeeklyPerformance` is BRIEFING section 6's shape: one entry per warehouse
 * and client for one ISO week (`week_start` is that week's Monday, UTC).
 */

export type ReportingWarehouse = "Monterrey" | "Zaragoza";

export type WeeklyPerformanceEntry = {
  warehouse: ReportingWarehouse;
  client_id: string;
  inbound_units_count: number;
  outbound_orders_count: number;
  stockout_events_count: number;
  discrepancy_events_count: number;
  discrepancy_rate: number;
};

export type WeeklyPerformance = {
  week_start: string;
  entries: WeeklyPerformanceEntry[];
};

/** The part of `GET /reporting/pipeline-runs/latest` the dashboard reads. */
export type PipelineRunStatus = {
  status: "running" | "completed" | "failed" | "skipped";
  finished_at: string | null;
  weeks_from: string;
  weeks_to: string;
  last_closed_week: string;
  stale: boolean;
  quality_status: "ok" | "degraded" | null;
};
