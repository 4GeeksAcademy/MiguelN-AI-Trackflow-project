"""The weekly rollup transform (`data/process/weekly_rollup.py`): fixtures, no database."""

from datetime import date, datetime, timezone
from itertools import count

import pandas as pd
import pytest

from data.process import weekly_rollup as rollup

SHOES = "395dfc8e-bb1e-5f97-b0d5-ea06f9af7b0e"
PERFUME = "ab34c859-c983-5ba9-8ca6-48aeece4aa48"
LAPTOP = "c030461b-a5f8-527f-a05b-8c67186118a5"
UNOWNED = "00000000-0000-4000-8000-00000000beef"

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)  # a Thursday
WEEKS = (date(2026, 10, 5), date(2026, 10, 12))

PRODUCTS = pd.DataFrame(
    [
        {"product_id": SHOES, "client_id": "stride-athletics", "warehouse": "Monterrey"},
        {"product_id": PERFUME, "client_id": "coco-beauty", "warehouse": "Monterrey"},
        {"product_id": LAPTOP, "client_id": "nexa-electronics", "warehouse": "Zaragoza"},
        {"product_id": UNOWNED, "client_id": None, "warehouse": "Zaragoza"},
    ]
)

_ids = count(1)


def event(event_type, at, **fields):
    row = {
        "id": f"00000000-0000-4000-8000-{next(_ids):012d}",
        "timestamp": at,
        "created_at": fields.pop("created_at", at),
        "event_type": event_type,
        "warehouse": "Monterrey",
        "client_id": None,
        "product_id": SHOES,
        "quantity": None,
        "order_id": None,
        "triggering_order_id": None,
        "threshold_band": None,
        "reconciliation_id": None,
        "schema_version": "1.0.0",
    }
    row.update(fields)
    return row


def build(*events, products=PRODUCTS, weeks=WEEKS):
    frame = pd.DataFrame(list(events)) if events else rollup.empty_events()
    return rollup.build_rollup(frame, products, weeks)


def row_for(result, warehouse, client_id, week):
    rows = result.rows
    match = rows[
        (rows["warehouse"] == warehouse)
        & (rows["client_id"] == client_id)
        & (rows["week_start"] == week)
    ]
    assert len(match) == 1
    return match.iloc[0]


def reasons(result):
    return dict(zip(result.exclusions["event_id"], result.exclusions["reason"], strict=True))


# --- Weeks ---------------------------------------------------------------------------


def test_closed_weeks_never_include_the_week_in_progress():
    assert rollup.closed_weeks(NOW) == WEEKS
    assert rollup.closed_weeks(NOW, lookback_weeks=1) == (date(2026, 10, 12),)
    # Monday 00:00 UTC closes the previous week.
    assert rollup.last_closed_week(datetime(2026, 10, 19, tzinfo=timezone.utc)) == date(
        2026, 10, 12
    )
    assert rollup.last_closed_week(datetime(2026, 10, 18, 23, 59, 59, tzinfo=timezone.utc)) == date(
        2026, 10, 5
    )


@pytest.mark.parametrize(
    ("week_start", "lookback", "message"),
    [
        (date(2026, 10, 13), 2, "Monday"),
        (date(2026, 10, 19), 2, "closed"),
        (None, 0, "lookback_weeks"),
        (None, rollup.MAX_LOOKBACK_WEEKS + 1, "lookback_weeks"),
    ],
)
def test_closed_weeks_rejects_unusable_requests(week_start, lookback, message):
    with pytest.raises(rollup.InvalidWeek, match=message):
        rollup.closed_weeks(NOW, week_start, lookback)


def test_window_covers_whole_weeks_in_utc():
    start, end = rollup.window_bounds(WEEKS)
    assert start == datetime(2026, 10, 5, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 19, tzinfo=timezone.utc)


def test_weeks_between_lists_every_monday_inclusive():
    assert rollup.weeks_between(date(2026, 9, 28), date(2026, 10, 12)) == (
        date(2026, 9, 28),
        date(2026, 10, 5),
        date(2026, 10, 12),
    )
    with pytest.raises(rollup.InvalidWeek):
        rollup.weeks_between(date(2026, 10, 12), date(2026, 9, 28))


# --- KPIs ----------------------------------------------------------------------------


