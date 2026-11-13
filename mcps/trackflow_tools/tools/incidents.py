"""Incidents Manager tools: create, update status, check one, search.

Each tool is one call to the Incidents Manager's real route. Status changes go
through the lifecycle endpoint `PATCH /api/incidents/{id}/status`, so the
Incidents Manager - not this server - decides whether a move is allowed.
"""

from typing import Annotated, Final

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field
from trackflow_shared.incidents import (
    ALLOWED_TRANSITIONS,
    REFERENCE_EXAMPLE,
    IncidentBranch,
    IncidentCategory,
    IncidentOrigin,
    IncidentStatus,
    allowed_transitions_from,
    normalize_reference,
)

from mcps.trackflow_tools.contracts import (
    DEFAULT_LIMIT,
    DESCRIPTION_MAX_LENGTH,
    MAX_LIMIT,
    TITLE_MAX_LENGTH,
    CreatedIncident,
    Incident,
    IncidentSearchResult,
)
from mcps.trackflow_tools.errors import ErrorCode, ToolFailure
from mcps.trackflow_tools.permissions import (
    CREATE_INCIDENT,
    GET_INCIDENT,
    SEARCH_INCIDENTS,
    UPDATE_INCIDENT_STATUS,
)
from mcps.trackflow_tools.tools import describe, parse, read_only_annotations, tool_meta
from mcps.trackflow_tools.upstream import IncidentsApi

SERVICE: Final = "Incidents Manager"


def lifecycle_text() -> str:
    """'open -> in_progress | discarded; ...; resolved: final', from the shared rule."""
    moves = []
    for status in IncidentStatus:
        targets = allowed_transitions_from(status)
        moves.append(
            f"{status.value} -> {' | '.join(t.value for t in targets)}"
            if targets
            else f"{status.value}: final"
        )
    return "; ".join(moves)


assert set(ALLOWED_TRANSITIONS) == set(IncidentStatus)  # every status has its moves

IncidentId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        description="The incident's id (UUID), as the Incidents Manager returns it in `id`.",
    ),
]


