"""Task log lines: one per attempt and outcome, with the fields DEV-55 asks for.

Every line names the task, its id, the attempt number (1 for the first run,
2 for the first retry, ...), the status the attempt reached and how long it
took. Failures add the full error message and the traceback.

    2026-10-27T09:14:03.512Z INFO task=telemetry.generate_report task_id=4f0c...
        attempt=1 status=success duration_ms=812.4 task finished

The loggers are `tasks.<task name>`, the sibling of the nightly export's
`jobs.<job name>`, and the format follows its UTC key=value lines.
`configure_task_logging` attaches the handler in the worker; anywhere else
(the API, tests) the records propagate to whatever handlers the root has.
"""

import logging
import sys
import time
from typing import Final, TextIO

LOG_FORMAT: Final = (
    "%(asctime)s %(levelname)s task=%(task_name)s task_id=%(task_id)s "
    "attempt=%(attempt)s status=%(status)s duration_ms=%(duration_ms)s %(message)s"
)
LOG_FIELDS: Final = ("task_name", "task_id", "attempt", "status", "duration_ms")
ROOT_LOGGER: Final = "tasks"


def task_logger(task_name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT_LOGGER}.{task_name}")


def log_fields(
    task_name: str,
    task_id: str | None,
    attempt: int | None,
    status: str,
    duration_ms: float | None = None,
) -> dict[str, str]:
    """The `extra` every task log line carries."""
    return {
        "task_name": task_name,
        "task_id": task_id or "-",
        "attempt": str(attempt) if attempt is not None else "-",
        "status": status,
        "duration_ms": f"{duration_ms:.1f}" if duration_ms is not None else "-",
    }


class _TaskFieldDefaults(logging.Filter):
    """Fill the task fields on records that did not set them, so the format never fails."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name in LOG_FIELDS:
            if not hasattr(record, name):
                setattr(record, name, "-")
        return True


def configure_task_logging(stream: TextIO | None = None) -> None:
    """INFO and above to stderr in the format above, with UTC timestamps.

    Idempotent. The `tasks` logger stops propagating once it has its own
    handler, so a line is not printed a second time in Celery's own format.
    """
    tasks = logging.getLogger(ROOT_LOGGER)
    tasks.setLevel(logging.INFO)
    if any(getattr(handler, "_task_logging", False) for handler in tasks.handlers):
        return
    formatter = logging.Formatter(LOG_FORMAT)
    formatter.converter = time.gmtime
    formatter.default_time_format = "%Y-%m-%dT%H:%M:%S"
    formatter.default_msec_format = "%s.%03dZ"
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(formatter)
    handler.addFilter(_TaskFieldDefaults())
    handler._task_logging = True  # type: ignore[attr-defined]
    tasks.addHandler(handler)
    tasks.propagate = False