def test_each_kpi_comes_from_its_own_event():
    result = build(
        event(rollup.INBOUND, "2026-10-12T09:00:00Z", quantity="30"),
        event(rollup.INBOUND, "2026-10-13T09:00:00Z", quantity="12"),
        event(rollup.OUTBOUND, "2026-10-13T10:00:00Z", quantity="5"),
        event(rollup.OUTBOUND, "2026-10-14T10:00:00Z", quantity="50"),
        event(rollup.THRESHOLD, "2026-10-14T10:00:01Z", threshold_band="low"),
        event(rollup.THRESHOLD, "2026-10-15T10:00:01Z", threshold_band="out_of_stock"),
        event(rollup.DISCREPANCY, "2026-10-16T08:00:00Z", reconciliation_id="r-1"),
    )
    row = row_for(result, "Monterrey", "stride-athletics", date(2026, 10, 12))
    assert row["inbound_units_count"] == 42  # Inbound Volume sums units
    assert row["outbound_orders_count"] == 2  # Outbound Throughput counts orders, not units
    assert row["stockout_events_count"] == 2  # both low and out_of_stock crossings
    assert row["discrepancy_events_count"] == 1
    assert row["discrepancy_rate"] == 0.5


def test_rate_is_zero_without_orders_and_rounded_otherwise():
    result = build(
        event(rollup.DISCREPANCY, "2026-10-12T08:00:00Z", reconciliation_id="r-1"),
        event(rollup.OUTBOUND, "2026-10-12T09:00:00Z", product_id=LAPTOP, warehouse="Zaragoza"),
        event(rollup.OUTBOUND, "2026-10-12T09:01:00Z", product_id=LAPTOP, warehouse="Zaragoza"),
        event(rollup.OUTBOUND, "2026-10-12T09:02:00Z", product_id=LAPTOP, warehouse="Zaragoza"),
        event(
            rollup.DISCREPANCY,
            "2026-10-12T10:00:00Z",
            product_id=LAPTOP,
            warehouse="Zaragoza",
            reconciliation_id="r-2",
        ),
    )
    no_orders = row_for(result, "Monterrey", "stride-athletics", date(2026, 10, 12))
    assert (no_orders["outbound_orders_count"], no_orders["discrepancy_rate"]) == (0, 0.0)
    assert (
        row_for(result, "Zaragoza", "nexa-electronics", date(2026, 10, 12))["discrepancy_rate"]
        == 0.3333
    )


def test_every_owned_pair_gets_a_zero_row_for_every_week():
    result = build()
    assert len(result.rows) == 3 * len(WEEKS)  # three owned products, one pair each
    assert set(result.rows["client_id"]) == {"stride-athletics", "coco-beauty", "nexa-electronics"}
    assert (result.rows.loc[:, list(rollup.COUNT_COLUMNS)] == 0).all().all()
    assert result.events_read == 0 and sum(result.events_excluded.values()) == 0


def test_no_products_and_no_events_gives_no_rows():
    result = build(products=PRODUCTS.iloc[0:0])
    assert result.rows.empty
    assert tuple(result.rows.columns) == rollup.ROW_COLUMNS


def test_rows_never_mix_clients():
    result = build(
        event(rollup.INBOUND, "2026-10-12T09:00:00Z", quantity="3", product_id=SHOES),
        event(rollup.INBOUND, "2026-10-12T09:00:00Z", quantity="4", product_id=PERFUME),
    )
    week = date(2026, 10, 12)
    assert row_for(result, "Monterrey", "stride-athletics", week)["inbound_units_count"] == 3
    assert row_for(result, "Monterrey", "coco-beauty", week)["inbound_units_count"] == 4
    assert not result.rows.duplicated(rollup.KEY).any()


def test_output_has_exactly_the_destination_columns_sorted():
    result = build(event(rollup.INBOUND, "2026-10-06T09:00:00Z", quantity="1"))
    assert tuple(result.rows.columns) == rollup.ROW_COLUMNS
    ordered = result.rows.sort_values(["week_start", "warehouse", "client_id"], ignore_index=True)
    pd.testing.assert_frame_equal(result.rows, ordered)


# --- Week assignment -----------------------------------------------------------------


def test_sunday_23_59_59_and_monday_00_00_utc_belong_to_different_weeks():
    sunday = event(rollup.INBOUND, "2026-10-11T23:59:59Z", quantity="1")
    monday = event(rollup.INBOUND, "2026-10-12T00:00:00Z", quantity="10")
    result = build(sunday, monday)
    assert (
        row_for(result, "Monterrey", "stride-athletics", date(2026, 10, 5))["inbound_units_count"]
        == 1
    )
    assert (
        row_for(result, "Monterrey", "stride-athletics", date(2026, 10, 12))["inbound_units_count"]
        == 10
    )


