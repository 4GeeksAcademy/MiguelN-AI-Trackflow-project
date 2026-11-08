"""Wire contracts for frontend telemetry batches and the technical report.

`TelemetryEvent` mirrors the envelope in `docs/telemetry/event-schemas.json` and
is the contract every stored event meets. Per-event `properties` are not checked
against their individual schemas yet. `TelemetryReport` is the result of the
`telemetry.generate_report` task that `POST /telemetry/report` enqueues, as
`GET /tasks/{task_id}` returns it.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAX_BATCH_SIZE = 100


class TelemetryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: UUID
    timestamp: datetime
    sessionId: UUID
    userId: UUID | None
    event_type: str = Field(pattern=r"^[a-z]+(_[a-z]+)+$", max_length=64)
    schemaVersion: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    requestId: UUID
    properties: dict[str, Any]


class TelemetryBatch(BaseModel):
    """The `{"events": [...]}` envelope, with each event left unparsed.

    Events are validated one by one afterwards, so a bad event is rejected on
    its own instead of failing the whole batch.
    """

    model_config = ConfigDict(extra="forbid")

    events: list[Any] = Field(min_length=1, max_length=MAX_BATCH_SIZE)


class TelemetryReceipt(BaseModel):
    """`received` is always `stored + rejected`."""

    received: int
    stored: int
    rejected: int


# --- The technical report ----------------------------------------------------
# One model per metric row of `services/telemetry/analysis.py`; that module's
# docstrings say what each field measures.

UtcDay = date  # a UTC calendar day, serialised as YYYY-MM-DD


class ReportPeriod(BaseModel):
    """The `[from, to)` window every metric was calculated over, in UTC."""

    model_config = ConfigDict(populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime


class DailyEvents(BaseModel):
    date: UtcDay
    events: int
    sessions: int
    event_types: int
    partial_day: bool


class ErrorRateByType(BaseModel):
    date: UtcDay
    error_class: str
    errors: int
    requests: int
    error_rate: float | None


class RouteLatency(BaseModel):
    date: UtcDay
    method: str
    route_template: str
    samples: int
    requests: int
    p50_ms: float
    p95_ms: float
    mean_ms: float
    max_ms: float
    error_rate: float


class EventTypeShare(BaseModel):
    date: UtcDay
    event_type: str
    events: int
    share: float


class ReportMetrics(BaseModel):
    events_per_day: list[DailyEvents]
    error_rate_by_type: list[ErrorRateByType]
    latency_by_route: list[RouteLatency]
    event_type_share: list[EventTypeShare]


class TelemetryReport(BaseModel):
    period: ReportPeriod
    metrics: ReportMetrics
