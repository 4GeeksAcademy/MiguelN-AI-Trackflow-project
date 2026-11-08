"""The KPI tasks of `compute_weekly_warehouse_client_kpis`, one at a time.

Each test calls a task's own function (`task.fn`): no flow, no Prefect server,
no database. The input is built in memory, shaped like `telemetry_events` rows
(BRIEFING section 3): the event type plus the payload fields the pipeline reads.

    python -m pytest tests/pipelines/test_pipeline.py
"""

from datetime import date
from itertools import count

import pandas as pd
import pytest

from data.pipelines.subflows.kpi_computation import (
    assemble_weekly_warehouse_client_rows,
    compute_discrepancy_rate,
    compute_inbound_volume,
    compute_outbound_throughput,
    compute_stockout_frequency,
    prepare_kpi_events,
)
from data.process import weekly_rollup
from data.process.weekly_rollup import (
    COUNTABLE_COLUMNS,
    DISCREPANCY,
    INBOUND,
    OUTBOUND,
    ROW_COLUMNS,
    THRESHOLD,
    KpiInputError,
)

WEEK = date(2026, 10, 12)
PREVIOUS_WEEK = date(2026, 10, 5)
WEEKS = (PREVIOUS_WEEK, WEEK)

SHOES = "395dfc8e-bb1e-5f97-b0d5-ea06f9af7b0e"
PERFUME = "ab34c859-c983-5ba9-8ca6-48aeece4aa48"
LAPTOP = "c030461b-a5f8-527f-a05b-8c67186118a5"
UNOWNED = "00000000-0000-4000-8000-00000000beef"

# `public.products`: the client that owns each product (the seeded owners).
PRODUCTS = pd.DataFrame(
    [
        {"product_id": SHOES, "client_id": "stride-athletics", "warehouse": "Monterrey"},
        {"product_id": PERFUME, "client_id": "coco-beauty", "warehouse": "Monterrey"},
        {"product_id": LAPTOP, "client_id": "nexa-electronics", "warehouse": "Zaragoza"},
        {"product_id": UNOWNED, "client_id": None, "warehouse": "Zaragoza"},
    ]
)

_event_ids = count(1)


def telemetry_event(event_type, at="2026-10-13T10:00:00Z", **payload):
    """A `telemetry_events` row as extraction reads it: payload fields arrive as text."""
    return {
        "id": f"00000000-0000-4000-8000-{next(_event_ids):012d}",
        "timestamp": at,
        "created_at": at,
        "event_type": event_type,
        "warehouse": "Monterrey",
        "client_id": None,
        "product_id": SHOES,
        "quantity": None,
        "order_id": None,
        "triggering_order_id": None,
        "threshold_band": "low" if event_type == THRESHOLD else None,
        "reconciliation_id": None,
        "schema_version": "1.0.0",
        **payload,
    }


def kpi_event(event_type, client_id, *, warehouse="Monterrey", week_start=WEEK, quantity=None):
    """A telemetry event as a KPI task receives it: its type and the fields it counts by."""
    return {
        "event_type": event_type,
        "warehouse": warehouse,
        "client_id": client_id,
        "week_start": week_start,
        "quantity": quantity,
    }


def frame(*rows):
    return pd.DataFrame(list(rows))


def records(result):
    return result.to_dict("records")


def repeat(times, event_type, client_id, **fields):
    return [kpi_event(event_type, client_id, **fields) for _ in range(times)]


# --- Inbound Volume -> inbound_units_count -------------------------------------------


def test_inbound_volume_sums_the_units_received_per_warehouse_client_and_week():
    kpi_events = frame(
        kpi_event(INBOUND, "stride-athletics", quantity=120),
        kpi_event(INBOUND, "stride-athletics", quantity=45),
        kpi_event(INBOUND, "stride-athletics", quantity=9),
        kpi_event(INBOUND, "stride-athletics", quantity=12, week_start=PREVIOUS_WEEK),
        kpi_event(INBOUND, "coco-beauty", quantity=30),
        kpi_event(INBOUND, "nexa-electronics", quantity=5, warehouse="Zaragoza"),
    )

    volume = compute_inbound_volume.fn(kpi_events)

    assert records(volume) == [
        {"warehouse": "Monterrey", "client_id": "coco-beauty", "week_start": WEEK,
         "inbound_units_count": 30},
        {"warehouse": "Monterrey", "client_id": "stride-athletics", "week_start": PREVIOUS_WEEK,
         "inbound_units_count": 12},
        # 120 + 45 + 9: units, summed per client, never mixed with coco-beauty's 30.
        {"warehouse": "Monterrey", "client_id": "stride-athletics", "week_start": WEEK,
         "inbound_units_count": 174},
        {"warehouse": "Zaragoza", "client_id": "nexa-electronics", "week_start": WEEK,
         "inbound_units_count": 5},
    ]  # fmt: skip


