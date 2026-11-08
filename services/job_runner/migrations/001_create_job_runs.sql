-- 001: the `job_runs` table (ticket DEV-53), in the public schema.
--
-- The same table that `services/job_runner/service.py::ensure_schema` creates
-- before every nightly run, written out for review and for the Supabase SQL
-- editor. Every statement is idempotent, so running it on a database where
-- the script already created the table changes nothing.
-- `tests/test_job_runner_postgres.py` checks both paths build the same table.
--
-- One row per execution of a scheduled job: pending -> processing ->
-- completed | failed. Kept apart from `reporting.pipeline_runs`, which
-- records what a pipeline computed, not the job that started it.
--
-- No BEGIN/COMMIT: the Supabase SQL editor sends the file as one implicit
-- transaction, and the PostgreSQL test runs it inside its own.

CREATE TABLE IF NOT EXISTS job_runs (
    id uuid NOT NULL,
    job_name text NOT NULL,
    target_date date NOT NULL,
    status text NOT NULL,
    started_at timestamptz,
    finished_at timestamptz,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT job_runs_status_ck
        CHECK (status in ('pending', 'processing', 'completed', 'failed')),
    CONSTRAINT job_runs_started_ck
        CHECK (status in ('pending', 'failed') or started_at is not null),
    CONSTRAINT job_runs_finished_ck
        CHECK ((status in ('completed', 'failed')) = (finished_at is not null)),
    CONSTRAINT job_runs_error_message_ck
        CHECK (status <> 'failed' or error_message is not null)
);

-- Per-day idempotency: "has this job already completed for this date?"
CREATE INDEX IF NOT EXISTS job_runs_job_name_target_date_idx
    ON job_runs (job_name, target_date);

-- The processing lock: at most one pending or processing row per job.
CREATE UNIQUE INDEX IF NOT EXISTS job_runs_one_active_idx
    ON job_runs (job_name) WHERE status in ('pending', 'processing');

-- Like every other table the backend owns: RLS on, no policies, so only the
-- database role in DATABASE_URL and the Supabase secret key can reach it.
ALTER TABLE job_runs ENABLE ROW LEVEL SECURITY;
