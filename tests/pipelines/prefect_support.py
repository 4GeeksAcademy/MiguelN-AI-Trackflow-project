"""Helpers for tests that run the pipeline's flows under `prefect_test_harness`."""

from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest
from prefect import Task
from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterParentFlowRunId
from prefect.client.schemas.objects import FlowRun
from prefect.client.schemas.sorting import FlowRunSort

from data.pipelines.subflows import collection_health_check, kpi_computation

CACHED_STAGES: tuple[ModuleType, ...] = (kpi_computation, collection_health_check)


def redirect_cached_results(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Send every cached task's results to `directory`, never to `data/raw/prefect-results`."""
    for module in CACHED_STAGES:
        for name, value in list(vars(module).items()):
            if isinstance(value, Task) and value.cache_key_fn is not None:
                monkeypatch.setattr(module, name, value.with_options(result_storage=directory))


def subflow_runs(parent_flow_run_id: UUID | None) -> list[tuple[str, FlowRun]]:
    """`(flow name, flow run)` for each subflow run a flow run started, oldest first."""
    assert parent_flow_run_id is not None, "the state does not belong to a flow run"
    with get_client(sync_client=True) as client:
        runs = client.read_flow_runs(
            flow_run_filter=FlowRunFilter(
                parent_flow_run_id=FlowRunFilterParentFlowRunId(any_=[parent_flow_run_id])
            ),
            sort=FlowRunSort.START_TIME_ASC,
        )
        return [(client.read_flow(child.flow_id).name, child) for child in runs]