def test_inbound_volume_counts_only_inbound_orders():
    kpi_events = frame(
        kpi_event(INBOUND, "stride-athletics", quantity=7),
        kpi_event(OUTBOUND, "stride-athletics", quantity=500),
        kpi_event(THRESHOLD, "stride-athletics"),
        kpi_event(DISCREPANCY, "stride-athletics"),
    )

    assert compute_inbound_volume.fn(kpi_events)["inbound_units_count"].tolist() == [7]


@pytest.mark.parametrize(
    "quantity",
    [None, "twelve", "12", 2.5, 0, -3, True],
    ids=["null", "text", "numeric-text", "fraction", "zero", "negative", "flag"],
)
def test_inbound_volume_refuses_a_quantity_that_is_not_a_whole_unit_count(quantity):
    kpi_events = frame(
        kpi_event(INBOUND, "stride-athletics", quantity=120),
        kpi_event(INBOUND, "stride-athletics", quantity=quantity),
    )

    with pytest.raises(KpiInputError, match=r"Inbound Volume: 1 inbound event\(s\)"):
        compute_inbound_volume.fn(kpi_events)


# --- Outbound Throughput -> outbound_orders_count ------------------------------------


def test_outbound_throughput_counts_orders_not_units():
    kpi_events = frame(
        kpi_event(OUTBOUND, "stride-athletics", quantity=10),
        kpi_event(OUTBOUND, "stride-athletics", quantity=1),
        kpi_event(OUTBOUND, "stride-athletics", quantity=500),
        kpi_event(OUTBOUND, "coco-beauty", quantity=2),
        kpi_event(INBOUND, "coco-beauty", quantity=40),
    )

    throughput = compute_outbound_throughput.fn(kpi_events)

    assert records(throughput) == [
        {"warehouse": "Monterrey", "client_id": "coco-beauty", "week_start": WEEK,
         "outbound_orders_count": 1},
        {"warehouse": "Monterrey", "client_id": "stride-athletics", "week_start": WEEK,
         "outbound_orders_count": 3},
    ]  # fmt: skip


def test_outbound_throughput_of_a_week_without_orders_is_empty_not_an_error():
    throughput = compute_outbound_throughput.fn(
        frame(kpi_event(INBOUND, "coco-beauty", quantity=1))
    )

    assert throughput.empty
    assert list(throughput.columns) == [
        "warehouse",
        "client_id",
        "week_start",
        "outbound_orders_count",
    ]


# --- Stockout Frequency -> stockout_events_count -------------------------------------


def test_stockout_frequency_counts_every_crossing_below_the_minimum():
    # Shoes drop 12 -> 5 (low) -> 0 (out of stock) in one week: two crossings.
    kpi_events = frame(
        kpi_event(THRESHOLD, "stride-athletics"),
        kpi_event(THRESHOLD, "stride-athletics"),
        kpi_event(THRESHOLD, "coco-beauty", week_start=PREVIOUS_WEEK),
        kpi_event(OUTBOUND, "stride-athletics"),
    )

    frequency = compute_stockout_frequency.fn(kpi_events)

    assert records(frequency) == [
        {"warehouse": "Monterrey", "client_id": "coco-beauty", "week_start": PREVIOUS_WEEK,
         "stockout_events_count": 1},
        {"warehouse": "Monterrey", "client_id": "stride-athletics", "week_start": WEEK,
         "stockout_events_count": 2},
    ]  # fmt: skip


# --- Discrepancy Rate -> discrepancy_events_count, discrepancy_rate ------------------


def test_discrepancy_rate_matches_the_briefing_definition():
    # BRIEFING section 4: discrepancy_rate = discrepancy_events_count / outbound_orders_count.
    kpi_events = frame(
        *repeat(8, OUTBOUND, "stride-athletics"),
        *repeat(3, DISCREPANCY, "stride-athletics"),
        *repeat(3, OUTBOUND, "coco-beauty"),
        *repeat(1, DISCREPANCY, "coco-beauty"),
        *repeat(4, OUTBOUND, "nexa-electronics", warehouse="Zaragoza"),
    )
    throughput = compute_outbound_throughput.fn(kpi_events)

    rate = compute_discrepancy_rate.fn(kpi_events, throughput)

    assert records(rate) == [
        # 1 / 3 = 0.33333..., rounded to 4 decimals so a re-run gives the same value.
        {"warehouse": "Monterrey", "client_id": "coco-beauty", "week_start": WEEK,
         "discrepancy_events_count": 1, "discrepancy_rate": 0.3333},
        # 3 / 8 = 0.375
        {"warehouse": "Monterrey", "client_id": "stride-athletics", "week_start": WEEK,
         "discrepancy_events_count": 3, "discrepancy_rate": 0.375},
        # 0 / 4: orders shipped, nothing found.
        {"warehouse": "Zaragoza", "client_id": "nexa-electronics", "week_start": WEEK,
         "discrepancy_events_count": 0, "discrepancy_rate": 0.0},
    ]  # fmt: skip


