"""Stage 3, load: validate, then publish to `reporting.weekly_warehouse_client_performance`.

    validate_weekly_rows -> load_weekly_rows (one transaction, design 3.1)

The only stage that writes, and it writes only to `reporting.*`.
"""

from datetime import date

import pandas as pd
from prefect import flow, task
from prefect.cache_policies import NO_CACHE

from data.pipelines import reporting_db, run_log
from data.pipelines.subflows.activity_extraction import WarehouseClientActivity
from data.pipelines.subflows.kpi_computation import WeeklyWarehouseClientKpis
from data.pipelines.subflows.policies import TASK_TIMEOUT_SECONDS, retry_on_transient_error
from data.pipelines.weekly_warehouse_client_performance import load
from data.process import collection_health, weekly_rollup


@task(cache_policy=NO_CACHE, timeout_seconds=TASK_TIMEOUT_SECONDS)
def validate_weekly_rows(
    rows: pd.DataFrame, weeks: tuple[date, ...], newest_closed_week: date
) -> pd.DataFrame:
    """The gate before the load: refuse rows the destination must never hold (design 3.5)."""
    return weekly_rollup.validate(rows, weeks, newest_closed_week)


# 2 retries, 15 s then 60 s. The load is one transaction: a failed attempt
# leaves nothing behind, and a retry writes the same rows again.
@task(
    retries=2,
    retry_delay_seconds=[15, 60],
    retry_condition_fn=retry_on_transient_error,
    timeout_seconds=TASK_TIMEOUT_SECONDS,
    cache_policy=NO_CACHE,
)
def load_weekly_rows(
    run: run_log.RunHandle,
    weeks: tuple[date, ...],
    rows: pd.DataFrame,
    kpis: WeeklyWarehouseClientKpis,
    health: collection_health.CollectionHealth | None,
    activity: WarehouseClientActivity,
) -> load.LoadResult:
    """Publish the weeks and mark the run completed, in one transaction."""
    return load.load_weekly_rows(
        reporting_db.get_pipeline_engine(),
        run_id=run.run_id,
        arrival_cutoff=run.arrival_cutoff,
        weeks=weeks,
        rows=rows,
        exclusions=kpis.exclusions,
        health=health,
        run_fields={
            "events_read": kpis.events_read,
            "events_excluded": kpis.events_excluded,
            "source_watermark": activity.source.source_watermark,
            "late_events_outside_window": activity.source.late_events_outside_window,
            "telemetry_dropped": activity.source.telemetry_dropped,
        },
    )


@flow
def load_weekly_warehouse_client_performance(
    run: run_log.RunHandle,
    weeks: tuple[date, ...],
    newest_closed_week: date,
    kpis: WeeklyWarehouseClientKpis,
    health: collection_health.CollectionHealth | None,
    activity: WarehouseClientActivity,
) -> load.LoadResult:
    """Validate the KPI rows, then upsert them on `(warehouse, client_id, week_start)`.

    `health` is None when collection health failed: the run is then recorded
    as `degraded`. A `RollupValidationError` stops the load before anything is
    written.
    """
    rows = validate_weekly_rows(kpis.rows, weeks, newest_closed_week)
    return load_weekly_rows(run, weeks, rows, kpis, health, activity)
