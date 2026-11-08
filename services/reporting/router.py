"""Business reporting endpoints, backed by the pipelines in `data/pipelines/`.

HTTP only: parse, call `data.pipelines`, map errors to status codes. Whether a
week was computed, what it holds and how a run starts are decided there; no
pipeline logic is duplicated here. This module is separate from
`services/telemetry/`, and imports nothing from it: the technical report
(`POST /telemetry/report`) serves engineering, this serves the business.

All three routes take a bearer token, like `/inventory` and `/suppliers`:
per-client volumes are commercially sensitive (design Q6). Responses expose no
person: the triggering user's UUID is stored on the run, never returned.
"""

import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

from data.pipelines import run_log
from data.pipelines.weekly_warehouse_client_performance import queries, trigger
from services.auth.dependencies import CurrentUser
from services.inventory.database import get_engine
from services.reporting.models import (
    PipelineRunAccepted,
    PipelineRunRequest,
    PipelineRunStatus,
    WeeklyPerformance,
)

logger = logging.getLogger("api.reporting")

router = APIRouter(prefix="/reporting", tags=["reporting"])


def _invalid(location: list[str], message: str) -> HTTPException:
    # The same 422 body FastAPI produces, as `POST /telemetry/report` does.
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        [{"loc": location, "msg": message, "type": "value_error"}],
    )


def _unavailable(error: Exception, what: str) -> HTTPException:
    # The class name only: a driver message can quote the connection URL.
    logger.error("%s failed: %s", what, type(error).__name__)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Reporting storage unavailable")


def _today() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/weekly-warehouse-client-performance", response_model=WeeklyPerformance)
def get_weekly_warehouse_client_performance(
    user: CurrentUser,
    week_start: date | None = Query(
        default=None,
        description="Monday of the ISO week (UTC). Defaults to the most recent computed week.",
    ),
) -> WeeklyPerformance:
    """Inbound Volume, Outbound Throughput, Stockout Frequency and Discrepancy Rate.

    One entry per warehouse and client for the week, ordered by warehouse and
    client. A computed week where nothing happened returns its zero rows; a
    week that was never computed returns 404, so "nothing happened" and "not
    calculated" stay distinguishable.
    """
    if week_start is not None:
        try:
            queries.check_requested_week(week_start, _today())
        except queries.InvalidWeek as error:
            raise _invalid(["query", "week_start"], str(error)) from None

    engine = get_engine()
    try:
        week = week_start or run_log.latest_completed_week(engine)
        if week is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No week has been computed yet")
        if not run_log.is_week_computed(engine, week):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That week has not been computed")
        entries = queries.get_weekly_performance(engine, week)
    except SQLAlchemyError as error:
        raise _unavailable(error, "weekly performance read") from None
    return WeeklyPerformance.model_validate({"week_start": week, "entries": entries})


@router.get("/pipeline-runs/latest", response_model=PipelineRunStatus)
def get_latest_pipeline_run(user: CurrentUser) -> PipelineRunStatus:
    """Status and metadata of the latest run, plus whether the report is stale."""
    try:
        run: dict[str, Any] | None = queries.get_pipeline_status(get_engine(), _today())
    except SQLAlchemyError as error:
        raise _unavailable(error, "pipeline run read") from None
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The pipeline has never run")
    return PipelineRunStatus.model_validate(run)


@router.post(
    "/pipeline-runs",
    response_model=PipelineRunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_pipeline_run(
    user: CurrentUser, payload: PipelineRunRequest | None = None
) -> PipelineRunAccepted:
    """Schedule a manual run and return without waiting for it.

    409 while a run holds the slot. Retrying a timed-out request is harmless:
    at worst it queues one more run, and the pipeline is idempotent.
    """
    week_start = payload.week_start if payload else None
    if week_start is not None:
        try:
            queries.closed_weeks(_today(), week_start)
        except queries.InvalidWeek as error:
            raise _invalid(["body", "week_start"], str(error)) from None

    try:
        if run_log.is_running(get_engine()):
            raise HTTPException(status.HTTP_409_CONFLICT, "A pipeline run is already in progress")
    except SQLAlchemyError as error:
        raise _unavailable(error, "pipeline run check") from None

    try:
        scheduled = trigger.trigger_run(week_start, UUID(user.id))
    except trigger.PrefectUnavailable as error:
        logger.error("pipeline trigger failed: %s", error)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The pipeline scheduler is unavailable"
        ) from None
    return PipelineRunAccepted(
        flow_run_id=scheduled.flow_run_id,
        pipeline_name=scheduled.pipeline_name,
        week_start=scheduled.week_start,
    )
