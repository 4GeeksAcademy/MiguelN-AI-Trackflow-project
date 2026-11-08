"""The technical report: the period it covers and the metrics calculated over it.

`POST /telemetry/report` resolves the period here, once, at request time, and
enqueues the `telemetry.generate_report` task with it (DEV-55). The worker
then calls `build_report`, which passes the same `start` and `end` to every
metric function in `analysis.py`; none of them has a default window of its
own. Finished reports live in the Celery result backend for a day, so there
is no process-local cache any more.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError

from services.telemetry import analysis
from services.telemetry.models import TelemetryReport

DEFAULT_WINDOW = timedelta(days=7)


class InvalidPeriod(ValueError):
    """The requested period is empty or reversed."""


class ReportUnavailable(Exception):
    """PostgreSQL could not be read. Nothing was calculated; a retry is safe."""


def to_utc(value: datetime) -> datetime:
    """A datetime without an offset is read as UTC, like event timestamps at ingest."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def resolve_period(
    start_date: datetime | None, end_date: datetime | None, *, now: datetime
) -> tuple[datetime, datetime]:
    """The `[start, end)` window in UTC.

    `end_date` defaults to `now` and `start_date` to seven days before the end,
    so no parameter means the last seven days and an end alone the seven days
    before it.
    """
    end = to_utc(end_date) if end_date is not None else to_utc(now)
    start = to_utc(start_date) if start_date is not None else end - DEFAULT_WINDOW
    if start >= end:
        raise InvalidPeriod("start_date must be before end_date")
    return start, end


def build_report(start: datetime, end: datetime) -> TelemetryReport:
    """Run every metric of the pipeline over the same period."""
    try:
        metrics = {
            "events_per_day": analysis.events_per_day(start, end),
            "error_rate_by_type": analysis.error_rate_by_type(start, end),
            "latency_by_route": analysis.latency_by_route(start, end),
            "event_type_share": analysis.event_type_share(start, end),
        }
    except SQLAlchemyError as error:
        # The class name only in the message, which reaches the task's result
        # and dead letter: a driver message can quote the database host. The
        # original stays chained for the worker's failure log.
        raise ReportUnavailable(type(error).__name__) from error

    return TelemetryReport.model_validate(
        {"period": {"from": start, "to": end}, "metrics": metrics}
    )
