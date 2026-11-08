"""Validation and transformation for the helpdesk CSV export.

This is the rule set the incident analyser project applied to
`incidents-trackflow.csv`, lifted out so the seed script rejects exactly the
rows the analyser counted as invalid. Keeping it here rather than inside
`/scripts` is what lets the two agree: the seed's totals per category and status
are meant to be cross-checked against the analyser's console report.

The CSV holds real customer email addresses. Every function in this module
treats `customer_email` as something to check and never to repeat: no issue
message, no return value and no log line carries the address itself.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from trackflow_shared.incidents import IncidentCategory, IncidentStatus

# --- CSV schema, as exported by the legacy helpdesk -------------------------

VALID_COUNTRIES: frozenset[str] = frozenset({"US", "ES"})

CARRIERS_BY_COUNTRY: dict[str, frozenset[str]] = {
    "US": frozenset({"UPS", "FEDEX", "DHL_US"}),
    "ES": frozenset({"MRW", "SEUR", "DHL_ES", "LOCAL_ES"}),
}

CSV_CATEGORIES: frozenset[str] = frozenset(
    {"LOST_PARCEL", "DELAYED_DELIVERY", "WRONG_ADDRESS", "RETURN_REQUEST", "DAMAGE"}
)

CSV_STATUSES: frozenset[str] = frozenset({"OPEN", "CLOSED", "DISCARDED"})

MIN_TRACKING_NUMBER_LENGTH = 8
MIN_DESCRIPTION_LENGTH = 5
SATISFACTION_SCORE_RANGE = (1, 5)


# --- CSV -> incident model mapping -----------------------------------------

# The analyser's five categories are narrower than the manager's nine. Each
# target below is the category whose own definition in
# `contexts/centralized-incident.md` names the CSV case outright: `carrier_issue`
# is defined as "delay, damage, SLA breach", which is why both DELAYED_DELIVERY
# and DAMAGE land there, and `delivery_failure` is defined as "failed attempt,
# incorrect address", which is why WRONG_ADDRESS does.
CATEGORY_MAP: dict[str, IncidentCategory] = {
    "LOST_PARCEL": IncidentCategory.LOST_PARCEL,
    "DELAYED_DELIVERY": IncidentCategory.CARRIER_ISSUE,
    "WRONG_ADDRESS": IncidentCategory.DELIVERY_FAILURE,
    "RETURN_REQUEST": IncidentCategory.RETURNS_ISSUE,
    "DAMAGE": IncidentCategory.CARRIER_ISSUE,
}

# The helpdesk had no "being worked on" state, so nothing maps to `in_progress`:
# a seeded incident starts in the state the helpdesk last recorded.
STATUS_MAP: dict[str, IncidentStatus] = {
    "OPEN": IncidentStatus.OPEN,
    "CLOSED": IncidentStatus.RESOLVED,
    "DISCARDED": IncidentStatus.DISCARDED,
}

# The CSV has no `title` column, only a one-line `description`. That line
# becomes the title; anything longer than this is cut on a word boundary and the
# untouched original stays in `description`.
MAX_DERIVED_TITLE_LENGTH = 80


# --- Validation -------------------------------------------------------------


@dataclass(frozen=True)
class ValidationIssue:
    """One broken rule on one row.

    `rule` is the stable key the console report groups by; `message` is the line
    a human reads. `field` names the CSV column at fault.
    """

    rule: str
    field: str
    message: str


# Report order, so the console summary always lists rules the same way.
VALIDATION_RULES: tuple[str, ...] = (
    "invalid_country",
    "invalid_carrier",
    "invalid_tracking_number",
    "invalid_category",
    "empty_description",
    "invalid_email",
    "closed_without_score",
    "score_out_of_range",
    "invalid_status",
    "invalid_date",
)


def _text(row: Mapping[str, str | None], field: str) -> str:
    return (row.get(field) or "").strip()


def validate_csv_row(row: Mapping[str, str | None]) -> list[ValidationIssue]:
    """Return every rule `row` breaks. An empty list means the row is valid.

    All rules are evaluated rather than short-circuiting on the first failure,
    because the console report counts how many rows fall into each rule type.
    """
    issues: list[ValidationIssue] = []

    country = _text(row, "country")
    carrier = _text(row, "carrier")
    tracking_number = _text(row, "tracking_number")
    category = _text(row, "category")
    description = _text(row, "description")
    email = _text(row, "customer_email")
    status = _text(row, "status")
    score = _text(row, "satisfaction_score")
    date = _text(row, "date")

    if country not in VALID_COUNTRIES:
        issues.append(
            ValidationIssue(
                "invalid_country",
                "country",
                f"Country must be US or ES, got {country!r}." if country else "Country is missing.",
            )
        )

    # A carrier is only judged against the declared country, so an unknown
    # country is reported once above rather than twice.
    if not carrier:
        issues.append(ValidationIssue("invalid_carrier", "carrier", "Carrier is missing."))
    elif country in CARRIERS_BY_COUNTRY and carrier not in CARRIERS_BY_COUNTRY[country]:
        issues.append(
            ValidationIssue(
                "invalid_carrier",
                "carrier",
                f"Carrier {carrier!r} does not operate in {country}.",
            )
        )

    if len(tracking_number) < MIN_TRACKING_NUMBER_LENGTH:
        issues.append(
            ValidationIssue(
                "invalid_tracking_number",
                "tracking_number",
                f"Tracking number must be at least {MIN_TRACKING_NUMBER_LENGTH} "
                f"characters, got {len(tracking_number)}.",
            )
        )

    if category not in CSV_CATEGORIES:
        issues.append(
            ValidationIssue(
                "invalid_category",
                "category",
                f"Category {category!r} is not one of the five valid categories."
                if category
                else "Category is missing.",
            )
        )

    if len(description) < MIN_DESCRIPTION_LENGTH:
        issues.append(
            ValidationIssue(
                "empty_description",
                "description",
                f"Description must be at least {MIN_DESCRIPTION_LENGTH} characters, "
                f"got {len(description)}.",
            )
        )

    # Deliberately only an "@" check, matching the analyser, and deliberately
    # without echoing the address: the value is customer personal data.
    if "@" not in email:
        issues.append(
            ValidationIssue(
                "invalid_email",
                "customer_email",
                "Customer email is missing or has no '@'.",
            )
        )

    if status not in CSV_STATUSES:
        issues.append(
            ValidationIssue(
                "invalid_status",
                "status",
                f"Status {status!r} is not one of OPEN, CLOSED or DISCARDED."
                if status
                else "Status is missing.",
            )
        )

    low, high = SATISFACTION_SCORE_RANGE
    if not score:
        if status == "CLOSED":
            issues.append(
                ValidationIssue(
                    "closed_without_score",
                    "satisfaction_score",
                    "A CLOSED incident must carry a satisfaction score.",
                )
            )
    else:
        try:
            value = int(score)
        except ValueError:
            issues.append(
                ValidationIssue(
                    "score_out_of_range",
                    "satisfaction_score",
                    f"Satisfaction score must be a whole number, got {score!r}.",
                )
            )
        else:
            if not low <= value <= high:
                issues.append(
                    ValidationIssue(
                        "score_out_of_range",
                        "satisfaction_score",
                        f"Satisfaction score must be between {low} and {high}, got {value}.",
                    )
                )

    # Not one of the analyser's rules, but the seed cannot invent a `created_at`
    # out of an unparseable date, so an unusable one is rejected here.
    if not parse_csv_date(date):
        issues.append(
            ValidationIssue(
                "invalid_date",
                "date",
                f"Date must be formatted YYYY-MM-DD, got {date!r}." if date else "Date is missing.",
            )
        )

    return issues


def parse_csv_date(value: str) -> datetime | None:
    """Read a `YYYY-MM-DD` cell as a UTC timestamp, or None if unreadable.

    The CSV records a day with no time, so the timestamp is pinned to midnight
    UTC. That preserves the original date, which is what matters for the
    historical seed.
    """
    try:
        day = datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        return None
    return day.replace(tzinfo=timezone.utc)


def derive_title(description: str, max_length: int = MAX_DERIVED_TITLE_LENGTH) -> str:
    """Shorten a description into the brief title the model requires.

    The cut lands on a word boundary and gets an ellipsis, so an operative
    scanning the list sees a readable line rather than a severed word. Short
    descriptions pass through untouched, which is the common case: the export's
    longest line is 65 characters.
    """
    text = " ".join(description.split())
    if len(text) <= max_length:
        return text

    # Reserve one character for the ellipsis before looking for the boundary.
    window = text[: max_length - 1]
    boundary = window.rfind(" ")
    head = window[:boundary] if boundary > 0 else window
    return f"{head.rstrip(',;:. ')}…"
