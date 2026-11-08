# `scripts` folder

This folder contains **helper scripts** for the monorepo: development automation, maintenance utilities, repetitive tasks (setup, lint, migrations, data generation, etc.), and internal tooling.

- **Main purpose**: group support tools that do not belong to a specific app, agent, or pipeline but make the team’s work easier.
- **Recommendation**: document each script (what it does, parameters, requirements, usage examples) and keep them reproducible (and safe) across environments.

## Scheduled jobs

- `nightly_export.py`: the nightly telemetry job (ticket DEV-53). It exports the previous UTC day of `telemetry_events` to `data/raw/telemetry_<date>.csv` (only if that file is absent), runs the business performance pipeline for the last closed week, and records the execution in `job_runs` through `services/job_runner`. One execution at a time, one completed run per day. `python scripts/nightly_export.py`, or `TARGET_DATE=YYYY-MM-DD` for another day that has ended.
- `cron/nightly_export.sh` and `cron/nightly_export.crontab`: the OS crontab entry (03:30 daily) and the wrapper it calls, which logs to `logs/nightly_export.log`.

Design, scheduling decision and operations: [`docs/NIGHTLY_EXPORT.md`](../docs/NIGHTLY_EXPORT.md).

> _Spanish version: [README.es.md](./README.es.md)._
