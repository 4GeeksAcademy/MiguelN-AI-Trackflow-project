"""The incident vocabulary: allowed values and the lifecycle that governs them.

These are the enumerations named in `contexts/centralized-incident.md`. They
live here rather than in `services/incidents/models.py` because the seed script
in `/scripts` has to produce exactly the values the API accepts, and a second
copy of the lists would drift the first time one of them gained a category.
"""

import re
from enum import Enum


class IncidentStatus(str, Enum):
    """Lifecycle state of an incident."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    DISCARDED = "discarded"


class IncidentOrigin(str, Enum):
    """Who reported the incident and from where."""

    CUSTOMER = "customer"
    BRANCH = "branch"
    INTERNAL = "internal"


class IncidentBranch(str, Enum):
    """The facility that manages or reports the incident.

    `CENTRAL` is the deliberate catch-all: an incident that does not belong to a
    physical site still needs a branch, because the field is required for every
    origin.
    """

    CENTRAL = "central"
    LA_WAREHOUSE = "la_warehouse"
    LA_OFFICE = "la_office"
    ZARAGOZA_WAREHOUSE = "zaragoza_warehouse"
    ZARAGOZA_OFFICE = "zaragoza_office"


class IncidentCategory(str, Enum):
    """What kind of operational failure the incident describes."""

    LOST_PARCEL = "lost_parcel"
    DELIVERY_FAILURE = "delivery_failure"
    INVENTORY_DISCREPANCY = "inventory_discrepancy"
    CARRIER_ISSUE = "carrier_issue"
    RETURNS_ISSUE = "returns_issue"
    WAREHOUSE_INCIDENT = "warehouse_incident"
    SYSTEM_FAILURE = "system_failure"
    CLIENT_COMPLAINT = "client_complaint"
    OTHER = "other"


# Branch display names. TrackFlow runs in English in Los Angeles and Spanish in
# Zaragoza, so the labels are kept per language and the stored value never is.
BRANCH_LABELS: dict[str, dict[IncidentBranch, str]] = {
    "en": {
        IncidentBranch.CENTRAL: "Central",
        IncidentBranch.LA_WAREHOUSE: "Los Angeles — Warehouse",
        IncidentBranch.LA_OFFICE: "Los Angeles — Office",
        IncidentBranch.ZARAGOZA_WAREHOUSE: "Zaragoza — Warehouse",
        IncidentBranch.ZARAGOZA_OFFICE: "Zaragoza — Office",
    },
    "es": {
        IncidentBranch.CENTRAL: "Central",
        IncidentBranch.LA_WAREHOUSE: "Los Ángeles — Almacén",
        IncidentBranch.LA_OFFICE: "Los Ángeles — Oficina",
        IncidentBranch.ZARAGOZA_WAREHOUSE: "Zaragoza — Almacén",
        IncidentBranch.ZARAGOZA_OFFICE: "Zaragoza — Oficina",
    },
}


# The lifecycle, as an adjacency map. A status whose entry is empty is final.
ALLOWED_TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.OPEN: frozenset({IncidentStatus.IN_PROGRESS, IncidentStatus.DISCARDED}),
    IncidentStatus.IN_PROGRESS: frozenset({IncidentStatus.RESOLVED, IncidentStatus.DISCARDED}),
    IncidentStatus.RESOLVED: frozenset(),
    IncidentStatus.DISCARDED: frozenset(),
}


# Categories that bite into a client SLA. Thomas (CEO) and Carlos (carrier ops)
# filter on these, so the set is named once instead of being retyped per query.
SLA_CRITICAL_CATEGORIES: frozenset[IncidentCategory] = frozenset(
    {IncidentCategory.LOST_PARCEL, IncidentCategory.CARRIER_ISSUE}
)


# The helpdesk reference a seeded incident came from (`incident_id` in the CSV,
# `contexts/indicent-file-analysis.md`): "TRF-" and six digits. Support staff
# quote it shortened ("ticket 482"), so `normalize_reference` restores the
# canonical form before anything is looked up.
REFERENCE_EXAMPLE = "TRF-000123"
REFERENCE_PATTERN = re.compile(r"^TRF-\d{6}$")
_LOOSE_REFERENCE = re.compile(r"^(?:TRF[\s-]*)?0*(\d{1,6})$", re.IGNORECASE)


def is_reference(value: str) -> bool:
    """True when `value` is a reference in canonical form, e.g. 'TRF-000123'."""
    return REFERENCE_PATTERN.fullmatch(value) is not None


def normalize_reference(raw: str) -> str | None:
    """'482', 'TRF-482', 'trf 000482' -> 'TRF-000482'; None if it is not a reference.

    Leading zeros are dropped before padding, so a number longer than six
    digits after them is not a reference.
    """
    match = _LOOSE_REFERENCE.fullmatch(raw.strip())
    if match is None or int(match.group(1)) == 0:
        return None
    return f"TRF-{int(match.group(1)):06d}"


def is_final_status(status: IncidentStatus) -> bool:
    """True when no transition leads out of `status`."""
    return not ALLOWED_TRANSITIONS[status]


def allowed_transitions_from(status: IncidentStatus) -> list[IncidentStatus]:
    """The statuses reachable from `status`, in lifecycle order."""
    reachable = ALLOWED_TRANSITIONS[status]
    return [candidate for candidate in IncidentStatus if candidate in reachable]


def can_transition(current: IncidentStatus, new: IncidentStatus) -> bool:
    """True when moving from `current` to `new` respects the lifecycle."""
    return new in ALLOWED_TRANSITIONS[current]


def explain_invalid_transition(current: IncidentStatus, new: IncidentStatus) -> str:
    """A plain-language reason why `current -> new` was refused.

    Written for whoever is looking at the form, not for a log: it names the
    states in the words the UI shows and says what can be done instead.
    """
    if current is new:
        return f"The incident is already '{current.value}'."
    if is_final_status(current):
        return f"'{current.value}' is a final state, so this incident can no longer change status."
    options = " or ".join(f"'{option.value}'" for option in allowed_transitions_from(current))
    return f"An incident that is '{current.value}' can only move to {options}, not '{new.value}'."