def test_discrepancy_rate_is_zero_when_no_order_was_shipped():
    # BRIEFING section 4: "0 if no orders that week", never a division by zero.
    kpi_events = frame(*repeat(2, DISCREPANCY, "coco-beauty"))
    throughput = compute_outbound_throughput.fn(kpi_events)

    rate = compute_discrepancy_rate.fn(kpi_events, throughput)

    assert records(rate) == [
        {"warehouse": "Monterrey", "client_id": "coco-beauty", "week_start": WEEK,
         "discrepancy_events_count": 2, "discrepancy_rate": 0.0},
    ]  # fmt: skip


# --- Defensive input checks shared by the four KPI tasks -----------------------------

NO_ORDERS = pd.DataFrame(columns=["warehouse", "client_id", "week_start", "outbound_orders_count"])
KPI_TASKS = {
    "Inbound Volume": lambda kpi_events: compute_inbound_volume.fn(kpi_events),
    "Outbound Throughput": lambda kpi_events: compute_outbound_throughput.fn(kpi_events),
    "Stockout Frequency": lambda kpi_events: compute_stockout_frequency.fn(kpi_events),
    "Discrepancy Rate": lambda kpi_events: compute_discrepancy_rate.fn(kpi_events, NO_ORDERS),
}
KPI_EVENT_TYPES = {
    "Inbound Volume": INBOUND,
    "Outbound Throughput": OUTBOUND,
    "Stockout Frequency": THRESHOLD,
    "Discrepancy Rate": DISCREPANCY,
}


@pytest.mark.parametrize("kpi", KPI_TASKS)
@pytest.mark.parametrize("missing", ["warehouse", "client_id", "week_start"])
def test_every_kpi_refuses_an_event_it_cannot_attribute(kpi, missing):
    # BRIEFING section 7: each row belongs to one client. A null where a key is
    # expected is refused, never counted into an "unknown" bucket.
    orphan = kpi_event(KPI_EVENT_TYPES[kpi], "stride-athletics", quantity=3)
    orphan[missing] = None

    with pytest.raises(KpiInputError, match=f"{kpi}: 1 event"):
        KPI_TASKS[kpi](frame(kpi_event(KPI_EVENT_TYPES[kpi], "coco-beauty", quantity=1), orphan))


@pytest.mark.parametrize("kpi", KPI_TASKS)
def test_every_kpi_refuses_events_without_a_countable_column(kpi):
    malformed = frame(kpi_event(KPI_EVENT_TYPES[kpi], "coco-beauty", quantity=1)).drop(
        columns="client_id"
    )

    with pytest.raises(KpiInputError, match=f"{kpi} needs the column"):
        KPI_TASKS[kpi](malformed)


# --- prepare_kpi_events: the defence against malformed telemetry ---------------------


@pytest.mark.parametrize(
    ("event_type", "payload", "reason"),
    [
        (INBOUND, {"quantity": None}, "invalid_quantity"),  # null where a quantity is required
        (INBOUND, {"quantity": "seven"}, "invalid_quantity"),  # text where a number is expected
        (INBOUND, {"quantity": "2.5"}, "invalid_quantity"),
        (INBOUND, {"quantity": "-3"}, "invalid_quantity"),
        (INBOUND, {"warehouse": None}, "unknown_warehouse"),
        (INBOUND, {"warehouse": "los_angeles"}, "unknown_warehouse"),
        (INBOUND, {"product_id": UNOWNED}, "missing_client_id"),
        (THRESHOLD, {"threshold_band": None}, "unknown_threshold_band"),
        (OUTBOUND, {"at": "2026-10-19T00:00:00Z"}, "outside_window"),  # the week in progress
    ],
)
def test_prepare_kpi_events_excludes_malformed_telemetry_instead_of_counting_it(
    event_type, payload, reason
):
    counted = telemetry_event(INBOUND, quantity="4")
    malformed = telemetry_event(event_type, **{"quantity": "9", **payload})

    prepared = prepare_kpi_events.fn(frame(counted, malformed), PRODUCTS, WEEKS)

    assert prepared.events_read == 2
    assert prepared.events_excluded[reason] == 1
    assert records(prepared.exclusions) == [{"event_id": malformed["id"], "reason": reason}]
    assert compute_inbound_volume.fn(prepared.countable)["inbound_units_count"].tolist() == [4]


