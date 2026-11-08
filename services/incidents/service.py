"""Incident persistence and the lifecycle rule that guards status changes.

HTTP-agnostic on purpose: this module raises `ValidationFailed` and
`IncidentNotFound`, and `router.py` decides what those become on the wire.
"""

import logging
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError
from tinydb import Query
from tinydb.table import Table
from trackflow_shared.incidents import can_transition, explain_invalid_transition

from services.core.errors import IncidentNotFound, ValidationFailed
from services.incidents.db import get_table
from services.incidents.models import (
    IncidentBranch,
    IncidentCategory,
    IncidentCreate,
    IncidentInDB,
    IncidentOrigin,
    IncidentStatus,
    IncidentSummary,
)
from services.incidents.read_cache import clear_read_cache

logger = logging.getLogger(__name__)


def _table() -> Table:
    return get_table()


def _all_incidents() -> list[IncidentInDB]:
    """Every stored incident that still reads as one.

    A row that no longer fits the model - edited by hand, or written before a
    rule changed - is skipped and logged by id, so one bad row cannot turn the
    whole list and the summary into a 500.
    """
    incidents = []
    for doc in _table().all():
        try:
            incidents.append(IncidentInDB(**doc))
        except ValidationError:
            logger.warning(
                "Skipping stored incident %s: it no longer matches the model",
                doc.get("id", "?"),
            )
    return incidents


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_incident(
    payload: IncidentCreate,
    *,
    created_at: datetime | None = None,
    source_incident_id: str | None = None,
) -> IncidentInDB:
    """Register an incident and return the stored row.

    `created_at` and `source_incident_id` exist for the historical seed, which
    has to preserve the helpdesk's original date and remember which record it
    came from. A request through the API passes neither, so the incident is
    stamped with the current time.
    """
    timestamp = created_at or _now()
    record = IncidentInDB(
        id=str(uuid4()),
        **payload.model_dump(),
        created_at=timestamp,
        # A brand new incident has never been modified, so both timestamps
        # start equal rather than `updated_at` being left null.
        updated_at=timestamp,
        source_incident_id=source_incident_id,
    )
    _table().insert(record.model_dump(mode="json"))
    clear_read_cache()
    return record


def list_incidents(
    *,
    status: IncidentStatus | None = None,
    origin: IncidentOrigin | None = None,
    branch: IncidentBranch | None = None,
    category: IncidentCategory | None = None,
) -> list[IncidentInDB]:
    """Every incident matching the filters given, newest first.

    Filters combine with AND. All four omitted returns the whole table; an
    empty table returns an empty list rather than failing.
    """
    incidents = _all_incidents()

    if status is not None:
        incidents = [item for item in incidents if item.status is status]
    if origin is not None:
        incidents = [item for item in incidents if item.origin is origin]
    if branch is not None:
        incidents = [item for item in incidents if item.branch is branch]
    if category is not None:
        incidents = [item for item in incidents if item.category is category]

    return sorted(incidents, key=lambda item: item.created_at, reverse=True)


def get_incident(incident_id: str) -> IncidentInDB | None:
    """The incident with this id, or None."""
    doc = _table().get(Query().id == incident_id)
    return None if doc is None else IncidentInDB(**doc)


def get_incident_by_source_id(source_incident_id: str) -> IncidentInDB | None:
    """The incident seeded from this helpdesk record, or None.

    The seed's idempotency check: a second run finds the row it already wrote
    and skips it.
    """
    doc = _table().get(Query().source_incident_id == source_incident_id)
    return None if doc is None else IncidentInDB(**doc)


def update_status(incident_id: str, new_status: IncidentStatus) -> IncidentInDB:
    """Move an incident to `new_status`, refreshing `updated_at`.

    Raises `IncidentNotFound` when the id is unknown and `ValidationFailed`
    when the move breaks the lifecycle - including a no-op move to the status
    the incident already holds, which would otherwise touch `updated_at`
    without changing anything.
    """
    incident = get_incident(incident_id)
    if incident is None:
        raise IncidentNotFound(incident_id)

    if not can_transition(incident.status, new_status):
        raise ValidationFailed(
            "status", explain_invalid_transition(incident.status, new_status)
        )

    updated_at = _now()
    _table().update(
        {"status": new_status.value, "updated_at": updated_at.isoformat()},
        Query().id == incident_id,
    )
    clear_read_cache()
    return incident.model_copy(update={"status": new_status, "updated_at": updated_at})


def summarize() -> IncidentSummary:
    """Totals by status, category, origin and branch.

    Buckets are pre-seeded with every allowed value at zero, so an empty
    database answers with zeros across the board instead of empty objects.
    """
    incidents = _all_incidents()

    by_status = dict.fromkeys(IncidentStatus, 0)
    by_category = dict.fromkeys(IncidentCategory, 0)
    by_origin = dict.fromkeys(IncidentOrigin, 0)
    by_branch = dict.fromkeys(IncidentBranch, 0)

    for incident in incidents:
        by_status[incident.status] += 1
        by_category[incident.category] += 1
        by_origin[incident.origin] += 1
        by_branch[incident.branch] += 1

    return IncidentSummary(
        total=len(incidents),
        by_status=by_status,
        by_category=by_category,
        by_origin=by_origin,
        by_branch=by_branch,
    )
