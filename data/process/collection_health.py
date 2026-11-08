"""Collection health: was the week's input complete, late, bursty or skewed?

The report says what happened; this says whether the telemetry behind it can be
trusted (`PIPELINE_DESIGN.md` 3.7, 9.4-9.6). It goes to the run log, never into
the report rows. Pure Pandas, like `weekly_rollup`: no I/O and no clock.

Capture coverage is the main signal. It divides the order events the rollup
counted by the orders the inventory tables hold for the same warehouse and
week. The inventory tables are the system of record, so a ratio below 1 means
lost measurements and above 1 means duplicated or injected ones, while growth
moves both sides together.
"""

from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

import pandas as pd

from data.process import weekly_rollup

COVERAGE_RANGE: Final = (0.95, 1.05)  # a starting value, revisited after 4 weeks (Q13)
LAG_QUANTILE: Final = 0.95

STAT_KEY: Final = ["warehouse", "day", "event_type"]
STAT_COLUMNS: Final = (
    *STAT_KEY,
    "telemetry_events",
    "counted_events",
    "inventory_orders",
    "lag_p95_seconds",
    "max_gap_minutes",
    "peak_events_per_minute",
    "future_dated",
)
ORDER_COUNT_COLUMNS: Final = ("warehouse", "day", "event_type", "inventory_orders")
ORDER_EVENTS: Final = (weekly_rollup.INBOUND, weekly_rollup.OUTBOUND)

QualityStatus = Literal["ok", "degraded"]


@dataclass(frozen=True)
class CollectionHealth:
    """Per-day stats for `pipeline_run_collection_stats`, plus the run's verdict."""

    stats: pd.DataFrame
    min_capture_coverage: float | None
    quality_status: QualityStatus


def empty_order_counts() -> pd.DataFrame:
    return pd.DataFrame({name: pd.Series(dtype="object") for name in ORDER_COUNT_COLUMNS})


PER_DAY_COLUMNS: Final = (
    *STAT_KEY,
    "telemetry_events",
    "lag_p95_seconds",
    "max_gap_minutes",
    "peak_events_per_minute",
    "future_dated",
)


def _per_day(events: pd.DataFrame) -> pd.DataFrame:
    """Raw counts, delivery lag, gaps, bursts and skew per warehouse, day and type."""
    if events.empty:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in PER_DAY_COLUMNS})

    frame = events.assign(
        day=events["effective_time"].dt.date,
        lag_seconds=(events["created_at"] - events["timestamp"]).dt.total_seconds(),
        future=events["timestamp"] > events["created_at"],
        minute=events["effective_time"].dt.floor("min"),
    ).sort_values([*STAT_KEY, "effective_time"], kind="stable")
    frame["gap_minutes"] = frame.groupby(STAT_KEY)["effective_time"].diff().dt.total_seconds() / 60
    grouped = frame.groupby(STAT_KEY)
    per_minute = frame.groupby([*STAT_KEY, "minute"]).size().groupby(level=STAT_KEY).max()
    return pd.DataFrame(
        {
            "telemetry_events": grouped.size(),
            "lag_p95_seconds": grouped["lag_seconds"].quantile(LAG_QUANTILE),
            "max_gap_minutes": grouped["gap_minutes"].max(),
            "peak_events_per_minute": per_minute,
            "future_dated": grouped["future"].sum(),
        }
    ).reset_index()


def compute(
    events: pd.DataFrame,
    exclusions: pd.DataFrame,
    order_counts: pd.DataFrame,
    weeks: tuple[date, ...],
) -> CollectionHealth:
    """Collection stats for the run's weeks.

    `events` are the extracted events (`weekly_rollup.EVENT_COLUMNS`),
    `exclusions` the transform's excluded `eventId`s, and `order_counts` the
    inventory orders per `warehouse`, UTC `day` and order event type. Events
    with an unknown warehouse are already counted in the run's exclusions, so
    they get no stats row.
    """
    normalized = weekly_rollup.normalize_events(events)
    in_scope = normalized[
        normalized["warehouse"].isin(weekly_rollup.WAREHOUSES).fillna(False)
        & normalized["week_start"].isin(weeks)
    ].astype({"warehouse": "object", "event_type": "object"})

    excluded_ids = set(exclusions["event_id"].astype("string").dropna())
    counted = in_scope[~in_scope["id"].isin(excluded_ids)]
    counted_per_day = (
        counted.assign(day=counted["effective_time"].dt.date)
        .groupby(STAT_KEY)
        .size()
        .rename("counted_events")
    )

    orders = order_counts.loc[:, list(ORDER_COUNT_COLUMNS)].astype(
        {"warehouse": "object", "event_type": "object"}
    )
    orders = orders[orders["day"].map(weekly_rollup.week_start_of_day).isin(weeks)]

    stats = (
        _per_day(in_scope)
        .merge(counted_per_day.reset_index(), on=STAT_KEY, how="left")
        .merge(orders, on=STAT_KEY, how="outer")
    )
    if stats.empty:
        return CollectionHealth(
            stats=pd.DataFrame(columns=list(STAT_COLUMNS)),
            min_capture_coverage=None,
            quality_status="ok",
        )

    stats = stats.sort_values(STAT_KEY, ignore_index=True)
    for name in ("telemetry_events", "counted_events", "future_dated"):
        stats[name] = stats[name].fillna(0).astype("int64")
    # An order type without inventory orders that day had zero orders. Stockout
    # and discrepancy events have no independent count in v1, so theirs is null.
    is_order_event = stats["event_type"].isin(ORDER_EVENTS)
    stats["inventory_orders"] = (
        stats["inventory_orders"].fillna(0).astype("Int64").mask(~is_order_event)
    )
    stats["peak_events_per_minute"] = stats["peak_events_per_minute"].astype("Int64")
    stats = stats.loc[:, list(STAT_COLUMNS)]

    coverage, degraded = _weekly_coverage(stats[is_order_event])
    return CollectionHealth(
        stats=stats,
        min_capture_coverage=coverage,
        quality_status="degraded" if degraded else "ok",
    )


def _weekly_coverage(order_stats: pd.DataFrame) -> tuple[float | None, bool]:
    """The lowest counted/inventory ratio per warehouse-week-type, and whether any is off.

    Weekly rather than daily: an order created at 23:59:59 whose event lands a
    second later belongs to the next day, which must not read as data loss.
    Events without any inventory order behind them are off by definition.
    """
    if order_stats.empty:
        return None, False
    weekly = (
        order_stats.assign(week_start=order_stats["day"].map(weekly_rollup.week_start_of_day))
        .groupby(["warehouse", "week_start", "event_type"])[["counted_events", "inventory_orders"]]
        .sum()
    )
    weekly = weekly[(weekly["counted_events"] > 0) | (weekly["inventory_orders"] > 0)]
    if weekly.empty:
        return None, False

    has_orders = weekly["inventory_orders"] > 0
    ratio = (weekly["counted_events"] / weekly["inventory_orders"].where(has_orders)).astype(
        "float64"
    )
    low, high = COVERAGE_RANGE
    degraded = bool((~has_orders).any() or (ratio[has_orders] < low).any())
    degraded = degraded or bool((ratio[has_orders] > high).any())
    measured = ratio[has_orders]
    return (round(float(measured.min()), 4) if len(measured) else None), degraded
