"""Response and request contracts of the reporting endpoints (PIPELINE_DESIGN.md 5.3).

`WeeklyPerformance` is the shape BRIEFING section 6 specifies, with the
monorepo's warehouse values (`Monterrey`, `Zaragoza`).
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class WeeklyPerformanceEntry(BaseModel):
    warehouse: str
    client_id: str
    inbound_units_count: int
    outbound_orders_count: int
    stockout_events_count: int
    discrepancy_events_count: int
    discrepancy_rate: float


class WeeklyPerformance(BaseModel):
    week_start: date
    entries: list[WeeklyPerformanceEntry]


class PipelineRunStatus(BaseModel):
    """The latest run. `flow_run_id`, `triggered_by` and `error_message` stay internal."""

    pipeline_name: str
    status: Literal["running", "completed", "failed", "skipped"]
    trigger: Literal["schedule", "manual", "backfill"]
    started_at: datetime
    finished_at: datetime | None
    weeks_from: date
    weeks_to: date
    arrival_cutoff: datetime
    # Derived when read: the newest closed week, and whether the latest
    # completed run is behind it (the scheduled run is late or failed).
    last_closed_week: date
    stale: bool
    events_read: int | None
    events_excluded: dict[str, int]
    rows_inserted: int | None
    rows_updated: int | None
    rows_unchanged: int | None
    rows_deleted: int | None
    weeks_revised: list[date]
    late_events_outside_window: int | None
    telemetry_dropped: int | None
    min_capture_coverage: float | None
    quality_status: Literal["ok", "degraded"] | None
    error_stage: Literal["extract", "transform", "validate", "load"] | None
    error_class: str | None


class PipelineRunRequest(BaseModel):
    """Body of `POST /reporting/pipeline-runs`. Without `week_start`, the default lookback."""

    model_config = ConfigDict(extra="forbid")

    week_start: date | None = None


class PipelineRunAccepted(BaseModel):
    flow_run_id: UUID
    pipeline_name: str
    week_start: date | None
    status: Literal["scheduled"] = "scheduled"
