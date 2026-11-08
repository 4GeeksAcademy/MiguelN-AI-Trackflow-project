"""HTTP surface for the incident manager. No business rules live here.

Route order matters in one place: `/summary` is declared before `/{incident_id}`
because an incident id is an arbitrary string, so the parameterised route would
otherwise swallow the literal path and try to look up an incident called
"summary".

Failures leave this module as exceptions - `IncidentNotFound` from here,
`ValidationFailed` from the service layer - and `services.core.http_errors`
decides what they become on the wire.
"""

from fastapi import APIRouter, Query, Response, status
from pydantic import TypeAdapter

from services.core.errors import IncidentNotFound
from services.incidents import service
from services.incidents.models import (
    IncidentBranch,
    IncidentCategory,
    IncidentCreate,
    IncidentCreatedResponse,
    IncidentInDB,
    IncidentOrigin,
    IncidentResponse,
    IncidentStatus,
    IncidentStatusUpdate,
    IncidentSummary,
)
from services.incidents.read_cache import incident_read_cache

router = APIRouter(prefix="/api/incidents", tags=["incidents"])
_INCIDENT_RESPONSE_LIST = TypeAdapter(list[IncidentResponse])


def to_response(incident: IncidentInDB) -> IncidentResponse:
    """Drop the seed-only bookkeeping before the incident goes on the wire."""
    return IncidentResponse(**incident.model_dump(exclude={"source_incident_id"}))


@router.post(
    "", response_model=IncidentCreatedResponse, status_code=status.HTTP_201_CREATED
)
def create_incident(payload: IncidentCreate) -> IncidentCreatedResponse:
    """Register a new incident.

    A missing or out-of-range field never reaches this function: the payload
    fails to validate and the handler in `services.main` answers 400 naming the
    field.
    """
    incident = service.create_incident(payload)
    return IncidentCreatedResponse(
        id=incident.id,
        title=incident.title,
        status=incident.status,
        branch=incident.branch,
    )


@router.get("", response_model=list[IncidentResponse])
def list_incidents(
    status: IncidentStatus | None = Query(default=None),
    origin: IncidentOrigin | None = Query(default=None),
    branch: IncidentBranch | None = Query(default=None),
    category: IncidentCategory | None = Query(default=None),
) -> Response:
    """List incidents, optionally filtered. Filters combine with AND."""
    cache_key = (
        "list",
        None if status is None else status.value,
        None if origin is None else origin.value,
        None if branch is None else branch.value,
        None if category is None else category.value,
    )
    cached_payload = incident_read_cache.get(cache_key)
    if cached_payload is not None:
        return Response(content=cached_payload, media_type="application/json")

    cache_generation = incident_read_cache.generation
    incidents = service.list_incidents(
        status=status, origin=origin, branch=branch, category=category
    )
    payload = _INCIDENT_RESPONSE_LIST.dump_json(
        [to_response(incident) for incident in incidents]
    )
    incident_read_cache.set(cache_key, payload, expected_generation=cache_generation)
    return Response(content=payload, media_type="application/json")


@router.get("/summary", response_model=IncidentSummary)
def get_summary() -> Response:
    """Aggregated metrics by status, category, origin and branch."""
    cache_key = ("summary",)
    cached_payload = incident_read_cache.get(cache_key)
    if cached_payload is not None:
        return Response(content=cached_payload, media_type="application/json")

    cache_generation = incident_read_cache.generation
    payload = service.summarize().model_dump_json().encode("utf-8")
    incident_read_cache.set(cache_key, payload, expected_generation=cache_generation)
    return Response(content=payload, media_type="application/json")


@router.get("/{incident_id}", response_model=IncidentResponse)
def get_incident(incident_id: str) -> IncidentResponse:
    """One incident in full."""
    incident = service.get_incident(incident_id)
    if incident is None:
        raise IncidentNotFound(incident_id)
    return to_response(incident)


@router.patch("/{incident_id}/status", response_model=IncidentResponse)
def update_incident_status(
    incident_id: str, payload: IncidentStatusUpdate
) -> IncidentResponse:
    """Advance an incident along its lifecycle.

    `open` leads to `in_progress` or `discarded`; `in_progress` leads to
    `resolved` or `discarded`; `resolved` and `discarded` are final. Anything
    else is a 400 that says which moves were available.
    """
    return to_response(service.update_status(incident_id, payload.status))
