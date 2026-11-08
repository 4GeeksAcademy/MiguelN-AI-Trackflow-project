# Async Task Queue (DEV-55)

Heavy work no longer runs inside the API's request cycle. When an endpoint receives a request that involves long-running work, it:

1. validates the request;
2. enqueues a Celery task on Redis;
3. answers `202 Accepted` with `{"task_id": "..."}` at once.

A Celery worker, a separate process that shares nothing with the FastAPI server, runs the task. The client asks for the status, and the result, at `GET /tasks/{task_id}` whenever it likes.

Redis is both the broker (the queue) and the result backend (statuses and results). The API and the worker read the same `REDIS_URL`.

Converted so far: the technical report. `GET /telemetry/report` read the whole requested period into pandas, four times over, inside the request, with no limit on the window. It is now `POST /telemetry/report`. The method changed because enqueueing work is not a safe GET: a retried or prefetched GET would start a second report.

## Running it

```bash
docker compose up -d redis flower   # Redis on localhost:6379, Flower on localhost:5555
npm run worker                      # the worker on the host, reading the root .env
npm run api                         # the API, in another terminal
```

Or run all of it in Docker: `docker compose up --build api worker redis flower db`. Run one worker or the other, never both. They share Redis but read different databases: the host worker uses the `.env` `DATABASE_URL`, and the Compose worker uses the Compose PostgreSQL.

The worker reads the repository's `.env` the way the API does, and validates the same settings at start-up. A missing `JWT_SECRET_KEY` or `DATABASE_URL` stops it before it takes any task.

To stop it:

- **`Ctrl+C` once:** warm shutdown. It takes no new tasks and finishes the ones it is running.
- **`Ctrl+C` again:** cold shutdown. The interrupted tasks are not acknowledged, so Redis delivers them again.
- **`docker compose stop worker`:** the same warm shutdown, with a 60 s grace period.
- **`uv run celery -A services.tasks.celery_app:celery_app control shutdown`:** stops every worker on that Redis.

```bash
curl -i -X POST "http://localhost:8000/telemetry/report?start_date=2026-10-13&end_date=2026-10-20"
# HTTP/1.1 202 Accepted
# location: /tasks/4f0c2b8e-...
# {"task_id":"4f0c2b8e-..."}

curl http://localhost:8000/tasks/4f0c2b8e-...
# {"task_id":"4f0c2b8e-...","status":"success","result":{"period":{...},"metrics":{...}}}
```

## Endpoints

| Endpoint | Answer |
| --- | --- |
| `POST /telemetry/report?start_date=&end_date=` | `202 {"task_id"}` plus a `Location: /tasks/{task_id}` header. `422` for an unreadable or empty period, checked before anything is queued. `503` when Redis is unreachable; Redis is tried for about half a second at most. |
| `GET /tasks/{task_id}` | `200 {"task_id", "status", "result"}`. `404` for an unknown id, or one whose result has expired (after 24 h). `422` for an id that is not a UUID. `503` when Redis is unreachable. |

The report's period rules are unchanged:
- the default is the last seven days;
- a date without an offset is read as UTC;
- `start_date` must be before `end_date`.

The period is resolved when the request arrives, so "the last seven days" means the seven days before the POST, however long the task waits in the queue.

`status` reflects the Celery state:

| Celery state | `status` | `result` |
| --- | --- | --- |
| `SENT` (queued by the API), `RECEIVED` | `pending` | `null` |
| `STARTED`, `RETRY` | `started` | `null` |
| `SUCCESS` | `success` | the report, in the shape the old GET answered |
| `FAILURE`, `REVOKED`, `REJECTED` | `failure` | `null` |

`RETRY` reads as `started` so that a status never moves backwards: the work has begun and is not finished. A failed task never shows its error. The error is in the worker's log and the dead letter queue.

Celery answers `PENDING` for any id it has no record of. The API therefore writes a `SENT` marker for the task before publishing it:
- **Why before:** written after publishing, the marker could overwrite what a fast worker had already recorded.
- **What it gives:** an id with no record is unknown, which is why the endpoint can answer 404.
- **The consequence:** every endpoint that queues work goes through `services.tasks.service.enqueue`. A task sent any other way answers 404 until it starts.

Both endpoints are public, like the report before them. Task ids are random UUID4s, and the report holds aggregates only. The first task with a sensitive result must protect both its POST and `GET /tasks/{task_id}`, for example by recording the caller with the marker.