def test_offsets_are_read_as_the_same_utc_instant():
    # 18:30 on Sunday in Monterrey is already Monday in UTC.
    result = build(event(rollup.INBOUND, "2026-10-11T18:30:00-06:00", quantity="2"))
    assert (
        row_for(result, "Monterrey", "stride-athletics", date(2026, 10, 12))["inbound_units_count"]
        == 2
    )


def test_a_clock_running_ahead_cannot_move_an_event_into_a_later_week():
    ahead = event(
        rollup.INBOUND,
        "2026-10-12T00:03:00Z",  # stamped Monday by the device
        created_at="2026-10-11T23:59:00Z",  # but it arrived on Sunday
        quantity="5",
    )
    result = build(ahead)
    assert (
        row_for(result, "Monterrey", "stride-athletics", date(2026, 10, 5))["inbound_units_count"]
        == 5
    )


def test_events_outside_the_weeks_are_excluded_not_dropped():
    late = event(rollup.INBOUND, "2026-10-19T08:00:00Z", quantity="1")
    result = build(late)
    assert reasons(result) == {late["id"]: "outside_window"}


# --- Client resolution ---------------------------------------------------------------


def test_payload_client_wins_over_the_product_owner():
    result = build(
        event(rollup.INBOUND, "2026-10-12T09:00:00Z", quantity="9", client_id="coco-beauty")
    )
    week = date(2026, 10, 12)
    assert row_for(result, "Monterrey", "coco-beauty", week)["inbound_units_count"] == 9
    assert row_for(result, "Monterrey", "stride-athletics", week)["inbound_units_count"] == 0


def test_product_owner_is_used_when_the_payload_has_no_valid_client():
    result = build(
        event(rollup.INBOUND, "2026-10-12T09:00:00Z", quantity="2", client_id="Not A Slug"),
        event(rollup.INBOUND, "2026-10-12T10:00:00Z", quantity="3", product_id=SHOES.upper()),
    )
    assert (
        row_for(result, "Monterrey", "stride-athletics", date(2026, 10, 12))["inbound_units_count"]
        == 5
    )


def test_an_event_without_any_client_is_excluded():
    orphan = event(rollup.INBOUND, "2026-10-12T09:00:00Z", quantity="2", product_id=UNOWNED)
    unknown = event(rollup.INBOUND, "2026-10-12T09:00:00Z", quantity="2", product_id="nope")
    result = build(orphan, unknown)
    assert reasons(result) == {
        orphan["id"]: "missing_client_id",
        unknown["id"]: "missing_client_id",
    }
    assert result.events_excluded["missing_client_id"] == 2


# --- Exclusions ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"warehouse": "los_angeles", "quantity": "1"}, "unknown_warehouse"),
        ({"warehouse": None, "quantity": "1"}, "unknown_warehouse"),
        ({"quantity": None}, "invalid_quantity"),
        ({"quantity": "0"}, "invalid_quantity"),
        ({"quantity": "-3"}, "invalid_quantity"),
        ({"quantity": "2.5"}, "invalid_quantity"),
        ({"quantity": "many"}, "invalid_quantity"),
    ],
)
def test_invalid_inbound_events_are_excluded_with_their_reason(fields, reason):
    bad = event(rollup.INBOUND, "2026-10-12T09:00:00Z", **fields)
    result = build(bad)
    assert reasons(result) == {bad["id"]: reason}
    assert result.events_excluded[reason] == 1
    assert (
        row_for(result, "Monterrey", "stride-athletics", date(2026, 10, 12))["inbound_units_count"]
        == 0
    )


def test_outbound_orders_do_not_need_a_quantity():
    result = build(event(rollup.OUTBOUND, "2026-10-12T09:00:00Z", quantity=None))
    assert result.exclusions.empty
    assert (
        row_for(result, "Monterrey", "stride-athletics", date(2026, 10, 12))[
            "outbound_orders_count"
        ]
        == 1
    )


def test_unknown_threshold_band_is_excluded():
    bad = event(rollup.THRESHOLD, "2026-10-12T09:00:00Z", threshold_band="healthy")
    assert reasons(build(bad)) == {bad["id"]: "unknown_threshold_band"}


def test_every_reason_is_reported_even_when_zero():
    assert set(build().events_excluded) == set(rollup.EXCLUSION_REASONS)


# --- Business-key deduplication ------------------------------------------------------


def test_one_order_reported_twice_counts_once():
    first = event(rollup.OUTBOUND, "2026-10-12T09:00:00Z", order_id="o-1")
    second = event(rollup.OUTBOUND, "2026-10-12T09:00:05Z", order_id="o-1")
    result = build(second, first)
    assert reasons(result) == {second["id"]: "duplicate_business_key"}
    assert (
        row_for(result, "Monterrey", "stride-athletics", date(2026, 10, 12))[
            "outbound_orders_count"
        ]
        == 1
    )


