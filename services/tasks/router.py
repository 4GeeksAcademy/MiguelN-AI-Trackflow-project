"""`GET /tasks/{task_id}`: the status of a task an endpoint enqueued (DEV-55).

Public, like `POST /telemetry/report`, the only endpoint that enqueues today:
a token on this side alone would let an anonymous caller start a report it
could never read. Task ids are random UUID4s and the report holds aggregates
only. The first task whose result is sensitive must protect both its POST
and this GET, for instance by recording the caller with the `SENT` marker.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from services.tasks import service
from services.tasks.models import TaskStatus

logger = logging.getLogger("api.tasks")

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskStatus)
def get_task(task_id: UUID) -> TaskStatus:
    """`pending`, `started`, `success` with the result, or `failure`.

    404 when the id is unknown or its result expired (after 24 hours); 503 when
    Redis cannot be reached. A sync route: reading Redis blocks.
    """
    try:
        state = service.get_status(str(task_id))
    except service.TaskNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found") from None
    except service.TaskQueueUnavailable as exc:
        logger.error("task status unavailable: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Task queue unavailable") from None
    return TaskStatus(task_id=state.task_id, status=state.status, result=state.result)
