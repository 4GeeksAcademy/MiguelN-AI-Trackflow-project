"""Stage 1, extract: read-only queries against the source tables (`PIPELINE_DESIGN.md` 2.5).

Every query runs in a `READ ONLY` transaction, so the database itself refuses
any write: `telemetry_events` is this pipeline's source, never its destination.
They go through SQLAlchemy over `DATABASE_URL`, not PostgREST, whose default
1000-row limit would silently truncate a busy week.

Source tables are named without a schema and resolve through the connection's
`search_path` (`public` in production), which is how the PostgreSQL tests point
the pipeline at disposable copies.

The event read is bounded twice:

- by **effective time** (`least(timestamp, created_at)`), the week rule of the
  transform, so an event stamped by a clock running ahead is still read for
  the week it arrived in. `timestamp >= :window_start` repeats the lower bound
  in a form the `timestamp` index can serve; it is implied by the next line.
- by **arrival** (`created_at <= :arrival_cutoff`), the run's snapshot, so the
  same window and cutoff always read the same rows.
"""

from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, Engine

from data.process import collection_health, weekly_rollup

EVENTS_SQL = text(
    """
    SELECT CAST(id AS text) AS id, timestamp, created_at, event_type,
           payload->>'warehouse'           AS warehouse,
           payload->>'client_id'           AS client_id,
           payload->>'product_id'          AS product_id,
           payload->>'quantity'            AS quantity,
           payload->>'order_id'            AS order_id,
           payload->>'triggering_order_id' AS triggering_order_id,
           payload->>'threshold_band'      AS threshold_band,
           payload->>'reconciliation_id'   AS reconciliation_id,
           tags->>'schemaVersion'          AS schema_version
    FROM telemetry_events
    WHERE event_type IN :event_types
      AND timestamp >= :window_start
      AND least(timestamp, created_at) >= :window_start
      AND least(timestamp, created_at) < :window_end
      AND created_at <= :arrival_cutoff
    ORDER BY timestamp, created_at, id
    """
).bindparams(bindparam("event_types", expanding=True))

# Arrived since the previous completed run, but for a week older than this
# window: a late event that only a backfill would count (design 9.3).
LATE_EVENTS_SQL = text(
    """
    SELECT count(*) FROM telemetry_events
    WHERE event_type IN :event_types
      AND created_at > :previous_cutoff
      AND created_at <= :arrival_cutoff
      AND least(timestamp, created_at) < :window_start
    """
).bindparams(bindparam("event_types", expanding=True))

# Client-side loss the emitters reported themselves (`telemetry_dropped`).
DROPPED_SQL = text(
    """
    SELECT coalesce(sum(CASE WHEN payload->>'dropped_count' ~ '^[0-9]{1,9}$'
                             THEN (payload->>'dropped_count')::bigint ELSE 0 END), 0)
    FROM telemetry_events
    WHERE event_type = 'telemetry_dropped'
      AND timestamp >= :window_start
      AND least(timestamp, created_at) >= :window_start
      AND least(timestamp, created_at) < :window_end
      AND created_at <= :arrival_cutoff
    """
)

PRODUCTS_SQL = text(
    "SELECT CAST(id AS text) AS product_id, client_id, CAST(warehouse AS text) AS warehouse "
    "FROM products ORDER BY id"
)

# The independent count of the same business facts, from the system of record.
# Both reads are ordered so the transform's content-hashed cache key is stable.
ORDER_COUNTS_SQL = text(
    """
    SELECT CAST(p.warehouse AS text) AS warehouse,
           CAST(o.created_at AT TIME ZONE 'UTC' AS date) AS day,
           :inbound AS event_type, count(*) AS inventory_orders
    FROM inbound_orders o JOIN products p ON p.id = o.product_id
    WHERE o.created_at >= :window_start AND o.created_at < :window_end
    GROUP BY 1, 2
    UNION ALL
    SELECT CAST(p.warehouse AS text), CAST(o.created_at AT TIME ZONE 'UTC' AS date),
           :outbound, count(*)
    FROM outbound_orders o JOIN products p ON p.id = o.product_id
    WHERE o.created_at >= :window_start AND o.created_at < :window_end
    GROUP BY 1, 2
    ORDER BY 1, 2, 3
    """
)


@dataclass(frozen=True)
class SourceExtract:
    """The events of the window, plus the run-log signals read alongside them."""

    events: pd.DataFrame
    source_watermark: datetime | None  # max(created_at) of the events read
    late_events_outside_window: int | None  # None on the first run: nothing to compare with
    telemetry_dropped: int


def _read_only(connection: Connection) -> None:
    connection.execute(text("SET TRANSACTION READ ONLY"))


def read_source_events(
    engine: Engine,
    *,
    window_start: datetime,
    window_end: datetime,
    arrival_cutoff: datetime,
    previous_cutoff: datetime | None,
) -> SourceExtract:
    """The four mandatory event types in the window, as one snapshot."""
    window = {
        "window_start": window_start,
        "window_end": window_end,
        "arrival_cutoff": arrival_cutoff,
    }
    with engine.connect() as connection, connection.begin():
        _read_only(connection)
        rows = connection.execute(
            EVENTS_SQL, {**window, "event_types": list(weekly_rollup.EVENT_TYPES)}
        ).all()
        late = None
        if previous_cutoff is not None:
            late = connection.execute(
                LATE_EVENTS_SQL,
                {
                    "event_types": list(weekly_rollup.EVENT_TYPES),
                    "previous_cutoff": previous_cutoff,
                    "arrival_cutoff": arrival_cutoff,
                    "window_start": window_start,
                },
            ).scalar_one()
        dropped = connection.execute(DROPPED_SQL, window).scalar_one()

    events = pd.DataFrame.from_records(
        [tuple(row) for row in rows], columns=list(weekly_rollup.EVENT_COLUMNS)
    )
    if events.empty:
        events = weekly_rollup.empty_events()
    watermark = max((row.created_at for row in rows), default=None)
    return SourceExtract(
        events=events,
        source_watermark=watermark,
        late_events_outside_window=int(late) if late is not None else None,
        telemetry_dropped=int(dropped),
    )


def read_product_clients(engine: Engine) -> pd.DataFrame:
    """Every product with its owner: the client lookup and the zero-row grid."""
    with engine.connect() as connection, connection.begin():
        _read_only(connection)
        rows = connection.execute(PRODUCTS_SQL).all()
    return pd.DataFrame.from_records(
        [tuple(row) for row in rows], columns=list(weekly_rollup.PRODUCT_COLUMNS)
    )


def read_inventory_order_counts(
    engine: Engine, *, window_start: datetime, window_end: datetime
) -> pd.DataFrame:
    """Inventory orders per warehouse, UTC day and order event type, for capture coverage."""
    with engine.connect() as connection, connection.begin():
        _read_only(connection)
        rows = connection.execute(
            ORDER_COUNTS_SQL,
            {
                "window_start": window_start,
                "window_end": window_end,
                "inbound": weekly_rollup.INBOUND,
                "outbound": weekly_rollup.OUTBOUND,
            },
        ).all()
    if not rows:
        return collection_health.empty_order_counts()
    frame = pd.DataFrame.from_records(
        [tuple(row) for row in rows], columns=list(collection_health.ORDER_COUNT_COLUMNS)
    )
    frame["inventory_orders"] = frame["inventory_orders"].astype("int64")
    return frame
