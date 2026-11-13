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
  - Knowledge assistant (Milestone 7, RAG): the protected `/knowledge` route ("Knowledge assistant" in the nav). `lib/knowledge.ts` posts `{question}` to `POST /knowledge/query` and refuses any reply without a non-empty `answer`, so a failure never looks like an empty answer. `components/knowledge/KnowledgeAssistantPage.tsx` has the question box, the briefing's example questions, and distinct loading, failure and answer states. The route is not in the telemetry section catalogue.
  - Dark mode (since 2026-11-08) for the whole backoffice, following `prefers-color-scheme`, with no toggle. `app/globals.css` does two things in dark mode:
    - gives the backoffice tokens dark values, plus a new `--on-brand` token (text on brand fills, which replaces `text-white` on buttons);
    - re-points the Tailwind palette variables the screens use (slate, red, orange, amber, emerald, blue), so existing classes keep their role with no `dark:` variants.

    `bg-white` became `bg-[color:var(--surface)]`. Every text pair clears WCAG AA in dark mode; the figures are in the CSS comment.
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
- Knowledge assistant / RAG (since 2026-11-08, Milestone 7, `contexts/RAG-briefing.md`; design in `docs/rag/rag-design.md`):
  - Corpus: the four briefing documents, copied verbatim into `docs/company-knowledge-base/` (identical copies in `contexts/`). They place TrackFlow in the US (Los Angeles) and Spain, unlike `contexts/context.md`; answers follow the documents.
  - `data/process/knowledge_base.py` (the one documented I/O exception in `data/process/`, allow-listed in `tests/test_reporting_api.py`):
    - `load_source_documents`;
    - `split_into_chunks`: one Markdown block per chunk, an intro merged with its list, the title prefixed; 14 chunks, 3/5/3/3;
    - `embed`: the embeddings model through the OpenAI SDK, NFC plus whitespace-collapse preprocessing;
    - `setup`: deterministic uuid5 IDs, stale points deleted, the collection recreated when the vector size changes; CLI `npm run rag:setup [-- --dry-run]`;
    - `RagSettings`: `QDRANT_URL`, `QDRANT_API_KEY`, `EMBEDDING_MODEL`, `GENERATION_MODEL` (must differ), `LLM_BASE_URL`, `LLM_API_KEY`, optional `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY`, and `LLM_TIMEOUT_SECONDS` (default 30, per request, 2 retries).
  - Models: the 4Geeks LiteLLM gateway, `LLM_BASE_URL=https://llm.4geeks.ai/v1` (`/v1` is required: without it requests time out). IDs exactly as `/v1/models` lists them, without the `litellm/` prefix (with it: 403). Embeddings `downtown-miami/openrouter/perplexity/pplx-embed-v1-0.6b` (1024 dimensions); generation `downtown-miami/openrouter/openai/gpt-6-luna` (chosen over `deepseek-v4-flash`, which also passes the rules but adds unsourced remarks). The gateway often stalls for 30-120 s, so run the batch CLIs with `LLM_TIMEOUT_SECONDS=120`.
  - `data/pipelines/rag.py`:
    - `retrieve(query, *, k=5, min_score=MIN_SCORE)` returns payload dicts plus `score`, with the threshold applied by Qdrant and again in Python;
    - `build_messages`: the system prompt sets the salesperson voice and the briefing's four business rules, and contains no digits;
    - `generate_answer`: temperature 0, called even with no context;
    - `query()` = `generate_answer(question, retrieve(question))`.
    - `MIN_SCORE = 0.30`, measured and recall-first: paraphrased in-scope questions score as low as 0.3285, while business-sounding out-of-scope ones reach 0.4489, so no value separates the two; the prompt handles what passes.
  - `services/knowledge/`: `POST /knowledge/query` (bearer token) takes `{question}` (1-1000 characters) and returns `{answer}` only. `KnowledgeBaseError` becomes a 503 with a generic message.
  - Eval: `data/eval/test-queries.json` (20 questions across the four documents, 3 about peaks, 5 tagged `paraphrase`, plus 6 out of scope); `scripts/evaluate_rag_retrieval.py` (`npm run rag:eval`) reports Recall@3 on the ranking and with the threshold, plus the threshold evidence (midpoint when separable, recall-first otherwise), and writes `data/eval/rag/retrieval_eval.json`.
  - Qdrant: `qdrant/qdrant:v1.20.0` in Compose (127.0.0.1:6333/6334, `qdrant_data` volume), matching `qdrant-client` 1.20.0; `openai` 3.46.0. Both were added with `uv add`, and `requirements.txt` was re-exported.
  - Tests use `QdrantClient(":memory:")` and fake models (`tests/pipelines/rag_support.py`, whose fixture makes real clients fail): `tests/pipelines/test_rag.py`, `test_knowledge_base.py`, `test_rag_retrieval_eval.py`, `tests/test_knowledge_api.py`, `tests/frontend/backoffice/knowledge.test.ts`.
