"""Pydantic contracts for the incident manager.

The allowed values themselves live in `trackflow_shared.incidents` because the
seed script in `/scripts` has to produce them too. This module only says how
they are shaped on the wire.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from trackflow_shared.incidents import (
    IncidentBranch,
    IncidentCategory,
    IncidentOrigin,
    IncidentStatus,
)

# Re-exported so callers can write `from services.incidents.models import
# IncidentStatus` without also knowing where the vocabulary is kept.
__all__ = [
    "IncidentBranch",
    "IncidentCategory",
    "IncidentCreate",
    "IncidentCreatedResponse",
    "IncidentInDB",
    "IncidentOrigin",
    "IncidentResponse",
    "IncidentStatus",
    "IncidentStatusUpdate",
    "IncidentSummary",
]

TITLE_MAX_LENGTH = 120
DESCRIPTION_MAX_LENGTH = 4000


class IncidentBase(BaseModel):
    """The fields a reporter fills in.

    `min_length=1` on the two text fields is what turns a blank form field into
    a 400 rather than an incident whose title is the empty string. Pydantic
    strips surrounding whitespace first, so a title of three spaces is caught as
    well.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    description: str = Field(min_length=1, max_length=DESCRIPTION_MAX_LENGTH)
    category: IncidentCategory
    origin: IncidentOrigin
    # Required for every origin. `central` is the value to use when the incident
    # belongs to no particular facility.
    branch: IncidentBranch


class IncidentCreate(IncidentBase):
    """`POST /api/incidents` payload.

    `status` is optional and defaults to `open`: an incident is registered
    before anyone is assigned to it. The three timestamps and the id are
    system-generated and cannot be supplied.
    """

    status: IncidentStatus = IncidentStatus.OPEN


class IncidentInDB(IncidentBase):
    """The stored row.

    `source_incident_id` holds the identifier of the helpdesk record a seeded
    incident came from. It is how `scripts/seed_incidents.py` stays idempotent
    and it is absent on anything created through the API, which is why it is
    excluded from the response model rather than being part of the contract.
    """

    id: str
    status: IncidentStatus
    created_at: datetime
    updated_at: datetime
    source_incident_id: str | None = None


class IncidentResponse(IncidentBase):
    """What the API returns. Deliberately without `source_incident_id`."""

    id: str
    status: IncidentStatus
    created_at: datetime
    updated_at: datetime


class IncidentCreatedResponse(BaseModel):
    """Confirmation fields shown after a report is filed."""

    id: str
    title: str
    status: IncidentStatus
    branch: IncidentBranch


class IncidentStatusUpdate(BaseModel):
    """`PATCH /api/incidents/{id}/status` payload. Status and nothing else."""

    status: IncidentStatus


class IncidentSummary(BaseModel):
    """Aggregated counts for the leadership dashboard.

    Every bucket lists all of its allowed values, including the ones at zero, so
    a caller can render a fixed set of tiles without discovering that an empty
    database returns an empty object.
    """

    total: int
    by_status: dict[IncidentStatus, int]
    by_category: dict[IncidentCategory, int]
    by_origin: dict[IncidentOrigin, int]
    by_branch: dict[IncidentBranch, int]
