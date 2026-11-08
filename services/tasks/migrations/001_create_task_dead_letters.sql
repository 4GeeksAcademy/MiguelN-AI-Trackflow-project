-- 001: the `task_dead_letters` table (ticket DEV-55), in the public schema.
--
-- The Dead Letter Queue of the Celery tasks in services/tasks/: one row per
-- task that failed for good, either after using up its retries
-- (`retries_exhausted`) or on an error not worth retrying (`not_retryable`).
--
-- The same table that `services/tasks/dead_letters.py::ensure_schema` creates
-- when a worker starts, written out for review and for the Supabase SQL
-- editor. Every statement is idempotent, so running it on a database where a
-- worker already created the table changes nothing.
-- `tests/test_task_dead_letters_postgres.py` checks both paths build the same table.
--
-- No BEGIN/COMMIT: the Supabase SQL editor sends the file as one implicit
-- transaction, and the PostgreSQL test runs it inside its own.

CREATE TABLE IF NOT EXISTS task_dead_letters (
    id uuid NOT NULL,
    task_id text NOT NULL,
    task_name text NOT NULL,
    args jsonb NOT NULL,
    kwargs jsonb NOT NULL,
    attempt integer NOT NULL,
    reason text NOT NULL,
    error_message text NOT NULL,
    failed_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    -- A redelivered final failure is recorded once (INSERT ... ON CONFLICT DO NOTHING).
    CONSTRAINT task_dead_letters_task_id_key UNIQUE (task_id),
    CONSTRAINT task_dead_letters_attempt_ck CHECK (attempt >= 1),
    CONSTRAINT task_dead_letters_reason_ck
        CHECK (reason in ('retries_exhausted', 'not_retryable'))
);

-- "What failed recently?"
CREATE INDEX IF NOT EXISTS task_dead_letters_failed_at_idx
    ON task_dead_letters (failed_at);

-- Like every other table the backend owns: RLS on, no policies, so only the
-- database role in DATABASE_URL and the Supabase secret key can reach it.
ALTER TABLE task_dead_letters ENABLE ROW LEVEL SECURITY;
