"""Telemetry endpoints: batch ingest and the technical report.

`POST /telemetry/events` is public on purpose: the plan accepts anonymous
pre-login events. Each event is validated on its own and the valid ones are
stored in `telemetry_events`.

`POST /telemetry/report` asks for the operational metrics calculated from
those events. Building them reads the whole period into pandas, so since
DEV-55 the endpoint only validates the period, enqueues the work for the
Celery worker and answers 202 with a `task_id`; `GET /tasks/{task_id}` serves
the report once it is ready. It is unauthenticated at this stage, like the
incident routes: the report holds aggregates only - counts, rates, latencies
per route template - and no user, session or event identifiers.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from services.tasks.models import TaskAccepted
from services.tasks.service import TaskQueueUnavailable, enqueue
from services.telemetry import report, service
from services.telemetry.models import TelemetryBatch, TelemetryReceipt
from services.telemetry.tasks import generate_telemetry_report

MAX_BODY_BYTES = 256 * 1024

logger = logging.getLogger("api.telemetry")

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("/events", response_model=TelemetryReceipt)
async def receive_events(request: Request) -> TelemetryReceipt:
    """Store the valid events of a batch and report what was stored and rejected.

    The raw body is parsed here rather than declared as a model parameter
    because `navigator.sendBeacon` sends `text/plain` to avoid a CORS preflight.
    A batch with rejected events still answers 200: resending it cannot make
    those events valid. Only a storage failure answers 5xx, which the frontend
    retries.
    """
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Batch too large")

    try:
        batch = TelemetryBatch.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()],
        ) from None

    try:
        return await run_in_threadpool(service.ingest, batch.events)
    except service.TelemetryStorageUnavailable as exc:
        logger.error("telemetry storage failed: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Telemetry storage unavailable"
        ) from None


@router.post("/report", status_code=status.HTTP_202_ACCEPTED, response_model=TaskAccepted)
def request_report(
    response: Response,
    start_date: datetime | None = Query(
        default=None,
        description="Start of the period, inclusive, in ISO 8601. Read as UTC without an "
        "offset. Defaults to seven days before end_date.",
    ),
    end_date: datetime | None = Query(
        default=None,
        description="End of the period, exclusive, in ISO 8601. Read as UTC without an "
        "offset. Defaults to now.",
    ),
) -> TaskAccepted:
    """Queue the operational metrics for the period and answer at once with the task id.

    The period is resolved here, at request time, so "the last seven days"
    means the seven days before this request, however long the task waits in
    the queue. An invalid period is refused before anything is queued. Poll
    the `Location` header, `GET /tasks/{task_id}`, for the report. A sync
    route: publishing to Redis blocks.
    """
    try:
        start, end = report.resolve_period(start_date, end_date, now=datetime.now(timezone.utc))
    except report.InvalidPeriod as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            [{"loc": ["query", "start_date"], "msg": str(exc), "type": "value_error"}],
        ) from None

    try:
        task_id = enqueue(generate_telemetry_report, start.isoformat(), end.isoformat())
    except TaskQueueUnavailable as exc:
        logger.error("telemetry report could not be queued: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Task queue unavailable") from None

    response.headers["Location"] = f"/tasks/{task_id}"
    return TaskAccepted(task_id=task_id)
