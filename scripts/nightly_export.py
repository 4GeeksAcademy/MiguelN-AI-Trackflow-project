#!/usr/bin/env python3
"""Nightly telemetry export (ticket DEV-53): back up a day of telemetry, then run the pipeline.

For the target date (the `TARGET_DATE` environment variable, `YYYY-MM-DD`, or
yesterday in UTC):

1. Export that UTC day of `telemetry_events` to `data/raw/telemetry_<date>.csv`,
   unless the file already exists. The CSV is a backup and audit snapshot;
   the pipeline reads the database, not this file.
2. Run the data pipeline as a subprocess for the last week closed by the end
   of the target date (`python -m data.pipelines.pipeline --week-start ...`).
3. Record the execution in `job_runs` through `services/job_runner`, which
   also enforces one execution at a time and one completed run per day.

Run from anywhere; the OS crontab runs it through `scripts/cron/nightly_export.sh`:

    python scripts/nightly_export.py
    TARGET_DATE=2026-10-20 python scripts/nightly_export.py

Exit status: 0 when the run completed, was a duplicate or found another
execution running; 1 when it failed; 2 when the configuration is invalid.
The design and the scheduling decision are in `docs/NIGHTLY_EXPORT.md`.
"""

import argparse
import csv
import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as clock_time
from pathlib import Path
from types import FrameType
from typing import IO, Any, Final, TextIO

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from data.pipelines.run_log import ABANDON_AFTER
from data.process.weekly_rollup import last_closed_week
from services.core.config import ConfigurationError
from services.inventory.database import dispose_engine, get_engine
from services.job_runner import service as job_runner
from services.telemetry.database import telemetry_events

JOB_NAME: Final = "nightly_export"
ROOT_DIR: Final = Path(__file__).resolve().parents[1]
RAW_DIR: Final = ROOT_DIR / "data" / "raw"

# The table's own column names, in its order: the CSV is a copy of the rows.
CSV_COLUMNS: Final = ("id", "timestamp", "event_type", "payload", "tags", "created_at")

PIPELINE_MODULE: Final = "data.pipelines.pipeline"
# The pipeline's longest possible run (4 attempts of 30 minutes plus 3 retry
# delays of 15), the same bound its run log uses to call a run abandoned.
PIPELINE_TIMEOUT: Final = ABANDON_AFTER
# How long a stopped pipeline gets after SIGTERM to run its crash hook, which
# marks its own `reporting.pipeline_runs` row failed, before SIGKILL.
STOP_GRACE_SECONDS: Final = 30
# How long the processes the pipeline started (Prefect's temporary server) get
# to shut down after SIGTERM before SIGKILL.
LEFTOVER_GRACE_SECONDS: Final = 10

TARGET_DATE_PATTERN: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
LOG_FORMAT: Final = (
    "%(asctime)s %(levelname)s job=%(job_name)s target_date=%(target_date)s "
    "status=%(status)s %(message)s"
)
LOG_FIELDS: Final = ("job_name", "target_date", "status")
# The level field of Prefect's console format: `19:17:44.508 | INFO    | ...`.
PREFECT_LEVEL: Final = re.compile(r"^\S+ \| (DEBUG|INFO|WARNING|ERROR|CRITICAL) +\|")

logger = job_runner.job_logger(JOB_NAME)


class InvalidTargetDate(ValueError):
    """`TARGET_DATE` is malformed, or names a day that has not ended yet in UTC."""


class PipelineTriggerError(RuntimeError):
    """The data pipeline subprocess failed, or did not finish in time."""


class Terminated(BaseException):
    """The process received SIGTERM.

    A `BaseException`, like `KeyboardInterrupt`, so no `except Exception` on
    the way up swallows it: the run is marked `failed` and the pipeline
    subprocess is killed.
    """


@dataclass(frozen=True)
class ExportResult:
    path: Path
    created: bool
    rows: int | None = None  # None when the file already existed


# --- Logging -------------------------------------------------------------------------


