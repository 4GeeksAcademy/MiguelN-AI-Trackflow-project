# `data/eval` folder

This folder is for **evaluation and validation**: evaluation datasets, golden sets, experiment results, metrics, and artifacts used to measure quality for models, RAG, agents, or pipelines.

- **Main purpose**: centralize evaluation inputs and outputs so improvements stay measurable across project milestones.
- **Recommendation**: document each evaluation set (what it measures, how it was built, success criteria) and avoid sensitive data; use synthetic or anonymized data when needed.

## In this folder

- `sales_forecast/` (versioned, rewritten by `scripts/train_sales_forecast.py`): the evaluation of the monthly revenue forecast on the 2 test years. It holds `metrics.json` (MSE, PSI, Gini and K2/R² for the model and a baseline, the split, and the data profile), `forecast_test_years.csv` (actual, forecast and 80% range per month) and `forecast_test_years.png` (the chart). The run is deterministic, so a re-run only changes these files when the data or the code changes. See `docs/SALES_FORECAST.md`.
- `weekly_warehouse_client_performance/` (git-ignored, created by the pipeline): one JSON validation snapshot per completed run. Each holds the weeks, events read and excluded by reason, the validation result, totals per week and warehouse, load counts and collection health. There are no per-client figures, which are commercially sensitive. Writing it is non-critical: a failure never fails the run.
- `weekly_warehouse_client_performance/run_log_fallback.jsonl` (git-ignored): the run log's fallback. When `reporting.pipeline_runs` cannot record a failed run - the database is unreachable, or the run failed before its row existed - the failure hook appends one JSON line: start, end, events read, status, error and the reason it was recorded here.

> _Spanish version: [README.es.md](./README.es.md)._
