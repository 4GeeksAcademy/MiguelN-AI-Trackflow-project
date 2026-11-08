"""Stage 1, extract: the week's warehouse and client activity, read-only (design 2.5).

Reads the four KPI events from `telemetry_events`, the client that owns each
product, and the inventory order counts that capture coverage compares
against. Nothing here writes: `telemetry_events` is this pipeline's source,
never its destination.
"""

from datetime import datetime

import pandas as pd
from prefect import flow, task
from prefect.cache_policies import NO_CACHE

from data.pipelines import reporting_db
from data.pipelines.subflows.policies import TASK_TIMEOUT_SECONDS, retry_on_transient_error
from data.pipelines.subflows.stage_data import StageData
from data.pipelines.weekly_warehouse_client_performance import extract


class WarehouseClientActivity(StageData):
    """Stage 1's output: one read-only snapshot of everything the KPIs are computed from."""

    source: extract.SourceExtract
    products: pd.DataFrame
    order_counts: pd.DataFrame


# 3 retries, 10 s / 30 s / 90 s: about two minutes of outage tolerated per read.
# The read is bounded by the run's arrival cutoff, so a retry reads exactly the
# same snapshot. Event rows are not persisted as Prefect results.
@task(
    retries=3,
    retry_delay_seconds=[10, 30, 90],
    retry_condition_fn=retry_on_transient_error,
    timeout_seconds=TASK_TIMEOUT_SECONDS,
    persist_result=False,
    cache_policy=NO_CACHE,
)
def extract_inventory_kpi_events(
    window_start: datetime,
    window_end: datetime,
    arrival_cutoff: datetime,
    previous_cutoff: datetime | None,
) -> extract.SourceExtract:
    """The four KPI event types of the window, read-only, as one snapshot."""
    return extract.read_source_events(
        reporting_db.get_pipeline_engine(),
        window_start=window_start,
        window_end=window_end,
        arrival_cutoff=arrival_cutoff,
        previous_cutoff=previous_cutoff,
    )


# Same retry policy as the event read: same database, same failure modes.
@task(
    retries=3,
    retry_delay_seconds=[10, 30, 90],
    retry_condition_fn=retry_on_transient_error,
    timeout_seconds=TASK_TIMEOUT_SECONDS,
    persist_result=False,
    cache_policy=NO_CACHE,
)
def extract_product_clients() -> pd.DataFrame:
    """`product_id, client_id, warehouse` for every product, read-only."""
    return extract.read_product_clients(reporting_db.get_pipeline_engine())


# Same retry policy as the event read.
@task(
    retries=3,
    retry_delay_seconds=[10, 30, 90],
    retry_condition_fn=retry_on_transient_error,
    timeout_seconds=TASK_TIMEOUT_SECONDS,
    persist_result=False,
    cache_policy=NO_CACHE,
)
def extract_inventory_order_counts(window_start: datetime, window_end: datetime) -> pd.DataFrame:
    """Inventory orders per warehouse and day: the independent count for coverage."""
    return extract.read_inventory_order_counts(
        reporting_db.get_pipeline_engine(), window_start=window_start, window_end=window_end
    )


@flow
def extract_weekly_warehouse_client_activity(
    window_start: datetime,
    window_end: datetime,
    arrival_cutoff: datetime,
    previous_cutoff: datetime | None,
) -> WarehouseClientActivity:
    """Read the window's KPI events, product owners and inventory order counts.

    `window_start`/`window_end` cover whole closed weeks; `arrival_cutoff` is the
    run's snapshot bound, so a re-run reads the same rows (design 2.5).
    """
    source = extract_inventory_kpi_events(window_start, window_end, arrival_cutoff, previous_cutoff)
    return WarehouseClientActivity(
        source=source,
        products=extract_product_clients(),
        order_counts=extract_inventory_order_counts(window_start, window_end),
    )
