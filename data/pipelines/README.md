# `data/pipelines` folder

This folder groups **all data pipelines in the monorepo** related to the company: ingestion, ETL/ELT, cleaning, transformation, and loading into analytical or production systems.

Each subfolder or file under `data/pipelines/` should represent **one pipeline or job set** (for example `sales-etl`, `telemetry-stream`, `customer-segmentation`) and include the required configuration (scripts, orchestration, connectors, schemas, etc.).

- **Main purpose**: consolidate in one place the data movement and transformation logic that powers the company’s applications and analytics.
- **Recommendation**: document pipelines as you add them—their goal, data sources and sinks, dependencies, and how to run them in development, testing, and production.

## Pipelines

### Weekly Warehouse & Client Performance

The rollup behind the CEO's weekly report: Inbound Volume, Outbound Throughput, Stockout Frequency and Discrepancy Rate per warehouse, client and ISO week (`BRIEFING.md`).

- **Source (read-only):** `public.telemetry_events` (the four mandatory inventory events), plus `products` for the owning client and `inbound_orders` / `outbound_orders` for capture coverage.
- **Destination:** `reporting.weekly_warehouse_client_performance`, with the run log `reporting.pipeline_runs` and its audit tables. The pipeline creates them on its first run.
- **Design and decisions:** [`PIPELINE_DESIGN.md`](./PIPELINE_DESIGN.md). Section 10 covers the implementation, section 11 the subflows and the dashboard.
- **Code:** `pipeline.py` (the main and backfill flows, state hooks and CLI), `subflows/` (one Prefect subflow per stage: `extract_weekly_warehouse_client_activity`, `compute_weekly_warehouse_client_kpis`, `measure_kpi_event_collection_health`, `load_weekly_warehouse_client_performance`, `export_weekly_performance_eval_snapshot`), `run_log.py`, `reporting_db.py`, `weekly_warehouse_client_performance/` (schema, extract, load, queries, trigger). The pure transforms, one function per KPI, live in `data/process/`.
- **API:** `services/reporting/` serves `GET /reporting/weekly-warehouse-client-performance`, `GET /reporting/pipeline-runs/latest` and `POST /reporting/pipeline-runs`, all with a bearer token.
- **Dashboard:** `/reporting` in `uis/backoffice` ("Weekly report"), the four KPIs per warehouse and client for leadership.

```bash
python data/pipelines/pipeline.py                            # the last 2 closed weeks, once
python data/pipelines/pipeline.py --week-start 2026-10-12    # one closed week
python data/pipelines/pipeline.py --backfill 2026-09-28 2026-10-12
prefect server start                                         # then, for the Monday 06:00 schedule:
PREFECT_API_URL=http://127.0.0.1:4200/api python data/pipelines/pipeline.py --serve
```

The database comes from `REPORTING_DATABASE_URL`, then the Prefect Secret block `trackflow-reporting-database-url`, then `DATABASE_URL`. Tests: `tests/pipelines/test_pipeline.py` (the KPI tasks, `python -m pytest tests/pipelines/test_pipeline.py`), `tests/pipelines/test_pipeline_flow.py` (the flows and subflows), `tests/test_weekly_rollup.py`, `test_collection_health.py`, `test_reporting_api.py`, and the opt-in `test_reporting_postgres.py` (`INVENTORY_TEST_DATABASE_URL`).

> _Spanish version: [README.es.md](./README.es.md)._
