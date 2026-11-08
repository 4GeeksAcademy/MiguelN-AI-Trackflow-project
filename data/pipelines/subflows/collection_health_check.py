"""Optional stage: is the telemetry behind the KPIs complete? (design 3.7, 9.4-9.6).

Coverage, arrival lag, gaps, bursts and clock skew of the KPI events. It goes
to the run log, never into the report rows. The main flow invokes it with
`return_state=True`: if it fails, the figures are still published and the run
is marked `degraded`, because withholding the numbers would be worse than
publishing them with a visible warning (design 3.5).
"""

from datetime import date

import pandas as pd
from prefect import flow, task

from data.pipelines.subflows.activity_extraction import WarehouseClientActivity
from data.pipelines.subflows.kpi_computation import WeeklyWarehouseClientKpis
from data.pipelines.subflows.policies import (
    CACHE_EXPIRATION,
    RESULTS_DIR,
    TASK_TIMEOUT_SECONDS,
    content_cache_key,
)
from data.process import collection_health, weekly_rollup


@task(
    cache_key_fn=content_cache_key("wwcp-collection-health", weekly_rollup, collection_health),
    cache_expiration=CACHE_EXPIRATION,
    persist_result=True,
    result_storage=RESULTS_DIR,
    timeout_seconds=TASK_TIMEOUT_SECONDS,
)
def compute_collection_health(
    events: pd.DataFrame,
    exclusions: pd.DataFrame,
    order_counts: pd.DataFrame,
    weeks: tuple[date, ...],
) -> collection_health.CollectionHealth:
    """Coverage, lag, gaps, bursts and clock skew. Observability, not the KPIs."""
    return collection_health.compute(events, exclusions, order_counts, weeks)


@flow
def measure_kpi_event_collection_health(
    activity: WarehouseClientActivity,
    kpis: WeeklyWarehouseClientKpis,
    weeks: tuple[date, ...],
) -> collection_health.CollectionHealth:
    """Compare the KPI events counted with the inventory orders of the same days."""
    return compute_collection_health(
        activity.source.events, kpis.exclusions, activity.order_counts, weeks
    )
