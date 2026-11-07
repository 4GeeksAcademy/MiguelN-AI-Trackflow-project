# `data/raw` folder

This folder is intended for **raw data** related to the company: dumps, exports, sample files, event samples, or untransformed datasets.

- **Main purpose**: serve as a landing zone or reference for original data before pipelines process it.
- **Recommendation**: document each dataset’s origin, format, expected size, privacy/PII considerations, and how it is versioned (ideally avoiding sensitive data in the repository).

## In this folder

- `incidents-trackflow.csv`: the helpdesk export that `scripts/seed_incidents.py` seeds from.
- `trackflow_sales.csv`: 120 months (2016-01 to 2025-12) of consolidated revenue and shipments, the input of `scripts/train_sales_forecast.py`. It is an identical copy of the delivered `contexts/company-sales.csv`, at the path the sales briefing names. These are company aggregates with no personal data. See `docs/SALES_FORECAST.md`.
- `prefect-results/` (git-ignored, created by the pipeline): Prefect's persisted results of the cached transform tasks (the four KPI tasks, the row assembly and collection health), the intermediate files behind the 1-hour cache. They hold counts per warehouse, client and week and collection stats, never event rows, event ids, payloads or user ids, and can be deleted at any time.
- `telemetry_<YYYY-MM-DD>.csv` (git-ignored, written by `scripts/nightly_export.py`): one UTC day of `telemetry_events`, exported once as a backup and audit snapshot, with the table's columns (`id,timestamp,event_type,payload,tags,created_at`). The pipeline reads the database, not these files. They carry pseudonymous user and session ids, which is why they stay out of git. See `docs/NIGHTLY_EXPORT.md`.

> _Spanish version: [README.es.md](./README.es.md)._
