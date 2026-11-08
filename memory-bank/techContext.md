# Technical Context

## Tech Stack (Website)

This repository currently contains two website implementations related to TrackFlow:

- `apps/website`: static marketing pages served by Flask.
- `uis/website`: Next.js app holding the public corporate page plus the session-protected hiring tracker and account views.

### Frontend

- `uis/backoffice`
  - The internal console: supplier directory and the centralized incident manager (`/incidents`, `/incidents/new`), both in the `(protected)` route group. Same Next.js 16.2.7 / React 19 / Tailwind v4 stack as `uis/website`.
  - The incident screens are where the data-viz rules apply: `components/incidents/IncidentSummaryPanel.tsx` picks its form before its colour, and the status tokens in `app/globals.css` carry their measured contrast and separation figures in a comment. See `docs/INCIDENTS.md`.
  - Inventory has four protected, literal `/backoffice/inventory/...` routes: `products`, `orders/inbound`, `orders/outbound`, and read-only `orders`. `lib/inventory.ts` owns every inventory call using the existing authenticated transport; live movement endpoints remain `/inventory/products/inbound` and `/inventory/products/outbound`. The shared form fetches selected outbound stock, ignores stale responses, and preserves API quantity errors during refresh. See `docs/INVENTORY.md`.
  - Leadership dashboard: the protected `/reporting` route ("Weekly report") renders the four BRIEFING KPIs for one ISO week, per warehouse and client, from `GET /reporting/weekly-warehouse-client-performance`, plus freshness notices from `GET /reporting/pipeline-runs/latest`. `lib/reporting.ts` validates both responses and holds the KPI names and plain-language descriptions; `components/reporting/WeeklyPerformanceDashboard.tsx` follows the incident panel's data-viz rules (one hue, table with bars, a full table view). The route is not in the telemetry section catalogue yet.
  - Telemetry capture: `services/telemetry-service.ts` queues events behind `track()` and posts batches to `NEXT_PUBLIC_TELEMETRY_ENDPOINT` (`http://localhost:8000/telemetry/events`) every 10 s or at 20 events, and by `sendBeacon` (`text/plain`) on page hide. It reads only the HTTP status: 2xx delivered, 429/5xx retried, other 4xx dropped. Empty endpoint disables capture (Jest). It can send an in-flight batch again by beacon; the backend's `id = eventId` dedupe absorbs that. See `docs/telemetry/telemetry-plan.md`.
- `uis/website`
  - Next.js `16.2.7` (App Router structure under `app/`)
  - React `19.2.4`
  - TypeScript `^5`
  - Tailwind CSS `^4` via PostCSS plugin (`@tailwindcss/postcss`)
  - `next/font` with Plus Jakarta Sans and Space Grotesk for branding consistency
  - `hooks/useLocationSearch.ts` wraps `useSyncExternalStore` to read the query string. `useSearchParams` would force the calling page behind a Suspense boundary at build time, and React 19 rejects setting state from an effect body, so this is the remaining supported route for a browser-only value.
- `apps/website`
  - HTML + vanilla JavaScript
  - Tailwind via CDN (`https://cdn.tailwindcss.com`)
  - No build step required

### Backend