## Retries and the dead letter queue

A task declares which errors are worth retrying. The report retries only `ReportUnavailable`, which means the database could not be read and nothing was calculated. Any other error is a defect and fails at once.

| Attempt | When |
| --- | --- |
| 1 | when a worker takes the task |
| 2 | 5 s after attempt 1 failed |
| 3 | 10 s after attempt 2 |
| 4 | 20 s after attempt 3 |

- **The backoff:** `max_retries=3` with exponential backoff, `countdown = 5 × 2^n`, capped at 300 s.
- **No jitter:** Celery adds jitter by default, which would randomise the waits instead of making them grow.
- **Time limits:** a report is stopped after 300 s (soft limit, which raises inside the task) and killed after 330 s.

A task that fails for good is recorded in `task_dead_letters`:
- `retries_exhausted` after attempt 4;
- `not_retryable` on the first non-retryable error.

The row is written by the worker's `on_failure` handler. If the database refuses the write (the database being down is the report's usual reason to fail), the record goes to the `tasks.record_dead_letter` recorder task:
- It retries the write from the queue for about five hours: 15 s doubling to 10 min, 36 times.
- If Redis is unreachable as well, every field of the record is logged at ERROR (`status=dead_letter_lost`).
- The task's own failure is never hidden by a failure to record it.

To replay a dead letter, send the same task with the arguments in its row:

```bash
uv run celery -A services.tasks.celery_app:celery_app call telemetry.generate_report \
  --args='["2026-10-13T00:00:00+00:00", "2026-10-20T00:00:00+00:00"]'
```

This runs the report again under a new task id. Its result can be read with `celery result <id>`, or in Flower. `GET /tasks/{id}` answers 404 until the replay starts, because it did not go through `enqueue`.

## `task_dead_letters`

Created by the worker at start-up, and again before every dead letter write if missing (`dead_letters.ensure_schema`, idempotent, under an advisory lock). It lives in `public` next to `job_runs`, in the database of `DATABASE_URL`.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid | Primary key, generated in Python |
| `task_id` | text | Unique. A message delivered again after its failure was recorded (late acknowledgement) is not recorded twice. |
| `task_name` | text | e.g. `telemetry.generate_report` |
| `args`, `kwargs` | jsonb | The task's arguments, enough to replay it |
| `attempt` | integer | The attempt it failed on, ≥ 1: 4 when retries ran out |
| `reason` | text | `retries_exhausted` or `not_retryable` |
| `error_message` | text | `Class: message`. A database error keeps only its class, because a driver message can quote the host. The full message is in the log. |
| `failed_at` | timestamptz | When the task failed, on the worker's clock |
| `recorded_at` | timestamptz | When the row was written, on the database's clock. Later than `failed_at` when the recorder task wrote it. |

- **Index:** `task_dead_letters_failed_at_idx`.
- **RLS:** on, with no policies, like every table the backend owns.
- **Migration:** `services/tasks/migrations/001_create_task_dead_letters.sql` is the same table as SQL, for review and the Supabase SQL editor.

## Configuration

| Setting | Value | Why |
| --- | --- | --- |
| Serializer | JSON only | Nothing a task receives or returns is unpickled. The report's bounds travel as ISO strings. |
| `task_track_started` | on | Gives the `started` status |
| `task_acks_late`, `worker_prefetch_multiplier=1` | on, 1 | A task whose worker dies goes back to the queue. One task at a time per process. |
| `task_send_sent_event`, `worker_send_task_events` | on | Flower sees queued, running and finished tasks |
| `result_expires` | 24 h | Results and `SENT` markers are kept for a day |
| Visibility timeout | 1 h | An unacknowledged message is delivered again after it. Every retry countdown stays below it (a test pins this). |
| Publish retry policy | 2 retries within about 0.5 s; 2 s connect timeout | The API answers 503 instead of holding the request when Redis is down |
| Redis (Compose) | `maxmemory 256mb`, `maxmemory-policy noeviction`, append-only file | A full Redis refuses new tasks instead of silently dropping queued ones, and queued tasks survive a restart |

`REDIS_URL` is read lazily, the first time Celery's configuration is used, so importing the API needs no settings.

## Flower

`docker compose up -d flower`, then http://localhost:5555:
- **Tasks:** queued (`PENDING` in Flower), started, retried, succeeded and failed tasks, with their arguments, attempts and runtimes.
- **Broker:** the depth of the queue.
- **Workers:** each connected worker.