def test_prepare_kpi_events_reads_a_numeric_quantity_like_its_text():
    # Ingest does not validate properties: a JSON number may arrive where text is expected.
    prepared = prepare_kpi_events.fn(frame(telemetry_event(INBOUND, quantity=6)), PRODUCTS, WEEKS)

    assert prepared.events_excluded["invalid_quantity"] == 0
    assert compute_inbound_volume.fn(prepared.countable)["inbound_units_count"].tolist() == [6]


def test_prepare_kpi_events_hands_the_kpis_no_identifiers():
    prepared = prepare_kpi_events.fn(
        frame(telemetry_event(INBOUND, quantity="4", order_id="order-1")), PRODUCTS, WEEKS
    )

    assert tuple(prepared.countable.columns) == COUNTABLE_COLUMNS
    assert records(prepared.countable) == [
        {"event_type": INBOUND, "warehouse": "Monterrey", "client_id": "stride-athletics",
         "week_start": WEEK, "quantity": 4},
    ]  # fmt: skip


# --- assemble_weekly_warehouse_client_rows: the destination table's rows -------------


def test_assembled_rows_have_the_destination_columns_and_a_zero_row_per_client():
    kpi_events = frame(kpi_event(INBOUND, "stride-athletics", quantity=5))
    throughput = compute_outbound_throughput.fn(kpi_events)

    rows = assemble_weekly_warehouse_client_rows.fn(
        compute_inbound_volume.fn(kpi_events),
        throughput,
        compute_stockout_frequency.fn(kpi_events),
        compute_discrepancy_rate.fn(kpi_events, throughput),
        PRODUCTS,
        (WEEK,),
    )

    assert tuple(rows.columns) == ROW_COLUMNS  # BRIEFING section 5, in its order
    assert records(rows) == [
        {"warehouse": "Monterrey", "client_id": "coco-beauty", "week_start": WEEK,
         "inbound_units_count": 0, "outbound_orders_count": 0, "stockout_events_count": 0,
         "discrepancy_events_count": 0, "discrepancy_rate": 0.0},
        {"warehouse": "Monterrey", "client_id": "stride-athletics", "week_start": WEEK,
         "inbound_units_count": 5, "outbound_orders_count": 0, "stockout_events_count": 0,
         "discrepancy_events_count": 0, "discrepancy_rate": 0.0},
        {"warehouse": "Zaragoza", "client_id": "nexa-electronics", "week_start": WEEK,
         "inbound_units_count": 0, "outbound_orders_count": 0, "stockout_events_count": 0,
         "discrepancy_events_count": 0, "discrepancy_rate": 0.0},
    ]  # fmt: skip


def test_the_kpi_tasks_turn_a_week_of_telemetry_into_the_hand_calculated_row():
    # One week of Stride Athletics in Monterrey, from raw telemetry rows:
    # 3 receipts (120 + 45 + 9 = 174 units), 8 orders shipped, 2 stock crossings,
    # 3 discrepancies found: 3 / 8 = 0.375.
    telemetry = frame(
        *(telemetry_event(INBOUND, quantity=units) for units in ("120", "45", "9")),
        *(telemetry_event(OUTBOUND, quantity="1") for _ in range(8)),
        telemetry_event(THRESHOLD, threshold_band="low"),
        telemetry_event(THRESHOLD, threshold_band="out_of_stock"),
        *(telemetry_event(DISCREPANCY, reconciliation_id=f"count-{n}") for n in range(3)),
    )

    prepared = prepare_kpi_events.fn(telemetry, PRODUCTS, (WEEK,))
    throughput = compute_outbound_throughput.fn(prepared.countable)
    rows = assemble_weekly_warehouse_client_rows.fn(
        compute_inbound_volume.fn(prepared.countable),
        throughput,
        compute_stockout_frequency.fn(prepared.countable),
        compute_discrepancy_rate.fn(prepared.countable, throughput),
        PRODUCTS,
        (WEEK,),
    )

    stride = rows[rows["client_id"] == "stride-athletics"]
    assert records(stride) == [
        {"warehouse": "Monterrey", "client_id": "stride-athletics", "week_start": WEEK,
         "inbound_units_count": 174, "outbound_orders_count": 8, "stockout_events_count": 2,
         "discrepancy_events_count": 3, "discrepancy_rate": 0.375},
    ]  # fmt: skip
    # The same input through the one-call transform gives the same rows.
    pd.testing.assert_frame_equal(
        rows, weekly_rollup.build_rollup(telemetry, PRODUCTS, (WEEK,)).rows
    )