- FastAPI service mounted from `services/main.py`, the composition root. It owns CORS, the app-wide exception handlers, and mounts one router per domain: `auth`, `users`, `profiles`, `suppliers`, `incidents`, `inventory`, `telemetry`. Run with `npm run api` (`uvicorn services.main:app --reload --port 8000`).
- Domain modules follow the layering in `docs/ARCHITECTURE_PROPOSAL.md`: `models.py` (Pydantic contracts), `service.py` (business rules and persistence), `router.py` (HTTP only).
- `services/core/` holds shared technical concerns and no business rules: `config.py` (pydantic-settings), `db.py` (TinyDB handle), `email.py` (transactional email transport), `errors.py` (domain errors), `http_errors.py` (exception -> response translation), `security.py` (bcrypt + JWT), `supabase_client.py` (one cached Supabase client built from the secret key, 10 s PostgREST timeout).
- Telemetry ingest lives in `services/telemetry/`. `POST /telemetry/events` parses `{"events": [...]}` loosely (1-100 events, 256 KiB), validates each event with the unchanged `TelemetryEvent` model, rejects individually what breaks the contract or that JSONB cannot hold (NaN/Infinity, NUL, a repeated `eventId` in the batch), and stores the rest with one Supabase `upsert(on_conflict="id", ignore_duplicates=True)`. It answers `{received, stored, rejected}` with 200 for any well-formed batch, 413/422 for a malformed envelope, 503 when Supabase fails. A replayed event counts as stored. Logs (`api.telemetry`) carry counts and `field:error_type` reasons, never values.
- Business reporting lives in `services/reporting/` (HTTP only) over the Prefect 3 pipeline in `data/pipelines/` (`prefect` 3.9, a runtime dependency since 2026-10-22). `data/` is a package in the wheel; dependencies run `services/reporting` -> `data/pipelines` -> `data/process`, and nothing in `data/` imports `services/` (`tests/test_reporting_api.py` checks both directions). The entry point is `data/pipelines/pipeline.py`: the flows `weekly_warehouse_client_performance` and `backfill_weekly_warehouse_client_performance`, the `open_run` task, state hooks and the CLI (`--week-start`, `--backfill`, `--serve`). Since 2026-10-25 the main flow invokes one subflow per stage from `data/pipelines/subflows/`: `extract_weekly_warehouse_client_activity`, `compute_weekly_warehouse_client_kpis` (one task per KPI: `compute_inbound_volume`, `compute_outbound_throughput`, `compute_stockout_frequency`, `compute_discrepancy_rate`, plus `prepare_kpi_events` and `assemble_weekly_warehouse_client_rows`), `load_weekly_warehouse_client_performance`, and the optional `measure_kpi_event_collection_health` and `export_weekly_performance_eval_snapshot` (`return_state=True`). Data crosses subflow boundaries in `StageData` containers, which Prefect stores as row counts: a plain text DataFrame parameter would be copied into Prefect's database. Shared retry, timeout and content-cache-key policies are in `subflows/policies.py`. The pure Pandas transforms are `data/process/weekly_rollup.py` (one function per KPI) and `collection_health.py`. Persisted transform results go to `data/raw/prefect-results/` and one validation snapshot per run to `data/eval/weekly_warehouse_client_performance/` (both git-ignored). The design and its departures are in `data/pipelines/PIPELINE_DESIGN.md` (section 10).
- Every run has a record. Failed runs keep `events_read` in `pipeline_runs` (the hook passes it through `PipelineStageError`); a run the database cannot record (outage, or failed before `open_run`) is appended by the failure hook to `data/eval/weekly_warehouse_client_performance/run_log_fallback.jsonl`. `run_log.fail_run` returns `failed` / `unchanged` / `missing` so the hook knows when to fall back. The router holds no week or freshness rules: `queries.check_requested_week` and `queries.get_pipeline_status` do.
- The flow's engine (`data/pipelines/reporting_db.py`) comes from `REPORTING_DATABASE_URL`, then the Prefect Secret block `trackflow-reporting-database-url`, then `DATABASE_URL`; the API passes its own inventory engine instead. The manual trigger calls `run_deployment("weekly-warehouse-client-performance/weekly")` and needs `PREFECT_API_URL`, otherwise 503. `python data/pipelines/pipeline.py --serve` serves that deployment (Monday 06:00 `America/Monterrey`, concurrency 1, `ENQUEUE`). It serves the flow imported from `data.pipelines.pipeline`, never from `__main__`: the runner cloudpickles `__main__` objects by value and every run crashed with a `RecursionError`.
- Scheduled jobs (DEV-53, since 2026-10-25) run outside the API from the OS crontab. They are not in FastAPI: no APScheduler, no lifespan hook.
  - `scripts/nightly_export.py` exports the previous UTC day of `telemetry_events` to `data/raw/telemetry_<date>.csv` (only if absent; atomic `.partial` rename; git-ignored).
  - It then runs `python -m data.pipelines.pipeline --week-start <Monday of the last week closed by the end of the target date>` as a subprocess in its own process group. The timeout is 3 h, `run_log.ABANDON_AFTER`. To stop it, SIGTERM goes to the pipeline alone first, so Prefect's crash hook can still reach its temporary API server; then to the group; then SIGKILL.
  - `TARGET_DATE` (`YYYY-MM-DD`, a day that has ended in UTC) overrides yesterday. Exit 0 completed/duplicate/aborted, 1 failed, 2 configuration.
  - `services/job_runner/` is the status control, taking an engine and no settings. `run_job` sweeps abandoned rows (> 6 h), aborts silently on a pending/processing row, skips a completed `(job_name, target_date)`, then marks pending → processing → completed, or `failed` on any `BaseException` (SIGTERM included). Database errors keep only their class in the row.
  - Log lines: `<UTC ts> <LEVEL> job=... target_date=... status=... message`. The pipeline's stderr is re-logged as `pipeline | ...`.
  - `scripts/cron/nightly_export.sh` (finds the repo, uses `.venv`, appends to git-ignored `logs/nightly_export.log`) and `scripts/cron/nightly_export.crontab` (`30 3 * * *`). Installed in the developer's user crontab on 2026-10-25.
  - See `docs/NIGHTLY_EXPORT.md`.
