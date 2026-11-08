"""Stage 2, transform: the four KPIs of BRIEFING section 2, one task each (design 2.7).

    prepare_kpi_events -> compute_inbound_volume
                       -> compute_outbound_throughput -> compute_discrepancy_rate
                       -> compute_stockout_frequency
                       -> assemble_weekly_warehouse_client_rows

Every task wraps a pure function of `data/process/weekly_rollup.py`: no I/O, no
clock, no retries (the same input fails the same way). The KPI tasks and the
assembly are cached by content for an hour (`content_cache_key`), and their
stored results hold counts per warehouse, client and week. The preparation is
not cached: its output holds one row per event, which never goes to result
storage.
"""

from datetime import date

import pandas as pd
from prefect import flow, task
from prefect.cache_policies import NO_CACHE

from data.pipelines.subflows.activity_extraction import WarehouseClientActivity
from data.pipelines.subflows.policies import (
    CACHE_EXPIRATION,
    RESULTS_DIR,
    TASK_TIMEOUT_SECONDS,
    content_cache_key,
)
from data.pipelines.subflows.stage_data import StageData
from data.process import weekly_rollup


class WeeklyWarehouseClientKpis(StageData):
    """Stage 2's output: the destination rows and what was left out of them."""

    rows: pd.DataFrame
    exclusions: pd.DataFrame
    events_read: int
    events_excluded: dict[str, int]


def _cached(name: str) -> dict:
    return {
        "cache_key_fn": content_cache_key(name, weekly_rollup),
        "cache_expiration": CACHE_EXPIRATION,
        "persist_result": True,
        "result_storage": RESULTS_DIR,
        "timeout_seconds": TASK_TIMEOUT_SECONDS,
    }


@task(cache_policy=NO_CACHE, persist_result=False, timeout_seconds=TASK_TIMEOUT_SECONDS)
def prepare_kpi_events(
    events: pd.DataFrame, products: pd.DataFrame, weeks: tuple[date, ...]
) -> weekly_rollup.PreparedEvents:
    """Normalize, resolve the client, exclude and deduplicate: what the KPIs may count.

    Malformed events (no warehouse, a quantity that is not a whole number of
    at least 1, no client, an unknown threshold band, outside the weeks, a
    duplicate) are excluded with their reason, never counted and never fatal.
    """
    return weekly_rollup.prepare_events(events, products, weeks)


@task(**_cached("wwcp-inbound-volume"))
def compute_inbound_volume(kpi_events: pd.DataFrame) -> pd.DataFrame:
    """Inbound Volume -> `inbound_units_count`: units received per warehouse, client and week."""
    return weekly_rollup.inbound_volume(kpi_events)


@task(**_cached("wwcp-outbound-throughput"))
def compute_outbound_throughput(kpi_events: pd.DataFrame) -> pd.DataFrame:
    """Outbound Throughput -> `outbound_orders_count`: orders picked and dispatched."""
    return weekly_rollup.outbound_throughput(kpi_events)


@task(**_cached("wwcp-stockout-frequency"))
def compute_stockout_frequency(kpi_events: pd.DataFrame) -> pd.DataFrame:
    """Stockout Frequency -> `stockout_events_count`: crossings below the minimum stock."""
    return weekly_rollup.stockout_frequency(kpi_events)


@task(**_cached("wwcp-discrepancy-rate"))
def compute_discrepancy_rate(
    kpi_events: pd.DataFrame, outbound_throughput: pd.DataFrame
) -> pd.DataFrame:
    """Discrepancy Rate -> `discrepancy_events_count`, `discrepancy_rate` (0 without orders)."""
    return weekly_rollup.discrepancy_rate(kpi_events, outbound_throughput)


@task(**_cached("wwcp-weekly-rows"))
def assemble_weekly_warehouse_client_rows(
    inbound_volume: pd.DataFrame,
    outbound_throughput: pd.DataFrame,
    stockout_frequency: pd.DataFrame,
    discrepancy_rate: pd.DataFrame,
    products: pd.DataFrame,
    weeks: tuple[date, ...],
) -> pd.DataFrame:
    """The rows of `reporting.weekly_warehouse_client_performance`, zero rows included."""
    return weekly_rollup.assemble_rows(
        inbound_volume, outbound_throughput, stockout_frequency, discrepancy_rate, products, weeks
    )


@flow
def compute_weekly_warehouse_client_kpis(
    activity: WarehouseClientActivity, weeks: tuple[date, ...]
) -> WeeklyWarehouseClientKpis:
    """Inbound Volume, Outbound Throughput, Stockout Frequency and Discrepancy Rate.

    One row per warehouse, client and week of `weeks`, in the destination
    table's columns, plus every excluded event and why.
    """
    prepared = prepare_kpi_events(activity.source.events, activity.products, weeks)
    inbound = compute_inbound_volume(prepared.countable)
    outbound = compute_outbound_throughput(prepared.countable)
    stockouts = compute_stockout_frequency(prepared.countable)
    discrepancies = compute_discrepancy_rate(prepared.countable, outbound)
    rows = assemble_weekly_warehouse_client_rows(
        inbound, outbound, stockouts, discrepancies, activity.products, weeks
    )
    return WeeklyWarehouseClientKpis(
        rows=rows,
        exclusions=prepared.exclusions,
        events_read=prepared.events_read,
        events_excluded=prepared.events_excluded,
    )