It runs the upstream `mher/flower` image and needs only the broker URL. It is a development tool: it has no authentication, so its port is published on localhost only.

## Logs

The worker writes one line per attempt and outcome on the `tasks.<task name>` loggers, the sibling of the nightly export's format:

```text
2026-10-27T09:14:03.104Z INFO task=telemetry.generate_report task_id=4f0c... attempt=1 status=started duration_ms=- task started
2026-10-27T09:14:03.913Z INFO task=telemetry.generate_report task_id=4f0c... attempt=1 status=success duration_ms=809.2 task finished
2026-10-27T09:20:11.502Z WARNING task=telemetry.generate_report task_id=9b1a... attempt=1 status=retry duration_ms=10012.7 task failed, retrying in 5s: ReportUnavailable: OperationalError <- caused by OperationalError: (psycopg2.OperationalError) connection to server ... failed
2026-10-27T09:20:46.877Z ERROR task=telemetry.generate_report task_id=9b1a... attempt=4 status=failure duration_ms=10009.4 task failed: ReportUnavailable: OperationalError <- caused by OperationalError: ...
2026-10-27T09:20:46.880Z ERROR task=telemetry.generate_report task_id=9b1a... attempt=4 status=dead_letter duration_ms=- moved to the dead letter queue: reason=retries_exhausted error=ReportUnavailable: OperationalError failed_at=... args=[...] kwargs={}
```

- **Fields on every line:** the task, its id, the attempt (1 for the first run), the status the attempt reached and its duration.
- **Retries:** add the countdown to the next attempt and the full error, including every exception it was raised from.
- **Failures:** add the full error and the traceback.
- **Statuses:** `started`, `success`, `retry`, `failure`, `dead_letter`, `dead_letter_lost`.
- **Celery's own lines** (worker start, `Task ... received`) keep Celery's format.

## Known gaps

- **A killed pool process leaves no dead letter.** If the operating system kills the process (out of memory, the hard time limit), Celery does not call `on_failure`, so no row is written. The worker's main process still logs `status=failure` with `attempt=-`. The 300 s soft limit makes a stuck report fail inside the task instead, where it is recorded.
- **A task killed in shutdown comes back late.** A task still running when the 60 s grace of `docker compose stop` ends is delivered again only after the 1 h visibility timeout.
- **A full Redis refuses new tasks.** The enqueue answers 503, and results expire after 24 h. The report window is still uncapped, so a very long period is a large result and a large pandas load.
- **Nothing is deduplicated.** Every POST computes again, because the former 60-second process cache was removed. Identical requests arriving together both run.
- **No endpoint lists dead letters yet.** Read them with SQL, or with `services.tasks.dead_letters.list_dead_letters`.

## Code

| Path | Role |
| --- | --- |
| `services/tasks/celery_app.py` | The Celery app and its configuration; `REDIS_URL` read lazily |
| `services/tasks/base.py` | `TrackedTask` (retries, backoff, dead letters) and the `tasks.record_dead_letter` recorder |
| `services/tasks/dead_letters.py` | The `task_dead_letters` table: `ensure_schema`, `insert_dead_letter`, `list_dead_letters` |
| `services/tasks/migrations/001_create_task_dead_letters.sql` | The table as SQL |
| `services/tasks/service.py` | `enqueue` (marker, then publish) and `get_status` (state mapping) |
| `services/tasks/router.py`, `models.py` | `GET /tasks/{task_id}` and its contracts |
| `services/tasks/signals.py` | Worker start-up (settings check, table, connections across the fork) and per-task logging |
| `services/tasks/logs.py` | The log format |
| `services/telemetry/tasks.py` | `telemetry.generate_report` |
| `services/telemetry/router.py` | `POST /telemetry/report` |
| `docker-compose.yml` | `redis`, `worker`, `flower` |
| `tests/tasks/` | Configuration, dead letter table, task retries and dead letters, enqueue, status endpoint, logs (Celery's in-memory transport; no Redis) |
| `tests/test_telemetry_report_api.py` | The 202 contract and the whole POST → task → GET path |
| `tests/test_task_dead_letters_postgres.py` | Opt-in, with `INVENTORY_TEST_DATABASE_URL`: DDL parity, JSONB, RLS, `ON CONFLICT` |