- Sales forecast (since 2026-11-06; real data 2026-11-07): `scripts/sales_forecast.py` (cleaning, 8/2 calendar-year split, causal features, XGBoost in a sklearn `Pipeline`, MSE / PSI / Gini / K2 = R² metrics) and the CLI `scripts/train_sales_forecast.py`. The CLI reads `data/raw/trackflow_sales.csv` (an identical copy of `contexts/company-sales.csv`) and writes `metrics.json`, `forecast_test_years.csv` and `forecast_test_years.png` to the versioned `data/eval/sales_forecast/`. `scikit-learn`, `xgboost` and `matplotlib` are runtime dependencies. On macOS, XGBoost needs `libomp`. Homebrew is not installed on the developer's Mac, so runs use `DYLD_LIBRARY_PATH=.venv/lib/python3.14/site-packages/sklearn/.dylibs` with `.venv/bin/python`; `uv run` drops `DYLD_*` variables. See `docs/SALES_FORECAST.md`.
- Sales forecast technical evaluation (since 2026-11-08):
  - `scripts/sales_forecast_evaluation.py` provides:
    - time-aware CV, `TimeSeriesSplit(n_splits=5, test_size=6)` over the 60 usable training months;
    - fold chronology asserts and fold-boundary leakage checks;
    - two learning curves;
    - the fit diagnosis rule (`classify_fit`);
    - corrective-action candidates scored on the same folds.
  - The CLI `scripts/evaluate_sales_forecast.py` reads only the training years 2016-2023. It writes `data/eval/evaluation_report.md`, `data/eval/learning_curve.png` and `data/eval/sales_forecast/evaluation.json`. Exit code 3 means a chronology or leakage check failed.
  - `forecast_recursive` takes an optional `feature_columns` (default `FEATURE_COLUMNS`), so candidates with other feature sets can be forecast. Shipped outputs are byte-identical.
  - Result: overfitting. The proposed action (depth 1 + 9 compact features) is not applied yet.
