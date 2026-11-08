"""The technical report as a Celery task (DEV-55).

`POST /telemetry/report` resolves and validates the period, then enqueues
this task and answers 202 with its id. A worker builds the report, and
`GET /tasks/{task_id}` serves it from the result backend once it is done.

The bounds travel as ISO 8601 strings: the JSON serializer would turn a
datetime into a tagged object. `ReportUnavailable` is the only error worth
retrying - the database could not be read, and nothing was calculated - so it
is retried with backoff; anything else is a defect and fails at once. Either
way a final failure lands in the dead letter queue (services/tasks/base.py).
"""

from datetime import datetime
from typing import Any, Final

from services.tasks.base import TrackedTask
from services.tasks.celery_app import celery_app
from services.telemetry import report

TASK_NAME: Final = "telemetry.generate_report"

# A soft limit raises inside the task, so a stuck report still ends in the
# dead letter queue; the hard limit kills the process 30 seconds later.
SOFT_TIME_LIMIT_SECONDS: Final = 300
TIME_LIMIT_SECONDS: Final = 330


@celery_app.task(
    name=TASK_NAME,
    base=TrackedTask,
    autoretry_for=(report.ReportUnavailable,),
    soft_time_limit=SOFT_TIME_LIMIT_SECONDS,
    time_limit=TIME_LIMIT_SECONDS,
)
def generate_telemetry_report(start: str, end: str) -> dict[str, Any]:
    """The report for `[start, end)`, as the JSON `GET /telemetry/report` used to answer."""
    built = report.build_report(datetime.fromisoformat(start), datetime.fromisoformat(end))
    return built.model_dump(mode="json", by_alias=True)