- Knowledge agent / LangGraph (since 2026-11-11, Milestone 7 Part 1, `lead-instr.png` + `stages-todo.png`; design in `docs/agent/agent-graph.md`):
  - What it is: the same RAG flow as an explicit graph in `services/agent/`. `langgraph` 1.2.20 and `langgraph-checkpoint-sqlite` 3.1.3 were added with `uv add`. `data/pipelines/rag.py` is unchanged and still never imports `services/`.
  - State (`AgentState`): `question`, `error`, `context`, `answer`. There is no conversation history. The outcome (`answered` / `no_information` / `rejected`) is derived from the state.
  - Nodes, each declaring the fields it reads and writes:
    - `receive_question`: strips the question; an empty one sets `error`.
    - `retrieve`: calls `rag.retrieve`.
    - `generate_answer`: calls `rag.generate_answer(question, context)` with retrieve's context and refuses an empty one.
    - `answer_no_information`: fixed text, no model call.
    - `rag.query()` is never called by the graph.
  - Conditional edges with an explicit `path_map`:
    - `route_question`: error → `END`, otherwise retrieve;
    - `route_context`: no chunk ≥ 0.30 → `answer_no_information`, otherwise generate.
    - Each router writes `{edge, decision, to, reason}` through `get_stream_writer()`.
  - `build_agent_graph()` raises `AgentGraphBuildError` before any run. Besides LangGraph's own `compile()` checks, it adds:
    - a contract check (declared fields exist in `AgentState`, no duplicate node or state-field names);
    - a reachability check on the declarations, both from `START` and to `END`. LangGraph 1.2.20 accepts an orphan node, and `get_graph()` draws a dead end as reaching `END`.
    - At run time, a node returning an undeclared field raises `AgentContractError`; LangGraph alone silently drops it.
    - `get_agent_graph()` (lru_cached) is built in the `services/main.py` lifespan, and `close_agent_graph()` runs on shutdown.
  - Checkpoints:
    - `SqliteSaver` after every step, `thread_id = run_id`.
    - `resume_run(run_id)` continues a failed run from the failed node without re-running earlier ones.
    - It refuses `running` runs, unknown runs and runs from another graph version.
  - Trace (`services/agent/tracing.py`, `TraceStore`): tables `agent_runs` and `agent_run_events` in the same SQLite file (`AGENT_STORE_PATH`, default git-ignored `data/agent/agent_runs.sqlite`, Compose `/app/var/agent_runs.sqlite`).
    - The runner streams `tasks` + `custom` + `values` and writes node rows (inputs limited to the declared reads, output or error, timing) and edge rows (decision and reason).
    - Errors keep their class; the message only for `KnowledgeBaseError` / `AgentContractError`.
    - `graph_fingerprint()` (nodes, edges, prompt, threshold, top-k, temperature, fixed texts) is stored on each run.
  - CLI: `npm run agent -- check | ask "<q>" | trace <id>|--last [--json] | runs | resume <id>`.
  - Evals:
    - Cases in `data/eval/agent/eval-cases.json`; recorded once from the real models by `npm run agent:record` (`scripts/record_agent_traces.py`, temp store, writes nothing if a run fails) into the committed `data/eval/agent/traces.json`.
    - `npm run agent:eval` (`tests/pipelines/test_agent_evals.py`) checks routing, the node contract, grounding (fact in the answer and in a retrieved chunk), faithfulness (figures) and freshness (fingerprint), offline.
  - Tests: `tests/pipelines/test_agent_graph.py`, `test_record_agent_traces.py`, `tests/test_agent_api.py`. The `isolated_agent_store` autouse fixture in `tests/conftest.py` pins `AGENT_STORE_PATH` to `tmp_path` and resets the caches.