- `packages/shared/trackflow_shared/` is a Python package mapped into the wheel from `pyproject.toml` next to `services` and `scripts`. It carries rules that both the API and the `/scripts` tooling must agree on and that neither owns: the incident enumerations and lifecycle (`incidents.py`) and the helpdesk-CSV validation plus CSV -> model maps (`incidents_csv.py`). It imports no framework.
- Error translation lives in `services/core/http_errors.py`. Domain errors (`ValidationFailed`, `IncidentNotFound`) are translated there, so the service layer stays HTTP-agnostic, and a bare `Exception` handler turns any unhandled failure into a 500 that names nothing about the exception, with the traceback going to the `trackflow.errors` logger. Every incident error body has the same `{detail: {field, message}}` shape. See `docs/INCIDENTS.md`.
- **The 422 -> 400 remapping is scoped to `/api/incidents`, and must stay that way.** Starlette's exception handlers are global, so `make_validation_error_handler(prefixes)` takes the prefix (read off `incidents_router.prefix`) and hands everything else to FastAPI's own handler. Applying it app-wide breaks password recovery: `resetPassword` in `uis/*/services/auth-service.ts` reads **any** 400 as a spent reset token and replaces the form with "request a new link", so a too-short password would tell the user their link had expired. `changePassword` reads a 400 as "current password is wrong". `tests/test_error_contract.py` pins both halves.
- Python Flask app (`services/server.py`) remains a separate lightweight static server:
  - Serves `apps/website/index.html` at `/`
  - Serves static files from `apps/website` and fallback static files from repository root

### Authentication

