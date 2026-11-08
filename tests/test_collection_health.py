"""Collection health (`data/process/collection_health.py`): fixture DataFrames, no database."""

from datetime import date

import pandas as pd
import pytest

from data.process import collection_health as health
from data.process import weekly_rollup as rollup
from tests.test_weekly_rollup import LAPTOP, PRODUCTS, WEEKS, event

WEEK = date(2026, 10, 12)


def orders(*rows):
    if not rows:
        return health.empty_order_counts()
    return pd.DataFrame(
        [{"warehouse": w, "day": d, "event_type": t, "inventory_orders": n} for w, d, t, n in rows]
    )


def compute(events, order_counts, products=PRODUCTS):
    frame = pd.DataFrame(events) if events else rollup.empty_events()
    result = rollup.build_rollup(frame, products, WEEKS)
    return health.compute(frame, result.exclusions, order_counts, WEEKS)


def stat(result, warehouse, day, event_type):
    stats = result.stats
    match = stats[
        (stats["warehouse"] == warehouse)
        & (stats["day"] == day)
        & (stats["event_type"] == event_type)
    ]
    assert len(match) == 1
    return match.iloc[0]


def test_full_coverage_is_ok():
    result = compute(
        [
            event(rollup.INBOUND, "2026-10-12T09:00:00Z", quantity="1"),
            event(rollup.OUTBOUND, "2026-10-13T09:00:00Z"),
        ],
        orders(
            ("Monterrey", date(2026, 10, 12), rollup.INBOUND, 1),
            ("Monterrey", date(2026, 10, 13), rollup.OUTBOUND, 1),
        ),
    )
    assert (result.min_capture_coverage, result.quality_status) == (1.0, "ok")


def test_orders_without_events_read_as_lost_capture():
    # The real 2026-09-28 case: the seed script wrote orders and no event was emitted.
    result = compute([], orders(("Monterrey", date(2026, 10, 12), rollup.INBOUND, 4)))
    row = stat(result, "Monterrey", date(2026, 10, 12), rollup.INBOUND)
    assert (row["telemetry_events"], row["counted_events"], row["inventory_orders"]) == (0, 0, 4)
    assert (result.min_capture_coverage, result.quality_status) == (0.0, "degraded")


def test_events_without_orders_read_as_duplication_or_injection():
    result = compute([event(rollup.OUTBOUND, "2026-10-13T09:00:00Z")], orders())
    assert result.min_capture_coverage is None
    assert result.quality_status == "degraded"


def test_coverage_counts_after_exclusions_and_dedupe():
    result = compute(
        [
            event(rollup.OUTBOUND, "2026-10-13T09:00:00Z", order_id="o-1"),
            event(rollup.OUTBOUND, "2026-10-13T09:00:04Z", order_id="o-1"),  # the same order
        ],
        orders(("Monterrey", date(2026, 10, 13), rollup.OUTBOUND, 1)),
    )
    row = stat(result, "Monterrey", date(2026, 10, 13), rollup.OUTBOUND)
    assert (row["telemetry_events"], row["counted_events"]) == (2, 1)
    assert result.quality_status == "ok"


def test_coverage_is_weekly_so_a_midnight_split_is_not_loss():
    result = compute(
        [event(rollup.INBOUND, "2026-10-14T00:00:01Z", quantity="1")],
        orders(("Monterrey", date(2026, 10, 13), rollup.INBOUND, 1)),
    )
    assert (result.min_capture_coverage, result.quality_status) == (1.0, "ok")


def test_lag_gap_burst_and_skew_per_day():
    result = compute(
        [
            event(rollup.OUTBOUND, "2026-10-13T09:00:00Z", created_at="2026-10-13T09:00:10Z"),
            event(rollup.OUTBOUND, "2026-10-13T09:00:20Z", created_at="2026-10-13T09:00:24Z"),
            event(rollup.OUTBOUND, "2026-10-13T11:00:00Z", created_at="2026-10-13T11:00:02Z"),
            # Stamped 273 s after it arrived, like the two real events of 2026-10-20.
            event(rollup.OUTBOUND, "2026-10-13T12:04:33Z", created_at="2026-10-13T12:00:00Z"),
        ],
        orders(("Monterrey", date(2026, 10, 13), rollup.OUTBOUND, 4)),
    )
    row = stat(result, "Monterrey", date(2026, 10, 13), rollup.OUTBOUND)
    assert row["telemetry_events"] == 4
    assert row["peak_events_per_minute"] == 2
    assert row["max_gap_minutes"] == pytest.approx(119 + 40 / 60)
    assert row["future_dated"] == 1
    assert row["lag_p95_seconds"] == pytest.approx(
        pd.Series([10.0, 4.0, 2.0, -273.0]).quantile(0.95)
    )


def test_stock_and_discrepancy_events_have_no_inventory_count():
    result = compute(
        [
            event(
                rollup.THRESHOLD,
                "2026-10-13T09:00:00Z",
                threshold_band="low",
                product_id=LAPTOP,
                warehouse="Zaragoza",
            )
        ],
        orders(),
    )
    row = stat(result, "Zaragoza", date(2026, 10, 13), rollup.THRESHOLD)
    assert pd.isna(row["inventory_orders"])
    assert (result.min_capture_coverage, result.quality_status) == (None, "ok")


def test_unknown_warehouses_and_other_weeks_get_no_stats():
    result = compute(
        [
            event(rollup.INBOUND, "2026-10-13T09:00:00Z", quantity="1", warehouse="los_angeles"),
            event(rollup.INBOUND, "2026-09-29T09:00:00Z", quantity="1"),
        ],
        orders(("Monterrey", date(2026, 9, 29), rollup.INBOUND, 1)),
    )
    assert result.stats.empty
    assert tuple(result.stats.columns) == health.STAT_COLUMNS


def test_nothing_at_all_is_ok_with_no_coverage():
    result = compute([], orders())
    assert result.stats.empty
    assert (result.min_capture_coverage, result.quality_status) == (None, "ok")