def register(mcp: FastMCP, incidents: IncidentsApi) -> list[str]:
    """Add the incident tools to `mcp`; returns their names."""

    def create_incident(
        title: Annotated[
            str,
            Field(
                min_length=1,
                max_length=TITLE_MAX_LENGTH,
                description="Short summary of what happened.",
            ),
        ],
        description: Annotated[
            str,
            Field(
                min_length=1,
                max_length=DESCRIPTION_MAX_LENGTH,
                description="What happened, with the details a support agent needs.",
            ),
        ],
        category: Annotated[IncidentCategory, Field(description="The kind of incident.")],
        origin: Annotated[
            IncidentOrigin,
            Field(description="Who reported it: a customer, a branch, or internal staff."),
        ],
        branch: Annotated[
            IncidentBranch,
            Field(description="The facility it belongs to; `central` when none in particular."),
        ],
    ) -> CreatedIncident:
        payload = {
            "title": title,
            "description": description,
            "category": category.value,
            "origin": origin.value,
            "branch": branch.value,
        }
        return parse(CreatedIncident, incidents.create(payload), SERVICE)

    mcp.tool(
        create_incident,
        name=CREATE_INCIDENT,
        description=describe(
            CREATE_INCIDENT,
            "Register a new incident in the Incidents Manager (`POST /api/incidents`). "
            "It starts in status `open`; the answer carries its new `id`.",
            use_when="someone reports a problem that has no incident yet.",
            errors=(ErrorCode.UPSTREAM_REJECTED, ErrorCode.UPSTREAM_UNAVAILABLE),
        ),
        annotations=ToolAnnotations(
            title="Create incident",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
        meta=tool_meta(CREATE_INCIDENT),
    )

    def update_incident_status(
        incident_id: IncidentId,
        status: Annotated[IncidentStatus, Field(description="The status to move the incident to.")],
    ) -> Incident:
        return parse(Incident, incidents.update_status(incident_id, status.value), SERVICE)

    mcp.tool(
        update_incident_status,
        name=UPDATE_INCIDENT_STATUS,
        description=describe(
            UPDATE_INCIDENT_STATUS,
            "Move an incident along its lifecycle through the Incidents Manager's lifecycle "
            "endpoint (`PATCH /api/incidents/{incident_id}/status`). Allowed moves: "
            f"{lifecycle_text()}. Moving to the status it already has is refused too. "
            "Returns the updated incident.",
            use_when="work on an incident starts, it is solved, or it is dropped. To act on a "
            "helpdesk reference ('ticket 482'), call get_incident first to obtain its `id`.",
            errors=(
                ErrorCode.INCIDENT_NOT_FOUND,
                ErrorCode.INVALID_STATUS_TRANSITION,
                ErrorCode.UPSTREAM_UNAVAILABLE,
            ),
        ),
        annotations=ToolAnnotations(
            title="Update incident status",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
        meta=tool_meta(UPDATE_INCIDENT_STATUS),
    )

    def get_incident(
        incident_id: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=64,
                description="The incident's id (UUID). Give this or `reference`, not both.",
            ),
        ] = None,
        reference: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=32,
                description=(
                    f"The helpdesk reference, e.g. '{REFERENCE_EXAMPLE}', 'TRF-123' or '123' "
                    "(short forms are normalised). Give this or `incident_id`, not both."
                ),
            ),
        ] = None,
    ) -> Incident:
        if (incident_id is None) == (reference is None):
            raise ToolFailure(
                ErrorCode.INVALID_ARGUMENTS,
                "Give exactly one of `incident_id` or `reference`.",
                problems=[{"field": "incident_id|reference", "message": "exactly one required"}],
            )
        if reference is not None:
            canonical = normalize_reference(reference)
            if canonical is None:
                raise ToolFailure(
                    ErrorCode.INVALID_ARGUMENTS,
                    f"'{reference}' is not a helpdesk reference (e.g. '{REFERENCE_EXAMPLE}').",
                    problems=[{"field": "reference", "message": "not a helpdesk reference"}],
                )
            return parse(Incident, incidents.find_by_reference(canonical), SERVICE)
        assert incident_id is not None
        return parse(Incident, incidents.get(incident_id), SERVICE)

    mcp.tool(
        get_incident,
        name=GET_INCIDENT,
        description=describe(
            GET_INCIDENT,
            "Read one incident - its current status and details - from the Incidents Manager "
            "(`GET /api/incidents/{incident_id}`, or `GET /api/incidents?reference=` for a "
            "helpdesk reference).",
            use_when="someone asks about a specific ticket or incident: its status, who "
            "reported it, what happened.",
            errors=(ErrorCode.INCIDENT_NOT_FOUND, ErrorCode.UPSTREAM_UNAVAILABLE),
        ),
        annotations=read_only_annotations("Get incident"),
        meta=tool_meta(GET_INCIDENT),
    )

    def search_incidents(
        status: Annotated[
            IncidentStatus | None, Field(description="Only incidents in this status.")
        ] = None,
        category: Annotated[
            IncidentCategory | None,
            Field(description="Only incidents of this category."),
        ] = None,
        origin: Annotated[
            IncidentOrigin | None,
            Field(description="Only incidents reported by this origin."),
        ] = None,
        branch: Annotated[
            IncidentBranch | None,
            Field(description="Only incidents of this branch."),
        ] = None,
        limit: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_LIMIT,
                description="The most incidents to return; `total` still counts every match.",
            ),
        ] = DEFAULT_LIMIT,
    ) -> IncidentSearchResult:
        filters = {
            name: value.value
            for name, value in (
                ("status", status),
                ("category", category),
                ("origin", origin),
                ("branch", branch),
            )
            if value is not None
        }
        rows = incidents.search(filters)
        found = [parse(Incident, row, SERVICE) for row in rows]
        return IncidentSearchResult(incidents=found[:limit], total=len(found))

    mcp.tool(
        search_incidents,
        name=SEARCH_INCIDENTS,
        description=describe(
            SEARCH_INCIDENTS,
            "Search incidents in the Incidents Manager (`GET /api/incidents`) by status, "
            "category, origin and branch. Filters combine with AND; none lists every incident.",
            use_when="someone asks which or how many incidents match a condition, e.g. the "
            "open lost-parcel incidents of the Zaragoza warehouse.",
            errors=(ErrorCode.UPSTREAM_UNAVAILABLE,),
        ),
        annotations=read_only_annotations("Search incidents"),
        meta=tool_meta(SEARCH_INCIDENTS),
    )

    return [CREATE_INCIDENT, UPDATE_INCIDENT_STATUS, GET_INCIDENT, SEARCH_INCIDENTS]
