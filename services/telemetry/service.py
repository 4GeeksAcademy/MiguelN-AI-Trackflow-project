"""Validates telemetry events one by one and stores the valid ones.

A batch is never all-or-nothing: an event that breaks the contract is counted
as rejected and the rest of the batch is stored regardless.
"""

import logging
import math
from collections import Counter
from datetime import timezone
from typing import Any
from uuid import UUID

import httpx
from postgrest import APIError
from postgrest.types import CountMethod, ReturnMethod
from pydantic import ValidationError

from services.core import supabase_client
from services.telemetry.database import telemetry_events
from services.telemetry.models import TelemetryEvent, TelemetryReceipt

logger = logging.getLogger("api.telemetry")

# The row contract. `packages/shared/types/telemetry.ts` states the same one for
# TypeScript, and `tests/test_telemetry_contract.py` fails if the two drift.
TAG_ENVELOPE_FIELDS = ("sessionId", "userId", "requestId", "schemaVersion")
TAG_DIMENSIONS = ("warehouse", "branch", "source_system", "client_app", "section")


class TelemetryStorageUnavailable(Exception):
    """Supabase did not take the batch. Nothing was stored; a retry is safe."""


def to_row(event: TelemetryEvent) -> dict[str, Any]:
    """Map one event onto a `telemetry_events` row, ready to send as JSON.

    - `id` is the `eventId`, so a retried event cannot become a second row.
    - `payload` is `properties`, exactly as sent.
    - `tags` holds what events are searched by: the envelope's correlation
      fields under their `TelemetryEvent` names, plus each context dimension
      the event carries (warehouse, branch, ...), copied out of `properties`.

    The envelope promises UTC, so a timestamp without an offset is read as UTC
    rather than as the database server's local time.
    """
    timestamp = event.timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    tags = event.model_dump(mode="json", include=set(TAG_ENVELOPE_FIELDS))
    tags.update(
        (key, event.properties[key])
        for key in TAG_DIMENSIONS
        if event.properties.get(key) is not None
    )

    return {
        "id": str(event.eventId),
        "timestamp": timestamp.isoformat(),
        "event_type": event.event_type,
        "payload": event.properties,
        "tags": tags,
    }


def _fits_jsonb(value: Any) -> bool:
    """False for what the model accepts but PostgreSQL's JSONB refuses.

    NaN and Infinity are not JSON, and JSONB cannot hold a NUL character. Left
    in, either one would fail the bulk insert for every event in the batch.
    """
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return "\x00" not in value
    if isinstance(value, dict):
        return all(_fits_jsonb(key) and _fits_jsonb(item) for key, item in value.items())
    if isinstance(value, list):
        return all(_fits_jsonb(item) for item in value)
    return True


def _rejection_reason(error: ValidationError) -> str:
    """`field:error_type` of the first problem; never the rejected value.

    An unexpected key is reported as `envelope`, because its name is whatever
    the sender chose to write and does not belong in the log either.
    """
    first = error.errors(include_input=False, include_url=False)[0]
    location = first["loc"]
    if not location:
        field = "event"
    elif location[0] in TelemetryEvent.model_fields:
        field = str(location[0])
    else:
        field = "envelope"
    return f"{field}:{first['type']}"


def validate_events(raw_events: list[Any]) -> tuple[list[TelemetryEvent], Counter[str]]:
    """Split a batch into contract-valid events and counted rejection reasons."""
    valid: list[TelemetryEvent] = []
    rejected: Counter[str] = Counter()
    seen: set[UUID] = set()

    for raw in raw_events:
        try:
            event = TelemetryEvent.model_validate(raw)
        except ValidationError as error:
            rejected[_rejection_reason(error)] += 1
            continue

        if not _fits_jsonb(event.properties):
            rejected["properties:not_storable"] += 1
        elif event.eventId in seen:
            rejected["eventId:duplicate_in_batch"] += 1
        else:
            seen.add(event.eventId)
            valid.append(event)

    return valid, rejected


def store_events(events: list[TelemetryEvent]) -> int:
    """Insert every event in one Supabase request and return how many rows were new.

    An `id` already in the table - a retry of a batch whose response was lost -
    is skipped rather than failing the insert, so a retry never stores an event
    twice. PostgREST runs the whole request as one statement: either every new
    row is stored or none is.
    """
    rows = [to_row(event) for event in events]
    try:
        response = (
            supabase_client.get_supabase()
            .table(telemetry_events.name)
            .upsert(
                rows,
                on_conflict="id",
                ignore_duplicates=True,
                returning=ReturnMethod.minimal,
                count=CountMethod.exact,
            )
            .execute()
        )
    except APIError as error:
        # The PostgreSQL error code only: the message can quote stored values.
        raise TelemetryStorageUnavailable(f"APIError {error.code}") from None
    except httpx.HTTPError as error:
        raise TelemetryStorageUnavailable(type(error).__name__) from None

    return len(rows) if response.count is None else response.count


def ingest(raw_events: list[Any]) -> TelemetryReceipt:
    """Validate and store one batch.

    A replayed event counts as stored: it is in the table, which is what the
    sender needs to know.
    """
    valid, rejected = validate_events(raw_events)
    inserted = store_events(valid) if valid else 0

    logger.info(
        "telemetry batch received=%d stored=%d rejected=%d replayed=%d types=%s",
        len(raw_events),
        len(valid),
        rejected.total(),
        len(valid) - inserted,
        dict(Counter(event.event_type for event in valid)),
    )
    if rejected:
        logger.warning("telemetry events rejected: %s", dict(rejected))

    return TelemetryReceipt(received=len(raw_events), stored=len(valid), rejected=rejected.total())
