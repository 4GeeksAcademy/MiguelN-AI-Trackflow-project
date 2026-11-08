"""Start a manual run through the Prefect deployment (`PIPELINE_DESIGN.md` 5.2).

`POST /reporting/pipeline-runs` calls `trigger_run`, which asks Prefect to
schedule a flow run of the `weekly` deployment and returns without waiting.
The run itself executes wherever the deployment is served
(`python data/pipelines/pipeline.py --serve`), never inside the API process,
and goes through the same concurrency limit as the Monday schedule.

Prefect is imported lazily, so the API starts and serves its read endpoints
without it.
"""

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final
from uuid import UUID

from data.pipelines.weekly_warehouse_client_performance.schema import PIPELINE_NAME

if TYPE_CHECKING:
    from prefect.client.schemas.objects import FlowRun

# Prefect names a flow after its function with `_` turned into `-`.
FLOW_NAME: Final = "weekly-warehouse-client-performance"
DEPLOYMENT_NAME: Final = "weekly"
DEPLOYMENT: Final = f"{FLOW_NAME}/{DEPLOYMENT_NAME}"


class PrefectUnavailable(RuntimeError):
    """The run could not be scheduled. The message is safe to log: a class name or a fixed text."""


@dataclass(frozen=True)
class TriggeredRun:
    flow_run_id: UUID
    pipeline_name: str
    week_start: date | None


def trigger_run(week_start: date | None, triggered_by: UUID) -> TriggeredRun:
    """Schedule one manual flow run. `week_start=None` means the default lookback.

    Without a configured `PREFECT_API_URL`, Prefect would start a temporary
    server inside the API process, where no worker could ever pick the run up,
    so that case is refused before any client is created.
    """
    from prefect.settings import get_current_settings

    if not get_current_settings().api.url:
        raise PrefectUnavailable("PREFECT_API_URL is not configured")

    from prefect.deployments import run_deployment

    parameters: dict[str, object] = {"trigger": "manual", "triggered_by": str(triggered_by)}
    if week_start is not None:
        parameters["week_start"] = week_start.isoformat()
    try:
        # Called from a worker thread with no event loop, so the async-dispatched
        # `run_deployment` runs its synchronous variant and returns the flow run.
        flow_run: FlowRun = run_deployment(  # type: ignore[assignment]
            DEPLOYMENT, parameters=parameters, timeout=0, as_subflow=False
        )
    except Exception as error:  # noqa: BLE001 - unreachable API, unknown deployment, HTTP error
        raise PrefectUnavailable(type(error).__name__) from None
    return TriggeredRun(flow_run_id=flow_run.id, pipeline_name=PIPELINE_NAME, week_start=week_start)
