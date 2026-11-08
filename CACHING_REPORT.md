# Caching and Performance Report

Date: 2026-10-13

## Summary

Request timing and a repeatable scratch-database benchmark were added before response caching. The strongest cache candidates were the incident list and summary: both scan and validate every TinyDB incident on each miss, and the same response can be reused across callers. At 5,000 incidents, warm medians fell from 53.494 ms to 0.661 ms for the full list and from 30.504 ms to 0.483 ms for summary. At the representative 95-row scale, both uncached medians were already below 1.5 ms, so the benefit there is small.

The implementation uses a process-local cache with a 15-second TTL, at most eight entries, and an 8 MiB aggregate JSON-payload limit. Oversized responses are returned but not cached. Successful incident creation and status changes invalidate every local incident read entry. A generation guard prevents an in-flight pre-write read from repopulating stale data after invalidation.

## Measurement Method

`services/main.py` logs each request through logger `api.timing`: method, route template, status, and elapsed milliseconds. It does not include query values, raw IDs, or credentials. The middleware duration ends when `call_next` returns the response headers; it is not a full ASGI response-body-send measurement. Translated error responses preserve their existing status/body contracts. Escaped exceptions are timed as 500s.

The repeatable benchmark in `scripts/benchmark_caching.py` uses FastAPI `TestClient` in-process, with PostgreSQL connection and schema hooks stubbed. It overrides configuration to development-only dummy settings and uses only the provided scratch TinyDB file. Supplier reads authenticate with a synthetic account. Each route has three warmups and 30 measured sequential requests; cold timing is the first request after clearing the incident cache. Reported percentiles use nearest-rank p95. Response bytes are serialized body size. These figures include TestClient overhead and are not wire-level production latency.

The request count of 30 per endpoint is a controlled repeated workload, not production traffic. There is no production request-frequency telemetry in this repository; actual route frequency and cross-worker cache-hit rate remain unmeasured.

## Scale Dataset

Both files were new temporary TinyDB databases under `/tmp`; no default database, user data, hosted service, or PostgreSQL instance was modified.

| Dataset | Incidents before/after seeding | Suppliers before/after seeding |
| --- | ---: | ---: |
| Representative synthetic fixture | 0 / 95 | 0 / 15 |
| Scale synthetic fixture | 0 / 5,000 | 0 / 5,000 |

The 95/15 fixture sizes reflect the repository's valid historical incident count and supplier seed count, but the benchmark contents themselves are synthetic. Incident titles, descriptions, timestamps, statuses, categories, origins, and branches vary and validate against the existing model. Supplier names, categories, countries, statuses, service zones, timestamps, and `rate_per_shipment` values vary; country/currency pairs are validated. TinyDB has no relational foreign keys for either domain. Inventory is SQL-backed and has product foreign keys, but it was intentionally not bulk-seeded because the approved scope is TinyDB-only.

Reproduce with a new scratch directory:

```sh
scratch_dir=$(mktemp -d /tmp/trackflow-caching.XXXXXX)
uv run python -m scripts.seed_performance_data --db-path "$scratch_dir/representative.json" --incidents 95 --suppliers 15
uv run python -m scripts.benchmark_caching --db-path "$scratch_dir/representative.json" --requests 30 --warmups 3
uv run python -m scripts.seed_performance_data --db-path "$scratch_dir/scale.json" --incidents 5000 --suppliers 5000
uv run python -m scripts.benchmark_caching --db-path "$scratch_dir/scale.json" --requests 30 --warmups 3
```

The seeder refuses the default DB path and any existing file; it is repeatable across fresh scratch files, not an in-place rerun/update command. Tests verify schema-valid variation, country/currency consistency, deterministic generated IDs, and refusal to overwrite. Remove the scratch directory after use.

## Before and After Results

All timings are milliseconds. The uncached and cached samples used the same generated file for each row count. Variance is expected in this local in-process benchmark; these are directional lab measurements, not an SLA.

