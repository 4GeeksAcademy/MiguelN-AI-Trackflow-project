"""The Weekly Warehouse & Client Performance pipeline's stages, one Prefect subflow each.

The main flow (`data/pipelines/pipeline.py`) invokes them in this order:

1. `extract_weekly_warehouse_client_activity`   (activity_extraction.py)
2. `compute_weekly_warehouse_client_kpis`       (kpi_computation.py)
3. `measure_kpi_event_collection_health`        (collection_health_check.py, optional)
4. `load_weekly_warehouse_client_performance`   (performance_load.py)
5. `export_weekly_performance_eval_snapshot`    (eval_snapshot_export.py, optional)

Each takes explicit inputs and returns its output; nothing passes between them
through module state. Data-heavy outputs cross the boundaries as `StageData`
(stage_data.py), so Prefect records their row counts and not their rows.
"""
