"""Operational metrics for the technical report, computed from `telemetry_events`.

This is the engineering team's radar, not a business report: how much the
system is used, how often its API calls fail and why, how fast they answer, and
which events dominate the stream. Conversion and revenue belong to the data
pipelines, not here.

Every public metric function follows the same rules:

- It takes an explicit `[start, end)` window and has no default of its own;
  `POST /telemetry/report` resolves the period once, and the report task
  passes it to all of them.
- It loads only the events it needs, transforms them with Pandas and returns
  JSON-ready rows: a list of dicts holding str, int, float, bool or None.
- It is independent and side-effect free: it never calls another metric
  function, and it reads without writing, caching, logging or keeping state.
  The table is append-only, so the same window gives the same result.
- It calculates with Pandas operations only. There is no Python loop or
  comprehension anywhere in this module; `tests/test_telemetry_analysis.py`
  fails if one appears.
- Timestamps are converted to UTC datetimes before anything is grouped. Grouped
  as text, or in the sender's own offset, an event lands on the wrong day and
  nothing reports the mistake.

Days are UTC calendar days. Request counts are reweighted by `sample_rate`,
which the telemetry plan defines as the probability that an
`api_latency_recorded` event was kept ("consumers divide by it").
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final, cast

import pandas as pd
from sqlalchemy import select

from services.inventory import database as inventory_database
from services.telemetry.database import telemetry_events

API_LATENCY = "api_latency_recorded"
API_ERROR = "api_error_recorded"

# The `error_class` enum of `api_error_recorded` in docs/telemetry/event-schemas.json.
# `unhandled` is a 5xx (a defect); every other class is a 4xx answer.
ERROR_CLASSES = ("validation", "domain_rule", "auth", "not_found", "conflict", "unhandled")
UNCLASSIFIED = "unclassified"

FAILED_STATUS_CLASSES = ("4xx", "5xx")
MIN_SAMPLE_RATE = 0.01  # the schema's minimum for `sample_rate`

DAY = pd.Timedelta(days=1)
RESOLUTION: Final = "us"  # PostgreSQL timestamps carry microseconds

Row = dict[str, Any]


def load_events(
    start: datetime,
    end: datetime,
    event_types: Sequence[str] | None = None,
    *,
    payload: bool = False,
) -> pd.DataFrame:
    """Events with `start <= timestamp < end`, optionally of the given types only.

    Columns: `timestamp`, `event_type`, `session_id` and, when asked for,
    `payload`. The filter runs in PostgreSQL, on the `timestamp` and
    `event_type` indexes, so a metric loads only the rows it needs. Read with
    SQLAlchemy rather than PostgREST, whose default 1000-row limit would
    silently truncate a busy week.
    """
    table = telemetry_events
    columns = [
        table.c.timestamp,
        table.c.event_type,
        table.c.tags["sessionId"].astext.label("session_id"),
    ]
    if payload:
        columns.append(table.c.payload)

    query = select(*columns).where(table.c.timestamp >= start, table.c.timestamp < end)
    if event_types is not None:
        query = query.where(table.c.event_type.in_(list(event_types)))

    with inventory_database.get_engine().connect() as connection:
        return pd.read_sql(query, connection)


def _utc(value: datetime) -> pd.Timestamp:
    """A naive datetime is read as UTC, the way the ingest endpoint reads timestamps."""
    stamp = pd.Timestamp(value)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return stamp.as_unit(RESOLUTION)


def _window(start: datetime, end: datetime) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_utc, end_utc = _utc(start), _utc(end)
    if start_utc >= end_utc:
        raise ValueError("start must be before end")
    return start_utc, end_utc


def _load(
    start: pd.Timestamp,
    end: pd.Timestamp,
    event_types: Sequence[str] | None = None,
    *,
    payload: bool = False,
) -> pd.DataFrame:
    """The window's events with `timestamp` as UTC datetimes and their UTC `date`.

    The conversion comes first, before any grouping. The window and event types
    are applied again here, so a metric stays correct whatever the loader hands
    back.
    """
    frame = load_events(start.to_pydatetime(), end.to_pydatetime(), event_types, payload=payload)
    # ISO8601 rather than a format inferred from the first value: senders differ
    # in fractional seconds, and an inferred format fails on the first mismatch.
    timestamp = pd.to_datetime(frame["timestamp"], utc=True, format="ISO8601")
    timestamp = timestamp.dt.as_unit(RESOLUTION)
    frame = frame.assign(timestamp=timestamp, date=timestamp.dt.floor("D"))

    keep = (frame["timestamp"] >= start) & (frame["timestamp"] < end)
    if event_types is not None:
        keep &= frame["event_type"].isin(list(event_types))
    return frame[keep]


def _days(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """Every UTC day the window touches. `end` is exclusive."""
    last = (end - pd.Timedelta(1, RESOLUTION)).floor("D")
    return pd.date_range(start.floor("D"), last, freq="D", name="date", unit=RESOLUTION)


def _request_weight(payload: pd.Series) -> pd.Series:
    """How many requests one `api_latency_recorded` event stands for: 1 / sample_rate."""
    rate = pd.to_numeric(payload.str.get("sample_rate"), errors="coerce").fillna(1.0)
    return 1.0 / rate.clip(lower=MIN_SAMPLE_RATE, upper=1.0)


def _weighted_percentile(calls: pd.DataFrame, keys: list[str], q: float) -> pd.Series:
    """Nearest-rank percentile of `duration_ms` per group, each call counted `weight` times."""
    ordered = calls.sort_values([*keys, "duration_ms"])
    weight = ordered.groupby(keys)["weight"]
    reached = weight.cumsum() >= q * weight.transform("sum")
    return ordered[reached].groupby(keys)["duration_ms"].first()


def _records(frame: pd.DataFrame) -> list[Row]:
    """JSON-ready rows: dates as `YYYY-MM-DD`, NaN as None, numpy scalars as Python ones."""
    frame = frame.assign(date=frame["date"].dt.strftime("%Y-%m-%d"))
    objects = frame.astype(object)
    return cast(list[Row], objects.where(frame.notna(), None).to_dict(orient="records"))


def events_per_day(start: datetime, end: datetime) -> list[Row]:
    """Event volume per UTC day: is telemetry flowing, and how busy is the system?

    One row per day the window touches, with `events`, distinct `sessions` and
    distinct `event_types`. Days without events are listed at zero: an empty
    day means capture or ingestion stopped, which is a finding, not a gap to
    skip. `partial_day` marks a first or last day the window covers only in
    part, so a short current day is not read as a drop in traffic.
    """
    start, end = _window(start, end)
    frame = _load(start, end)
    days = _days(start, end)

    daily = (
        frame.groupby("date")
        .agg(
            events=("event_type", "size"),
            sessions=("session_id", "nunique"),
            event_types=("event_type", "nunique"),
        )
        .reindex(days, fill_value=0)
    )
    daily = daily.assign(partial_day=(days < start) | (days + DAY > end)).reset_index()
    return _records(daily[["date", "events", "sessions", "event_types", "partial_day"]])


def event_type_share(start: datetime, end: datetime) -> list[Row]:
    """Which event types dominate each UTC day, as counts and as a share of the day.

    Sorted by day, then by volume, largest first. A type that outgrows the
    activity behind it (`page_load_measured` reports up to five Web Vitals per
    page view) shows where capture volume, and storage, actually goes. Only
    days with events appear; a share of nothing is undefined.
    """
    start, end = _window(start, end)
    frame = _load(start, end)

    counts = frame.groupby(["date", "event_type"]).size().rename("events").reset_index()
    day_total = counts.groupby("date")["events"].transform("sum")
    counts = counts.assign(share=(counts["events"] / day_total).round(4)).sort_values(
        ["date", "events", "event_type"], ascending=[True, False, True]
    )
    return _records(counts[["date", "event_type", "events", "share"]])


def error_rate_by_type(start: datetime, end: datetime) -> list[Row]:
    """API error rate per UTC day and error class: how often calls fail, and why.

    `errors` counts the day's `api_error_recorded` events of that class.
    `requests` is the day's estimated API request count, from
    `api_latency_recorded` reweighted by `sample_rate`; every class of a day
    shares it, so the day's overall error rate is the sum of its rows. A day
    without requests has `error_rate` None: undefined, not zero.

    Every day of the window is listed with every catalogued class, at zero when
    nothing failed, followed by any class the data carries that the catalogue
    does not (`unclassified` when it carries none). `unhandled` (5xx) is a
    defect that should page an engineer; the 4xx classes are expected in small
    numbers (a mistyped password is `auth`) and a signal when they climb.
    """
    start, end = _window(start, end)
    frame = _load(start, end, (API_LATENCY, API_ERROR), payload=True)

    calls = frame[frame["event_type"] == API_LATENCY]
    requests = _request_weight(calls["payload"]).groupby(calls["date"]).sum()

    errors = frame[frame["event_type"] == API_ERROR]
    errors = errors.assign(
        error_class=errors["payload"].str.get("error_class").fillna(UNCLASSIFIED)
    )
    uncatalogued = errors.loc[~errors["error_class"].isin(ERROR_CLASSES), "error_class"]
    classes = pd.Index(ERROR_CLASSES).append(pd.Index(uncatalogued.drop_duplicates().sort_values()))
    grid = pd.MultiIndex.from_product([_days(start, end), classes], names=["date", "error_class"])

    table = (
        errors.groupby(["date", "error_class"])
        .size()
        .reindex(grid, fill_value=0)
        .rename("errors")
        .reset_index()
    )
    day_requests = table["date"].map(requests).fillna(0.0)
    table = table.assign(
        requests=day_requests.round().astype(int),
        error_rate=(table["errors"] / day_requests.where(day_requests > 0)).round(4),
    )
    return _records(table[["date", "error_class", "errors", "requests", "error_rate"]])


def latency_by_route(start: datetime, end: datetime) -> list[Row]:
    """API latency and failure share per UTC day and route: how fast it answers, where it fails.

    Per method and route template: `samples` (measurements stored), `requests`
    (estimated, reweighted by `sample_rate`), `p50_ms`, `p95_ms` and `mean_ms`
    weighted the same way, the slowest `max_ms`, and `error_rate`, the share of
    4xx/5xx answers. Percentiles are nearest-rank, so the p95 of three samples
    is their maximum: read it next to `samples`. Sorted by day, then slowest
    p95 first.

    Today `api_latency_recorded` comes from the backoffice, so `duration_ms` is
    the browser's round trip, network included, not handler time alone.
    """
    start, end = _window(start, end)
    frame = _load(start, end, (API_LATENCY,), payload=True)

    payload = frame["payload"]
    calls = frame.assign(
        method=payload.str.get("method"),
        route_template=payload.str.get("route_template"),
        duration_ms=pd.to_numeric(payload.str.get("duration_ms"), errors="coerce"),
        weight=_request_weight(payload),
        failed=payload.str.get("status_class").isin(FAILED_STATUS_CLASSES),
    ).dropna(subset=["method", "route_template", "duration_ms"])
    calls = calls.assign(
        weighted_ms=calls["duration_ms"] * calls["weight"],
        failed_weight=calls["weight"].where(calls["failed"], 0.0),
    )

    keys = ["date", "method", "route_template"]
    routes = calls.groupby(keys).agg(
        samples=("duration_ms", "size"),
        requests=("weight", "sum"),
        failed_weight=("failed_weight", "sum"),
        weighted_ms=("weighted_ms", "sum"),
        max_ms=("duration_ms", "max"),
    )
    routes = routes.assign(
        p50_ms=_weighted_percentile(calls, keys, 0.50),
        p95_ms=_weighted_percentile(calls, keys, 0.95),
        mean_ms=routes["weighted_ms"] / routes["requests"],
        error_rate=(routes["failed_weight"] / routes["requests"]).round(4),
        requests=routes["requests"].round().astype(int),
    )
    routes = (
        routes.round({"p50_ms": 1, "p95_ms": 1, "mean_ms": 1, "max_ms": 1})
        .reset_index()
        .sort_values(
            ["date", "p95_ms", "route_template", "method"],
            ascending=[True, False, True, True],
        )
    )
    return _records(
        routes[
            [
                "date",
                "method",
                "route_template",
                "samples",
                "requests",
                "p50_ms",
                "p95_ms",
                "mean_ms",
                "max_ms",
                "error_rate",
            ]
        ]
    )