- Knowledge agent tools (since 2026-11-11, Milestone 7 Part 2; **moved behind MCP on 2026-11-12**, Part 3; design in `docs/agent/agent-graph.md`):
  - What it is: the graph reads live operational data (tickets, stock), not only the knowledge base. Tickets are deliberately not indexed into Qdrant.
  - Graph: `receive_question` -> `plan_sources` -> the planned sources in the fixed order `lookup_ticket_mcp`, `lookup_stock_mcp`, `retrieve` -> `generate_answer` | `answer_lookup_failed` | `answer_no_information`. Routers `route_question`, `route_after_plan`, `route_after_ticket`, `route_after_stock`, `route_after_retrieve`, each recording its decision and reason. The Part 2 nodes `lookup_ticket` / `lookup_stock` were replaced by the MCP client nodes; reads, writes, routers and decisions are unchanged.
  - State adds `plan`, `ticket_lookup`, `stock_lookup` (JSON-mode dicts). `derive_outcome`: `answered` = generated from chunks or a lookup with `outcome == ok`; a fixed answer is `no_information`.
  - `services/agent/planner.py::choose_sources`: the generation model with the three planner tools (`lookup_ticket`, `lookup_stock`, `search_knowledge_base`; schemas from the Pydantic contracts), `tool_choice="required"`, temperature 0. Calls are validated; no usable call, or a model failure, falls back to the knowledge base alone. The planner's tool names are its choices, not MCP tools.
  - `services/agent/tools/`: `contracts.py` (unchanged models; `endpoint` is now e.g. `MCP get_incident(reference=TRF-000482)`), `mcp_lookups.py` (planner request -> MCP read tool -> the same result models), `mcp_client.py` (`langchain-mcp-adapters`: `MultiServerMCPClient.session` + `load_mcp_tools`, allow-list `get_incident` / `search_incidents` / `query_inventory`, `asyncio.run` per call inside the sync node thread, exception groups unwrapped into labels), `oauth.py` (client-credentials token cache, renewed 60 s before expiry, dropped on 401, one retry), `passages.py`. The direct HTTP modules (`incidents.py`, `inventory.py`, `http.py`, `auth.py`) were **deleted**; a test guards that the agent has no other path to the services.
  - Settings: `AGENT_MCP_URL` (default `http://localhost:8100/mcp`, Compose `http://mcp:8100/mcp`), `AGENT_OAUTH_TOKEN_URL`, `AGENT_OAUTH_CLIENT_ID`, `AGENT_OAUTH_CLIENT_SECRET`, `AGENT_OAUTH_AUDIENCE`, `AGENT_OAUTH_SCOPES` (default `incidents:read inventory:read`), `AGENT_TOOL_TIMEOUT_SECONDS` (whole MCP lookup budget, default 5, at most 10). `INCIDENTS_API_URL`, `INVENTORY_API_URL` and `AGENT_INVENTORY_*` are gone. Unset OAuth client -> `unavailable / not_configured` without a request.
  - Failure labels on `unavailable`: `not_configured`, `oauth_*`, `mcp_unreachable`, `mcp_timeout`, `mcp_unauthorized`, `mcp_forbidden`, `mcp_http_<code>`, `mcp_protocol_error`, `tool_missing`, `invalid_response`, or the server's tool error code (`upstream_unavailable`, `insufficient_scope`, ...). The fixed fallback texts in `state.py` are unchanged.
  - Evals: 17 cases; `fallback-incident-service-down` became `fallback-mcp-server-down` (`"services": {"mcp_server": "unreachable"}`). New checks: every lookup is an MCP read tool call, and `test_the_routing_is_unchanged_since_the_direct_tools` against `data/eval/agent/routing-baseline.json` (Part 2 decisions frozen from commit e5fd285). `traces.json` must be re-recorded (live stack) before the evals pass again.
  - Tests: `tests/pipelines/test_agent_mcp_client.py` (replaces `test_agent_tools.py`), `tests/mcps/test_agent_over_mcp.py` (agent and whole graph against the real MCP server under uvicorn on a free port), plus the updated graph/API/recorder/eval suites. The autouse `isolated_agent_tools` fixture forbids `oauth.make_http_client` and sets `mcp_client.http_client_factory` to a forbidding factory unless a test injects one.
