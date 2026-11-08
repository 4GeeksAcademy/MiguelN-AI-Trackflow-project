"""The Celery application, with Redis as broker and result backend (DEV-55).

The API imports it to enqueue work; the worker is a separate process started
from it:

    celery -A services.tasks.celery_app:celery_app worker --loglevel=INFO

`REDIS_URL` is read lazily, the first time the configuration is used, through
`add_defaults`. Importing the API therefore never validates the settings,
which keeps test collection independent of `.env`. Values set explicitly with
`conf.update` win over those defaults, which is how the tests point the app at
Celery's in-memory transport without any Redis.
"""

from datetime import timedelta
from typing import Final

from celery import Celery

from services.core.config import get_settings

# Modules holding tasks. The worker imports them at start; the API imports the
# ones it enqueues along with its routers.
TASK_MODULES: Final = ("services.tasks.base", "services.telemetry.tasks")

# Seconds. The Redis transport redelivers an unacknowledged message after this
# long, so every retry countdown must stay well under it (see base.py).
VISIBILITY_TIMEOUT: Final = 3600

celery_app = Celery("trackflow", include=list(TASK_MODULES))

celery_app.conf.update(
    # JSON only: nothing a task receives or returns is ever unpickled.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_accept_content=["json"],
    enable_utc=True,
    timezone="UTC",
    # Record STARTED, so GET /tasks/{id} can tell a running task from a queued one.
    task_track_started=True,
    # Acknowledge when a task ends, not when it arrives: a task whose worker
    # dies mid-run goes back to the queue instead of vanishing. One message at
    # a time per worker process, so a long report does not hold others back.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    broker_connection_retry_on_startup=True,
    # Events for Flower: `task-sent` shows queued tasks; the worker's events
    # show received, started, retried, succeeded and failed ones.
    task_send_sent_event=True,
    worker_send_task_events=True,
    # Finished results (and the API's `SENT` markers) stay readable for a day.
    result_expires=timedelta(hours=24),
    # Fail fast when Redis is unreachable, so the API answers 503 instead of
    # holding the request through Celery's default 3 retries per second.
    task_publish_retry_policy={
        "max_retries": 2,
        "interval_start": 0,
        "interval_step": 0.5,
        "interval_max": 1,
    },
    broker_transport_options={
        "socket_connect_timeout": 2,
        "visibility_timeout": VISIBILITY_TIMEOUT,
    },
    redis_socket_connect_timeout=2,
    redis_socket_timeout=10,
)


def _connection_settings() -> dict[str, str]:
    url = get_settings().redis_url.get_secret_value()
    return {"broker_url": url, "result_backend": url}


celery_app.add_defaults(_connection_settings)

# Signal handlers: worker start-up, per-task logging. Imported last because
# they reference this module's app.
from services.tasks import signals  # noqa: E402,F401
