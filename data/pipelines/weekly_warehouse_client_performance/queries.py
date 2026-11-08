"""Read the published report and the pipeline's status: what `services/reporting/` serves.

The endpoints only translate HTTP. Which weeks may be asked for, whether a week
was computed, which rows it has and whether the report is stale are decided
here and in `run_log`.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.engine import Engine

from data.pipelines import run_log
from data.pipelines.weekly_warehouse_client_performance.schema import (
    reporting_schema,
    weekly_warehouse_client_performance,
)
from data.process.weekly_rollup import InvalidWeek, closed_weeks, last_closed_week, week_start_of

__all__ = [
    "InvalidWeek",
    "check_requested_week",
    "closed_weeks",
    "get_pipeline_status",
    "get_weekly_performance",
    "last_closed_week",
    "week_start_of",
]

ENTRY_FIELDS = (
    "warehouse",
    "client_id",
    "inbound_units_count",
    "outbound_orders_count",
    "stockout_events_count",
    "discrepancy_events_count",
    "discrepancy_rate",
)


def get_weekly_performance(engine: Engine, week_start: date) -> list[dict[str, Any]]:
    """Every warehouse/client row of one week, ordered by warehouse, then client.

    An empty list is a real answer when the week was computed and no client
    owns products; whether it was computed is `run_log.is_week_computed`.
    """
    table = weekly_warehouse_client_performance
    with engine.connect() as connection:
        name = f'"{reporting_schema(connection)}".{table.name}'
        if connection.execute(text("SELECT to_regclass(:name)"), {"name": name}).scalar() is None:
            return []
        rows = connection.execute(
            select(*(table.c[field] for field in ENTRY_FIELDS))
            .where(table.c.week_start == week_start)
            .order_by(table.c.warehouse, table.c.client_id)
        ).all()
    entries = []
    for row in rows:
        entry = dict(row._mapping)
        rate = entry["discrepancy_rate"]
        entry["discrepancy_rate"] = float(rate) if isinstance(rate, Decimal) else rate
        entries.append(entry)
    return entries


def check_requested_week(week_start: date, now: datetime) -> None:
    """Refuse a week the KPI query can never answer: not a Monday, or in the future.

    A Monday of the week in progress is a valid question whose answer is "not
    computed" (404), not an invalid one, so only weeks after today are refused.
    Raises `InvalidWeek`, whose message is safe to return to the caller.
    """
    if week_start.weekday() != 0:
        raise InvalidWeek("week_start must be a Monday")
    if week_start > now.astimezone(timezone.utc).date():
        raise InvalidWeek("week_start is in the future")


def get_pipeline_status(engine: Engine, now: datetime) -> dict[str, Any] | None:
    """The latest run's public fields plus how fresh the report is, or None if it never ran.

    `last_closed_week` is the newest week that has ended. `stale` is true when
    no completed run reaches it yet, which means the scheduled run is late or
    failed; a dashboard can show that without knowing Prefect exists.
    """
    run = run_log.get_latest_run(engine)
    if run is None:
        return None
    completed_through = run_log.latest_completed_week(engine)
    newest = last_closed_week(now)
    return {
        **run,
        "events_excluded": run["events_excluded"] or {},
        "weeks_revised": run["weeks_revised"] or [],
        "last_closed_week": newest,
        "stale": completed_through is None or completed_through < newest,
    }
