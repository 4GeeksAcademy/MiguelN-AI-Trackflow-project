"""Worker start-up and per-task logging, through Celery signals.

Start-up (the worker only; the API never starts one):

- `worker_init` runs once in the main worker process, before the pool forks.
  It validates the settings, so a misconfigured worker fails at start rather
  than on its first task, creates `task_dead_letters` if missing, and then
  closes its database connections so no child inherits them.
- `worker_process_init` runs in every pool child after the fork and drops any
  engine inherited anyway, without closing the parent's sockets.

Logging, one line per event, on the `tasks.<task name>` loggers (logs.py):

- `task_prerun`: `status=started` with the attempt number.
- `task_retry`: WARNING `status=retry` with the duration, the countdown to the
  next attempt and the full error.
- `task_failure`: ERROR `status=failure` with the duration, the full error and
  the traceback.
- `task_postrun`: `status=success` with the duration.
"""

import logging
import time
from typing import Any

from celery import Task
from celery.signals import (
    after_setup_logger,
    task_failure,
    task_postrun,
    task_prerun,
    task_retry,
    worker_init,
    worker_process_init,
)
from sqlalchemy.exc import SQLAlchemyError

from services.core.config import get_settings
from services.inventory import database as inventory_database
from services.tasks import dead_letters
from services.tasks.logs import configure_task_logging, log_fields, task_logger

worker_logger = logging.getLogger("tasks.worker")

# perf_counter at the start of each attempt, keyed by (task id, retries): in
# eager mode a retry runs nested inside the attempt that requested it.
_attempt_started: dict[tuple[str, int], float] = {}


def full_error_message(error: BaseException) -> str:
    """`Class: message`, followed by every exception it was raised from.

    For the logs only. The dead letter row keeps the shorter, redacted form
    of `job_runner.service.describe_error`.
    """
    parts = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        detail = " ".join(str(current).split())
        parts.append(f"{type(current).__name__}: {detail}" if detail else type(current).__name__)
        current = current.__cause__ or current.__context__
    return " <- caused by ".join(parts)


def _attempt(task: Task, task_id: str | None) -> int | None:
    """1 for the first run, 2 for the first retry. None outside the task's own process."""
    request = task.request
    if task_id is None or request.id != task_id:
        return None
    return int(request.retries or 0) + 1


def _elapsed_ms(task_id: str | None, attempt: int | None, *, pop: bool) -> float | None:
    if task_id is None or attempt is None:
        return None
    key = (task_id, attempt - 1)
    started = _attempt_started.pop(key, None) if pop else _attempt_started.get(key)
    return None if started is None else (time.perf_counter() - started) * 1000


# --- Worker start-up -----------------------------------------------------------------


@worker_init.connect
def prepare_worker(**_: Any) -> None:
    get_settings()  # ConfigurationError here stops the worker before it consumes anything
    try:
        dead_letters.ensure_schema(inventory_database.get_engine())
    except SQLAlchemyError as error:
        # Not fatal: the dead letter path creates the table again before writing.
        worker_logger.warning(
            "task_dead_letters could not be checked at start-up: %s", type(error).__name__
        )
    finally:
        inventory_database.dispose_engine()


@worker_process_init.connect
def reset_inherited_connections(**_: Any) -> None:
    inventory_database.reset_engine_after_fork()


@after_setup_logger.connect
def setup_task_logging(**_: Any) -> None:
    configure_task_logging()


# --- Per-task logging ----------------------------------------------------------------


@task_prerun.connect
def log_task_started(sender: Task, task_id: str, **_: Any) -> None:
    attempt = _attempt(sender, task_id)
    if attempt is not None:
        _attempt_started[(task_id, attempt - 1)] = time.perf_counter()
    task_logger(sender.name).info(
        "task started", extra=log_fields(sender.name, task_id, attempt, "started")
    )


@task_retry.connect
def log_task_retry(sender: Task, request: Any, reason: Any, **_: Any) -> None:
    task_id = request.id
    attempt = int(request.retries or 0) + 1
    error = getattr(reason, "exc", None) or reason
    task_logger(sender.name).warning(
        "task failed, retrying in %ss: %s",
        getattr(reason, "when", None),
        full_error_message(error),
        extra=log_fields(
            sender.name, task_id, attempt, "retry", _elapsed_ms(task_id, attempt, pop=False)
        ),
    )


@task_failure.connect
def log_task_failure(sender: Task, task_id: str, exception: BaseException, **_: Any) -> None:
    attempt = _attempt(sender, task_id)
    task_logger(sender.name).error(
        "task failed: %s",
        full_error_message(exception),
        exc_info=(type(exception), exception, exception.__traceback__),
        extra=log_fields(
            sender.name, task_id, attempt, "failure", _elapsed_ms(task_id, attempt, pop=False)
        ),
    )


@task_postrun.connect
def log_task_finished(sender: Task, task_id: str, state: str | None = None, **_: Any) -> None:
    attempt = _attempt(sender, task_id)
    duration_ms = _elapsed_ms(task_id, attempt, pop=True)
    if state == "SUCCESS":
        task_logger(sender.name).info(
            "task finished",
            extra=log_fields(sender.name, task_id, attempt, "success", duration_ms),
        )
