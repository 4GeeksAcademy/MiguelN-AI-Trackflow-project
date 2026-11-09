# Development Progress

Last updated: 2026-11-08

## Update 2026-11-08 (night) - Milestone 7: first real run (Docker Qdrant + 4Geeks models)

Goals accomplished:
- Docker Desktop installed by the developer. Qdrant runs from Compose, is healthy, and is reached from `.venv`. The `docker` CLI is not on the shell PATH yet; it lives at `/Applications/Docker.app/Contents/Resources/bin`.
- 4Geeks models configured (developer-owned `.env`) and checked against the gateway:
  - base URL `https://llm.4geeks.ai/v1` (`/v1` required);
  - IDs without the `litellm/` prefix;
  - embeddings `pplx-embed-v1-0.6b` (1024 dimensions);
  - generation `gpt-6-luna`, chosen over `deepseek-v4-flash` after both answered the same 9 questions with the same context. Both follow every briefing rule; DeepSeek adds unsourced remarks.
- `npm run rag:setup` indexed 14 chunks (3/5/3/3) into `trackflow_knowledge`.
- Threshold measured:
  - On the original 15 questions (document wording) the score groups separated, and 0.48 was suggested.
  - Real questions at 0.48 lost the "3 to 5 business days" chunk (0.444) and the Carlos Vega chunk (0.329). Both chat models then answered the delivery-times question wrongly.
  - Added 5 `paraphrase` questions and 2 business-sounding out-of-scope ones. The groups now overlap (lowest correct 0.3285, highest out-of-scope 0.4489).
  - `suggest_threshold` now gives a recall-first value when they overlap. **`MIN_SCORE = 0.30`.**
  - Recall@3 = **100%** (20/20), on the ranking and with the threshold; 17 of 20 at rank 1.
- Prompt: the `Sources:` line is left out when no passage was used (models were writing lines like "Sources: No relevant passage provided").
- `LLM_TIMEOUT_SECONDS` setting (default 30, max 600). The gateway stalls for 30-120 s at times; the batch CLIs need `LLM_TIMEOUT_SECONDS=120`. Passed through Compose; documented in `.env.example`.
- `docs/rag/rag-design.md`: the *Pending* sections are filled (models, dimension, threshold evidence, results, model choice).
- Tests: backend 980 passed, 51 skipped (+8: timeout setting, recall-first suggestion). Ruff and mypy are clean.

Still missing / next goals:
1. The developer must **save** the corrected `.env` lines. The runs above passed `EMBEDDING_MODEL`, `GENERATION_MODEL` and `LLM_BASE_URL` as environment overrides, because the file on disk still held `litellm/...`, a placeholder and no `/v1`.
2. The root `.env` lacks the Compose variables (`APP_ENV`, `COMPOSE_DATABASE_URL`, `POSTGRES_*`, `NEXT_PUBLIC_API_URL`), so the full `docker compose up` does not start. Qdrant alone was started with `--env-file docker-compose.env.example`.
3. Try the backoffice page end to end (`npm run api` + `npm run dev:backoffice`) once `.env` is saved.
4. Commit when asked (AGENTS.md workflow), including `data/eval/rag/retrieval_eval.json`.
5. Still open: the geography mismatch (documents: Los Angeles; `context.md`: Monterrey).

## Update 2026-11-08 (later) - Milestone 7: RAG knowledge assistant, Phases 1-6 + backoffice dark mode