def test_events_without_a_business_id_are_never_merged():
    a = event(rollup.OUTBOUND, "2026-10-12T09:00:00Z")
    b = event(rollup.OUTBOUND, "2026-10-12T09:00:00Z")
    result = build(a, b)
    assert result.exclusions.empty
    assert (
        row_for(result, "Monterrey", "stride-athletics", date(2026, 10, 12))[
            "outbound_orders_count"
        ]
        == 2
    )


def test_inbound_and_outbound_with_the_same_order_id_are_different_facts():
    result = build(
        event(rollup.INBOUND, "2026-10-12T09:00:00Z", order_id="o-1", quantity="1"),
        event(rollup.OUTBOUND, "2026-10-12T09:00:00Z", order_id="o-1"),
    )
    assert result.exclusions.empty


def test_threshold_crossing_dedupes_by_triggering_order_and_band():
    low = event(
        rollup.THRESHOLD, "2026-10-12T09:00:00Z", triggering_order_id="o-1", threshold_band="low"
    )
    again = event(
        rollup.THRESHOLD, "2026-10-12T09:00:03Z", triggering_order_id="o-1", threshold_band="low"
    )
    empty = event(
        rollup.THRESHOLD,
        "2026-10-12T09:00:03Z",
        triggering_order_id="o-1",
        threshold_band="out_of_stock",
    )
    result = build(low, again, empty)
    assert reasons(result) == {again["id"]: "duplicate_business_key"}
    assert (
        row_for(result, "Monterrey", "stride-athletics", date(2026, 10, 12))[
            "stockout_events_count"
        ]
        == 2
    )


def test_latest_discrepancy_report_wins():
    first = event(rollup.DISCREPANCY, "2026-10-12T09:00:00Z", reconciliation_id="r-1")
    recount = event(rollup.DISCREPANCY, "2026-10-13T09:00:00Z", reconciliation_id="r-1")
    other_product = event(
        rollup.DISCREPANCY, "2026-10-12T09:00:00Z", reconciliation_id="r-1", product_id=PERFUME
    )
    result = build(recount, first, other_product)
    assert reasons(result) == {first["id"]: "duplicate_business_key"}


# --- Validation gate -----------------------------------------------------------------


def valid_rows():
    return build(event(rollup.OUTBOUND, "2026-10-12T09:00:00Z")).rows


def test_valid_rows_pass_unchanged():
    rows = valid_rows()
    assert rollup.validate(rows, WEEKS, rollup.last_closed_week(NOW)) is rows


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda rows: pd.concat([rows, rows.iloc[[0]]], ignore_index=True), "repeat"),
        (lambda rows: rows.assign(week_start=date(2026, 10, 13)), "not a Monday"),
        (lambda rows: rows.assign(week_start=date(2026, 9, 28)), "outside the weeks"),
        (lambda rows: rows.assign(warehouse="los_angeles"), "warehouse"),
        (lambda rows: rows.assign(client_id="Bad Client"), "client_id"),
        (lambda rows: rows.assign(stockout_events_count=-1), "negative"),
        (lambda rows: rows.assign(discrepancy_rate=0.9), "rate"),
        (lambda rows: rows.drop(columns="discrepancy_rate"), "columns"),
    ],
)
def test_validation_refuses_rows_the_table_must_never_hold(change, message):
    with pytest.raises(rollup.RollupValidationError, match=message):
        rollup.validate(change(valid_rows()), WEEKS, rollup.last_closed_week(NOW))


def test_validation_refuses_a_week_that_has_not_closed():
    rows = valid_rows().assign(week_start=date(2026, 10, 19))
    with pytest.raises(rollup.RollupValidationError, match="not closed"):
        rollup.validate(rows, (*WEEKS, date(2026, 10, 19)), rollup.last_closed_week(NOW))


def test_transform_is_deterministic():
    events = [
        event(rollup.INBOUND, "2026-10-12T09:00:00Z", quantity="3"),
        event(rollup.OUTBOUND, "2026-10-13T09:00:00Z", order_id="o-9"),
        event(rollup.OUTBOUND, "2026-10-13T09:00:01Z", order_id="o-9"),
    ]
    first, second = build(*events), build(*events)
    pd.testing.assert_frame_equal(first.rows, second.rows)
    pd.testing.assert_frame_equal(first.exclusions, second.exclusions)