class _JobFieldDefaults(logging.Filter):
    """Fill the job fields on records that did not set them, so the format never fails."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name in LOG_FIELDS:
            if not hasattr(record, name):
                setattr(record, name, JOB_NAME if name == "job_name" else "-")
        return True


def configure_logging(stream: TextIO | None = None) -> None:
    """INFO and above to stderr, one line each: UTC timestamp, level, job, date, status.

    Idempotent, so calling `main` twice in one process does not double every line.
    """
    jobs = logging.getLogger("jobs")
    jobs.setLevel(logging.INFO)
    if any(getattr(handler, "_nightly_export", False) for handler in jobs.handlers):
        return
    formatter = logging.Formatter(LOG_FORMAT)
    formatter.converter = time.gmtime
    formatter.default_time_format = "%Y-%m-%dT%H:%M:%S"
    formatter.default_msec_format = "%s.%03dZ"
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(formatter)
    handler.addFilter(_JobFieldDefaults())
    handler._nightly_export = True  # type: ignore[attr-defined]
    jobs.addHandler(handler)


def _log(level: int, target_date: date | None, status: str, message: str, *args: object) -> None:
    logger.log(level, message, *args, extra=job_runner.log_fields(JOB_NAME, target_date, status))


# --- Target date ---------------------------------------------------------------------


def resolve_target_date(
    environ: Mapping[str, str] = os.environ, now: datetime | None = None
) -> date:
    """`TARGET_DATE` when set (strictly `YYYY-MM-DD`), otherwise yesterday in UTC.

    Only a day that has already ended in UTC can be exported completely, so
    today and future dates are refused.
    """
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    raw = environ.get("TARGET_DATE", "").strip()
    if not raw:
        return today - timedelta(days=1)
    if not TARGET_DATE_PATTERN.fullmatch(raw):
        raise InvalidTargetDate("TARGET_DATE must be a date in YYYY-MM-DD format")
    try:
        target = date.fromisoformat(raw)
    except ValueError:
        raise InvalidTargetDate("TARGET_DATE is not a valid calendar date") from None
    if target >= today:
        raise InvalidTargetDate("TARGET_DATE must be a day that has already ended in UTC")
    return target


def day_bounds(target_date: date) -> tuple[datetime, datetime]:
    """`[00:00 UTC of target_date, 00:00 UTC of the next day)`."""
    start = datetime.combine(target_date, clock_time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def pipeline_week(target_date: date) -> date:
    """The Monday of the last ISO week that had closed by the end of `target_date`.

    For a Sunday, the week that Sunday ends; for any other day, the week before.
    """
    return last_closed_week(day_bounds(target_date)[1])


# --- Export --------------------------------------------------------------------------


def export_path(target_date: date, output_dir: Path | None = None) -> Path:
    return (output_dir or RAW_DIR) / f"telemetry_{target_date.isoformat()}.csv"


@contextmanager
def open_event_stream(engine: Engine, target_date: date) -> Iterator[Iterable[Mapping[Any, Any]]]:
    """The day's events, oldest first, streamed from one `READ ONLY` transaction.

    Read with SQLAlchemy over `DATABASE_URL`, like the pipeline's extract:
    PostgREST's default 1000-row limit would silently truncate a busy day.
    The day is bounded on `timestamp`, the event's own time, as in
    `services/telemetry/analysis.py`, so the `timestamp` index serves it.
    """
    start, end = day_bounds(target_date)
    query = (
        select(*(telemetry_events.c[name] for name in CSV_COLUMNS))
        .where(telemetry_events.c.timestamp >= start, telemetry_events.c.timestamp < end)
        .order_by(telemetry_events.c.timestamp, telemetry_events.c.id)
    )
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        yield connection.execution_options(yield_per=1000).execute(query).mappings()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def write_csv(rows: Iterable[Mapping[Any, Any]], path: Path) -> int:
    """Write the rows with a header row; return how many rows were written.

    Timestamps are ISO 8601 in UTC; `payload` and `tags` are compact JSON with
    sorted keys, so the same rows always give the same bytes.
    """
    count = 0
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(
                (
                    str(row["id"]),
                    _iso(row["timestamp"]),
                    row["event_type"],
                    _json(row["payload"]),
                    _json(row["tags"]),
                    _iso(row["created_at"]),
                )
            )
            count += 1
    return count


def export_telemetry(
    engine: Engine, target_date: date, output_dir: Path | None = None
) -> ExportResult:
    """Export the day to CSV unless its file exists already.

    The rows go to a hidden `.partial` file that is renamed into place only
    once complete, so a run killed mid-export never leaves a truncated file
    that the next run would take for a finished export.
    """
    path = export_path(target_date, output_dir)
    if path.exists():
        return ExportResult(path, created=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.partial")
    try:
        with open_event_stream(engine, target_date) as rows:
            count = write_csv(rows, partial)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    return ExportResult(path, created=True, rows=count)


# --- Pipeline ------------------------------------------------------------------------


def pipeline_command(week_start: date) -> list[str]:
    return [sys.executable, "-m", PIPELINE_MODULE, "--week-start", week_start.isoformat()]


def _text(output: str | bytes | None) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""


def _last_line(output: str | bytes | None) -> str:
    lines = [line.strip() for line in _text(output).splitlines() if line.strip()]
    return lines[-1] if lines else ""


def forward_pipeline_log(output: str | bytes | None, target_date: date | None) -> None:
    """Re-log the pipeline's stderr line by line, so every line names the job and its status.

    Each line keeps the level Prefect gave it (`HH:MM:SS.mmm | ERROR   | ...`).
    A Python traceback is an error; any other line without a level keeps the
    previous line's.
    """
    level = logging.INFO
    for line in _text(output).splitlines():
        if not line.strip():
            continue
        found = PREFECT_LEVEL.search(line)
        if found:
            level = logging.getLevelNamesMapping()[found.group(1)]
        elif line.startswith("Traceback (most recent call last)"):
            level = logging.ERROR
        _log(level, target_date, "processing", "pipeline | %s", line.rstrip())


def _signal_group(process_group: int, signum: int) -> None:
    """Signal every process in the group; a group that has already exited is fine."""
    with suppress(ProcessLookupError):
        os.killpg(process_group, signum)


def _group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_pipeline(process: subprocess.Popen[bytes]) -> None:
    """Stop the pipeline and everything it started, in the order that keeps its run log right.

    1. SIGTERM to the pipeline alone. Prefect turns it into a crashed flow
       run and calls the crash hook, which marks the pipeline's own
       `reporting.pipeline_runs` row failed. That needs Prefect's temporary
       API server, which the pipeline CLI runs as a separate process.
    2. SIGTERM to the rest of its process group: that server, and anything
       else the pipeline left behind.
    3. SIGKILL to whatever is still there after the grace periods.
    """
    with suppress(ProcessLookupError):
        process.send_signal(signal.SIGTERM)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=STOP_GRACE_SECONDS)
    _signal_group(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + LEFTOVER_GRACE_SECONDS
    while _group_exists(process.pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    _signal_group(process.pid, signal.SIGKILL)
    process.wait()


def _read(output: IO[bytes]) -> str:
    output.seek(0)
    return output.read().decode("utf-8", errors="replace")


def trigger_pipeline(week_start: date, target_date: date | None = None) -> dict[str, Any] | None:
    """Run the pipeline CLI for one closed week and return its JSON summary.

    The child inherits the environment, so it reads the same `.env` and
    `DATABASE_URL`. It runs in its own process group, with its output in
    temporary files rather than pipes, so its exit is noticed even if a
    process it started outlives it. Its stderr (Prefect's log) is re-logged
    through this job's logger once it exits.

    Exit 0 includes the pipeline skipping itself because another run holds
    its slot, which it reports without a summary. Any other exit status, or no
    exit within `PIPELINE_TIMEOUT`, raises `PipelineTriggerError`. If this
    process is interrupted (SIGTERM, Ctrl-C), the pipeline is stopped first.
    """
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            pipeline_command(week_start),
            cwd=ROOT_DIR,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=PIPELINE_TIMEOUT.total_seconds())
        except subprocess.TimeoutExpired:
            stop_pipeline(process)
            forward_pipeline_log(_read(stderr), target_date)
            hours = PIPELINE_TIMEOUT.total_seconds() / 3600
            raise PipelineTriggerError(
                f"The data pipeline did not finish within {hours:g} hours and was stopped"
            ) from None
        except BaseException:
            stop_pipeline(process)
            forward_pipeline_log(_read(stderr), target_date)
            raise
        # Anything the pipeline left running (normally nothing) goes with it.
        _signal_group(process.pid, signal.SIGTERM)
        log, summary = _read(stderr), _read(stdout)

    forward_pipeline_log(log, target_date)
    if returncode != 0:
        detail = _last_line(log)
        raise PipelineTriggerError(
            f"The data pipeline exited with status {returncode}" + (f": {detail}" if detail else "")
        )
    return parse_summary(summary)


def parse_summary(stdout: str | None) -> dict[str, Any] | None:
    """The JSON object the pipeline prints last on stdout (indented), or None."""
    lines = (stdout or "").splitlines()
    starts = [number for number, line in enumerate(lines) if line.startswith("{")]
    if not starts:
        return None
    try:
        summary = json.loads("\n".join(lines[starts[-1] :]))
    except ValueError:
        return None
    return summary if isinstance(summary, dict) else None


def _display(path: Path) -> str:
    return str(path.relative_to(ROOT_DIR)) if path.is_relative_to(ROOT_DIR) else str(path)


def run_nightly_export(engine: Engine, target_date: date, output_dir: Path | None = None) -> None:
    """The job's work: the export, then the pipeline. Any failure propagates to `run_job`."""
    export = export_telemetry(engine, target_date, output_dir)
    if export.created:
        _log(
            logging.INFO,
            target_date,
            "processing",
            "Exported %d events to %s",
            export.rows,
            _display(export.path),
        )
    else:
        _log(
            logging.INFO,
            target_date,
            "processing",
            "%s already exists; export skipped",
            _display(export.path),
        )

    week_start = pipeline_week(target_date)
    _log(
        logging.INFO,
        target_date,
        "processing",
        "Running the data pipeline for the week of %s",
        week_start.isoformat(),
    )
    summary = trigger_pipeline(week_start, target_date)
    if summary is None:
        _log(
            logging.INFO,
            target_date,
            "processing",
            "The data pipeline exited without a summary (it skipped itself)",
        )
    else:
        _log(
            logging.INFO,
            target_date,
            "processing",
            "The data pipeline finished: status=%s weeks=%s events_read=%s "
            "rows inserted/updated/unchanged/deleted=%s/%s/%s/%s",
            summary.get("status"),
            ",".join(summary.get("weeks") or []),
            summary.get("events_read"),
            summary.get("rows_inserted"),
            summary.get("rows_updated"),
            summary.get("rows_unchanged"),
            summary.get("rows_deleted"),
        )


# --- Command line --------------------------------------------------------------------


def _raise_terminated(signum: int, frame: FrameType | None) -> None:
    # Once is enough: a second SIGTERM must not interrupt marking the run failed.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise Terminated(f"received {signal.Signals(signum).name}")


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        description="Export yesterday's telemetry (or TARGET_DATE's) to CSV and run the "
        "data pipeline, recording the execution in job_runs."
    ).parse_args(argv)
    configure_logging()

    try:
        target_date = resolve_target_date()
    except InvalidTargetDate as error:
        _log(logging.ERROR, None, "failed", "%s", error)
        return 2

    previous_handler = signal.signal(signal.SIGTERM, _raise_terminated)
    try:
        try:
            engine = get_engine()
            job_runner.ensure_schema(engine)
        except ConfigurationError as error:
            _log(logging.ERROR, target_date, "failed", "%s", error)
            return 2
        except SQLAlchemyError as error:
            _log(
                logging.ERROR,
                target_date,
                "failed",
                "Cannot prepare job_runs: %s",
                job_runner.describe_error(error),
            )
            return 1

        # Logged and recorded by run_job; only the exit status is decided here.
        try:
            job_runner.run_job(
                engine, JOB_NAME, target_date, lambda: run_nightly_export(engine, target_date)
            )
        except Terminated:
            return 143
        except KeyboardInterrupt:
            return 130
        except Exception:  # noqa: BLE001
            return 1
        return 0
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        dispose_engine()


if __name__ == "__main__":
    sys.exit(main())