- TrackFlow MCP server (since 2026-11-12, the "MCP Server for company tools" ticket; design in `docs/mcp/mcp-server.md`, tool reference `docs/mcp/TOOLS.md`, runbook `docs/mcp/PLAYGROUND.md`):
  - Code in `mcps/trackflow_tools/` (package `mcps` added to the wheel and the Docker image). It never imports `services/` (test); it calls the API over HTTP and shares only `trackflow_shared`.
  - Dependencies (`uv add`): `fastmcp>=3.4.11,<4` (fastmcp 4 needs `mcp>=2.3`, adapters need `mcp<2`), `mcpauth>=0.2.0b1,<0.3` (pre-release; only one with resource-server mode), `langchain-mcp-adapters>=0.3.2` (brings `mcp` 1.30.0).
  - Transport: Streamable HTTP, `stateless_http=True`, own process on :8100, path from `MCP_RESOURCE_URL`. Host/Origin guard on (`allowed_hosts` = resource host + `MCP_ALLOWED_HOSTS`), CORS only with `MCP_CORS_ORIGINS`, `/healthz` public.
  - Auth: MCP Auth resource-server mode (FastMCP auth unused): PRM at `/.well-known/oauth-protected-resource/mcp`; bearer middleware with a custom verify built from MCP Auth's `create_verify_jwt` + one cached `PyJWKClient` + `TokenVerifier.validate_jwt_issuer` (built-in "jwt" mode refetches the JWKS every request); audience `MCP_AUTH_AUDIENCE`. `AuthContextBridge` puts `AuthInfo` on a **new** per-request `scope["state"]` dict (asgi-lifespan shares one dict across requests). Provider: Auth0 (to be configured by the developer).
  - Tools: `create_incident` (`incidents:create`), `update_incident_status` (`incidents:update`, via `PATCH /api/incidents/{id}/status`), `get_incident` / `search_incidents` (`incidents:read`), `query_inventory` (`inventory:read`; operations `list_products` / `get_product` / `list_orders`). Policy table in `permissions.py`, validated at startup (exit 5 on a write inventory tool).
  - FastMCP middleware, outermost first: `AuditMiddleware` (JSONL `MCP_AUDIT_LOG_PATH`, default `logs/mcp_tool_calls.jsonl`), `ErrorShapingMiddleware` (validation -> `invalid_arguments`, unknown -> `unknown_tool`, masked exceptions -> `internal_error`, `ReadOnlyViolation` in the cause chain -> `inventory_read_only`), `ReadOnlyInventoryGuard`, `ScopeGuard`. Tool errors are JSON `{"error", "message", ...}`; codes in `errors.ErrorCode`; CLI exit codes 0-5.
  - Upstream: `IncidentsApi` (no auth), `ReadOnlyInventoryApi` (GET only, `ReadOnlyViolation` otherwise) with the `MCP_INVENTORY_EMAIL` / `_PASSWORD` service account. The client's OAuth token is never forwarded.
  - CLI: `npm run mcp` (serve), `npm run mcp:tools [-- --json | --write docs/mcp/TOOLS.md]`, `npm run mcp:token -- --client agent|tester`. Compose service `mcp` (same image, `127.0.0.1:8100`). `.devcontainer/devcontainer.json` (Python 3.14, uv, Node 24, ports 8000/8100) for the Codespaces + MCP Playground validation.
  - Tests: `tests/mcps/` (128, offline: RSA key as Auth0, real Incidents Manager via `TestClient`, fake inventory via `MockTransport`).
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
- `incidents` keys on a UUID4 `id` like `users` and `profiles`. Rows carry `source_incident_id` - the `incident_id` of the helpdesk CSV row they were seeded from (`TRF-000123`) - which is `None` on anything created through the API and is what makes `scripts/seed_incidents.py` idempotent. Since 2026-11-11 `IncidentResponse` publishes it read-only as `reference` (support staff quote it as "ticket 482"), and `GET /api/incidents?reference=TRF-000123` filters on it exactly. The format and the short-form normalisation (`normalize_reference`: "482" -> "TRF-000482") are one shared rule in `packages/shared/trackflow_shared/incidents.py`.
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
  - Token-protected knowledge assistant: `POST /knowledge/query` (`{question}` -> `{answer}`, the generated text only; 422 for a blank, over-long or extra-field body; 503 when Qdrant or a model is unavailable or not configured).
  - Token-protected knowledge agent (LangGraph, coexists with the above):
    - `POST /agent/query`: `{question}` -> `{run_id, answer}`. 422 `{detail: {message, run_id}}` when the graph refuses an empty question; 422 for an over-long or extra-field body; 503 (retryable node failure) or 500 (other) with the failed step's name and the `run_id`, never exception text.
    - `GET /agent/runs/{run_id}`: the run's trace, for the user who ran it only (404 otherwise).
    - The router only calls `runner.run_agent`.
  - Unauthenticated at this stage: `POST /api/incidents`, `GET /api/incidents` (filters `status`, `origin`, `branch`, `category`, `reference`), `GET /api/incidents/summary`, `GET /api/incidents/{id}`, `PATCH /api/incidents/{id}/status`. Anyone in the company reports incidents, and no incident route exposes the commercially sensitive data that makes `/suppliers` token-only. Revisit when the panel lands.
- The TrackFlow MCP server (:8100, separate process) exposes the incident routes and inventory reads to OAuth-authorized MCP clients: `POST /mcp` (Streamable HTTP, bearer token validated by MCP Auth) and the public `GET /.well-known/oauth-protected-resource/mcp` and `GET /healthz`. See `docs/mcp/mcp-server.md`.
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
