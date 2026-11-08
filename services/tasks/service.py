"""Enqueue a task and read its status: the API's side of the task queue (DEV-55).

`enqueue` writes a `SENT` marker in the result backend before it publishes
the message. Celery answers `PENDING` for any id it has no record of, so
without the marker an unknown id and a queued task would look the same.
Written before publishing, the marker can never overwrite what the worker
records afterwards (STARTED, RETRY, SUCCESS, FAILURE); written after, it
could. The worker's own retries do not pass through here, so they never touch
it, and it expires with the result (`result_expires`, 24 hours).

So an id with no record is unknown or expired, and answers 404. A task sent
without `enqueue` would also answer 404 until it starts: every endpoint that
hands work to the queue uses `enqueue`.
"""

import logging
from dataclasses import dataclass
from typing import Any, Final, cast
from uuid import uuid4

from celery import Task
from kombu.exceptions import OperationalError as BrokerError
from redis.exceptions import RedisError

from services.tasks.celery_app import celery_app
from services.tasks.models import TaskStatusName

SENT: Final = "SENT"

# What the client sees for each Celery state. RETRY reads as started: the work
# has begun and is not finished, so a status never moves backwards.
STATUS_BY_STATE: Final[dict[str, TaskStatusName]] = {
    SENT: "pending",
    "RECEIVED": "pending",
    "STARTED": "started",
    "RETRY": "started",
    "SUCCESS": "success",
    "FAILURE": "failure",
    "REVOKED": "failure",
    "REJECTED": "failure",
}

# Redis refused or dropped the connection, or the broker gave up publishing.
QUEUE_ERRORS: Final = (RedisError, BrokerError, OSError)

logger = logging.getLogger("api.tasks")


class TaskQueueUnavailable(RuntimeError):
    """Redis could not be reached; nothing was enqueued or read."""


class TaskNotFound(LookupError):
    """No record of the task id: never enqueued, or its result has expired."""


@dataclass(frozen=True)
class TaskState:
    task_id: str
    status: TaskStatusName
    result: Any


def enqueue(task: Task, *args: Any) -> str:
    """Publish `task(*args)` and return its id at once, without waiting for it."""
    task_id = str(uuid4())
    try:
        task.backend.store_result(task_id, None, SENT)
        task.apply_async(args=list(args), task_id=task_id)
    except QUEUE_ERRORS as error:
        _forget(task, task_id)
        raise TaskQueueUnavailable(type(error).__name__) from error
    return task_id


def _forget(task: Task, task_id: str) -> None:
    """Drop the marker of a task that was never published; a leftover only expires."""
    try:
        task.backend.forget(task_id)
    except QUEUE_ERRORS:
        pass


def get_status(task_id: str) -> TaskState:
    try:
        meta = celery_app.backend.get_task_meta(task_id)
    except QUEUE_ERRORS as error:
        raise TaskQueueUnavailable(type(error).__name__) from error

    state = str(meta.get("status"))
    if state == "PENDING":
        raise TaskNotFound(task_id)
    status = STATUS_BY_STATE.get(state)
    if status is None:
        logger.warning("task %s is in unexpected state %s; reported as pending", task_id, state)
        status = cast(TaskStatusName, "pending")
    result = meta.get("result") if status == "success" else None
    return TaskState(task_id=task_id, status=status, result=result)
