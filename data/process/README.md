# `data/process` folder

This folder contains **processed/intermediate data** and/or artifacts produced by pipelines (for example: clean datasets, features, aggregates, intermediate tables, or transformation outputs).

- **Main purpose**: clearly separate “raw” data from data ready for analysis, modeling, or app consumption.
- **Recommendation**: document which pipeline produces each artifact, its schema, refresh cadence, and how quality is validated (checks, constraints, data tests).

## In this folder

- `weekly_rollup.py`: the Weekly Warehouse & Client Performance transform. `prepare_events` (week assignment, client resolution, exclusions, business-key dedupe), one function per KPI (`inbound_volume`, `outbound_throughput`, `stockout_frequency`, `discrepancy_rate`), `assemble_rows` (zero rows, destination columns) and the validation gate. `build_rollup` runs them in one call.
- `collection_health.py`: capture coverage, lag, gaps, bursts and clock skew for the same run.

Both are pure Pandas, with no I/O, clock or randomness, so the flow, the backfill and the tests share them. See `data/pipelines/PIPELINE_DESIGN.md` 2.7 and 3.7.

> _Spanish version: [README.es.md](./README.es.md)._
