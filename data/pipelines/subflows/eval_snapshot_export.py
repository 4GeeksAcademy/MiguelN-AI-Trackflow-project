"""Optional stage: a secondary export of the run's validation summary to `data/eval/`.

Runs after the load has committed. The main flow invokes it with
`return_state=True`, so a missing eval file never fails a run whose data is
already published.
"""

import json
from dataclasses import asdict
from datetime import date, timezone
from pathlib import Path

import pandas as pd
from prefect import flow, task
from prefect.cache_policies import NO_CACHE

from data.pipelines import run_log
from data.pipelines.subflows.kpi_computation import WeeklyWarehouseClientKpis
from data.pipelines.weekly_warehouse_client_performance import load
from data.pipelines.weekly_warehouse_client_performance.schema import PIPELINE_NAME
from data.process import collection_health, weekly_rollup


@task(cache_policy=NO_CACHE, timeout_seconds=60)
def write_eval_snapshot(
    run: run_log.RunHandle,
    weeks: tuple[date, ...],
    kpis: WeeklyWarehouseClientKpis,
    health: collection_health.CollectionHealth | None,
    loaded: load.LoadResult,
    output_dir: Path,
    code_version: str | None,
) -> str:
    """Write the run's validation summary to `output_dir` and return the file's path.

    Totals per week and warehouse, the exclusions, the validation outcome, the
    load counts and the collection health: what a reviewer needs to check a
    run without database access. No per-client figures (they are commercially
    sensitive) and no event payloads.
    """
    rows = kpis.rows
    totals = (
        rows.groupby(["week_start", "warehouse"], as_index=False)[
            list(weekly_rollup.COUNT_COLUMNS)
        ].sum()
        if len(rows)
        else pd.DataFrame(columns=["week_start", "warehouse", *weekly_rollup.COUNT_COLUMNS])
    )
    snapshot = {
        "pipeline_name": PIPELINE_NAME,
        "run_id": str(run.run_id),
        "arrival_cutoff": run.arrival_cutoff.astimezone(timezone.utc).isoformat(),
        "code_version": code_version,
        "weeks": [week.isoformat() for week in weeks],
        "events_read": kpis.events_read,
        "events_excluded": kpis.events_excluded,
        "validation": {"passed": True, "rows": len(rows)},
        "totals_by_week_and_warehouse": [
            {
                "week_start": total["week_start"].isoformat(),
                "warehouse": total["warehouse"],
                **{name: int(total[name]) for name in weekly_rollup.COUNT_COLUMNS},
            }
            for total in totals.to_dict("records")
        ],
        "load": {**asdict(loaded), "weeks_revised": [w.isoformat() for w in loaded.weeks_revised]},
        "collection_health": None
        if health is None
        else {
            "quality_status": health.quality_status,
            "min_capture_coverage": health.min_capture_coverage,
            "stats_rows": len(health.stats),
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    cutoff = run.arrival_cutoff.astimezone(timezone.utc)
    path = output_dir / f"{cutoff:%Y%m%dT%H%M%SZ}_{run.run_id}.json"
    partial = path.with_suffix(".json.partial")
    partial.write_text(json.dumps(snapshot, indent=2, default=str) + "\n")
    partial.replace(path)
    return str(path)


@flow
def export_weekly_performance_eval_snapshot(
    run: run_log.RunHandle,
    weeks: tuple[date, ...],
    kpis: WeeklyWarehouseClientKpis,
    health: collection_health.CollectionHealth | None,
    loaded: load.LoadResult,
    output_dir: Path,
    code_version: str | None,
) -> str:
    """Export the run's validation summary (no per-client figures) for review."""
    return write_eval_snapshot(run, weeks, kpis, health, loaded, output_dir, code_version)