- Stateless JWT only. There is no session store and no auth cookie.
- `OAuth2PasswordBearer` extracts `Authorization: Bearer <token>`; `python-jose` signs and validates it with `HS256`.
- `services/auth/dependencies.py::get_current_user` is the single gate for protected routes: decode, validate, load the user from TinyDB, 401 on any failure.
- Passwords are hashed with `libpass[bcrypt]` at cost 12. The import path stays `from passlib.hash import bcrypt`.
- Every JWT carries a `typ` claim naming what it may do: `access` for a session, `password_reset` for a reset link. Each decoder accepts only its own kind, so a reset link cannot be replayed as a bearer credential and a session cannot reset a password. Tokens minted before the claim existed are read as access tokens.
- Password recovery (`POST /auth/forgot-password`, `/auth/reset-password`, `/auth/change-password`) is in `services/auth/service.py`. Reset tokens are signed JWTs whose `jti` is registered in a TinyDB `password_resets` table so each one works exactly once; the table holds the `jti` and never the token. Setting a password by either route spends every outstanding reset link for that user. See `docs/PASSWORD-RECOVERY.md`.
- Configuration lives in a git-ignored `.env` (`JWT_SECRET_KEY`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES`, `RESEND_API_KEY`, `EMAIL_FROM`, `FRONTEND_BASE_URL`, `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`), with `.env.example` committed. See `docs/AUTHENTICATION.md`.
- Password recovery screens (`/forgot-password`, `/reset-password`, `/account/change-password`) exist in both Next.js apps. `FRONTEND_BASE_URL` is a single value, so the emailed link points at one of them - `uis/website` on :3000 by default. See `docs/AUTHENTICATION-FRONTEND.md`.
- Frontend side: the token is stored in `localStorage` under `trackflow.access_token` and attached to every protected call. Protected views live in an `app/(protected)/` route group guarded by a client-side `AuthProvider`/`AuthGuard` pair; Next.js middleware is deliberately not used, because it cannot read `localStorage` and there is no auth cookie. A 401 clears the token and redirects to `/login?next=<path>`. See `docs/AUTHENTICATION-FRONTEND.md`.
- The public corporate page at `uis/website/` and the static `apps/website` carry no session logic at all: no token read, no redirect.

### Database

- TinyDB, one JSON file at `data/suppliers.json` (override with `DB_PATH`), with one table per domain: `suppliers`, `users`, `profiles`, `password_resets`, `incidents`.
- `incidents` keys on a UUID4 `id` like `users` and `profiles`. Rows carry an internal `source_incident_id` - the `incident_id` of the helpdesk CSV row they were seeded from - which is excluded from `IncidentResponse` and is `None` on anything created through the API. It is what makes `scripts/seed_incidents.py` idempotent.
- `User` and `Profile` are TinyDB-only and are never mirrored into Supabase/SQLModel. Their `id` is a UUID4 string, not a TinyDB `doc_id`, because future PostgreSQL tables reference it as `user_uuid`.
- Suppliers still key on the TinyDB `doc_id` integer, unchanged from the original implementation.
- Inventory is in Supabase PostgreSQL via the private `DATABASE_URL` setting. `services/inventory/database.py` owns a lazy cached SQLModel/psycopg2 engine and a per-request `get_db` session generator; its identity accessor reuses the existing TinyDB handle. No global SQL session.
- `services/inventory/models.py` holds Product, InboundOrder, and OutboundOrder SQLModel tables; standalone Pydantic contracts are in `schemas.py`, business rules in `service.py`, and the HTTP router in `routers/inventory.py`. Warehouse values are Monterrey/Zaragoza from `contexts/context.md`; the developer explicitly selected that source over the older TypeScript exercise.
- Product stock is never stored: separate inbound/outbound aggregates calculate it per warehouse-scoped Product UUID. `(sku, warehouse)` is unique. All movement writers lock the Product row with `SELECT FOR UPDATE` and validate/insert/commit within READ COMMITTED transactions. Orders use the authenticated TinyDB account UUID without a SQL user foreign key.
- Lifespan verifies SQL connectivity and initializes missing inventory tables with `SQLModel.metadata.create_all`, enabling RLS with no public policies, then disposes the engine on shutdown/failure. This is initialization, not schema migration. The Supabase transaction pooler on port 6543 uses SSL, pre-ping and bounded pooling.
- Offline tests forbid psycopg2 connections and mock SQL startup; PostgreSQL integration tests require explicit `INVENTORY_TEST_DATABASE_URL`, create isolated `inventory_test_*` schemas with schema translation, and clean them up. Actual row-lock contention, constraint enforcement, SQL-backed API behavior, and seed idempotency were verified.
- Telemetry events are in Supabase's `telemetry_events`, created by hand in the dashboard: `id` uuid PK (= `eventId`), `timestamp` timestamptz, `event_type` text, `payload` jsonb (`properties` verbatim), `tags` jsonb (`sessionId`, `userId`, `requestId`, `schemaVersion` plus any of `warehouse`, `branch`, `source_system`, `client_app`, `section` the event carries), `created_at` default `now()`. Indexes `telemetry_events_timestamp_idx` (DESC), `telemetry_events_event_type_idx`, `telemetry_events_tags_gin_idx`.
- `services/telemetry/database.py` mirrors that table and runs at startup over `DATABASE_URL`: `CREATE TABLE/INDEX IF NOT EXISTS` (index names must match Supabase's, since `IF NOT EXISTS` matches by name), RLS on with no policies, and `BEFORE UPDATE OR DELETE` / `BEFORE TRUNCATE` triggers that make the table append-only. A retention purge must disable the trigger deliberately. Rows are written only through the Supabase client, never SQLAlchemy.
- The row contract is stated twice: `services/telemetry/service.py::to_row` (what runs) and `packages/shared/types/telemetry.ts` (`toTelemetryRow`, for TypeScript). `tests/test_telemetry_contract.py` fails if their field lists drift or the TS event type stops using the real `TelemetryEvent` names.
- `tests/test_telemetry_postgres.py` (same opt-in variable, `telemetry_test_*` schemas via `SET LOCAL search_path`, `sslmode` read from the URL) checks the DDL on a fresh database and on the hand-made Supabase shape. Both PostgreSQL suites were run against Supabase on 2026-10-19.
- `products.client_id` (`varchar(64)`, nullable, slug check `ck_product_client_id`) names the brand client that owns a product. `initialize_schema` adds the column and check when missing, because `create_all` never alters a table, and `ProductResponse` does not carry it. Seeded owners: `stride-athletics` (shoes), `coco-beauty` (perfume), `nexa-electronics` (laptop), assigned once by the seed, which refuses to overwrite a different owner. Assigned on Supabase on 2026-10-22.
- The `reporting` schema, created by the pipeline's `open_run` (`data/pipelines/weekly_warehouse_client_performance/schema.py`, idempotent, under an advisory lock):
  - `weekly_warehouse_client_performance`: BRIEFING's table plus warehouse, Monday and count checks, `wwcp_week_start_idx`, a revisions trigger that raises unless `reporting.run_id` is set, and a `TRUNCATE` guard.
  - `pipeline_runs`: the run log, with one `running` row per pipeline through a partial unique index.
  - `weekly_warehouse_client_performance_revisions` (append-only), `pipeline_run_collection_stats` and `pipeline_run_exclusions`.
  - RLS is on everywhere, with no policies. Unique constraints are matched by columns, because a hand-made BRIEFING table has PostgreSQL's truncated name.
  - The load is one transaction: `set_config` run id, `pg_advisory_xact_lock`, stale-snapshot check, upsert that skips unchanged rows, stale-key delete, stats, exclusions, then `completed`. Extraction runs in `READ ONLY` transactions.
- `job_runs` (public, `services/job_runner/models.py`, created by `ensure_schema` before every nightly run, reviewable SQL in `services/job_runner/migrations/001_create_job_runs.sql`):
  - Columns: `id` uuid (Python-generated), `job_name`, `target_date` date NOT NULL, `status` pending/processing/completed/failed, `started_at`, `finished_at`, `error_message`, `created_at default now()`.
  - Checks `job_runs_{status,started,finished,error_message}_ck`.
  - Indexes: `job_runs_job_name_target_date_idx`, and the lock `job_runs_one_active_idx` (unique `job_name` where status in pending/processing).
  - RLS on. Kept separate from `reporting.pipeline_runs`.
  - Created on Supabase on 2026-10-25. `tests/test_job_runner_postgres.py` (same opt-in variable, `job_runner_test_*` schemas) checks the SQL file and `ensure_schema` build the same table, plus a real two-connection race.
- `tests/test_reporting_postgres.py` (same opt-in variable) creates a `source_test_*` schema reached through `SET LOCAL search_path` and a `reporting_test_*` schema reached through `schema_translate_map`. It passed on Supabase on 2026-10-22.
- `scripts/seed_inventory.py` uses the three sample products in `contexts/coding-fundamentals.md`, with approved Los Angeles-to-Monterrey mapping and a separately approved synthetic laptop outbound. Three opening receipts (45/9/120) minus one laptop outbound yield 45/8/120 stock. `npm run seed:inventory -- --user-uuid <active TinyDB UUID>` runs atomically with stable seed IDs, shared `record_order` locking, conflict refusal, and no refill on repeat. Hosted seed and unchanged second run were verified on 2026-10-04.

### APIs / Integrations

- External REST API integration in `uis/website` through `NEXT_PUBLIC_API_URL`.
- Endpoints exposed by the in-repo FastAPI service:
  - Public: `POST /users`, `POST /auth/login`, `POST /auth/token`, `POST /auth/forgot-password`, `POST /auth/reset-password`
  - Token-protected: `GET /users`, `GET /users/{id}`, `PUT /users/{id}`, `DELETE /users/{id}`, `GET /auth/me`, `POST /auth/change-password`, `GET /profiles/me`, `PUT /profiles/me`, and all six `/suppliers` routes
  - Token-protected inventory: `GET /inventory/products`, `POST /inventory/products`, `GET /inventory/products/{id}`, `POST /inventory/products/inbound`, `POST /inventory/products/outbound`, `GET /inventory/orders`. Both GET and write authentication were explicitly confirmed. No new role restrictions.
  - Public on purpose (the plan accepts anonymous pre-login events): `POST /telemetry/events`.
  - Token-protected reporting (per-client volumes are commercially sensitive): `GET /reporting/weekly-warehouse-client-performance?week_start=` (BRIEFING section 6 shape; defaults to the latest `weeks_to` of a completed run; 404 for a week never computed, 422 for a non-Monday or future week), `GET /reporting/pipeline-runs/latest` (adds derived `last_closed_week` and `stale`; hides `flow_run_id`, `triggered_by`, `error_message`), `POST /reporting/pipeline-runs` (202, 409 while a run row is `running`, 503 without Prefect).
  - Unauthenticated at this stage: `POST /api/incidents`, `GET /api/incidents` (filters `status`, `origin`, `branch`, `category`), `GET /api/incidents/summary`, `GET /api/incidents/{id}`, `PATCH /api/incidents/{id}/status`. Anyone in the company reports incidents, and no incident route exposes the commercially sensitive data that makes `/suppliers` token-only. Revisit when the panel lands.
- `uis/backoffice` consumes the `/suppliers` routes with `Authorization: Bearer <token>` on every call.
- Inventory calls in `uis/backoffice` use the same token and 401 handling. Its own error presentation preserves readable API-authored 4xx/5xx messages without changing `friendly-error.ts` for other features. Stock labels use display-only thresholds 0 / 1-9 / 10+; no schema or reorder rules were added.
- Both Next.js apps consume `POST /users`, `POST /auth/login`, `GET /auth/me` and `PUT /profiles/me` for their sign-in, registration and profile views.
- Endpoints consumed by `uis/website` from a separate external API:
  - `GET /records`
  - `GET /records/:id`
  - `POST /records`
  - `PATCH /records/:id`
  - `PUT /records/:id`
  - `GET /records/:id/notes`
  - `POST /records/:id/notes`
  - `DELETE /records/:id/notes/:noteId`
- Supabase REST (PostgREST) through the Python `supabase` client (`supabase>=2.33`), used only to insert telemetry events. `SUPABASE_URL` and `SUPABASE_SECRET_KEY` are required settings; the secret key bypasses RLS and stays server-side. Docker Compose passes both through and needs a real project (see `docs/DOCKER-DEV.md`).
- Transactional email through Resend (`resend>=2.0`), used only to carry the password reset link. `services/core/email.py` picks the backend from configuration: Resend when `RESEND_API_KEY` is set, otherwise a console backend that logs the whole message so the recovery flow is walkable locally without an email account. Message bodies live in `services/auth/emails.py`, which knows nothing about the provider.
- Branding assets and web fonts are loaded from external sources (Google Fonts and social metadata targets).

### Language / Type System

- TypeScript in the Next.js tracker app with `strict: true` enabled in `tsconfig.json`.
- JavaScript (vanilla) for `apps/website/signup.js` form behavior.
- Python for Flask static server (`server.py`).
- `pytest` (dev dependency group) for the backend, run with `npm run test:api`. `tests/conftest.py` gives each test its own TinyDB file by setting `DB_PATH` **and** clearing the `lru_cache` on `services.core.db.get_db` - the redirect alone is not enough, and without the cache clear one test's rows leak into the next and into `data/suppliers.json`.
  - `tests/conftest.py` also pins every setting through an autouse fixture, so the suite needs no `.env` and never uses a live Resend key.
  - `uv run pytest --cov` measures the authentication API and fails under 70% (`[tool.coverage.*]` in `pyproject.toml`). `time-machine` moves the clock for expiry tests.
  - Pipeline tests live in `tests/pipelines/`: `test_pipeline.py` unit-tests each KPI task through `task.fn` with in-memory telemetry rows (no Prefect server, no database; `python -m pytest tests/pipelines/test_pipeline.py`), and `test_pipeline_flow.py` runs the flows and subflows under `prefect_test_harness` with the database mocked. `prefect_support.redirect_cached_results` keeps cached results out of `data/raw/`.
  - See `TESTING.md`.
- Jest in both Next.js apps: `npm test` runs `jest --coverage` from a `jest.config.ts` that Node 24 loads natively through `next/jest`.
  - The test files live in `tests/frontend/`. `shared/` runs against each app's copy of the duplicated auth modules, and `@identity-api-client` maps to each app's identity client.
- The repo still ships no ruff/mypy config; both are run as throwaway tools (`uvx ruff`, `uvx mypy`) at line length 100.

## Architectural Decisions Made

1. Monorepo-style separation by concern.
	- Distinct folders for apps, UIs, shared packages, agents, and skills.
2. Split website strategy.
  - Keep a static marketing site (`apps/website`) separate from the product UI (`uis/website`).
3. Backend decoupling for the tracker.
	- The Next.js tracker delegates data operations to an external API service via environment variable instead of coupling to local API routes.
4. Service-layer API access in the tracker.
	- Dedicated modules (`services/candidates-service.ts`, `services/notes-service.ts`) encapsulate HTTP calls.
5. Payload normalization boundary.
	- Candidate and note payloads are normalized in `lib/` utilities to absorb schema variants (for example `fullName` vs `name`, `status` vs `currentStatus`).
6. Password-recovery forms never ask the API a question they should not.
	- `/forgot-password` renders one confirmation constant for every success, so the registered and unregistered cases are character-identical, and `requestPasswordReset` returns `void` so there is nothing for a caller to inspect.
	- The form locks after a successful submit, and the submit handler returns early once sent, so the disabled attribute is the visible half of the guard rather than the whole of it.
	- Confirmation fields are checked in the browser and never sent; the API has no field to reject them with.
7. Client-rendered interaction model for tracker screens.
	- Main pages use `"use client"` and browser-side state management for filtering, forms, and optimistic-ish refresh behavior.
8. Domain rules that two runtimes share live in `packages/shared/`, not in either.
	- The incident vocabulary and the CSV rules are used by both the API and `scripts/seed_incidents.py`. Keeping them in `services/incidents/` would have made the seed depend on the web layer; duplicating them would have let the two drift the first time a category was added.
9. One gate decides what a user reads about a failure.
	- `uis/backoffice/lib/friendly-error.ts` surfaces an API message only when it arrived in the documented `{field, message}` shape and the status is 400 or 404. Everything else - 5xx, unreadable bodies, network errors, thrown non-Errors - gets frontend-authored text. Without it, `ApiError`'s "Request failed with status 500" fallback and the browser's `TypeError: Failed to fetch` reach the screen.
10. Shared visual identity through design tokens.
	- CSS custom properties and brand fonts in `globals.css` and `layout.tsx` define consistent TrackFlow theming.

## Technical Constraints

1. `NEXT_PUBLIC_API_URL` is mandatory for tracker API communication.
	- Missing configuration causes runtime errors in API client initialization.
	- `uis/website` needs a second variable, `NEXT_PUBLIC_AUTH_API_URL`, because its identity calls go to the in-repo FastAPI service while `NEXT_PUBLIC_API_URL` points at the external records API. It defaults to `http://localhost:8000`.
2. External API contract dependency.
	- UI behavior depends on `/records` and `/notes` endpoints and their HTTP semantics (status codes, JSON content types).
3. No in-repo backend/data source for the tracker.
	- Local development of full tracker workflows requires a separately running API service.
4. Mixed frontend paradigms increase maintenance overhead.
	- Static HTML/JS pages and Next.js/TypeScript app coexist with different tooling and conventions.
5. Schema inconsistency handling is required.
	- Multiple possible field names for candidate data imply unstable upstream payload shapes.
6. Limited server-side rendering usage for data-heavy tracker pages.
	- Current client-side fetching model can impact first-load latency and SEO relevance for authenticated app content.
7. Password reset email delivery depends on external configuration.
	- With `RESEND_API_KEY` unset the API still works, but no mail leaves the machine: the reset link is only logged. This is deliberate for development and wrong for anything else.
	- Resend's shared sender `onboarding@resend.dev` needs no DNS setup but in test mode only delivers to the address owning the Resend account. Mailing real recipients requires verifying a domain.
	- `FRONTEND_BASE_URL` must point at whichever Next.js app serves `/reset-password`, or the emailed link goes nowhere.
8. Root Flask dependency version appears non-standard.
	- Root `package.json` declares `flask` under npm dependencies, while runtime uses Python `flask` in `server.py`; setup consistency depends on developers understanding this split.
