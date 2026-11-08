"""Stage 3, load: one transaction that publishes a run's weeks (`PIPELINE_DESIGN.md` 3.1-3.2).

In order, inside `engine.begin()`:

1. `set_config('reporting.run_id', ...)`, so the revisions trigger can name the run.
2. `pg_advisory_xact_lock`, which serializes loads even when a script bypasses
   Prefect's concurrency limit.
3. The stale-snapshot check: if a completed run covering any of these weeks
   read a later snapshot, this one gives way, so an older read never
   overwrites fresher figures.
4. Upsert on `(warehouse, client_id, week_start)`, updating only rows whose
   figures changed.
5. Delete the keys of these weeks that the new result no longer contains.
6. The run's collection stats and exclusions, then the run marked `completed`.

Any failure rolls all of it back: readers see last week's complete numbers or
this week's, never a mix. A re-run over unchanged data writes nothing and
reports `0 inserted / 0 updated / 0 deleted`, which is the idempotency proof.
Only transaction-scoped features are used, so the Supabase transaction pooler
(port 6543) is safe.
"""

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, insert, literal_column, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from data.pipelines import run_log
from data.pipelines.weekly_warehouse_client_performance import schema
from data.process import collection_health, weekly_rollup

RATE_QUANTUM = Decimal(1).scaleb(-weekly_rollup.RATE_DECIMALS)


@dataclass(frozen=True)
class LoadResult:
    status: Literal["completed", "skipped"]
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_unchanged: int = 0
    rows_deleted: int = 0
    weeks_revised: tuple[date, ...] = ()
    blocked_by_run_id: UUID | None = None


class StaleSnapshot(Exception):
    """A completed run covering the same weeks read a later snapshot."""

    def __init__(self, newer_run_id: UUID) -> None:
        super().__init__(newer_run_id)
        self.newer_run_id = newer_run_id