Goals accomplished. The CTO's ticket is `lead-instr.png`, with its phases in `stage-todo-1.png` and `stages-todo-2.png`. The brief is `contexts/RAG-briefing.md` (= the ticket's "company-BRIEFING.md"): Miguel Torres's commercial team asks policy questions in natural language and gets a generated, sourced answer in a salesperson's voice.
- Preparation:
  - Qdrant `v1.20.0` added to `docker-compose.yml` (localhost-only ports, `qdrant_data` volume, `QDRANT_URL` via `COMPOSE_QDRANT_URL`).
  - `uv add qdrant-client` (1.20.0) and `uv add openai` (3.46.0, for the 4Geeks models' OpenAI-compatible API); `requirements.txt` re-exported (the Docker image installs from it).
  - The four briefing documents were copied verbatim into `docs/company-knowledge-base/`; the user added them to `contexts/`.
- Phase 1 (`data/process/knowledge_base.py`):
  - `load_source_documents`;
  - `split_into_chunks`: Markdown blocks, never cut mid-rule, intros merged with their lists, the title prefixed; 14 chunks, 3/5/3/3;
  - `embed`: the dedicated embeddings model, NFC + whitespace normalisation;
  - `setup()` into `trackflow_knowledge`, idempotent: uuid5 IDs, stale points deleted, the collection recreated on a vector-size change;
  - `RagSettings` refuses the same model ID for embeddings and generation;
  - CLI `npm run rag:setup [-- --dry-run]`.
- Phase 2 (`data/pipelines/rag.py`):
  - `retrieve` (k=5, `MIN_SCORE` applied twice, plain dicts);
  - `build_messages`: salesperson voice and the briefing's four business rules; no digits in the system prompt, so every figure comes from the chunks;
  - `generate_answer`: temperature 0, called even with no context;
  - `query()` = `generate_answer(question, retrieve(question))`.
- Phase 3 (`services/knowledge/`): `POST /knowledge/query` with a bearer token, `{question}` -> `{answer}` only, 503 on knowledge-base failures; the router imports only `query()`.
- Phase 4 (`uis/backoffice`, `/knowledge`, "Knowledge assistant"):
  - question box, example questions, distinct loading, failure and answer states;
  - an empty or unreadable reply is an error;
  - **dark mode for the whole backoffice**, chosen by the user over page-only: `globals.css` dark tokens plus Tailwind palette re-pointing, a new `--on-brand` token, `bg-white` -> `--surface`, all text pairs WCAG AA.
  - Verified in Chrome (playwright-core from the scratchpad, API mocked) in light and dark: blank question blocked, loading, answer with sources, 503, empty answer. Reporting, incidents and login checked in dark mode.
- Phase 5 tests:
  - `tests/pipelines/test_rag.py` (22, with `retrieve` on in-memory Qdrant and a threshold-ignoring stub, and `query` with retrieval and the LLM mocked);
  - `test_knowledge_base.py` (32), `test_rag_retrieval_eval.py` (12), `tests/test_knowledge_api.py` (10);
  - Jest `knowledge.test.ts` (11).
- Phase 5b: `data/eval/test-queries.json` (15 questions across the four documents, 2 about peaks, 4 out of scope) and `scripts/evaluate_rag_retrieval.py` (`npm run rag:eval`): Recall@3 on the ranking and with the threshold, plus the threshold evidence.
- Phase 6: `docs/rag/rag-design.md` (flow, chunking, embedding practices, threshold method, prompt rules, API, UI, idempotency, eval, runbook, limitations).
- The existing guard `test_pure_transforms_do_no_io` failed on `knowledge_base.py`. On the user's decision, `knowledge_base.py` is allow-listed as the one documented I/O module in `data/process/`; the guard still covers every other module.
- Verification:
  - backend 972 passed, 51 skipped (896 + 76 new);
  - backoffice Jest 13 suites, 291 tests;
  - `tsc` and ESLint clean;
  - `ruff format`, `ruff check` (with the picked-up config and `--isolated`) and mypy on the new modules clean;
  - both CLIs exit 2 with a clear message when no model is configured.

Still missing / next goals:
1. **The first real run**, now blocked only on **the 4Geeks model details** in `.env`: `EMBEDDING_MODEL`, `GENERATION_MODEL`, `LLM_BASE_URL`, `LLM_API_KEY`. Then run `npm run rag:setup` and `npm run rag:eval`.
   - Done 2026-11-08: Docker Desktop is installed (engine 29.9.2, Compose v5.6.1). `docker compose up -d qdrant` started `qdrant/qdrant:v1.20.0`; the compose healthcheck reports healthy, the dashboard answers on `localhost:6333`, and `get_qdrant_client()` from `.venv` connects (server 1.20.0, no collections yet). Prep step 1 (Qdrant connectivity) is confirmed.
   - The root `.env` currently fails Compose's parser (a stray character on its last line), so the full stack (`docker compose up`) cannot start until the developer fixes that line. Qdrant was started with `--env-file docker-compose.env.example`, without reading `.env`.
2. Set `MIN_SCORE` (provisional 0.30) from the eval's threshold window. Record the model IDs, vector dimension, Recall@3 and threshold in `docs/rag/rag-design.md` (marked *Pending*), and commit `data/eval/rag/retrieval_eval.json`.
3. Check real answers against the briefing constraints: Black Friday, international returns, storage discounts, carrier exceptions, and an out-of-scope question.
4. Optional: Spanish questions in the eval set; `/knowledge` and `/reporting` in the telemetry section catalogue.
5. The geography mismatch (KB documents: Los Angeles; `contexts/context.md`: Monterrey) is still open with the developer.
6. Nothing is committed yet. Commit only on the developer's request, after the AGENTS.md pre-commit workflow.

## Update 2026-11-08 - Sales forecast: formal technical evaluation (fit diagnosis)

Goals accomplished. This is the tech lead's ticket in `lead-instr.png`, worked through the five phases of `stages-todo.png`. The ticket asks whether the forecast is under-fitted, over-fitted or well fitted, how stable it is, and what the specific corrective action is. The report is `data/eval/evaluation_report.md`, summarised in `docs/SALES_FORECAST.md → Technical evaluation`.
- Before starting:
  - The model reproduces exactly: a re-run gives byte-identical `metrics.json`, CSV and PNG, and its 23 tests pass.
  - No new dependency was needed. Everything used is already in `pyproject.toml`, and any future addition goes through `uv add` only.
- Phase 1, time-aware cross-validation (`scripts/sales_forecast_evaluation.py`):
  - sklearn `TimeSeriesSplit(n_splits=5, test_size=6)` with expanding windows over the 60 usable training months (2019-01..2023-12, after the 36-month warm-up). Fold 1 trains on 30 months; the default sizing would have left it 10.
  - `assert_chronological` refuses mixed, shuffled, gapped or overlapping folds.
  - `check_fold_boundary_leakage` runs three tamper checks per fold. All pass on the real data.
  - Metrics are reported as mean ± std (ddof=1).
  - Validation is one month ahead, the same task as the training fit; the recursive error is reported next to it.
- Phase 2, learning curves (`data/eval/learning_curve.png`):
  - the same 5 CV windows with growing `max_train_size` (12 to 30 months);
  - validation on 2023 with 12 to 48 training months.
- Phase 3, metrics: MAE and RMSE for training and validation, in EUR and as % of mean monthly revenue.
  - **Business cost:** under-forecasting costs more. Revenue tracks shipment volume, so a miss is a workload miss for warehouse staff and carrier capacity, and peak shortfalls cost more than proportionally.
  - RMSE is therefore the decision metric and MAE the plain-language one. The signed bias is added because both metrics ignore direction.
- Phase 4, diagnosis (`classify_fit`): **overfitting**.
  - Validation RMSE is 4.51 ± 0.97% against 1.34 ± 0.19% in training (gap 3.37×). The learning-curve gap at 48 months is 3.38×, and validation was still falling (−7.5% in the last step).
  - It is not underfitting: the model beats a seasonal-naive rule in 4 of 5 folds.
  - **Root cause:** too much capacity for 30-60 rows. That is 17 features and depth-2 interactions, while more history does not exist.
  - **Corrective action, proposed and not applied (developer's decision):** depth-1 trees + 9 compact features, chosen in CV over 3 alternatives. Validation RMSE 4.25 ± 0.44%: −6% on the mean, −55% on the fold spread, gap 1.46×.
- Phase 5, tests: `tests/pipelines/test_sales_forecast_evaluation.py` (20 tests).
  - The required chronological-order test.
  - Refusal of broken folds.
  - Leakage across fold boundaries, with a negative control: a centred window must be flagged.
  - Learning-curve windows.
  - MAE and RMSE mean ± std.
  - The diagnosis rule and the selection rule.
  - Test years ×3 leave the evaluation identical.
  - Exit code 3 on a failed check.
- CLI `scripts/evaluate_sales_forecast.py`:
  - writes the report, chart and `data/eval/sales_forecast/evaluation.json`;
  - deterministic (two runs give identical files) and takes about 7 s;
  - exit codes 0 / 2 / 3.
- `scripts/sales_forecast.py`: `forecast_recursive` gets an optional `feature_columns` (default unchanged). The shipped outputs are byte-identical after the change.
- Docs: `docs/SALES_FORECAST.md` (section + pieces table), `data/eval/README.md`, `memory-bank/techContext.md`.
- Verification:
  - Full backend suite: 896 passed, 51 skipped (876 + 20 new).
  - `ruff format`: clean.
  - `ruff check`: clean apart from the E402 `sys.path` bootstrap shared with `train_sales_forecast.py`.
  - mypy on the new files: clean with and without `pandas-stubs`. The 24 pre-existing stub errors in the 2026-11-06 files are unchanged.

Still missing / next goals:
1. Tech lead's decision on the corrective action. If approved, apply depth 1 + the 9 compact features in `scripts/sales_forecast.py`, re-run `train_sales_forecast.py` **once**, and compare the 2024-2025 metrics (the test years were never used to choose it).
2. The peak is flattened: 5 of 6 Nov-Dec validation months are under-forecast, which is the expensive direction, and depth 1 + compact does not change it. An asymmetric loss (`reg:quantileerror` above the median) is the option to weigh with Thomas, a trade-off against RMSE.
3. The growth-year bias flips sign with the briefing's alternating 9%/3% growth: an over-forecast in low years, an under-forecast in high years. A growth-regime feature or external drivers would address it.
4. Still open from 2026-11-07: the developer's decision on the 24 `pandas-stubs` mypy errors, a permanent libomp install, and forecasting 2026 onwards.
5. Nothing is committed yet. Commit only on the developer's request.

## Update 2026-11-07 - Sales forecast: real-data run on the company dataset

Goals accomplished (finishes the 2026-11-06 work against `stages-todo.png`, now with the real `contexts/company-sales.csv`, which the previous session could not read):
- Data:
  - The dataset is at `data/raw/trackflow_sales.csv`, the path the briefing names. It is an identical copy of `contexts/company-sales.csv`, and the CLI's default input.
  - Verified against the briefing: 120 `consolidated` rows from 2016-01 to 2025-12, no blanks, gaps or duplicates (the cleaning report is all zeros), and every data-profile check passes.
  - Measured patterns: yearly growth alternates high/low (9.6, 1.8, 9.4, 2.9, 8.0, 2.8, 10.1, 4.0, 7.9%), Nov-Dec +25/+24%, February −15.9%.
- Results on the 2024-2025 test years (forecast recursively from the end of 2023):

  | | XGBoost | Seasonal-naive baseline |
  |---|---|---|
  | MSE | 2.10e9 EUR² | 1.16e10 EUR² |
  | RMSE, % of the mean month | 3.53% | 8.31% |
  | MAPE | 2.75% | 7.33% |
  | K2 = R² | 0.928 | 0.602 |
  | Gini | 0.931 | 0.921 |

  - 23 of 24 months fall inside the 80% range (±7.0% in year 1, ±7.5% in year 2). The exception is 2025-08, 0.87% under the lower edge.
  - Parameters chosen: depth 2, 200 trees, learning rate 0.03.
  - Runs are deterministic: two runs give identical outputs.
- PSI noise check: `psi_shift_p_value` re-splits the pooled values at random (2000 permutations) to show how large PSI gets by chance.
  - The seasonal-pattern PSI of 0.156 is labelled "moderate shift" by the rule of thumb, but p = 0.66, so it is within the noise of 24 test months, not a real shift.
  - `metrics.json` and the CLI summary report `p_value_vs_no_shift` next to each PSI.
- Fixes:
  - If the CSV carried US/Spain rows, the US share means would have been labelled and printed as PSI values. They no longer are.
  - `metrics.json` stores the dataset path repo-relative instead of an absolute home path.
  - `requirements.txt` gets back its header comment and the `Flask>=3.1,<4` line, which the 2026-11-06 `uv export` had dropped.
- Docs:
  - `docs/SALES_FORECAST.md`: real results in place of the synthetic ones, a plain-language answer for Finance, the XGBoost justification backed by the numbers, the PSI noise check, and macOS setup without Homebrew.
  - The `data/raw` READMEs (EN and ES) and the `data/eval` README (EN) list the new files.
- Tests: 3 new in `tests/pipelines/test_sales_forecast.py`, for 23 in total.
  - The 8/2 split and no-leakage rule on the real company file.
  - The PSI p-value separates noise from a real shift.
  - Market rows are not mislabelled as PSI.
- Verification:
  - Full backend suite: 876 passed, 51 skipped.
  - `ruff format`: clean.
  - `ruff check`: only the pre-existing E402 for the `sys.path` bootstrap in `scripts/train_sales_forecast.py`.
  - mypy: clean without `pandas-stubs`. With the stubs it reports 24 index-typing errors in the 2026-11-06 code (`Index` vs `DatetimeIndex`, `int(Hashable)`). None are runtime bugs. They are pending the developer's decision per AGENTS.md.

Still missing / next goals:
1. Developer decision on the 24 `pandas-stubs` mypy errors (fix with typed casts or leave).
2. "K2 Score" is read as R² (sklearn `r2_score`). If the course means D'Agostino's K² normality test on the residuals (`scipy.stats.normaltest`), it is a small addition.
3. A permanent libomp install (Homebrew + `brew install libomp`) would remove the need for the `DYLD_LIBRARY_PATH` workaround.
4. Forecast 2026 onwards by refitting on all 10 years. This is not built yet.
5. Still open from 2026-11-06: the briefing's US/Los Angeles context vs the memory bank's Mexico/Monterrey.

## Update 2026-11-06 - Sales forecast: monthly revenue prediction model (XGBoost)

Goals accomplished (tech lead's ticket `lead-instr.png`, worked through the phases of `stages-todo.png` with `contexts/company-sales.csv` and `contexts/trackflow-sales-briefing.md`; full write-up in `docs/SALES_FORECAST.md`):
- Environment: `uv 0.12.17` is installed. `pyproject.toml` already declared `pandas`, `scikit-learn`, `xgboost` and `matplotlib`, so neither `uv init` nor `uv add` was needed.
- Data (`scripts/sales_forecast.py`):
  - Contract validation against the briefing's columns and markets.
  - Explicit, counted cleaning (exact duplicates, derivation of one missing field from the other two, refusal of conflicts, gaps and non-positive revenue).
  - A data profile checked against the briefing (alternating 9%/3% growth, Nov-Dec peak, February dip).
- Split: calendar years 2016-2023 for training and 2024-2025 for testing. Anything that isn't exactly 10 full years is refused, and the split is asserted disjoint and ordered.
- Model:
  - sklearn `Pipeline` of `StandardScaler` and `XGBRegressor`, chosen over Random Forest for data size, explainability and tuning time.
  - Detrended target `revenue / trailing 12-month mean`, because trees can't extrapolate growth. Causal lag, rolling, seasonal and calendar features.
  - Parameters chosen by recursive walk-forward backtests (origins 2019-2021) inside the training years.
  - The 24 test months are forecast recursively, with no measured test value fed back.
- Evaluation:
  - MSE (plus RMSE as % of mean revenue), K2 = R², normalized Gini, MAE and MAPE for the model and for a seasonal-naive baseline.
  - PSI for the seasonal pattern train vs test, for fitted vs forecast ratios, and for the US/Spain mix when those rows exist.
  - An 80% variability range from backtest errors, per horizon year.
- CLI `scripts/train_sales_forecast.py`: writes `metrics.json`, `forecast_test_years.csv` and `forecast_test_years.png` to `data/eval/sales_forecast/`. Exit code 2 means a contract error.
- Tests: `tests/pipelines/test_sales_forecast.py` (20, synthetic data).
  - The 8/2 split.
  - No leakage: features don't change when future values change, and the end-to-end forecast is identical when the test years are multiplied ×3.
  - Cleaning and metrics.
- Verification (synthetic briefing-shaped data, because the real CSV is under a Copilot content exclusion):
  - 20/20 tests pass.
  - MAPE 1.7% vs 5.1% for the baseline, R² 0.97, 100% of test months inside the 80% range.
  - ruff format and check clean; mypy clean on the three files.

Still missing / next goals:
1. Run on the real `contexts/company-sales.csv` (`uv run python scripts/train_sales_forecast.py`) and replace the synthetic numbers in `docs/SALES_FORECAST.md → Results`.
2. macOS needs `brew install libomp` for XGBoost to import in `.venv`. It was verified here only in a scratch venv linked to scikit-learn's bundled libomp.
3. The briefing names the dataset `data/raw/trackflow_sales.csv` and describes a US/Los Angeles context, which differs from the memory bank's Mexico/Monterrey. To be confirmed with the tech lead.
4. Possible follow-ups:
   - Forecast the months after 2025 (refit on all 10 years).
   - Per-market models, if the CSV carries US/Spain rows.
   - External drivers (clients, pricing).

## Update 2026-10-25 (later) - DEV-53: nightly telemetry script, status control and cron trigger

Goals accomplished (CTO's ticket DEV-53, `lead-instr.png`, worked through the six sections of `stages-todo.png`; full write-up in `docs/NIGHTLY_EXPORT.md`):
- Phase 1, data model:
  - `public.job_runs` (`id, job_name, target_date NOT NULL, status pending|processing|completed|failed, started_at, finished_at, error_message, created_at`) with lifecycle checks.
  - Indexes `job_runs_job_name_target_date_idx` and the unique partial `job_runs_one_active_idx` (the lock, with no lock table or column).
  - RLS on; reviewable SQL `services/job_runner/migrations/001_create_job_runs.sql` next to the idempotent `ensure_schema`.
  - Kept apart from `reporting.pipeline_runs`, which is a different layer.
- Phase 2, status control (`services/job_runner/`):
  - `create_job_run`, `mark_processing/completed/failed`, `get_job_run`, `list_job_runs`, `has_processing_lock`, `has_completed_for_date`, `sweep_abandoned` and `run_job`.
  - Any `BaseException`, Ctrl-C and SIGTERM included, marks the row `failed` with the message, is logged at ERROR with the traceback, and propagates.
  - A row left `processing` by SIGKILL is swept to `failed` after 6 h.
- Phase 3, `scripts/nightly_export.py`:
  - `TARGET_DATE` or yesterday in UTC. The CSV export to `data/raw/telemetry_<date>.csv` happens only if absent, from a READ ONLY transaction with an atomic rename.
  - Then the pipeline as a subprocess: `python -m data.pipelines.pipeline --week-start <last week closed by the end of the target date>`, as decided with the developer.
  - The result goes to `job_runs`. It runs as `python scripts/nightly_export.py` from any working directory.
- Phase 4, idempotency and locking: an active row means a silent abort with the cancellation logged; a completed `(nightly_export, target_date)` means a duplicate skip. A failed day can be re-run, keeping its CSV and re-running only the pipeline.
- Phase 5, trigger:
  - OS crontab, `30 3 * * *`, through `scripts/cron/nightly_export.sh`. The decision (not FastAPI, not the Prefect deployment; a scheduler container as the production follow-up) and the PR text are in `docs/NIGHTLY_EXPORT.md`.
  - A temporary entry proved cron runs it: it fired at 19:34 EDT and exported 174 events for 2026-10-20 on Supabase, completed. The nightly entry is now installed in the developer's crontab.
- Phase 6, observability: INFO for start, finish and skips, ERROR for failures. Every line carries a UTC timestamp, the job, the target date and the status. The pipeline's stderr is re-logged line by line with Prefect's level.
- Found while verifying:
  - The pipeline CLI runs Prefect's temporary API server as a separate process. `subprocess.run`'s SIGKILL orphaned it, and it held the stderr pipe open.
  - Signalling the whole group at once stopped the server before the pipeline's crash hook could report, leaving its `pipeline_runs` row `running`.
  - Fix: the pipeline runs in its own process group with file-backed output; SIGTERM goes to the pipeline first (its row ends `failed`/`Crashed`), then to the group, then SIGKILL.
  - macOS `crontab <file>` truncates long paths, so the entry is installed on stdin.
- Verification:
  - Unit: `tests/test_job_runner.py` (31, SQLite) and `tests/test_nightly_export.py` (39).
  - Opt-in `tests/test_job_runner_postgres.py` (7) passed on local PostgreSQL 16 and on Supabase, with no test schemas left.
  - Real runs on local PostgreSQL: completed, duplicate, two concurrent processes (one refused by the unique index), pipeline failure then retry, SIGTERM mid-pipeline (exit 143 after 1.4 s, both run logs failed, no leftover processes), SIGKILL then lock abort then sweep.
  - Supabase: a run for 2026-10-24 (0 events, header-only CSV, pipeline 3 rows unchanged), then a duplicate skip.
  - Backend suite: 797 passed, 48 skipped.
  - ruff format and check clean on the changed files. mypy on the new modules shows only the known `Settings` noise, after fixing two annotation-level errors in the new script (`RowMapping` vs `Mapping[str, Any]`, `getLevelName`'s return type) with no behaviour change.

Still missing / next goals:
1. A dedicated scheduler container (for example supercronic in `docker-compose.yml`) running the same wrapper, for production. Docker is not installed here to verify it.
2. The pipeline CLI has no `--trigger` option, so nightly runs appear as `trigger = manual` in `reporting.pipeline_runs`. Adding one changes the pipeline code, which was out of scope.
3. No catch-up: a night missed while the Mac sleeps is not exported automatically (run it with `TARGET_DATE`). `logs/nightly_export.log` is not rotated (about 40 lines a night).
4. No alert when a nightly run fails: the evidence is the `job_runs` row and the log. A failure notification, for example together with the Prefect Automations still pending, would close that.
5. Supabase now holds `job_runs` with 2 completed rows (2026-10-24, 2026-10-20), plus 2 new `manual` rows in `reporting.pipeline_runs` from these runs. The report rows are unchanged. `data/raw/` holds the two real exports.

## Update 2026-10-25 - Pipeline to production: subflows, KPI unit tests, CLI, leadership dashboard

Goals accomplished (CTO's Part 3 ticket, `lead-instr.png`, worked through the four-phase checklist in `stages-todo.png`; detail in `data/pipelines/PIPELINE_DESIGN.md` section 11). `uv add "prefect>=3"` changed nothing: Prefect 3.9.0 was already locked.
- Phase 1, subflows: the main flow `weekly_warehouse_client_performance` keeps `open_run` and invokes one subflow per stage from `data/pipelines/subflows/`: `extract_weekly_warehouse_client_activity`, `compute_weekly_warehouse_client_kpis`, `load_weekly_warehouse_client_performance`, plus the optional `measure_kpi_event_collection_health` and `export_weekly_performance_eval_snapshot` with `return_state=True`. Inputs and outputs are explicit. The transform is split into one task per BRIEFING KPI (`compute_inbound_volume`, `compute_outbound_throughput`, `compute_stockout_frequency`, `compute_discrepancy_rate`), plus `prepare_kpi_events` and `assemble_weekly_warehouse_client_rows`. The functions they wrap are in `data/process/weekly_rollup.py`, and `build_rollup` composes them, so the 46 existing rollup tests still cover the whole transform.
- Found while refactoring: Prefect stores subflow parameters in its database, and a DataFrame of text columns JSON-encodes, so passing events or per-client rows between subflows would have copied them into Prefect and its UI. The stages now pass `StageData` containers that Prefect records as row counts, and a test pins it. Also confirmed: on a flow retry, Prefect 3.9 re-runs completed subflows whose results were not persisted, so retries keep working without persisting event rows (covered by a test).
- Phase 2, tests: `tests/pipelines/test_pipeline.py` (43 tests) calls each task's function in isolation with in-memory telemetry-shaped rows. It covers defensive refusals (`KpiInputError` for null, text, fractional or negative quantities and null keys; `prepare_kpi_events` excludes the same malformed telemetry with its reason) and hand-calculated BRIEFING values (174 units; 3/8 = 0.375; 1/3 = 0.3333; 0 without orders). `python -m pytest tests/pipelines/test_pipeline.py` passes. `tests/test_pipeline_flow.py` moved to `tests/pipelines/` and gained 3 tests (subflow order, parameter privacy, flow retry).
- Phase 3, CLI: on Supabase, `python data/pipelines/pipeline.py` exited 0 with 0 inserted / 0 updated / 6 unchanged, so the refactor reproduces the published rows exactly. `--week-start`, `--backfill` and `--flow-retries 0` also exited 0, and a non-Monday week still exits 2. A real served run (`--serve`, local Prefect server, `prefect deployment run`) completed with all five subflows.
- Phase 4, dashboard: `/reporting` in `uis/backoffice` ("Weekly report" in the nav) shows the four KPIs under BRIEFING's names. Each has its plain-language meaning, the week's total and a bar per warehouse and client, and one table holds every figure. The period is written out (Monday to Sunday, UTC), with previous and next week controls and freshness and quality notices from the run log. `lib/reporting.ts` validates the responses; `tests/frontend/backoffice/reporting.test.ts` has 18 tests. Checked in Chrome against the real API and Supabase (zero weeks), and with mocked non-zero figures, including a phone layout.
- Verification: backend 727 passed, 41 skipped (46 new). Opt-in `tests/test_reporting_postgres.py`: 18 passed on Supabase, no test schemas left. Backoffice lint, typecheck and Jest (280) pass; ruff and mypy are clean on the changed Python. The typecheck found three annotation-level errors in the new code (a `Real` comparison, a pandas-stubs Series/DataFrame return, an optional flow run id), fixed without behaviour changes.

Still missing / next goals:
1. Everything in the 2026-10-22 list still stands: the `reporting_pipeline` role and Secret block, Prefect Automations, the `reporting_lookback_weeks` Variable, the `client_id` event property, and running the Docker changes.
2. `/reporting` is not in the telemetry section catalogue (`lib/telemetry-routes.ts` and the `section` enum in `event-schemas.json`), so dashboard views are not measured. That needs an additive telemetry schema change.
3. The week of 2026-10-19, the first with real order events, closes 2026-10-26. Until a run publishes it, the dashboard shows the zero weeks of 2026-10-05 and 2026-10-12.
4. Supabase now also holds this session's verification runs in `reporting.pipeline_runs` (fail-fast, plain, `--week-start`, backfill and served runs); the report rows are unchanged. A test account `reporting-dashboard-e2e@example.com` exists only in a scratchpad TinyDB copy, not in `data/suppliers.json`.

## Update 2026-10-22 - Business performance pipeline implemented (Prefect 3)

Goals accomplished:
- Implemented `data/pipelines/PIPELINE_DESIGN.md` following the five-phase checklist (`stages-todo.png`) and the tech lead's ticket (`lead-instr.png`). Design section 10 maps every checklist item to the code and lists the departures. `BRIEFING.md` supplied the KPI names, the destination table and the endpoint contract. `prefect>=3` was added with `uv add` (3.9.0; only `certifi` moved), and `requirements.txt` was re-exported in its existing format.
- Phase 1, flows and tasks: `data/pipelines/pipeline.py` is the entry point. It holds `weekly_warehouse_client_performance` and `backfill_weekly_warehouse_client_performance`, with 9 tasks: `open_run`, three read-only extracts, `transform_weekly_rollup`, `compute_collection_health`, `validate_weekly_rows`, `load_weekly_rows`, and the non-critical `write_eval_snapshot` writing to `data/eval/`. The pure transforms are in `data/process/` and the SQL in `data/pipelines/weekly_warehouse_client_performance/`.
- Phase 2, resilience: every database task retries transient errors only, with the delays justified in comments; the flow retries 3 times, 15 minutes apart. Collection health and the eval snapshot run with `return_state=True`, so their failure degrades the run instead of stopping it. Both transforms have a content-hashed `cache_key_fn` with a 1-hour `cache_expiration`, persisted under `data/raw/prefect-results/`.
- Phase 3, idempotency: a one-transaction load upserts on `(warehouse, client_id, week_start)`, deletes stale keys and marks the run `completed`. It also holds an advisory lock, refuses a stale snapshot, and writes a revisions trigger row for every changed figure. `reporting.pipeline_runs` records start, end, counts, status, errors, cutoff, exclusions and coverage.
- Phase 4, script: `python data/pipelines/pipeline.py` ran without errors on Supabase. It created the `reporting` schema and inserted 6 zero rows (3 clients, weeks 2026-10-05 and 2026-10-12, which hold no in-scope events). A re-run reported 0 inserted / 0 updated / 6 unchanged, with both transforms `Cached`. The schedule (Monday 06:00 `America/Monterrey`) and the run commands are in design 4.4 and `data/pipelines/README.md`.
- Phase 5, endpoints: `services/reporting/` serves the KPI query, the latest-run status and the manual trigger, all with a bearer token and all importing from `data/pipelines`. They were checked over HTTP with a real Prefect server and the served deployment: 401/200/422/404 on the reads, then 202 on `POST`, after which the served run completed with `triggered_by` recorded. That check found that serving from `__main__` crashed every run (a cloudpickle `RecursionError`); `serve()` now serves the importable module.
- Client dimension (design Q1, developer's choice: product owner only): nullable `products.client_id` with a slug check, added by inventory `initialize_schema`, absent from product responses. The seeded owners are `stride-athletics`, `coco-beauty` and `nexa-electronics`, assigned on Supabase by the seed, whose second run changed nothing. Two existing tests that pinned the old product column set were updated.
- Config: `data` added to the wheel packages, pipeline outputs git-ignored, `.env.example` documents `PREFECT_API_URL` and `REPORTING_DATABASE_URL`. The Dockerfile copies `data/pipelines` and `data/process`, and compose moves the TinyDB volume from `/app/data` to `/app/var`, since the volume would otherwise hide the pipeline code. The volume's data stays.
- Verification: ruff format and check are clean on all changed files, and mypy is clean on `data/`, `services/reporting`, `services/inventory` and the seed. The backend suite gave 675 passed, 40 skipped, with 105 new tests (111 after the follow-up). The opt-in PostgreSQL `tests/test_reporting_postgres.py` (17) passed on a local PostgreSQL 16 and on Supabase through the pooler, alongside the telemetry and inventory suites (33 passed). No test schemas were left behind.

- Follow-up the same day, closing two gaps found when re-checking the checklist:
  - Phase 3, run metadata: failed and skipped runs now keep `events_read`, and when `reporting.pipeline_runs` cannot take a failed run's record, the failure hook appends start, end, events read, status, error and reason to `data/eval/weekly_warehouse_client_performance/run_log_fallback.jsonl`. `run_log.fail_run` now reports `failed` / `unchanged` / `missing`. Verified with an unreachable database: 3 `open_run` attempts, `Failed`, exit 1, one fallback line.
  - Phase 5, boundary: the week rules and the `stale` derivation moved from the router to `queries.check_requested_week` and `queries.get_pipeline_status`. The status codes and messages are unchanged.
  - Neither touched the tasks, retries, caching, load, endpoints or contracts. ruff and mypy are clean; the backend suite gave 681 passed, 41 skipped; `tests/test_reporting_postgres.py` (18) passed locally and on Supabase.

Still missing / next goals:
1. The `reporting_pipeline` database role and the `trackflow-reporting-database-url` Secret block. The pipeline uses `DATABASE_URL` until they exist.
2. Prefect Automations for `Late`/`Failed`/`Crashed` and their notification block; the Prefect Variable `reporting_lookback_weeks`.
3. The `client_id` event property (schema `1.1.0`, backoffice payload, `POST /inventory/products`), and backend emission of the order events (design Q11).
4. Nothing serves the deployment permanently yet. The week of 2026-10-19, the first with real order events, closes on 2026-10-26 and needs that morning's scheduled run or a manual one.
5. The Docker changes were not run (Docker is not installed here). The API's 409 only covers a run whose row is already `running`; before that, Prefect's `ENQUEUE` serialises runs.
6. A deterministic failure (validation) also waits out the 3 flow retries, because Prefect 3 has no flow retry condition; its alert fires after about 45 minutes.
7. Supabase now holds the 4 runs of the verification in `reporting.pipeline_runs`, and the 6 zero rows. A local test account `reporting-e2e@example.com` exists only in a scratch TinyDB copy, not in `data/suppliers.json`.

## Update 2026-10-19 - Telemetry storage in Supabase (stub replaced)

Goals accomplished:
- Pre-flight (2026-10-17): real Chrome confirmed the backoffice `TelemetryService` delivers batches to the stub with 200, by fetch and by `sendBeacon`. The API itself could not start: a stray `$` after the quoted `DATABASE_URL` in the git-ignored `.env` stopped python-dotenv from loading it (fixed), and the Supabase project was unreachable (restored 2026-10-19). Phases 1-3 were first built against a throwaway local PostgreSQL, then completed on Supabase as below.
- Phase 1, storage: Supabase's `telemetry_events` is the table created in the dashboard (`id` uuid PK, `timestamp`, `event_type`, `payload` jsonb, `tags` jsonb, `created_at`). `services/telemetry/database.py` mirrors it and, at startup over `DATABASE_URL`, adds only what is missing: the three indexes (`timestamp` DESC, `event_type`, GIN on `tags`, named exactly as in Supabase because `IF NOT EXISTS` matches by name), RLS with no policies, and triggers rejecting UPDATE, DELETE and TRUNCATE, so events are append-only.
- Row contract: `id` = `eventId` (a retry cannot duplicate), `payload` = `properties` verbatim, `tags` = `sessionId`, `userId`, `requestId`, `schemaVersion` plus the context dimensions the event carries (`warehouse`, `branch`, `source_system`, `client_app`, `section`). Stated in Python (`service.to_row`) and TypeScript (`packages/shared/types/telemetry.ts`, `toTelemetryRow`, re-exported from the shared index); `tests/test_telemetry_contract.py` keeps the field lists and the real `TelemetryEvent` names in sync, and both mappings produced identical rows on sample events under Node 24.
- Phase 2, real endpoint: same URL and envelope. The list is parsed loosely, each event is validated with the unchanged `TelemetryEvent.model_validate`, and invalid events are rejected one by one - including NaN/Infinity and NUL characters that JSONB refuses, and a repeated `eventId` within the batch - while the rest is stored with one Supabase `upsert(on_conflict="id", ignore_duplicates=True)`. Response `{received, stored, rejected}`: 200 for any well-formed batch, 413/422 for a malformed envelope, 503 when Supabase fails (the frontend retries only 429/5xx). Logs carry counts and `field:error_type` reasons, never values.
- Supabase Python client added (`supabase` 2.33; the resolver moved `websockets` to 15.0.1), behind `services/core/supabase_client.py` with a 10 s timeout. `SUPABASE_URL` and `SUPABASE_SECRET_KEY` are required server-only settings; `.env.example` documents them and the previously undocumented `DATABASE_URL`; Docker Compose passes both through (`docs/DOCKER-DEV.md`). The unused `telemetry_endpoint` setting was removed. The frontend did not change.
- Phase 3, end to end on Supabase: a failed login (stored as `api_error_recorded`, 401), registration and sign-in, and a 1-unit inbound plus 1-unit outbound order (stock unchanged at 45) gave 47 rows, all batches 200, no duplicates; `tags` searches by warehouse, client app and section return the right rows. A curl batch mixing 2 valid and 6 invalid events returned `{"received":8,"stored":2,"rejected":6}`, and its replay stored nothing twice. A real frontend race - an in-flight batch re-sent by beacon - was absorbed by the `id` dedupe (20 events skipped).
- Both opt-in PostgreSQL suites ran against Supabase: telemetry 11/11, inventory 4/4 after updating `test_postgres_api_uses_tinydb_uuid_and_computed_stock`, stale since the 2026-10-12 serialization audit removed `user_uuid` from movement responses and flattened order history.
- Verification: ruff format and check clean; mypy shows only the known noise; shared types `tsc --strict` and backoffice typecheck clean; backend 501 passed, 22 skipped (the 15 opt-in PostgreSQL tests, passed separately on Supabase, and 7 Flask error-contract tests that skip because `flask` is not in the uv environment); website Jest 170 and backoffice Jest 262 passed.

Still missing / next goals:
1. Sign-in events (`user_registered`, `login_succeeded`, the first dashboard view) carry `userId: null`, because `AuthProvider` attaches the user only after `/auth/me`; the plan uses `login_succeeded` for daily active users. The fix is in `auth-service.ts`, a frontend change.
2. Per-event `properties` are not yet validated against `event-schemas.json`.
3. Backend-emitted mandatory events are still not instrumented: `login_failed`, `direct_stock_edit_rejected`, server-side `api_latency_recorded`, `inventory_discrepancy_detected`.
4. The proposed 90-day retention needs a purge job that deliberately disables the append-only trigger.
5. Plan gaps: no country dimension; inventory `warehouse` (Monterrey/Zaragoza) and incident `branch` (`la_*`) use different location vocabularies; frontend events carry no client app on the envelope (the `Origin` header could supply it).
6. Pre-existing, unrelated: concurrent authenticated requests can 500 with `JSONDecodeError` because TinyDB shares one file handle across threads; `flask` is missing from the uv environment.
7. Supabase keeps the E2E test data the guard protects: 47 browser rows, 2 curl rows (`tags.sessionId` `0e2e0000-0000-4000-8000-00000000c0de`) and a manual `test.ping` row. The test account `telemetry-e2e-1792409010@example.com` exists only in the local TinyDB.

## Update 2026-10-15 - Backoffice telemetry capture

Goals accomplished:
- Added `inventory_discrepancy_detected` to `docs/telemetry/telemetry-plan.md` and `event-schemas.json` after CTO feedback: the twelfth mandatory event, 44 in the catalogue. It is a stream event whose capture point (a reconciliation workflow or WMS connector) does not exist yet; it records a count mismatch as evidence and never changes stock (`717bf1a`).
- Added the public stub ingest endpoint `POST /telemetry/events` in `services/telemetry/`. It validates the envelope with extra fields forbidden, caps a batch at 100 events and 256 KiB, parses the raw body so `navigator.sendBeacon`'s `text/plain` avoids a CORS preflight, and only logs counts per event type; nothing is stored. Public on purpose, because the plan accepts anonymous pre-login events. `telemetry_endpoint` setting added for a future sink and unused by the stub.
- Added `uis/backoffice/services/telemetry-service.ts`. `track()` is the only entry point. Events are queued and flushed in batches (every 10 s or at 20 queued, at most 100 per request and 500 queued), retried up to 3 times with exponential backoff, and sent by beacon on `visibilitychange`/`pagehide`. Drops are counted and reported as `telemetry_dropped` in the next batch. The session id lives in `sessionStorage`; `userId` is the pseudonymous account UUID from the token. An empty `NEXT_PUBLIC_TELEMETRY_ENDPOINT` disables capture, which keeps every Jest test inert.
- `TelemetryTracker` (root layout) captures `section_viewed`/`section_left`, `page_load_measured`, and uncaught errors and rejections. `useFlowTelemetry` reports one `flow_started` and exactly one `flow_completed` or `flow_abandoned` per form, counting field names and never values. Wired into registration, password reset/change, incident report/status, supplier create/status and inventory orders. `api-client` sends `X-Client-App`, `X-Session-Id` and `X-Request-Id` (the API's CORS already allows any header) and records `api_error_recorded` and `session_expired`. The frontend emits 28 event types plus `telemetry_dropped`.
- Aligned the plan and schemas with what the frontend can know: `order_id`, `transaction_ms` and `triggering_order_id` became optional (backend-only); `error_class` gained `uncaught_exception` and `unhandled_rejection`; incident `category`, `origin`, `branch` and status fields are now enums instead of free strings.
- `uis/backoffice/.gitignore` now ignores `next-env.d.ts` and `*.tsbuildinfo`, matching `uis/website`; both were untracked in a separate commit because builds and dev runs regenerate them.
- Verification: ruff format (line length 100) and check on the changed Python files, backoffice ESLint auto-fix and typecheck all clean. Backend 460 passed, 11 skipped (the opt-in PostgreSQL tests), including the new `tests/test_telemetry_api.py`; backoffice Jest 262/262, including the new `telemetry-service` and `telemetry-events` suites.

Still missing / next goals:
1. The plan's open questions (section 4) still need the Friday review: confirm the mandatory metrics, pick a sink, set retention, and get the Zaragoza data-protection contact's view on `userId`. The ingest endpoint stays a logging stub until the sink is chosen.
2. Backend-emitted mandatory events are not instrumented: `login_failed`, `direct_stock_edit_rejected`, `api_latency_recorded`, `inventory_discrepancy_detected` (which needs a reconciliation endpoint first), and the backend-only `transaction_ms`.
3. The ingest endpoint validates only the envelope; per-event `properties` are not checked against `event-schemas.json` yet.
4. Alert rules on stream events (plan section 5, step 3) and `uis/website` instrumentation are not started.

## Update 2026-10-14 - Telemetry plan

Goals accomplished:
- Wrote `docs/telemetry/telemetry-plan.md` answering the management RFI, with `docs/telemetry/event-schemas.json` (JSON Schema draft-07, schema version 1.0.0) as its companion (`52c7231`). Design only, no instrumentation code.
- Catalogued 43 events across seven categories (inventory, authentication, navigation, performance, errors, operations, pipeline): 11 mandatory and 32 further opportunities, 13 streamed and 30 batched, each with a hypothesis and decision.
- Designed the standard envelope and its privacy rules (pseudonymous `userId`, route templates instead of raw paths, no free text), plus the stream-versus-batch delivery strategy, throttling, and risks/exclusions.
- Derived the mandatory floor from the tech lead's message, `contexts/context.md` and `contexts/coding-fundamentals.md`, since no explicit "mandatory metrics" list exists in the repository; every derived item is marked for correction.

Still missing / next goals:
1. Confirm the mandatory metrics, the sink, retention and `userId` handling at the Friday review (plan section 4).
2. Implement in the plan's suggested order: envelope and emitter, mandatory events, alert rules, then navigation events and the ingest endpoint.

## Update 2026-10-13 - Incident read caching

Goals accomplished:
- Added request timing middleware in `services/main.py` (logger `api.timing`: method, route template, status, elapsed ms; no query values, IDs or credentials) and a repeatable scratch-database benchmark: `scripts/seed_performance_data.py` and `scripts/benchmark_caching.py` (`d23c001`). The seeder refuses the default DB path and any existing file.
- Added `services/incidents/read_cache.py`, a process-local cache for `GET /api/incidents` and `/api/incidents/summary`: 15-second TTL, at most eight entries and 8 MiB of JSON payload. Create and status changes invalidate it, and a generation guard stops an in-flight older read from repopulating stale data.
- At 5,000 synthetic incidents, warm medians fell from 53.494 ms to 0.661 ms (list) and from 30.504 ms to 0.483 ms (summary). At the representative 95-row scale both were already under 1.5 ms. Supplier, identity and inventory reads are deliberately not cached; the full per-endpoint assessment is in `CACHING_REPORT.md`.
- Extracted the candidate registration modal in `uis/website` and loaded it and the backoffice supplier registration form with `next/dynamic`.
- Verification (from `CACHING_REPORT.md`): backend 452 passed, 11 skipped; website Jest 170 and backoffice Jest 216 passed; both typechecks, lints and production builds passed.

Still missing / next goals:
1. The cache is per-process: in a multi-worker deployment, another worker's entries can stay stale for up to 15 seconds. Add shared invalidation before promising cross-worker read-after-write consistency. If incident routes become user-scoped, the cache key must include the access scope.
2. Production request frequency and cache-hit rate are unmeasured; the benchmark is an in-process lab measurement.
3. Browser Network confirmation of the lazy-loaded chunks, bundle-byte deltas and React Profiler evidence were not captured.

## Update 2026-10-12 - Serialization audit

Goals accomplished:
- Audited all 30 business endpoints and the four generated docs routes against their in-repo callers in `docs/serialization-audit.md` (`37dc92f`). No endpoint returned a raw ORM object or untyped dictionary.
- Narrowed responses to what each consumer uses: `/auth/me` and `PUT /profiles/me` omit the account id, active flag and profile `user_id`; supplier create/list use `SupplierListItem`; incident creation returns only its confirmation fields; inventory availability is id plus stock, movements return flat confirmations, and order history is a flat projection. User and supplier deletion now declare a bodyless 204.
- Updated the backoffice projections, guards and tests to the new shapes. No authorization or database behaviour changed.
- Verification (from the audit): backend 438 passed, 11 skipped, coverage 89.60%; backoffice Jest 216 and website Jest 170 passed; both typechecks passed; OpenAPI schemas checked programmatically.

Still missing / next goals:
1. Narrowing user administration responses, the `/profiles/me` foreign key, and routes with no in-repo caller waits on API-owner confirmation of external consumers.
2. Supplier rate/status mutations still return the full supplier, because the detail view replaces its state with it; decide that contract separately.
3. Swagger and ReDoc were not exercised in a browser.

## Update 2026-10-10 - Frontend performance audit

Goals accomplished:
- Captured three-run Lighthouse 12.8.2 baselines in Chrome 154 for the corporate homepage (desktop/mobile), authenticated candidate pipeline, and authenticated inventory products view. Protected pages used synthetic local data only. Baseline score tables, methods, findings, and duplication candidates are in `AUDIT.md`; raw HTML/JSON and report screenshots are under `audit/before/`.
- Installed and reviewed the `core-web-vitals` and `performance` skills for GitHub Copilot under `.agents/skills/`. CrUX is unavailable for localhost; Lighthouse CLI is the documented lab fallback. Cloudflare `web-perf` was not installed because Cloudflare deployment and its required DevTools MCP are not established.
- Fixed the corporate homepage's measured text contrast failures with a darker muted-blue token and local blue/orange text corrections. Three equivalent desktop Lighthouse reruns now report Accessibility 100 (from 96), zero contrast failures, and Performance 100; LCP remains 545-553 ms.
- Website lint/auto-fix, typecheck, and Jest passed for this correction: 171 tests.
- Removed redundant `aria-label` overrides from the visible TrackFlow brand links in both frontends. One equivalent Lighthouse rerun per affected route reports no label-content/name mismatch; the homepage remains Accessibility 100, and authenticated pages were verified against the synthetic API.
- Both apps' lint/auto-fix and typechecks passed; website's 171 tests and backoffice's 214 tests passed for the nav correction.
- Extracted the repeated status/stage selects into a local `CandidateFilter` with screen-reader labels. Browser verification confirmed both filters remain query-driven; Lighthouse `select-name` now passes with zero failures, Accessibility rose from 92 to 96, and Performance remains 100. Website lint, typecheck and all 171 tests passed.
- Corrected the remaining candidate-page contrast failures (blue eyebrow and orange motto) with local text colors. The authenticated candidate page now has Lighthouse Accessibility 100, zero contrast failures, and Performance 100 (FCP 211 ms, LCP 729 ms in the validation run); website lint, typecheck, and all 171 tests pass.
- Added the existing TrackFlow favicon to the backoffice root app route. Lighthouse on authenticated inventory products now reports Best Practices 100 (from 96), no console errors, and 100 across all four categories. Backoffice lint, typecheck, and all 214 tests pass.
- Repeated the complete three-run Lighthouse matrix after fixes using the same production URLs, browser and presets. Final scores are 100 in all four categories for every measured target. Median loading metrics are documented in `REPORT.md`; no field improvement is claimed because CrUX is unavailable for localhost.
- Added `REPORT.md`, updated `AUDIT.md`, and captured four before/after Lighthouse report screenshots with complete HTML/JSON triplicates under `audit/before/` and `audit/after/`. Synthetic authenticated data was used; no production records or credentials were included.
- Final verification: both production builds passed, both apps' auto-fix lint and typechecks passed, website Jest 171/171 and backoffice Jest 214/214 passed, and browser checks confirmed the candidate labels/filter behavior and protected-page fixture state.
- Committed the five focused corrections: `ee5eeaf`, `9bc92ef`, `3fa8dd8`, `298c604`, `6acc68b`. Final audit documentation and evidence are being committed separately.

Still missing / next goals:
1. Collect real-user field Core Web Vitals after deployment; localhost has no eligible CrUX data, so these results are controlled lab evidence only.
2. Consider cross-app `StateMessage` and authentication-provider deduplication separately; they were documented but deferred because they require broader shared-package boundaries and are not performance bottlenecks.

## Update 2026-10-06 - Backoffice inventory interface

Goals accomplished:
- Added the centralized, typed `uis/backoffice/lib/inventory.ts` facade for all six authenticated endpoints. Reused the stored bearer token and existing 401 session handling; inventory components contain no direct fetch calls. Malformed successful responses now produce visible errors rather than empty lists.
- Built four literal `/backoffice/inventory/...` views under the existing protected layout: Products, Inbound order, Outbound order, and read-only Orders history. Existing login, supplier, incident, and account URLs are unchanged. Navigation wraps on narrow screens and selects the longest matching route.
- Products display Name, SKU, Warehouse, Current stock, written/color stock status and product-preselected inbound/outbound links. Display-only thresholds are commented: 0 empty, 1-9 low, 10+ healthy. Monterrey/Zaragoza and the approved entities are preserved.
- Both forms use named product options and strict positive integer quantities, block duplicate submissions, preserve failed values, and clear successful forms with confirmation. Outbound loads fresh selected-product stock before enabling Quantity, ignores obsolete successes/failures, blocks above-stock submissions, and keeps server 400 quantity text inline during stock refresh.
- History shows embedded product names, SKU/Warehouse, quantity, labeled order direction, timezone-labeled creation dates and full user_uuid without edit/delete actions or extra identity/product requests. Responsive rows expose every field on mobile without horizontal scrolling.
- Inventory preserves readable API-authored 400/422/500 messages and safely handles HTML, unreadable/malformed bodies and offline failures. Other features' shared error policies and the backend remain unchanged. The developer approved live `/inventory/products/inbound|outbound` paths instead of nonexistent `/inventory/orders/...` aliases.
- Verification: 38 focused facade/helper tests, all 213 backoffice Jest tests, and 84 repeatable Playwright browser checks passed. Lint with auto-fix, typecheck, production build and touched-file diagnostics passed. Reviewed desktop/mobile screenshots. Browser tests intercept every identity/inventory request; no operational stock writes were performed.
- Documented routes, API alignment, stock thresholds, setup and repeatable verification in `docs/INVENTORY.md`. Preserved unrelated image changes and protected context files. No commit created.

Still missing / next goals:
1. Verify live end-to-end movement writes against an explicitly approved isolated inventory environment; browser acceptance currently uses API test responses.
2. Backend production-readiness work (migrations, privileged SQL access restrictions, and retry/idempotency design) remains outside this frontend task. Ambiguous write failures must not be automatically retried.

## Update 2026-10-04 - Centralized inventory, Stage 6

Goals accomplished:
- Searched all requested `contexts/*.md` and root Markdown files. Found three sample products in `contexts/coding-fundamentals.md`; root supplier seed data and incident CSV seeds are unrelated and were not reused for inventory.
- Obtained explicit approval to map the samples' Los Angeles warehouses to Monterrey, preserve the exact names/SKUs and target balances 45/8/120, and define a separate synthetic one-laptop outbound scenario. The source Pending shipment was not treated as completed history.
- Added `scripts/seed_inventory.py` and `npm run seed:inventory -- --user-uuid <UUID>`. Actor must be an existing active TinyDB account; the developer selected the account for the live run. No user credentials or SQL identity rows were created.
- Made seeding atomic and idempotent with stable UUIDs, explicit conflict refusal, inbound-before-outbound ordering, and the shared inventory service's transaction-scoped `record_order` locks/stock checks. Reruns preserve timestamps and do not refill stock consumed by later API movements.
- Added 10 focused seed tests and an opt-in PostgreSQL reconciliation/idempotency case. Full backend verification: 434 passed, 11 skipped; authentication coverage 89.52%. All 4 real PostgreSQL tests passed in disposable schemas. Seed/inventory mypy, E/F/I lint, formatting, and editor diagnostics are clean after developer-approved concrete-model typing fixes.
- Loaded Supabase with 3 products, 3 inbound receipts (45 shoes, 9 laptops, 120 perfumes), and 1 synthetic laptop outbound. Net stocks are 45/8/120. The second hosted run inserted nothing and skipped all seven records.
- Verified stock and user UUID attribution through the running authenticated `/inventory/products` and `/inventory/orders` endpoints. Restarted the managed API to load the shared transaction helper. Updated `docs/INVENTORY.md` and technical context. Protected context files and unrelated existing work were preserved; no commit was created.

Still missing / next goals:
1. Schema migration tooling and privileged SQL access controls remain the next production-readiness work; `create_all` is not a migration mechanism.
2. Extend seed scenarios only with explicit source/approval; the existing outbound is a synthetic demo, not a historical completed shipment.

## Update 2026-10-04 - Centralized inventory, Stages 2-5

Goals accomplished:
- Verified the developer-configured `DATABASE_URL` against Supabase with `SELECT 1`, without reading or printing the excluded `.env` contents. Preserved the current configuration edits.
- Added SQLModel Product, InboundOrder, and OutboundOrder tables: UUID primary keys, product foreign keys, positive-quantity constraints, warehouse validation, unique `(sku, warehouse)`, UTC timestamps, and TinyDB `user_uuid` references without SQL identity replication.
- Initialized the three hosted inventory tables at startup with `SQLModel.metadata.create_all`; enabled RLS with no public policies to prevent Supabase REST clients bypassing FastAPI authentication. Existing TinyDB identity, suppliers, and incidents remain unchanged.
- Added standalone Pydantic schemas with strict positive integer quantities within PostgreSQL's integer range, rejected system fields, and computed `current_stock` responses.
- Mounted the six requested `/inventory` routes, all authenticated per the developer's decision. Product and order listings support warehouse filters; order responses include direction, product data, and the authenticated TinyDB UUID.
- Added separately aggregated inbound-minus-outbound stock queries, zero-stock product creation, and Product-row locking for every movement. Insufficient stock returns descriptive 400 without insertion; missing products return 404 and duplicate warehouse SKUs return 409.
- Verification: 424 backend tests passed, 10 skipped; authentication coverage 89.52% clears the 70% gate. The 3 opt-in real PostgreSQL tests passed in disposable schemas and cleaned up afterward. A competing-withdrawal test verified an actual row-lock wait and that only one withdrawal persisted.
- New inventory and touched integration modules pass mypy after developer-approved fixes to SQLModel runtime table attribute and SQLAlchemy query typing. The previously accepted Settings constructor warnings remain documented. Formatting, E/F/I lint, and editor diagnostics are clean.
- Updated `docs/INVENTORY.md` and the technical context. Started the API at `http://127.0.0.1:8000`; verified live startup, route registration, protected reads, and Swagger. No commit was created.

Still missing / next goals:
1. Stage 6 remains deferred until the developer supplies the seed context/minima, explicitly reconfirmed during this work. No invented demo data or seed script was added.
2. `create_all` initializes missing tables, not migrations. Add an explicit migration mechanism before later schema changes or production rollout.
3. Restrict privileged direct SQL access: stock safety assumes movement writes use the locked inventory service. Do not add public Supabase write policies.
4. Rotate the password previously shared in chat if it has not already been rotated. No credentials were committed or repeated in implementation output.

## Update 2026-10-04 - Centralized inventory, Stage 1

Goals accomplished:
- Added required, redacted `DATABASE_URL` settings with PostgreSQL/driver/SSL validation and sanitized errors.
- Added `services/inventory/database.py`: reuse of the existing TinyDB handle, a cached SQLModel/psycopg2 engine, and a yield-based request session dependency. No global SQL session or replicated identity tables.
- FastAPI startup initializes the shared TinyDB handle and performs a read-only PostgreSQL `SELECT 1`; shutdown and failed startup dispose the SQL engine.
- Added test isolation that pins a non-live URL and prohibits psycopg2 connections, plus 24 focused configuration, connection, session, and lifecycle tests.
- Verification: 24 focused tests passed; full backend suite 355 passed, 7 skipped. Touched-file formatting, E/F/I lint, and editor diagnostics passed.
- Mypy reports two missing arguments at `Settings()`: the known `jwt_secret_key` finding and the analogous new `database_url` finding. The developer declined the Pydantic mypy plugin and chose to leave these documented.
- Documented setup and limitations in `docs/INVENTORY.md`. Existing dependency edits and other user changes were preserved. No commit was created.

Still missing / next goals:
1. The developer will configure `DATABASE_URL` directly: `.env` is excluded from Copilot access. Rotate the password shared in chat. Live Supabase connectivity has not been verified.
2. Stages 2-5: SQLModel Product/InboundOrder/OutboundOrder tables and startup schema creation, separate Pydantic schemas, authenticated `/inventory` routes, and warehouse-scoped computed stock with transactional locking.
3. Stage 6: seed minima await the developer's additional context. Use Monterrey/Zaragoza from `contexts/context.md`; every inventory GET is authenticated per the confirmed decision.
4. No hosted schema or business data was created or changed during Stage 1.

## Update 2026-09-29 - AUTH-088: unit tests for the authentication API

Goals accomplished:
- Ran the mandatory memory-bank reading sequence before work: `context.md`, `projectbrief.md`, `techContext.md`, `progress.md`.
- Delivered ticket AUTH-088 in the developer's four stages, on branch `feature/auth-088-unit-tests`. A refactor had broken token expiry unnoticed, so every auth endpoint now has at least one happy-path, one edge-case and one failure-mode test.
- **Stage 1, test plan.** New root `TESTING.md` covers how to run both suites, what each covers, the fixtures and what they guarantee, and the test plan itself: every case for all 13 endpoints plus the token gate and password hashing, in priority order, each with the reason it exists. It also records the bugs found and fixed, the behaviours pinned as open questions, and the gaps unit tests cannot cover.
- **Stage 2, pytest.** 216 tests in `tests/auth/`, one module per endpoint (`test_register.py`, `test_login.py`, `test_token.py`, ...), plus `test_current_user.py` (the gate and a route inventory) and `test_password_hashing.py`.
  - Tests call the service and router functions directly and assert decisions, never HTTP serialisation.
  - Token expiry is tested by moving the clock with `time-machine`, not by crafting tokens.
  - A root `pinned_settings` fixture makes the whole suite independent of `.env`, and it can never pick up a live Resend key.
  - `uv run pytest`: 331 passed, 7 skipped (Flask tests; Flask is not in the uv env).
  - `uv run pytest --cov`: 88.9% of the authentication API, `services/auth` at 99-100%. `fail_under = 70` in `pyproject.toml` enforces the ticket's threshold.
- **Stage 3, Jest.** `jest.config.ts` at the root of each Next.js app, loaded natively by Node 24, with `npm test` = `jest --coverage` and a root `npm run test:ui`.
  - The shared tests in `tests/frontend/shared/` run against both apps' copies of the duplicated auth modules, so the copies cannot drift apart unnoticed. `tests/frontend/{website,backoffice}/` cover the clients that differ.
  - website: 170 passed. backoffice: 175 passed. 98% of lines and 100% of functions in the auth modules.
- **Stage 4, AI-assisted workflow.** An AI review of the endpoint logic found the bugs below. Each was reproduced against the installed libraries, pinned by a failing test, then fixed:
  - BUG-1: `_decode` now requires `exp`. A correctly signed token without one was accepted forever, the exact class of the original incident.
  - BUG-2: open redirect after login in both apps (`?next=/%5Cevil.com`, `/%09/evil.com`, `%2F%0A%2Fevil.com`). `sanitizeNextPath` now refuses backslashes and control characters and keeps a value only if it resolves to the same origin.
  - BUG-3: `PUT /users/{own id}` changed password or email with the session alone. It now requires `current_password`, admins included on their own account, and any password set there spends reset links.
  - BUG-4/BUG-5: passwords of 72 characters or fewer but over 72 bytes (for example with `ñ`), or containing NUL, crashed bcrypt 5 as a 500 on four routes. The shared `Password` type now refuses both with a 422.
  - F-6: long or NUL passwords at login logged a fake "stored hash could not be read" warning. They now fail without calling bcrypt.
  - F-7: one unreadable user row made `GET /users` a 500. Such rows are now skipped and logged, like incidents.
  - BUG-6, from a second review pass: reset links survived an email change and a deactivation. `PUT /users/{id}` now spends them in both cases.
- Proved the suite bites: seven regressions were introduced one at a time (tokens without `exp`, lifetime in hours instead of minutes, deactivated users let through, a removed `Depends`, the missing dummy bcrypt check, reusable reset links, an open `sanitizeNextPath`). All seven were caught.
- Formatting and linting: `uvx ruff format` and `ruff check` are clean on every changed Python file, and `npm run lint -- --fix` is clean in both apps. Jest's `coverage/` is now ignored by ESLint and git, and `.coverage` by git.
- Typechecking:
  - `uvx mypy` reports only the two known pre-existing findings.
  - `npm run typecheck` is clean in both apps.
  - The frontend tests were also type-checked once against each app's modules.
- Documentation: `docs/AUTHENTICATION.md` (the `PUT /users` rule, the byte-based password rule, `exp` required) and `docs/PASSWORD-RECOVERY.md` (a new section on when outstanding links die).

Still missing / next goals:
1. **Open questions pinned by tests, awaiting a decision** (listed in `TESTING.md`):
   - an all-spaces password is accepted;
   - a new password may equal the current one;
   - any user can list and read every account;
   - an admin can demote, deactivate or delete the last admin;
   - an account can be deleted with the session alone;
   - `PUT /profiles/me` stores `""` verbatim.
2. **Rate limiting** for login, forgot-password and change-password, and **token revocation** after a password change. These are still the biggest auth gaps, and unit tests cannot cover them.
3. A damaged user row still makes lookups of that one account answer 500. Decide whether to treat it as "no account" (which risks duplicates) or keep the 500.
4. The Jest tests live outside both apps' `tsconfig.json`, so no command type-checks them. A `tests/frontend/tsconfig.json` would close that.
5. The auth modules are still duplicated across the two apps. The shared tests now make any drift visible, but moving them into `packages/shared` would remove it.
6. The root `.gitignore` has a pre-existing typo, `.env.*.local~`: files like `.env.production.local` are not actually ignored.

## Update 2026-09-23 (later) - Centralized Incident Manager, stage 4 (frontend)

Goals accomplished:
- Built the incident manager's screens in **`uis/backoffice`**, resolving the open question from this morning's entry. The incident manager is internal - warehouse floor terminals, the CTO and the CEO - so it belongs in the backoffice console next to the supplier directory, not in the public-facing `uis/website`. Both screens sit in the `(protected)` route group. Full write-up in `docs/INCIDENTS.md`.
- **Registration form** (`/incidents/new`, `components/incidents/IncidentForm.tsx`): every model field, with `id`/`created_at`/`updated_at` stated as generated rather than faked as disabled inputs. `branch` is always visible and always required for every origin, with all five CONTEXT display names including "Central". When `origin` is `branch` the field takes an accent ring, a tinted background and the reminder "You are reporting from a specific location", wired into `aria-describedby` so the emphasis never rides on colour alone. Submitting shows a spinner, disables the button and sets `aria-busy`; the handler also returns early while a request is open, so a double tap cannot fire a second one. Success clears the fields and confirms with the title, status, branch and generated reference.
- **Incident list** (`/incidents`): filters by `status`, `origin` and `branch` held in the query string, loading and error states, a **Try again** button, and an empty state that always says why it is empty - the no-data case links to the form, the no-results case names the filters that produced it and offers to clear them.
- **Status editable from the row**, offering only the transitions the lifecycle allows and showing "Final state" where there are none. The change is optimistic so a terminal responds to the tap; a failure reapplies the status captured *before* the request and shows the reason next to the control. A confirmed change refreshes the summary.
- **Summary panel** with its own request and its own states, on the same page as the list, so neither can take the other down. Verified: killing the summary endpoint leaves the list fully working, and vice versa.
- **Nav**: added "Incidents" and "Report incident". Also fixed `isActiveLink`, which marked both as current on `/incidents/new` because one href is a prefix of the other - it now picks the longest match, so only one link ever carries `aria-current="page"`.
- **79 browser assertions** across five Playwright runs against real Chrome, the dev server and the API with the 95 seeded incidents: list + summary, interactions, form, failure modes, and the empty database. All passing. 101 backend tests still pass; typecheck, lint and production build all clean.

Decisions worth recording:
- **`lib/friendly-error.ts` is the only place that decides what a user reads about a failure.** A message is surfaced only when it arrived in the API's documented `{field, message}` shape *and* the status is 400 or 404 - the two the API writes plain language for. A 5xx, an unreadable body, an HTML error page from a proxy, a `TypeError: Failed to fetch`, or a thrown non-Error all get text written in the frontend. Without that rule `ApiError`'s own fallback ("Request failed with status 500") and the browser's network errors go straight to the screen, which is exactly what "never the raw server error text" forbids. There is also a guard rejecting any message containing a traceback, a stack frame, a `file:line`, an HTML tag or more than 300 characters.
- **The summary panel's form was chosen before its colour**, following the `dataviz` skill. `total` is the hero figure and the only one on the page. Category has nine classes - past the point where more colours help - so category, origin and branch are all a table with single-hue bars, every label and count written out, no tooltip because nothing is reachable only by hovering. Status is the one breakdown that earns colour, because its classes are states rather than identities.
- **The status palette was validated, not eyeballed, and the first attempt failed.** Mapping open to `serious` put `warning` and `serious` in adjacent tiles at normal-vision ΔE 13.6, below the 15 gate - hard to tell apart even with full colour vision. Moving open to `critical` clears both gates (CVD ΔE 11.3, normal-vision ΔE 27.6). `warning` stays 1.83:1 on white, a documented property of the fixed status palette, so it appears only as a filled mark beside dark text with an icon and a label. The numbers are recorded beside the tokens in `app/globals.css`.
- **Incident calls use `requestApi`, not `requestAuthenticatedApi`.** The API's incident routes carry no token requirement. Sending a bearer token the API does not read, through a helper whose whole job is handling a 401 that cannot happen, would describe a contract that does not exist. `services/incidents-service.ts` is the single file that changes if those routes are ever put behind auth.
- **Rows stay put when a status change makes them stop matching an active filter.** Removing the row the reader just acted on, out from under the pointer, is worse than showing it until the next load. Same behaviour as the supplier directory.

Fixed along the way:
- **A regression this morning's backend work introduced, caught only by running the app.** The `RequestValidationError` handler was registered app-wide, which turned FastAPI's 422 into a 400 on *every* route. `resetPassword` in `uis/*/services/auth-service.ts` reads any 400 as a spent reset token and replaces the form with "request a new link" - so a new password that was merely too short would have told the user their reset link had expired and sent them to restart a recovery that was working. `changePassword` has the same shape, reading a 400 as "your current password is wrong". The handler is now built by `make_validation_error_handler(prefixes)` and scoped to the incident router's own prefix, read off `incidents_router.prefix` so the two cannot drift. `tests/test_error_contract.py` pins both halves: `/api/incidents` answers 400, `/users` and `/auth/reset-password` still answer 422.
- The status control showed its generic fallback instead of the API's explanation of *why* a transition was refused, because that message arrives attached to the `status` field and the control is that field. It now prefers the field message.
- Seeded rows printed the same sentence as both title and description - the CSV had only a description, so short ones became both. The description is now shown only when it differs from the title.

Still missing / next goals:
1. **Bilingual UI.** The CONTEXT asks for the branch dropdown in the user's language, and `BRANCH_LABELS` in `packages/shared/trackflow_shared/incidents.py` already carries the Spanish names. `uis/backoffice` has no i18n layer at all, so the screens are English-only for now; adding one is its own piece of work, not something to fake per-dropdown.
2. **Authentication on the incident routes**, now that it is clear who reaches them and from where. Still a deliberate omission, not an oversight.
3. **An incident detail page.** The list carries everything the model holds, so there is nowhere to go for one incident yet - and no place to record who is handling it.
4. **The SLA quick-filter.** `SLA_CRITICAL_CATEGORIES` exists in shared code and still has no consumer; the list filters by category only through the URL, and there is no category dropdown on the panel (the brief asked for status, origin and branch).
5. **Unresolved for more than 24 hours** - what the CEO actually asked for, still unimplemented on either side. `created_at` and `status` are enough; it needs a metric on `/summary` or a filter.
6. The two pre-existing mypy errors are still open, both in files none of this work touched.

## Update 2026-09-23 - Centralized Incident Manager, stages 1-3

Goals accomplished:
- Built the data model, the historical seed and the backend for the centralized incident manager described in `contexts/centralized-incident.md`. Stages 1-3 of the milestone; the browser form and panel are the next stage. Full write-up in `docs/INCIDENTS.md`.
- **Shared vocabulary first.** `packages/shared/trackflow_shared/` is now a Python package, mapped into the wheel from `pyproject.toml` alongside `services` and `scripts`. It holds `incidents.py` (the four enumerations, `ALLOWED_TRANSITIONS`, bilingual branch labels, `SLA_CRITICAL_CATEGORIES`) and `incidents_csv.py` (the helpdesk-CSV rules and the CSV -> model maps). The seed has to produce exactly the values the API accepts, so a second copy of the category list would have drifted the first time one of them gained an entry.
- **Data model** (`services/incidents/models.py`), TinyDB table `incidents`: UUID4 `id`, required `title` (1-120) and `description` (1-4000) with whitespace stripped *before* the length check so a field of three spaces is a 400, enum `category` / `status` / `origin` / `branch`, and `created_at` / `updated_at` set by the service. `IncidentCreate` has no `id` or timestamps, so a client that sends them has them ignored.
- **Lifecycle** enforced from one adjacency map: `open -> in_progress | discarded`, `in_progress -> resolved | discarded`, `resolved` and `discarded` final. A refused move returns 400 with the reason in plain language and does not touch `updated_at`. A move to the status the incident already holds is refused for the same reason - it would change the modification timestamp without changing the incident.
- **Endpoints** under `/api/incidents`: `POST`, `GET` with `status` / `origin` / `branch` / `category` filters combining with AND, `GET /summary`, `GET /{id}`, `PATCH /{id}/status`. `/summary` is declared before `/{id}` because an incident id is an arbitrary string and the parameterised route would otherwise match the literal path. Left unauthenticated at this stage: the CONTEXT has anyone in the company reporting an incident, and no route exposes the commercially sensitive data that makes `/suppliers` token-only.
- **Empty database reads cleanly.** `GET /api/incidents` returns `[]`; `/summary` returns `total: 0` with every bucket present at zero - four statuses, nine categories, three origins, five branches - so a dashboard can render a fixed set of tiles instead of discovering an empty object.
- **Error handling centralised** in `services/core/http_errors.py`, registered app-wide rather than per router. `RequestValidationError` is remapped from FastAPI's 422-with-pydantic-dictionaries to a 400 naming one field in a sentence (`missing` -> "This field is required.", `enum` -> "Must be one of: ..."), with the `body` / `query` prefix stripped so the field is `title` rather than `body.title`; `detail` holds the first problem and `errors` holds all of them, so a form can focus one input and mark every bad field. A bare `Exception` handler makes any unhandled failure a 500 whose body names nothing about the exception - not its type, module or line - with the traceback going to the `trackflow.errors` logger. Every error response, 400 / 404 / 500 alike, has the same `{detail: {field, message}}` shape.
- **`scripts/seed_incidents.py`** loads the analyser project's CSV (copied to `data/raw/incidents-trackflow.csv`) as `origin: "customer"`, `branch: "central"` records. It reuses `validate_csv_row` rather than reimplementing the rules, so it rejects exactly the rows the analyser counted as invalid. Invalid rows are never inserted and are reported at the end, grouped by rule and then individually by CSV line number and incident id.
- **Verified against the analyser's published figures, exactly:** 100 rows, 95 inserted, 5 rejected with one row per rule; `open` 29 / `resolved` 52 / `discarded` 14; `lost_parcel` 14, `delivery_failure` 19, `returns_issue` 17, `carrier_issue` 45 (`DELAYED_DELIVERY` 38 + `DAMAGE` 7). `GET /api/incidents/summary` returns the same totals after seeding.
- **96 tests** in `tests/`, all passing. `conftest.py` gives each test its own TinyDB file by redirecting `DB_PATH` *and* clearing the `lru_cache` on the TinyDB handle - without the second half, one test's incidents leak into the next and into `data/suppliers.json`. Coverage includes all four legal transitions and all ten illegal ones, a forced unhandled exception asserted to return 500 with no traceback or file path in the body, and that no console line or issue message ever repeats a customer email.
- Added `npm run seed:incidents` and `npm run test:api`. Bumped the API description and version to 0.3.0.

Decisions worth recording:
- **Idempotency keys on the CSV `incident_id`, not on `title + created_at`.** Each seeded incident stores `source_incident_id`, an internal field excluded from `IncidentResponse` so it is not part of the API contract. The CONTEXT offers `title + created_at` as a fallback, but in the supplied export that pair repeats across 7 of the 100 rows, so keying every row on it would silently drop real incidents as duplicates. The fallback is still used for a row with no `incident_id`.
- **`GET /api/incidents/{id}` returns 404 for an unknown id, not the 400 the brief asked for.** Confirmed with the developer before implementing. It matches HTTP semantics and the existing `/suppliers/{id}`; the response body keeps the same shape as a validation error, so a client reads both the same way.
- **`branch` is `central` for every seeded record**, per the CONTEXT's mapping table and its rule that a customer-origin incident with no specific facility uses `central`. The transformation list in the brief mentioned "location -> branch", but the export's only location column is `country` (US / ES), and turning that into `la_office` / `zaragoza_office` would invent facility detail the helpdesk never recorded. Worth revisiting if the country breakdown is wanted for the CEO's Los Angeles vs. Zaragoza view - it would need a model field, not a branch guess.
- **Two CSV categories map onto `carrier_issue`.** Each target is the category whose own CONTEXT definition names the CSV case outright: `carrier_issue` is defined as "delay, damage, SLA breach", which covers both `DELAYED_DELIVERY` and `DAMAGE`. The seed prints its own category breakdown so the totals stay cross-checkable against the analyser.
- `UP042`, `UP017` and `B008` from ruff are left as-is in the new code: all three are existing conventions in `services/suppliers` and `services/users`, and matching the surrounding code beats the modernisation.

Still missing / next goals:
1. **Stage 4 - the browser form and panel.** An incident form sized for warehouse-floor terminals (large touch targets, minimal free text, branch dropdown labelled in the user's language from `BRANCH_LABELS`), a list with the four filters, and a summary panel. The `/api/incidents` contract and `docs/INCIDENTS.md` are what it builds against.
2. **Decide whether the panel goes in `uis/website` or `uis/backoffice`.** The incident manager is internal, which argues for `backoffice`, but `uis/website` already carries the protected route group and the auth provider.
3. **Authentication on the incident routes**, once the panel exists and it is clear who logs in from the warehouse floor. It is a deliberate omission now, not an oversight.
4. **A quick-filter for `SLA_CRITICAL_CATEGORIES`** in the panel - the set exists in shared code and nothing consumes it yet.
5. **Unresolved-for-more-than-24-hours** is what the CEO asked for and is not implemented anywhere. It needs no new field (`created_at` and `status` are enough) but it does need either a computed metric on `/summary` or a filter.
6. **The two pre-existing mypy errors are still open**, both in files this work did not touch: the pydantic-settings false positive in `services/core/config.py` and the `**dict[str, object]` unpacking in `services/suppliers/seed.py`.

## Update 2026-09-22 (later) - AUTH-03 frontend

Goals accomplished:
- Built the second half of AUTH-03: the three password screens, in **both** Next.js apps, on the API delivered earlier today.
- Routes added, following each app's existing structure rather than inventing a new one:
	- `uis/website`: `app/forgot-password/`, `app/reset-password/`, `app/(protected)/account/change-password/`.
	- `uis/backoffice`: `app/(auth)/forgot-password/`, `app/(auth)/reset-password/`, `app/(protected)/account/change-password/`. The public screens join the existing `(auth)` group, so they inherit the signed-out header; the protected one joins `(protected)` and is guarded by construction.
- `/forgot-password`: email field, one confirmation message, and the form locked afterwards. The email input and the submit button are both disabled and the button reads "Link sent", and the submit handler returns early once sent - so a programmatic `form.requestSubmit()` is refused too, not just a click on a disabled button.
- `/reset-password`: reads `?token=`, takes a new password plus confirmation, and on success redirects to `/login?reset=success`, where `LoginPage` renders a success banner. Two failure kinds are handled differently on purpose: a `400` means the token is dead, so the form is *replaced* by the error and a "Request a new link" button, because resubmitting cannot help; a `422` is mapped back onto the password input and the form stays, because a second attempt can succeed. A missing token shows the same replaced state without any request.
- `/account/change-password`: current password, new password and confirmation, with the match validated before any request. A `400` lands on the current-password field. Fields are cleared on success so the new password is not left sitting in the DOM.
- `/login` gained the "Forgot your password?" link under the password field, and `/account/profile` gained a link to the change-password screen, so neither new route is reachable only by typing the URL.
- Held the line on user enumeration in the client, not just the API: one confirmation string rendered from a constant for every success, so the registered and unregistered cases are character-identical; client validation that only checks the address *looks* valid and never asks the API whether it exists; and `requestPasswordReset` returning `void`, so there is deliberately nothing for a caller to inspect. A failure re-enables the form, which is safe because the route does not fail for an unknown address.
- Added `hooks/useLocationSearch.ts` to both apps, wrapping `useSyncExternalStore`. `useSearchParams` would force the calling page behind a Suspense boundary at build time (the reason `LoginPage` has always read `?next=` off `window`), and reading in an effect trips React 19's `react-hooks/set-state-in-effect`. The hook returns `null` on the server and the real query string on the client, so `/reset-password` shows "Checking your reset link..." instead of flashing a "link is missing" error at someone whose link is fine.
- Shared code rather than copying logic where it mattered:
	- `validateResetPasswordForm` and `validateChangePasswordForm` share one `newPasswordErrors` helper, so the two cannot drift apart.
	- `toFieldErrors` gained an optional alias map, so the API's snake_case field names (`new_password`, `current_password`) land on the camelCase inputs instead of falling through to a form-level message. Existing call sites were untouched.
	- `AuthField` gained `disabled:` styling, so a disabled input reads as disabled in every form, not just the new ones.
- A successful reset clears any stored token before redirecting, since it was issued against the old password. A successful change keeps the session, because the user is already signed in and proved it. Neither is a revocation story, which is still on the backlog.
- Executed formatting with auto-fix: `npm run lint -- --fix` in both apps. The first pass failed with four `react-hooks/set-state-in-effect` errors, which is what prompted the `useSyncExternalStore` hook rather than a suppression. Clean afterwards.
- Executed typechecking: `npm run typecheck` clean in both apps, and `npm run build` succeeded with all four new routes prerendered as static content.
- Attempted the test suite; still no `test` script anywhere, unchanged from every prior entry.
- Verified in a real headless browser against a running API on a scratch database: 102 checks across the two apps, all passing. Per app:
	- The `/login` link exists, points at `/forgot-password` and navigates there.
	- `/forgot-password` catches an empty and a malformed address client-side with no request sent; a registered address shows the confirmation on exactly one request; the input and button are disabled afterwards and the button reads "Link sent"; a programmatic re-submit sends nothing.
	- An unknown address produces a character-identical confirmation, a `200`, and the same locked form - the enumeration check, asserted on the rendered text rather than on the API alone.
	- `/reset-password` with no token shows the error, renders no form, and offers a link to `/forgot-password`; with a valid token it catches a mismatch and a short password client-side without a request, then redirects to `/login?reset=success` and shows the banner, leaving no stale token behind; the new password signs in and the old one does not; replaying the same link shows the error, removes the form, and offers a new link, and changes nothing.
	- `/account/change-password` sends an anonymous visitor to `/login` with the right `next`; is reachable from the profile page; catches a mismatch client-side with no request; reports a wrong current password after a real call; and on success clears the fields, keeps the session, and swaps which password works.
	- Two bugs in the verification script itself were found and fixed before trusting it: it matched the API on `127.0.0.1` while the apps call `localhost`, which made every "no request fired" assertion vacuous, and it waited on text that exists on both the source and destination page, so a navigation assertion passed before the navigation happened.
- Confirmed the disabled state is real rather than assumed: computed `opacity` settles at `0.6` and `cursor` at `not-allowed` on both the input and the button in both apps. The first screenshot was taken mid-transition and looked unstyled, which is what prompted measuring it.
- Reviewed the rendered screens at 1100px in both apps and removed a redundancy found there: the change-password subtitle repeated the signed-in email already shown in the session bar directly above it.
- Documentation: extended `docs/AUTHENTICATION-FRONTEND.md` with the route table, the anti-enumeration measures, the form lock, the query-string hook and why it exists, the recoverable-versus-not failure split, confirmation fields, field-name translation, and what happens to sessions. Updated `docs/PASSWORD-RECOVERY.md` to point at it, and added a section on the single `FRONTEND_BASE_URL` serving two apps.
- The production database was left untouched: 15 suppliers, 1 user, 1 profile. All verification ran against a scratch copy.

Future goals / still missing:
- Promote the 102 browser checks and the 111 backend checks into a committed suite. They are the closest thing this repo has to tests and they still live in a scratchpad.
- The two apps now duplicate five more files each. The case for moving `types/auth.ts`, `lib/auth.ts`, `lib/auth-storage.ts`, `hooks/useLocationSearch.ts` and the auth components into `packages/shared` is stronger than it was, since a change to the recovery rules now has to be made twice.
- Decide whether the backoffice should own the reset link instead of the website, or whether the API should learn which app a request came from.
- Carried over: rate-limiting `POST /auth/forgot-password`, a "your password was changed" notification, a guard against the console email backend running in production, refresh tokens or revocation, admin-scoping `GET /users`, and `uis/website` having no `.env.local`.

## Update 2026-09-22 (later) - AUTH-03 backend

Goals accomplished:
- Ran the mandatory memory-bank reading sequence before work: `context.md`, `projectbrief.md`, `techContext.md`, `progress.md`.
- Implemented the backend half of AUTH-03, password recovery and change. The frontend half (`/forgot-password`, `/reset-password`, `/account/change-password`) is the next stage and is deliberately not started.
- Before writing anything, restored two uncommitted working-tree edits that reverted earlier auth work, with the developer's confirmation:
	- `pyproject.toml` had lost `libpass[bcrypt]`, `python-jose[cryptography]`, `pydantic-settings` and `python-multipart` from its dependencies, plus the `seed-user` script. The venv still had the packages, so the API ran, but a `uv sync` would have pruned them and broken it.
	- `services/suppliers/db.py` had gone back to opening its own TinyDB handle instead of sharing `services/core/db.py`, which would have put two independent write caches on the same JSON file.
- Three new endpoints on the existing `/auth` router:
	- `POST /auth/forgot-password` - `{email}`, always `200` with an identical body whether or not the address is registered. The send is queued as a FastAPI background task so the two cases cannot be told apart by timing either. A deactivated account is treated exactly like a missing one.
	- `POST /auth/reset-password` - `{token, new_password}`. `400` for every way a token can fail, with one generic message; which way it actually failed goes to the log.
	- `POST /auth/change-password` - `{current_password, new_password}`, bearer token required. `400` on a wrong current password, because the session is fine and only the payload is not.
- Reset tokens are signed JWTs carrying `sub`, `jti`, `typ`, `iat` and `exp`. Signature and expiry are self-contained, so a forged or stale token is refused without a storage lookup.
- Single use is enforced by a `jti` registry in a new TinyDB table, `password_resets`. The row holds the `jti` and never the token, so a leaked database file does not hand anyone a working reset link. Rows past their expiry are purged whenever a new token is issued.
- Added a `typ` claim to every JWT: `access` for sessions, `password_reset` for reset links. Each decoder accepts only its own kind, so a reset link cannot be replayed as a bearer credential and a session cannot reset a password. Tokens minted before the claim existed are read as access tokens, so adding it signed nobody out.
- Setting a password by either route spends every outstanding reset link for that user. A reset link requested and then left unused is a live credential for its whole window, so changing the password has to close it.
- Integrated Resend (`uv add resend`, 2.47.0) in two layers that do not know about each other: `services/core/email.py` is transport only, and `services/auth/emails.py` owns the reset message. The backend is chosen by configuration rather than at the call site - Resend when `RESEND_API_KEY` is set, otherwise a console backend that logs the whole message including the link, so the flow is walkable locally without an email account. Delivery failures are caught and logged, never surfaced, since the send runs after the response.
- The email is one fluid column capped at 600px with inline styles, 16px body text, a 48px-tall button and a plain-text alternative, with no images and no external stylesheet. Verified in a real headless browser at 390px and 800px: no horizontal overflow at either width, button 308x48 on the phone viewport.
- Made the password rule one shared `Password` type in `services/users/models.py` (`Annotated[str, Field(min_length=8, max_length=72)]`) instead of a `Field` instance reused across models, so registration, admin edits, reset and change all validate identically.
- Configuration: four new variables, all documented in a reorganised `.env.example` and in the docs. `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` (default 30) is validated at 15-60 by pydantic-settings, so a value outside the ticket's window fails at startup rather than at the first reset. `RESEND_API_KEY` is never hardcoded; `.env` stays git-ignored.
- Fixed the root `npm run api:suppliers` script, which pointed at `services.suppliers.main:app` - a module that stopped existing when the API gained a composition root. It is now `npm run api` against `services.main:app`, which is what `docs/AUTHENTICATION.md` already told people to run. Added `npm run seed:user`.
- Documentation: new `docs/PASSWORD-RECOVERY.md` (endpoint contract, token design, the Resend integration and its setup, configuration table, a local walkthrough, and an explicit list of what is not covered). Updated `docs/AUTHENTICATION.md` with the three routes, the `400` status code, the new configuration, the `typ` claim, and the module map.
- Executed formatting: `ruff format --line-length 100` reports every file written in this change already clean. The two files it would reformat, `services/server.py` and `services/users/router.py`, were not touched by this work and were left alone.
- Executed linting: `ruff check --select E,F,W,I,B,UP,SIM,C4` finds nothing in the new code beyond two `UP017` suggestions to use `datetime.UTC`; those were declined because the rest of the codebase uses `datetime.now(timezone.utc)` and matching it matters more.
- Executed typechecking: `mypy --ignore-missing-imports` over the changed modules reports one error, `Settings()` missing a named argument in `services/core/config.py`. It is the standard pydantic-settings false positive and predates this work.
- Attempted the test suite; still no `test` script anywhere, unchanged from the 2026-08-23, 2026-09-19, 2026-09-20 and 2026-09-21 entries.
- Verified end to end against a running API on an isolated copy of the database: 111 checks, all passing.
	- HTTP flow (65): registration and sign-in; a known address, an unknown address and a differently-cased address all returning the same `200`; no email composed and no registry row created for an unknown address; the token's subject, type, `jti` and 30-minute lifetime; the registry storing the `jti` and not the token; rejection of a garbage string, a tampered signature, a token signed with another secret, an expired token, an unregistered `jti`, and an access token presented as a reset token, each `400` with the same generic message; a too-short new password `422` without spending the token; the happy path setting the password, the old one ceasing to work and the token being stamped used; a replay `400` that changes nothing; two concurrent links where spending the newer kills the older; change-password rejecting an absent and a malformed bearer token with `401`, a wrong current password with `400`, a short new password with `422`, and succeeding with `200`; a reset link outstanding at change time dead afterwards; reset tokens rejected as sessions by `/auth/me`, `/suppliers` and `/profiles/me`; and, at rest, no plain-text password anywhere, a bcrypt `$2b$12$` hash, user rows carrying no reset state and registry rows carrying no token material.
	- Email integration (46): backend selection for an absent, empty and present API key; the exact Resend payload shape and that the key reaches the SDK; `EmailDeliveryError` wrapping both a provider rejection and an unreachable provider; `send_password_reset_email` returning `False` rather than raising; link construction including the trailing-slash case; the message stating the expiry and single use in both parts; the mobile-readability properties listed above; and the configuration guards at 14, 15, 30, 60 and 61 minutes.
- Audited the two security requirements the developer restated, 26 further checks, all passing:
	- Expiry: a reset token always carries an `exp` 30 minutes out, inside the mandated 15-60 window. Expiry is enforced at two independent gates - the JWT `exp`, and the registry row's `expires_at`. Confirmed a correctly signed but expired token is refused even when its `jti` is live and unused, and that a live JWT whose registry row was forced to look expired is refused too. Neither changes the stored password.
	- Single use: after one successful reset the row is stamped `used_at`, three consecutive replays are refused, and the password is unchanged by all three. The JWT itself is still cryptographically valid at that point, which is the whole reason the registry exists. Siblings die too: with three links outstanding, spending one kills both the older and the newer.
	- Keys: no provider key pattern (`re_`, `SG.`, `sk-`, `AKIA`) appears in any tracked file, and no literal-looking secret assignment does either. `.env` is git-ignored, has never been committed in any branch, and `.env.example` ships `RESEND_API_KEY=` empty. The key is read in exactly one place, as a pydantic-settings field with a `None` default; the only `os.getenv` in the codebase is for `DB_PATH`.
	- Also confirmed that with a provider key present, neither the key, the reset token nor the reset link reaches the log - only an acknowledgement that a send happened. `DUMMY_HASH` in `services/core/security.py` looks like a hardcoded credential but is a fixed bcrypt hash used to equalise login timing; no account uses it.
- The production database was left untouched: 15 suppliers, 1 user, 1 profile. All verification ran against a scratch copy with the developer's account stripped out.

Future goals / still missing:
- Build the AUTH-03 frontend: `/forgot-password`, `/reset-password` reading the token from the query string, and `/account/change-password`. Decide whether it lands in `uis/website`, `uis/backoffice` or both, and point `FRONTEND_BASE_URL` at whichever serves `/reset-password`.
- Set a real `RESEND_API_KEY` and decide the sender. Until a domain is verified with Resend, `onboarding@resend.dev` only delivers to the address that owns the Resend account.
- Guard the console email backend against production. With `RESEND_API_KEY` unset the API logs the full reset link, which is a working credential in the log. That is deliberate and necessary for local development, but nothing currently stops a deployment starting in that mode. An `ENVIRONMENT` setting that refuses to start when it is `production` and no provider key is configured would close it.
- Rate-limit `POST /auth/forgot-password`. Nothing currently stops an attacker filling a real user's inbox with reset links, even though each one is cheap and every previous link dies as soon as one is used.
- Send a "your password was changed" notification, which is the usual way someone discovers an account takeover.
- Consider rejecting a new password identical to the current one, on both routes. Deliberately left out to keep this change on spec.
- Carried over from 2026-09-21 and earlier: no `test` scripts anywhere and the 111 checks above still live in a scratchpad rather than a committed suite; the auth modules are duplicated across the two UIs; there is no refresh-token or revocation story; `GET /users` is still token-only rather than admin-scoped; and `uis/website` has no `.env.local` so `/candidates` cannot load records data locally.

## Update 2026-09-22

Goals accomplished:
- Ran the mandatory memory-bank reading sequence before work: `context.md`, `projectbrief.md`, `techContext.md`, `progress.md`.
- Gave the `uis/backoffice` sign-in and registration screens the same header bar as the signed-in views, so the app no longer changes shape at the door. The wordmark links to `/`, the dashboard.
- Split `BackofficeNav` rather than duplicating it:
	- `components/common/BackofficeNavShell.tsx` holds the bar itself, the wordmark link and the Dashboard / Suppliers / Profile destinations, with an optional `actions` slot. It owns the `usePathname` active-link state and has no session of its own.
	- `components/common/BackofficeNav.tsx` is now a thin session-aware wrapper that fills `actions` with the signed-in email and the Log out button.
	- The auth screens render the shell directly, so they show the destinations without the account controls there is no session to back.
- Added an `app/(auth)/` route group with a layout that renders the shell, and moved `login/` and `register/` into it. Like `(protected)`, the group adds no URL segment, so `/login` and `/register` are unchanged. The two groups now state the app's two modes structurally.
- Removed the centred wordmark `AuthCard` used to draw, since the header bar carries the branding, and tightened the card's vertical padding to suit.
- On the auth screens the destinations still point at protected routes; following one bounces back to `/login` through the existing guard, which is the intended behaviour rather than a dead link.
- `uis/website` needed no change: its sign-in and registration screens already inherit `TrackFlowNav` from the root layout.
- Executed formatting with auto-fix and typechecking in `uis/backoffice`: both clean.
- Attempted the test suite; still no `test` script, unchanged from every prior entry.
- Verified in a real headless browser: 11 checks on the signed-out header (both screens render exactly one header, carrying the wordmark and all three destinations, with no Log out; the wordmark's href is `/` and following it returns to `/login` via the guard) and 6 on the signed-in header against a scratch database (one header, destinations intact, account email and Log out present, suppliers still load with the token, logout still works). All 17 passed.
- The production database was left untouched by this work. It now holds one user, registered by the developer through the UI while reviewing the flows; the verification account went to a scratch copy that was deleted afterwards.

Future goals / still missing:
- Carried over from 2026-09-21: no `test` scripts anywhere, the auth modules are duplicated across the two apps, there is no refresh-token or revocation story, `GET /users` is still token-only rather than admin-scoped, and `uis/website` has no `.env.local` so `/candidates` cannot load records data locally.

## Update 2026-09-21

Goals accomplished:
- Ran the mandatory memory-bank reading sequence before work: `context.md`, `projectbrief.md`, `techContext.md`, `progress.md`.
- Implemented AUTH-02: authentication flows and protected views in both Next.js apps, closing the loop opened by AUTH-01. No separate auth app was created; the flows were integrated into `uis/website` and `uis/backoffice`.
- Identified the views that require a session and protected exactly those:
	- `uis/backoffice`: `/`, `/suppliers`, `/suppliers/[id]`, `/account/profile`.
	- `uis/website`: `/candidates`, `/candidates/[id]`, `/account/profile`.
	- Left public: `uis/website/` (the Milestone 1 corporate page), `/login` and `/register` in both apps, and the static `apps/website`, which is not a Next.js app and was not touched.
- Protection is structural rather than per-page: protected routes moved into an `app/(protected)/` route group whose layout renders `AuthProvider` + `AuthGuard`. Route groups add no URL segment, so every existing path is unchanged, and any route added under that folder is protected by construction.
- The check is client-side on purpose. The token lives in `localStorage` and there is no auth cookie, so Next.js middleware cannot read it. This is recorded in `docs/AUTHENTICATION-FRONTEND.md` so the decision is not re-litigated.
- Authentication views: `/login` (email and password, error message on failure) and `/register` (email, password with confirmation, plus optional `name`/`phone`/`address`). Registration calls `POST /users`, then `POST /auth/login` with the same credentials, stores the token and redirects. A failure between the two steps is reported as "account created, sign in manually" rather than as a registration failure.
- Account view: `/account/profile` shows the email and role from `GET /auth/me` and edits name, phone and address through `PUT /profiles/me`. A cleared input is sent as an explicit null, which is how the API erases a field.
- Token lifecycle centralized: stored under `trackflow.access_token`, attached as `Authorization: Bearer <token>` by `requestAuthenticatedApi`, and cleared on logout. A 401 clears the token and dispatches a `trackflow:unauthorized` event that `AuthProvider` turns into a redirect, so expiry is handled in one place instead of at every call site. The redirect carries a `?next=` path that is sanitized to same-origin absolute paths before use.
- Every `localStorage` access is wrapped in `try`/`catch`, so a browser that blocks site data sends the user to `/login` instead of throwing.
- `uis/backoffice`: all six `/suppliers` calls now go through `requestAuthenticatedApi`. `BackofficeNav` moved into the protected layout and gained the signed-in email, a Profile link and a Log out button, so the sign-in screens render without it.
- `uis/website`: identity calls go through a new `lib/auth-api-client.ts` on `NEXT_PUBLIC_AUTH_API_URL`, separate from `lib/api-client.ts`, because `NEXT_PUBLIC_API_URL` there points at the external records API. Sending the TrackFlow bearer token to that third-party host would leak a credential; verified in the browser that it never receives one.
- The public corporate page stays inert: `TrackFlowNav` remains a server component and gained only a static `Sign in` link. Verified in the browser that loading `/` performs no token read and no identity API call, both anonymously and while signed in.
- `ApiError` now carries the decoded body alongside the flattened message, so forms map failures back onto inputs: a 422 `detail` array becomes per-field messages, a 409 on `POST /users` lands on the email field, and a 401 from login stays a form-level message because the API reports a wrong email and a wrong password identically.
- Executed formatting with auto-fix: `npm run lint -- --fix` in both UIs (clean).
- Executed typechecking: `npm run typecheck` in both UIs (clean). Added the missing `typecheck` script to `uis/website`, which only had `lint`. `npm run build` succeeded for both apps with every route accounted for.
- Attempted the test suite; neither UI has a `test` script, unchanged from the 2026-08-23, 2026-09-19 and 2026-09-20 entries.
- Verified end to end in a real headless browser against a running API on an isolated copy of the database: 57 checks, all passing.
	- Route protection (17): every protected route in both apps redirects an anonymous visitor to `/login` with the correct `next` path, and the public page renders with no token read and no identity call.
	- Token lifecycle in `uis/backoffice` (25): wrong password shows the API message and stores nothing, client-side validation runs before any request, registration validates password length and confirmation, a duplicate email surfaces the 409 on the email field, a real registration creates the account and signs in, the profile loads from `GET /auth/me` and persists through `PUT /profiles/me` across a reload, `/suppliers` and `/profiles/me` carry the bearer header, a tampered token is cleared and redirected, and logout clears the token and closes the protected view again.
	- `uis/website` (15): sign-in honours `?next=`, the session bar shows the signed-in email, the external records API receives no bearer token, the profile view works, and logout clears the token while `/` stays reachable throughout.
- The production database was left untouched: 15 suppliers, 0 users, 0 profiles. All verification ran against a scratch copy.
- Documentation: added `docs/AUTHENTICATION-FRONTEND.md` (protected-view table, route-group layout, the middleware decision, token lifecycle, module layout, form error handling, configuration) and replaced the now-stale "Known follow-up" section in `docs/AUTHENTICATION.md`, which still said the backoffice sent no token.

Future goals / still missing:
- Define and standardize test scripts (`test`) across root and UI packages, and promote the browser verification above into a committed suite. Still outstanding from the 2026-08-23, 2026-09-19 and 2026-09-20 entries.
- The auth modules are duplicated between the two apps because `localStorage` is origin-scoped and `packages/shared` is not wired into either app's build. If a third app appears, move `types/auth.ts`, `lib/auth-storage.ts`, `lib/auth.ts` and the auth components into a shared package.
- Add refresh tokens or a token-revocation story. A token stays valid until it expires, and the UI has no silent-refresh path, so a long session ends with an abrupt redirect to `/login`.
- Decide whether `GET /users` and `GET /users/{id}` should be narrowed to admin and manager, carried over from the 2026-09-20 entry.
- `uis/website` still has no `.env.local`; `NEXT_PUBLIC_AUTH_API_URL` falls back to `http://localhost:8000` and `NEXT_PUBLIC_API_URL` for the records API remains unset, so `/candidates` cannot load data locally.

## Update 2026-09-20

Goals accomplished:
- Ran the mandatory memory-bank reading sequence before work: `context.md`, `projectbrief.md`, `techContext.md`, `progress.md`.
- Implemented AUTH-01: stateless JWT authentication and route protection across the FastAPI service. No session or cookie authentication anywhere; the bearer token is the only credential.
- Restructured the API into a composition root plus domain modules, following `docs/ARCHITECTURE_PROPOSAL.md`:
	- `services/main.py` now owns the FastAPI app and CORS, and mounts every domain router. `services/suppliers/main.py` became `services/suppliers/router.py`.
	- New `services/core/`: `config.py` (pydantic-settings, validated at startup), `db.py` (shared TinyDB handle, one table per domain), `errors.py` (domain errors the routers translate to HTTP), `security.py` (bcrypt + JWT).
	- New `services/users/`, `services/profiles/`, `services/auth/`, each with models, service and router layers.
	- `services/suppliers/db.py` now delegates to `services/core/db.py` and keeps owning the `suppliers` table name, so `seed.py` was unchanged.
- `User` and `Profile` are stored in TinyDB only, in the `users` and `profiles` tables of the existing database file. The `id` is a UUID4 string rather than a TinyDB `doc_id`, because PostgreSQL tables will reference it as `user_uuid`.
- `User` holds credentials only (`id`, `email`, `hashed_password`, `is_active`, `role`, `created_at`); display name and contact data live on the one-to-one `Profile` (`id`, `user_id`, `name`, `phone`, `address`). `Role` is an `Enum` of `admin|manager|user`, so anything else is rejected with 422.
- Endpoints added: `POST/GET/PUT/DELETE /users`, `GET/PUT /profiles/me`, `POST /auth/login`, `POST /auth/token` (OAuth2 form variant that powers the Authorize button in `/docs`), `GET /auth/me`.
- `POST /users` hashes the password, accepts optional `name`/`phone`/`address` and creates the linked profile in the same operation, rolling the user row back if the profile insert fails. It always creates role `user`, so nobody can self-grant privileges at signup. `DELETE /users/{id}` removes the linked profile too.
- Added a reusable `get_current_user` dependency that reads `Authorization: Bearer <token>` via `OAuth2PasswordBearer`, decodes and validates the JWT with `python-jose`, loads the user from TinyDB, and raises 401 on any failure. A valid token for a deactivated account is 403, not 401.
- Protected 13 of 16 routes. Only `POST /users`, `POST /auth/login` and `POST /auth/token` are public. All six existing `/suppliers` routes are now token-protected, which exceeds the minimum of five.
- Authorization rules: `PUT`/`DELETE /users/{id}` are self-or-admin (403 otherwise), `role` and `is_active` are admin-only, and `/profiles/me` is scoped to the caller by construction.
- Passwords use `libpass[bcrypt]` at cost 12, capped at 72 bytes. A login against an unknown email still performs one bcrypt verification so a missing account is not faster to probe.
- Secrets moved to a git-ignored `.env` with a committed `.env.example`: `JWT_SECRET_KEY` (validated at minimum 32 chars), `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`. Nothing is hardcoded.
- Added `uv run seed-user` to bootstrap or promote an admin, since `POST /users` deliberately cannot create one. Renamed the `api:suppliers` npm script to `api`, pointing at `services.main:app`.
- Dependencies added via `uv add`: `libpass[bcrypt]`, `python-jose[cryptography]`, `pydantic-settings`, `python-multipart`.
- Verified with a 74-check end-to-end suite against a running server on an isolated database: registration and duplicate/validation failures, login success and failure, `/profiles/me` scoping and partial updates, ownership and admin rules, deactivated accounts, all six supplier routes rejecting unauthenticated calls, and cascade deletion leaving no orphaned profile rows. Confirmed directly against the stored records that no plain-text password is persisted, that hashes are bcrypt `$2b$` cost 12, and that `User` carries no profile fields.
- Verified 401 on every token failure mode: absent header, malformed token, garbage string, wrong scheme, missing `Bearer` prefix, empty bearer, tampered payload, stripped signature, `alg=none` forgery, a `role` claim escalated to `admin`, a token signed with a different secret, an expired token, and a token whose user has been deleted.
- Confirmed `ACCESS_TOKEN_EXPIRE_MINUTES` is honoured end to end by issuing a token with a one-minute lifetime, checking `exp - iat = 60`, and re-calling the route after it lapsed.
- The production database was left untouched: 15 suppliers, 0 users, 0 profiles. All verification ran against a scratch database.
- Added `docs/AUTHENTICATION.md` covering module layout, endpoint table, status-code contract, configuration, and the manual `/docs` walkthrough.

Future goals / still missing:
- Update `uis/backoffice` to log in and send `Authorization: Bearer <token>`. Its `/suppliers` calls now return 401, which is the expected outcome of this stage and the next piece of work.
- Decide whether `GET /users` and `GET /users/{id}` should be narrowed to admin and manager. They currently require a token only, which is what the ticket specified, but listing every account's email is broad for a plain `user`.
- Add refresh tokens or a token-revocation story. Today a token stays valid until it expires; deactivating a user is caught because `get_current_user` reloads the record on every request, but there is no way to revoke a single token.
- Define and standardize test scripts (`test`) across root and UI packages, and promote the end-to-end auth checks into a committed pytest suite. Still outstanding from the 2026-08-23 and 2026-09-19 entries.
- Refresh `techContext.md`, partially addressed in this update for the auth layer.

## Update 2026-09-19

Goals accomplished:
- Ran the mandatory memory-bank reading sequence before work: `context.md`, `projectbrief.md`, `techContext.md`, `progress.md`.
- Built the Supplier Directory frontend in `uis/backoffice`, consuming the FastAPI service in `services/suppliers` at `http://localhost:8000`:
	- Foundation: `types/supplier.ts` (unions, `SUPPLIER_CATEGORIES`, `CURRENCY_BY_COUNTRY`), `types/async-state.ts`, `lib/api-client.ts`, `lib/supplier.ts` (labels, rate formatting, `validateSupplierForm`, `buildSupplierPayload`, payload normalization), `services/suppliers-service.ts` (list, detail, create, rate patch, status patch).
	- Shared UI and navigation: `components/common/StateMessage.tsx`, new `components/common/BackofficeNav.tsx` (Dashboard / Suppliers with `usePathname` active-link state) wired into `app/layout.tsx`, and `components/suppliers/SupplierStatusBadge.tsx`.
	- List and filters: `app/suppliers/page.tsx` plus `SuppliersListPage`, with server-side country/category filtering persisted to the URL via `router.replace(..., { scroll: false })`, and inline `SupplierRateEditor` / `SupplierStatusToggle` that swap the edited row in state from the API response instead of refetching the list.
	- Create form: `SupplierCreateForm` with client validation first and currency auto-derived from country as a read-only field.
	- Detail view: `app/suppliers/[id]/page.tsx` (Next.js 16 async `params`) plus `SupplierDetailPage`, reusing the same rate editor and status toggle.
	- Configuration: `.env.local` and `.env.example` with `NEXT_PUBLIC_API_URL`, `next dev -p 3001` so the backoffice does not collide with `uis/website` on port 3000, and a new `typecheck` script.
- Centralized FastAPI error handling in `lib/api-client.ts`, covering both response shapes: `{ detail: string }` for 404 and the `{ detail: [{ loc, msg, type }] }` validation array for 422, which is flattened into a readable `field: message` string.
- Executed formatting with auto-fix: `npm run lint -- --fix` in `uis/backoffice` (clean).
- Executed typechecking: `npm run typecheck` in `uis/backoffice` (clean), and `npm run build` succeeded with `/suppliers` prerendered and `/suppliers/[id]` server-rendered on demand.
- Attempted the test suite; `uis/backoffice` still has no `test` script, unchanged from the 2026-08-23 entry.
- Verified the feature end to end against the running API and a headless browser: 15 rows listed, Laser Ship and SAP WM Cloud rendered as suspended, `country=Spain` returning 6 rows and `category=carrier_international` returning 2 without a page reload, supplier creation succeeding and a 422 surfacing in the form, and rate/status edits updating the row immediately and persisting across a refresh. Data created during verification was removed afterwards, leaving the seeded directory at 15 suppliers.

Future goals / still missing:
- Define and standardize test scripts (`test`) across root and UI packages so pre-commit test validation can run consistently.
- Add baseline automated tests for `lib/api-client.ts` error extraction, `lib/supplier.ts` validation and payload building, and the suppliers service layer.
- Refresh `techContext.md`, whose "Current consumed endpoints" section still describes only the `uis/website` `/records` API and does not yet mention the suppliers service or the backoffice UI.
- Decide whether the backoffice needs pagination, supplier deletion, editing of name/country/categories, and authentication, all of which were deliberately excluded from this iteration.

## Update 2026-08-23

Goals accomplished:
- Ran the mandatory memory-bank reading sequence before work: `context.md`, `projectbrief.md`, `techContext.md`, `progress.md`.
- Executed formatting with auto-fix in both UI apps:
	- `npm --prefix uis/website run lint -- --fix`
	- `npm --prefix uis/backoffice run lint -- --fix`
- Executed typechecking in both UI apps:
	- `npx tsc --noEmit` in `uis/website`
	- `npx tsc --noEmit` in `uis/backoffice`
- Attempted to run test suites at root and UI-app levels; no `test` script is currently defined in these `package.json` files.

Future goals / still missing:
- Define and standardize test scripts (`test`) across root and UI packages so pre-commit test validation can run consistently.
- Add at least baseline automated tests (unit/integration) for critical UI service and normalization layers.

## Executive Summary

The project has a solid foundation on the frontend side, with two clear deliverables already implemented:

1. A branded static marketing website in `apps/website` (homepage + signup flow + client-side validation).
2. A functional Next.js operations UI in `uis/website` with candidate and notes CRUD interactions against an external API.

This means the project is already demonstrating product thinking, UI execution, and service-layer integration patterns. However, compared to TrackFlow's business objectives, most logistics-core capabilities (warehouse unification, carrier intelligence, returns automation, CX automation, telemetry, and executive dashboards) are still pending and currently represented mostly as narrative/UX framing rather than implemented business systems.

## Current State by Project Objective

### 1) Unified operational backbone (Mexico + Spain)

Status: In progress (early stage)

What exists:
- A typed frontend architecture that can consume external operational records (`/records` endpoints).
- Normalization utilities in `uis/website/lib/candidate.ts` and `uis/website/lib/notes.ts` that tolerate schema variations.

What's missing:
- No in-repo unified inventory model/API.
- No warehouse, carrier, returns, or ERP integration implementation.
- No shared domain model yet for TrackFlow logistics entities in `packages/shared/types/index.ts` (currently placeholder types only).

### 2) Reduce manual work through automation

Status: Not started for core logistics automation

What exists:
- Form validation and UI workflows (candidate management, notes).
- Signup form validation in `apps/website/signup.js`.

What's missing:
- No implemented automation workflows for order ingestion, carrier selection, tracking updates, return approvals, or weekly executive reports.
- `workflows/` exists structurally but automation pipelines are not implemented.

### 3) Improve decision-making with real-time dashboards

Status: Early UI groundwork

What exists:
- Tracker UI includes list/search/filter and detail views that simulate dashboard-like workflow for candidate operations.
- Branded visual system and reusable status UI component (`StateMessage`).

What's missing:
- No real-time operational KPIs (shipments, delivery performance, returns, CSAT, costs).
- No telemetry/data pipeline feeding dashboards.
- No executive dashboard implementation.

### 4) Increase service quality with proactive support

Status: Not started (business capability)

What exists:
- Branded web presence and contact/signup pathways.

What's missing:
- No bilingual support agent.
- No RAG knowledge base.
- No unified ticketing model.
- No proactive alerting or sentiment analysis.

### 5) Strengthen scalability and resilience

Status: Foundation only

What exists:
- Separation of concerns: static site, Next.js UI, service layer, normalization helpers.
- Strict TypeScript setup in the Next.js UI and clear API abstraction via `api-client.ts`.

What's missing:
- No centralized logging/monitoring.
- No health checks, no alerting, no incident workflow automation.
- No deployment pipeline or runtime observability standards documented.

## Implemented Assets (Verified)

### Product/UI Deliverables

- `apps/website/index.html`
	- Corporate landing page aligned to TrackFlow narrative.
	- SEO/social metadata + structured data.
	- Responsive layout and dark mode toggle.

- `apps/website/signup.html` + `apps/website/signup.js`
	- Multi-section onboarding form mapped to TrackFlow operational realities.
	- Client-side validation with inline feedback and accessibility attributes.

- `uis/website`
	- Candidate list page with load/error/success states.
	- Search + filter by status/stage via query params.
	- Candidate creation modal (POST `/records`).
	- Candidate detail page with:
		- Status/stage update (PATCH `/records/:id`)
		- Full profile update (PUT `/records/:id`)
		- Notes list/create/delete (`/records/:id/notes`)

### Technical Building Blocks

- `uis/website/lib/api-client.ts`
	- Base URL handling and request wrapper.
	- Centralized HTTP error handling.

- `uis/website/lib/candidate.ts`
	- Payload normalization and flexible field mapping.
	- Candidate form validation and data transformation helpers.

- `uis/website/lib/notes.ts`
	- Notes payload normalization from multiple possible API shapes.

- `uis/website/services/*.ts`
	- Service-layer abstraction for API calls.

## Gaps and Risks Right Now

1. Backend dependency risk:
- The tracker requires `NEXT_PUBLIC_API_URL` and external endpoints; without them, flows break at runtime.

2. Domain mismatch risk:
- Current implemented app domain is "talent pipeline" while business objectives focus on logistics operations. The architecture is reusable, but domain alignment work is still needed.

3. Data contract instability risk:
- Extensive field normalization suggests upstream payloads may be inconsistent; this can slow feature velocity and increase bugs.

4. Delivery risk for later milestones:
- No telemetry, pipeline, RAG, agent, or workflow implementations yet, which are required for mid/late milestones.

## Recommended Next Steps

## Phase 1 (Immediate: 3-5 days) - Align scope and unblock end-to-end delivery

1. Define the canonical TrackFlow domain schema in `packages/shared/types/index.ts`.
2. Decide if `uis/website` will be repurposed to logistics entities (recommended) or kept as a separate demo UI.
3. Create a minimal backend contract document for:
	 - inventory
	 - shipments/tracking
	 - carriers
	 - returns
	 - tickets/CX
4. Add `.env.example` and setup docs for `NEXT_PUBLIC_API_URL` and required endpoints.

Deliverable:
- Shared domain types + API contract v1 + environment setup baseline.

## Phase 2 (Near term: 1-2 weeks) - Build first logistics vertical slice

Target one complete workflow first:
- "Shipment tracking and incident visibility"

Scope:
1. Implement backend endpoints (or mock service if backend is out of scope this sprint).
2. Build a Next.js operations page for shipment list/detail and status timeline.
3. Add basic KPIs: on-time %, delayed shipments, incidents by carrier.
4. Add tests for service adapters and normalization logic.

Deliverable:
- One production-like vertical slice that maps directly to TrackFlow operations pain.

## Phase 3 (Following 2-4 weeks) - Add automation + observability

1. Workflow automation (n8n or script-based):
	 - scheduled weekly executive summary generation
	 - alerting for delivery delay thresholds
2. Telemetry baseline:
	 - structured logs
	 - API error-rate tracking
	 - basic health dashboard
3. Returns automation MVP:
	 - rule-based approval engine (start with deterministic rules)

Deliverable:
- First automation and observability capabilities tied to measurable ops impact.

## Phase 4 (After core ops baseline) - AI capabilities

1. RAG knowledge base for CX policies and SOPs (ES/EN).
2. Bilingual support assistant for top repetitive intents (tracking + return status).
3. Carrier recommendation assistant with transparent rule explanations.

Deliverable:
- AI features built on top of stable operational data and workflows.

## Priority Backlog (Ordered)

1. Canonical logistics data model + shared types.
2. Backend/API contract for core entities.
3. First operations dashboard vertical slice.
4. Automated weekly report generation.
5. Telemetry/monitoring foundation.
6. Returns automation MVP.
7. CX RAG + assistant.

## Suggested Definition of "Next Milestone Done"

The next milestone should be considered complete when all of the following are true:

1. At least one logistics workflow is fully functional end-to-end (UI + API + data).
2. KPI widgets are driven by real endpoint data (not static placeholders).
3. Errors and empty states are handled across list/detail views.
4. Minimum test coverage exists for data normalization and service layer logic.
5. A short runbook explains how to run frontend + API + environment locally.