| Route | Rows | Uncached median / p95 | Cached miss | Cached warm median / p95 | Response bytes | Retained cache bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `GET /api/incidents` | 95 | 1.415 / 1.929 | 6.800 | 0.542 / 1.002 | 36,645 | 36,645 |
| `GET /api/incidents?status=open` | 95 | 1.102 / 1.644 | 1.202 | 0.561 / 0.838 | 9,515 | 9,515 |
| `GET /api/incidents/summary` | 95 | 0.974 / 1.364 | 1.331 | 0.483 / 0.873 | 438 | 438 |
| `GET /suppliers` | 15 | 1.462 / 1.991 | 2.381 | 1.495 / 2.096 | 3,508 | 0 |
| `GET /api/incidents` | 5,000 | 53.494 / 77.174 | 58.558 | 0.661 / 1.081 | 1,929,874 | 1,929,874 |
| `GET /api/incidents?status=open` | 5,000 | 35.442 / 60.644 | 59.931 | 0.561 / 1.038 | 477,210 | 477,210 |
| `GET /api/incidents/summary` | 5,000 | 30.504 / 54.809 | 29.774 | 0.483 / 0.767 | 470 | 470 |
| `GET /suppliers` | 5,000 | 80.659 / 106.780 | 79.425 | 80.468 / 107.234 | 1,184,017 | 0 |

At 5,000 rows, warm incident list and summary medians improved by approximately 98.8% and 98.4%, respectively. Cold misses remain comparable to the uncached full scan. Supplier timings are essentially unchanged because that endpoint is deliberately not cached. At the measured maximum incident list size, one entry retained about 1.93 MiB of JSON; the aggregate payload cap permits at most about four entries of that size (actual Python object overhead is additional). The eight-entry limit also bounds small filtered and summary keys.

## Endpoint Assessment

Frequency values below are unknown in production. The controlled benchmark invoked the selected reads 30 times per route; it does not establish real-world request volume. Cost observations are from code inspection unless a route appears in the measured table above.

| Operation | Cost / computation | Freshness and frequency evidence | Decision |
| --- | --- | --- | --- |
| `POST /auth/login` | Password verification (bcrypt) and user lookup | Credentials change; login frequency not measured | Never cache; credential verification must run per request |
| `POST /auth/token` | Same password verification as login | Credentials change; frequency not measured | Never cache |
| `GET /auth/me` | User and profile lookups | Caller-specific; profile can change | Never share-cache; low-cost lookup |
| `POST /auth/forgot-password` | User lookup and queued email side effect | State-changing/security-sensitive | Never cache |
| `POST /auth/reset-password` | Token validation, reset-registry access, password hashing | One-time mutation | Never cache |
| `POST /auth/change-password` | Password verification/hash and credential mutation | State-changing | Never cache |
| `POST /users` | Hashing plus user/profile writes | State-changing | Never cache |
| `GET /users` | TinyDB full scan and model validation | Authenticated and exposes account data; frequency unknown | Do not cache sensitive list |
| `GET /users/{user_id}` | TinyDB lookup | Account-specific; frequency unknown | Do not share-cache |
| `PUT /users/{user_id}` | Account mutation, possible password hashing/reset invalidation | Fresh credentials required | Never cache |
| `DELETE /users/{user_id}` | User/profile deletion | State-changing | Never cache |
| `GET /profiles/me` | TinyDB lookup | Per-user personal data, can change | Never share-cache |
| `PUT /profiles/me` | Profile mutation | State-changing | Never cache |
| `GET /suppliers` | Full TinyDB scan, Python filters, response validation; measured 80.659 ms median at 5,000 | Rates are commercially sensitive and updates are allowed; traffic frequency unknown | Candidate by compute cost, but not cached until freshness/access policy and invalidation are separately established |
| `POST /suppliers` | TinyDB insert and validation | State-changing | Never cache |
| `GET /suppliers/{supplier_id}` | TinyDB document lookup plus auth | Individual sensitive supplier record | No cache; lookup is not a full scan |
| `PATCH /suppliers/{supplier_id}/rate` | Rate update | Financial value changes | Never cache |
| `PATCH /suppliers/{supplier_id}/status` | Status update | Operational state changes | Never cache |
| `DELETE /suppliers/{supplier_id}` | TinyDB delete | State-changing | Never cache |
| `GET /api/incidents` | Full TinyDB read/model validation, Python filters and sort; measured | Repeated identical filter keys can reuse response; writes invalidate; TTL bounds external changes | Cache, with all four normalized filters in key |
| `POST /api/incidents` | Validates and inserts one row | State-changing | Never cache; invalidates incident reads |
| `GET /api/incidents/summary` | Full TinyDB read/model validation and aggregation; measured | Same summary is shared; create/status writes invalidate | Cache |
| `GET /api/incidents/{incident_id}` | TinyDB document lookup | Individual mutable record; frequency unknown | No cache; low-cost lookup |
| `PATCH /api/incidents/{incident_id}/status` | Read, lifecycle validation, and update | Status changes immediately | Never cache; invalidates incident reads |
| `GET /inventory/products` | SQL joins and inbound/outbound stock aggregates | Stock changes with every movement | No cache; current stock freshness takes priority; SQL scale not measured here |
| `POST /inventory/products` | SQL insert and uniqueness validation | State-changing | Never cache |
| `GET /inventory/products/{id}` | SQL stock aggregates for one product | Stock changes with movements | No cache; freshness takes priority |
| `POST /inventory/products/inbound` | Locked transactional SQL write | State-changing | Never cache |
| `POST /inventory/products/outbound` | Locked stock checks and transactional SQL write | State-changing | Never cache |
| `GET /inventory/orders` | Union of inbound/outbound history, product join, and sort | History changes on every movement; frequency and scale unknown | No cache pending scale evidence and freshness policy |

