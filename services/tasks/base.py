"""`TrackedTask`: automatic retries with exponential backoff, and the Dead Letter Queue.

A task built on it declares which errors are worth retrying
(`autoretry_for`, on the task decorator). Those are retried up to
`max_retries = 3` times, waiting 5, 10 and 20 seconds: `countdown = 5 * 2**n`,
without jitter, which Celery adds by default and which would make the waits
random instead of increasing. Any other error fails at once.

When a task fails for good, `on_failure` records a dead letter
(`dead_letters.py`):

- `retries_exhausted`: the error was retryable and the last retry failed too.
  Celery raises the original error then, not `MaxRetriesExceededError`, so the
  reason is read from the error type and the retry count.
- `not_retryable`: anything else, such as a bug or bad input.

`attempt` is the attempt the task failed on: 4 for an exhausted task, the
first run plus three retries.

The most likely cause of a failed task is the database being down, which is
also where dead letters are written. So a dead letter that cannot be written
is handed to the `tasks.record_dead_letter` recorder task, which keeps trying
for about five hours from the queue. If Redis is unreachable as well, the
complete record is logged at ERROR, the only place left. `on_failure` never
raises: a failure to record must not hide the task's own failure.
"""

import logging
from datetime import UTC, datetime
from typing import Any, Final

from celery import Task
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from services.inventory import database as inventory_database
from services.job_runner.service import describe_error
from services.tasks import dead_letters
from services.tasks.celery_app import celery_app
from services.tasks.dead_letters import DeadLetter, DeadLetterReason
from services.tasks.logs import log_fields, task_logger

MAX_RETRIES: Final = 3
BACKOFF_SECONDS: Final = 5
BACKOFF_MAX_SECONDS: Final = 300

RECORDER_NAME: Final = "tasks.record_dead_letter"
# 15 s doubling up to 10 minutes, 36 times: about five hours of retries.
RECORDER_BACKOFF_SECONDS: Final = 15
RECORDER_BACKOFF_MAX_SECONDS: Final = 600
RECORDER_MAX_RETRIES: Final = 36
# Every countdown stays under celery_app.VISIBILITY_TIMEOUT, or Redis would
# deliver the waiting message a second time (pinned by tests/tasks).


def dead_letter_engine() -> Engine:
    """The database dead letters go to: the backend's own `DATABASE_URL`. Replaced in tests."""
    return inventory_database.get_engine()


def write_dead_letter(letter: DeadLetter) -> bool:
    """Insert the row, creating the table first if a worker could not at start-up."""
    engine = dead_letter_engine()
    dead_letters.ensure_schema(engine)
    return dead_letters.insert_dead_letter(engine, letter)


def _log_letter(level: int, letter: DeadLetter, status: str, message: str) -> None:
    task_logger(letter.task_name).log(
        level,
        "%s: reason=%s error=%s failed_at=%s args=%s kwargs=%s",
        message,
        letter.reason,
        letter.error_message,
        letter.failed_at.isoformat(),
        letter.args,
        letter.kwargs,
        extra=log_fields(letter.task_name, letter.task_id, letter.attempt, status),
    )


def send_to_dead_letter_queue(letter: DeadLetter) -> None:
    """Write the dead letter now, or hand it to the recorder task, or log it whole."""
    try:
        inserted = write_dead_letter(letter)
    except Exception as error:  # the database, or settings that cannot reach it
        task_logger(letter.task_name).warning(
            "dead letter not written (%s); queued for the recorder task",
            type(error).__name__,
            extra=log_fields(letter.task_name, letter.task_id, letter.attempt, "dead_letter"),
        )
    else:
        _log_letter(
            logging.ERROR,
            letter,
            "dead_letter",
            "moved to the dead letter queue" if inserted else "dead letter already recorded",
        )
        return

    try:
        record_dead_letter.apply_async(args=[letter.to_message()])
    except Exception as error:  # Redis is unreachable as well
        _log_letter(
            logging.ERROR,
            letter,
            "dead_letter_lost",
            f"dead letter could not be recorded or queued ({type(error).__name__})",
        )


class TrackedTask(Task):
    """Base class for tasks that retry with exponential backoff and end in the DLQ."""

    max_retries = MAX_RETRIES
    retry_backoff = BACKOFF_SECONDS
    retry_backoff_max = BACKOFF_MAX_SECONDS
    retry_jitter = False

    def dead_letter_reason(self, exc: BaseException) -> DeadLetterReason:
        retryable = isinstance(exc, tuple(getattr(self, "autoretry_for", ())))
        retries = int(self.request.retries or 0)
        if retryable and self.max_retries is not None and retries >= self.max_retries:
            return "retries_exhausted"
        return "not_retryable"

    def on_failure(self, exc: Exception, task_id: str, args: Any, kwargs: Any, einfo: Any) -> None:
        try:
            letter = DeadLetter(
                task_id=task_id,
                task_name=self.name,
                args=list(args or []),
                kwargs=dict(kwargs or {}),
                attempt=int(self.request.retries or 0) + 1,
                reason=self.dead_letter_reason(exc),
                error_message=describe_error(exc),
                failed_at=datetime.now(UTC),
            )
            send_to_dead_letter_queue(letter)
        except Exception:
            logging.getLogger("tasks.worker").exception(
                "dead letter handling failed for task %s", task_id
            )


class DeadLetterRecorder(Task):
    """The recorder's own last resort: log the record it could not write."""

    def on_failure(self, exc: Exception, task_id: str, args: Any, kwargs: Any, einfo: Any) -> None:
        try:
            letter = DeadLetter.from_message(args[0])
        except Exception:
            logging.getLogger("tasks.worker").error(
                "dead letter recorder failed on an unreadable record: %r", args
            )
            return
        _log_letter(
            logging.ERROR,
            letter,
            "dead_letter_lost",
            f"dead letter could not be recorded ({describe_error(exc)})",
        )


@celery_app.task(
    name=RECORDER_NAME,
    base=DeadLetterRecorder,
    autoretry_for=(SQLAlchemyError,),
    max_retries=RECORDER_MAX_RETRIES,
    retry_backoff=RECORDER_BACKOFF_SECONDS,
    retry_backoff_max=RECORDER_BACKOFF_MAX_SECONDS,
    retry_jitter=False,
)
def record_dead_letter(message: dict[str, Any]) -> bool:
    """Write a dead letter the failing task could not. True when a row was inserted."""
    return write_dead_letter(DeadLetter.from_message(message))