def _plain(value: Any) -> Any:
    """A DataFrame cell as a value psycopg2 can adapt: no numpy scalars, no NaN/NA."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if math.isnan(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {str(name): _plain(value) for name, value in record.items()}
        for record in frame.to_dict("records")
    ]


def _report_records(rows: pd.DataFrame) -> list[dict[str, Any]]:
    records = _records(rows)
    for record in records:
        # Exact decimal, so the database compares 0.3333 with 0.3333, not a float.
        record["discrepancy_rate"] = Decimal(str(record["discrepancy_rate"])).quantize(RATE_QUANTUM)
    return records


def _newer_completed_run(
    connection: Connection, run_id: UUID, arrival_cutoff: datetime, weeks: tuple[date, ...]
) -> UUID | None:
    runs = schema.pipeline_runs
    return connection.execute(
        select(runs.c.id)
        .where(
            runs.c.pipeline_name == schema.PIPELINE_NAME,
            runs.c.status == "completed",
            runs.c.id != run_id,
            runs.c.arrival_cutoff > arrival_cutoff,
            runs.c.weeks_from <= max(weeks),
            runs.c.weeks_to >= min(weeks),
        )
        .order_by(runs.c.arrival_cutoff.desc())
        .limit(1)
    ).scalar()


def _published_weeks(connection: Connection, run_id: UUID, weeks: tuple[date, ...]) -> set[date]:
    """The weeks some earlier completed run already published."""
    runs = schema.pipeline_runs
    spans = connection.execute(
        select(runs.c.weeks_from, runs.c.weeks_to).where(
            runs.c.pipeline_name == schema.PIPELINE_NAME,
            runs.c.status == "completed",
            runs.c.id != run_id,
            runs.c.weeks_from <= max(weeks),
            runs.c.weeks_to >= min(weeks),
        )
    ).all()
    return {week for week in weeks for start, end in spans if start <= week <= end}


def _upsert(connection: Connection, records: list[dict[str, Any]]) -> list[tuple[bool, date]]:
    """Insert new keys and update changed ones. Unchanged rows return nothing."""
    if not records:
        return []
    table = schema.weekly_warehouse_client_performance
    values = pg_insert(table).values(records)
    figures = [table.c[name] for name in schema.FIGURE_COLUMNS]
    incoming = [values.excluded[name] for name in schema.FIGURE_COLUMNS]
    upsert = values.on_conflict_do_update(
        index_elements=[table.c.warehouse, table.c.client_id, table.c.week_start],
        set_={
            **{name: values.excluded[name] for name in schema.FIGURE_COLUMNS},
            "computed_at": func.now(),
        },
        where=tuple_(*figures).is_distinct_from(tuple_(*incoming)),
    ).returning(literal_column("(xmax = 0)").label("inserted"), table.c.week_start)
    return [(bool(row.inserted), row.week_start) for row in connection.execute(upsert)]


def _delete_stale_keys(
    connection: Connection, records: list[dict[str, Any]], weeks: tuple[date, ...]
) -> list[date]:
    """Remove keys of the recomputed weeks that the new result does not contain.

    A product moved to another client, for example, leaves its old client's
    row behind; without this the week would no longer match the source.
    """
    table = schema.weekly_warehouse_client_performance
    keys = [(r["warehouse"], r["client_id"], r["week_start"]) for r in records]
    statement = delete(table).where(table.c.week_start.in_(weeks))
    if keys:
        statement = statement.where(
            tuple_(table.c.warehouse, table.c.client_id, table.c.week_start).not_in(keys)
        )
    return [row.week_start for row in connection.execute(statement.returning(table.c.week_start))]


def load_weekly_rows(
    engine: Engine,
    *,
    run_id: UUID,
    arrival_cutoff: datetime,
    weeks: tuple[date, ...],
    rows: pd.DataFrame,
    exclusions: pd.DataFrame,
    health: collection_health.CollectionHealth | None,
    run_fields: dict[str, Any],
) -> LoadResult:
    """Publish the validated rows of `weeks` and mark the run completed, atomically.

    `run_fields` carries the run-log columns the flow measured on the way
    (`events_read`, `events_excluded`, `source_watermark`, ...). When the
    collection health could not be computed, `health` is None: the figures are
    still published and the run is marked `degraded`.
    """
    records = _report_records(rows)
    try:
        with engine.begin() as connection:
            connection.execute(select(func.set_config(schema.RUN_ID_SETTING, str(run_id), True)))
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": schema.advisory_key(connection, "load")},
            )
            newer = _newer_completed_run(connection, run_id, arrival_cutoff, weeks)
            if newer is not None:
                raise StaleSnapshot(newer)

            published = _published_weeks(connection, run_id, weeks)
            changes = _upsert(connection, records)
            deleted = _delete_stale_keys(connection, records, weeks)

            if health is not None and not health.stats.empty:
                stats = [{"run_id": run_id, **record} for record in _records(health.stats)]
                connection.execute(insert(schema.collection_stats), stats)
            if not exclusions.empty:
                excluded = [
                    {
                        "run_id": run_id,
                        "event_id": UUID(record["event_id"]),
                        "reason": record["reason"],
                    }
                    for record in _records(exclusions)
                ]
                connection.execute(insert(schema.exclusions), excluded)

            inserted = sum(1 for was_insert, _ in changes if was_insert)
            updated = len(changes) - inserted
            touched = {week for _, week in changes} | set(deleted)
            result = LoadResult(
                status="completed",
                rows_inserted=inserted,
                rows_updated=updated,
                rows_unchanged=len(records) - len(changes),
                rows_deleted=len(deleted),
                weeks_revised=tuple(sorted(touched & published)),
            )
            run_log.complete_run(
                connection,
                run_id,
                **run_fields,
                rows_inserted=result.rows_inserted,
                rows_updated=result.rows_updated,
                rows_unchanged=result.rows_unchanged,
                rows_deleted=result.rows_deleted,
                weeks_revised=list(result.weeks_revised),
                min_capture_coverage=(health.min_capture_coverage if health else None),
                quality_status=(health.quality_status if health else "degraded"),
            )
        return result
    except StaleSnapshot as stale:
        run_log.skip_run(
            engine,
            run_id,
            error_class="StaleSnapshot",
            blocked_by_run_id=stale.newer_run_id,
            message="A completed run of the same weeks read a later snapshot",
            events_read=run_fields.get("events_read"),
        )
        return LoadResult(status="skipped", blocked_by_run_id=stale.newer_run_id)