## Cache and Freshness Policy

- Keys are process-local tuples. List keys contain `status`, `origin`, `branch`, and `category`; the summary has a separate key.
- TTL is 15 seconds. Successful create/status writes clear list and summary entries. Generation checks stop a read started before a write from storing its older result afterward.
- At most eight LRU entries and 8 MiB of JSON payload are retained. A single payload above 8 MiB is not cached. This byte count excludes Python/container overhead.
- The cache is not distributed. In a multi-worker deployment, a write in one worker cannot invalidate another worker's entries; those can remain stale for up to 15 seconds. Use shared invalidation/cache before promising cross-worker read-after-write consistency.
- The cache is intentionally not user-scoped because incident routes are currently unauthenticated and their response does not vary by caller. If authorization becomes scoped, cache keys must include the access scope or this cache must be removed.
- No Redis was introduced. No supplier, identity, or inventory response is cached.

## Frontend Findings

The candidate page already memoizes normalization, status/stage option construction, and filtering by their dependencies. No additional `useMemo` was added: the available audit contains no React Profiler evidence of expensive repeated renders, and premature memoization would add complexity without measured benefit. React DevTools Profiler was not available through the current automated test environment, so render-time improvement is unclaimed.

Two user-triggered code paths are now dynamically imported with `next/dynamic`: the extracted candidate registration modal and the supplier registration form. Both appear as separate module-specific chunks in generated dev and production output. Browser Network confirmation that each request begins only after the click could not be completed: the integrated browser's candidate click never mounted the dialog, and the backoffice auth mock did not match its configured API origin. No real credentials or API data were used. Existing frontend typechecks, lint, Jest suites, and production builds pass; bundle-byte deltas and React Profiler recordings have not been captured.

## Validation

- Timing middleware and existing error contracts: `uv run pytest tests/test_timing_middleware.py tests/test_error_contract.py -q` (16 passed, 7 skipped).
- Seeder: `uv run pytest tests/test_seed_performance_data.py -q` (5 passed).
- Cache and incident API: `uv run pytest tests/test_incident_read_cache.py tests/test_incidents_api.py -q` (56 passed after byte-limit coverage).
- Website: TypeScript check passed; lint passed; Jest passed (170 tests).
- Backoffice: TypeScript check passed; lint passed; Jest passed (216 tests).
- Full backend suite passed (452 passed, 11 skipped); both production builds passed. Browser Network timing and React Profiler evidence remain unverified for the reasons above.
