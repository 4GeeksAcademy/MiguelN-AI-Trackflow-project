"""Response contracts of the task queue endpoints (DEV-55)."""

from typing import Any, Literal

from pydantic import BaseModel

TaskStatusName = Literal["pending", "started", "success", "failure"]


class TaskAccepted(BaseModel):
    """The 202 body of an endpoint that enqueued its work."""

    task_id: str


class TaskStatus(BaseModel):
    """`GET /tasks/{task_id}`: where the task is, and its result once it succeeded.

    `result` stays null until `status` is `success`. A failed task never shows
    its error: it is in the worker's log and the dead letter queue.
    """

    task_id: str
    status: TaskStatusName
    result: Any = None
