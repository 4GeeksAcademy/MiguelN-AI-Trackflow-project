"""Load the helpdesk CSV export into the incident manager as historical data.

    uv run seed-incidents [path/to/incidents.csv]

Every row in the export was reported by a client company or an end consumer, so
each incident is seeded with `origin: "customer"`. The export has no `title` and
no facility, so the seed derives a title from the description and files every
record under the `central` branch, per `contexts/centralized-incident.md`.

Validation is not reimplemented here: the rules come from
`trackflow_shared.incidents_csv`, the same set the analyser project applied, so
the totals this script reports can be cross-checked against the analyser's
console output. Rows that break a rule are never inserted and are listed at the
end of the run.

Running it twice inserts nothing the second time. Each incident remembers the
`incident_id` of the helpdesk record it came from, and a row whose id is already
present is skipped. The CONTEXT offers `title + created_at` as a fallback key,
and this script uses it only for a row with no `incident_id` - in the supplied
export that pair repeats across 7 of the 100 rows, so keying every row on it
would silently drop real incidents as duplicates.
"""

import argparse
import csv
import sys
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from trackflow_shared.incidents import IncidentOrigin
from trackflow_shared.incidents_csv import (
    CATEGORY_MAP,
    STATUS_MAP,
    VALIDATION_RULES,
    ValidationIssue,
    derive_title,
    parse_csv_date,
    validate_csv_row,
)

from services.incidents import service
from services.incidents.db import get_db_path, get_table
from services.incidents.models import IncidentBranch, IncidentCreate

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CSV_PATH = ROOT_DIR / "data" / "raw" / "incidents-trackflow.csv"

# Fixed for every seeded record: the export is entirely customer-reported, and a
# customer complaint belongs to no particular facility.
SEED_ORIGIN = IncidentOrigin.CUSTOMER
SEED_BRANCH = IncidentBranch.CENTRAL

# Columns the seed reads. A file missing any of them is rejected before the
# first insert, rather than producing a run where every row fails the same way.
REQUIRED_COLUMNS = frozenset(
    {
        "date",
        "country",
        "carrier",
        "tracking_number",
        "category",
        "description",
        "status",
        "customer_email",
    }
)


@dataclass
class RejectedRow:
    """A row that was not inserted, and why.

    Identified by CSV line number and `incident_id` only. The rejected row may
    have failed on `customer_email`, and that value is customer personal data
    that must not reach the console.
    """

    line: int
    incident_id: str
    issues: list[ValidationIssue]


@dataclass
class SeedReport:
    """What one run did, for the console summary and for the tests."""

    csv_path: Path
    db_path: Path
    total_rows: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    rejected: list[RejectedRow] = field(default_factory=list)
    inserted_by_category: Counter[str] = field(default_factory=Counter)
    inserted_by_status: Counter[str] = field(default_factory=Counter)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    @property
    def issues_by_rule(self) -> Counter[str]:
        """How many rows fell into each rule.

        Counted per row, not per issue: a row that breaks two rules is counted
        once under each, which is how the analyser reports it.
        """
        counts: Counter[str] = Counter()
        for row in self.rejected:
            for rule in {issue.rule for issue in row.issues}:
                counts[rule] += 1
        return counts


class SeedError(Exception):
    """The run could not complete. The message says whether anything was inserted.

    Every message is written for whoever ran the script: what failed, and what
    to do about it. Re-running after a fix is always safe, because rows that were
    already seeded are skipped.
    """


# Not one of the analyser's CSV rules: a row that passes all of them can still
# break a limit of the incident model itself, e.g. a description longer than
# the model accepts. It is rejected like any other invalid row.
MODEL_RULE = "rejected_by_model"


def read_rows(csv_path: Path) -> Iterator[tuple[int, Mapping[str, str | None]]]:
    """Yield `(csv_line_number, row)` for each data row.

    Line numbers count the header, so they match what a spreadsheet shows and
    the first data row is line 2.
    """
    try:
        handle = csv_path.open(newline="", encoding="utf-8")
    except OSError as error:
        raise SeedError(f"{csv_path} could not be opened ({error.strerror}).") from error

    with handle:
        reader = csv.DictReader(handle)
        try:
            fieldnames = reader.fieldnames
        except csv.Error as error:
            raise SeedError(f"{csv_path} is not a readable CSV file ({error}).") from error

        if fieldnames is None:
            raise SeedError(f"{csv_path} is empty: no header row.")

        missing = REQUIRED_COLUMNS - set(fieldnames)
        if missing:
            raise SeedError(
                f"{csv_path} is missing required column(s): {', '.join(sorted(missing))}."
            )

        # start=2 because line 1 is the header, so the first data row is line 2
        # and the numbers match what a spreadsheet shows.
        try:
            yield from enumerate(reader, start=2)
        except csv.Error as error:
            raise SeedError(
                f"{csv_path} could not be parsed near line {reader.line_num} ({error}). "
                "Rows before it may already be seeded; fix the file and run again."
            ) from error


def to_incident(row: Mapping[str, str | None]) -> IncidentCreate:
    """Build the incident payload from a row already known to be valid.

    The description does double duty because the export has no title column: it
    is shortened into `title` and kept whole in `description`.
    """
    description = " ".join((row.get("description") or "").split())
    return IncidentCreate(
        title=derive_title(description),
        description=description,
        category=CATEGORY_MAP[(row.get("category") or "").strip()],
        status=STATUS_MAP[(row.get("status") or "").strip()],
        origin=SEED_ORIGIN,
        branch=SEED_BRANCH,
    )


def dedupe_key(row: Mapping[str, str | None], incident: IncidentCreate) -> str:
    """The value that identifies this helpdesk record across runs.

    `incident_id` when the export has one. Otherwise the CONTEXT's fallback,
    `title + created_at`, which is weaker: it cannot tell two incidents apart
    when a customer reported the same thing twice on the same day.
    """
    incident_id = (row.get("incident_id") or "").strip()
    if incident_id:
        return incident_id
    return f"{incident.title}|{(row.get('date') or '').strip()}"


def seed(csv_path: Path) -> SeedReport:
    """Insert every valid, not-yet-seeded row. Returns what happened.

    Rows are processed independently: one bad row does not stop the run, and
    one already-present row does not stop the rest from being inserted.
    """
    report = SeedReport(csv_path=csv_path, db_path=get_db_path())

    # Checked before the first row is touched, so an unreadable database stops
    # the run with nothing half-done. TinyDB reads the whole file on first use.
    try:
        len(get_table())
    except (OSError, ValueError) as error:
        raise SeedError(
            f"The database at {report.db_path} could not be read ({type(error).__name__}). "
            "Nothing was inserted."
        ) from error

    for line, row in read_rows(csv_path):
        report.total_rows += 1

        issues = validate_csv_row(row)
        if issues:
            report.rejected.append(
                RejectedRow(
                    line=line,
                    incident_id=(row.get("incident_id") or "").strip() or "(no id)",
                    issues=issues,
                )
            )
            continue

        row_id = (row.get("incident_id") or "").strip() or "(no id)"

        try:
            incident = to_incident(row)
        except ValidationError as error:
            report.rejected.append(
                RejectedRow(line=line, incident_id=row_id, issues=_model_issues(error))
            )
            continue

        key = dedupe_key(row, incident)

        if service.get_incident_by_source_id(key) is not None:
            report.skipped_existing += 1
            continue

        created_at = parse_csv_date(row.get("date") or "")
        if created_at is None:
            # Unreachable while validation rejects unreadable dates. Checked
            # explicitly rather than asserted - `python -O` strips asserts, and
            # the seed would then stamp a historical incident with today's date.
            report.rejected.append(
                RejectedRow(
                    line=line,
                    incident_id=row_id,
                    issues=[ValidationIssue("invalid_date", "date", "Date is missing or invalid.")],
                )
            )
            continue

        try:
            service.create_incident(incident, created_at=created_at, source_incident_id=key)
        except OSError as error:
            raise SeedError(
                f"Could not write to the database at {report.db_path} ({error.strerror}). "
                f"{report.inserted} row(s) were inserted before the failure; "
                "running again is safe, as rows already seeded are skipped."
            ) from error
        report.inserted += 1
        report.inserted_by_category[incident.category.value] += 1
        report.inserted_by_status[incident.status.value] += 1

    return report


def _model_issues(error: ValidationError) -> list[ValidationIssue]:
    """One issue per field the incident model refused, named by its CSV column.

    `include_input=False` because the rejected value can be customer text.
    `title` is derived from `description`, so both point at that column.
    """
    issues = []
    for problem in error.errors(include_input=False, include_url=False):
        model_field = str(problem["loc"][0]) if problem["loc"] else "row"
        column = "description" if model_field in {"title", "description"} else model_field
        issues.append(ValidationIssue(MODEL_RULE, column, f"{model_field}: {problem['msg']}."))
    return issues


# --- Console report ---------------------------------------------------------

_RULE_LABELS: dict[str, str] = {
    "invalid_country": "Missing or invalid country",
    "invalid_carrier": "Carrier missing or not valid for country",
    "invalid_tracking_number": "Missing or short tracking number",
    "invalid_category": "Missing or invalid category",
    "empty_description": "Missing or too-short description",
    "invalid_email": "Missing or invalid customer email",
    "closed_without_score": "Closed incident with no score",
    "score_out_of_range": "Satisfaction score out of range",
    "invalid_status": "Missing or invalid status",
    "invalid_date": "Missing or invalid date",
    MODEL_RULE: "Valid in the export, rejected by the incident model",
}


def _dotted(label: str, value: object, width: int = 44) -> str:
    """`label ......... value`, so the numbers line up in a column."""
    padding = "." * max(width - len(label) - 1, 1)
    return f"  {label} {padding} {value}"


def print_report(report: SeedReport) -> None:
    """Print the run summary, rejected rows last as the CONTEXT requires."""
    print("=" * 62)
    print("  TRACKFLOW - HISTORICAL INCIDENT SEED")
    print(f"  Source file: {report.csv_path.name}")
    print(f"  Database:    {report.db_path}")
    print("=" * 62)
    print()
    print(_dotted("TOTAL ROWS IN FILE", report.total_rows))
    print(_dotted("Inserted", report.inserted))
    print(_dotted("Skipped (already seeded)", report.skipped_existing))
    print(_dotted("Rejected (invalid)", report.rejected_count))

    if report.inserted_by_category:
        print()
        print("INSERTED BY CATEGORY")
        for category, count in sorted(report.inserted_by_category.items()):
            print(_dotted(category, count))

    if report.inserted_by_status:
        print()
        print("INSERTED BY STATUS")
        for status_value, count in sorted(report.inserted_by_status.items()):
            print(_dotted(status_value, count))

    if not report.rejected:
        print()
        print("No invalid records found.")
        return

    counts = report.issues_by_rule
    print()
    print("INVALID RECORDS BY RULE")
    for rule in (*VALIDATION_RULES, MODEL_RULE):
        if counts[rule]:
            print(_dotted(_RULE_LABELS.get(rule, rule), counts[rule]))

    print()
    print(f"REJECTED ROWS ({report.rejected_count}) - not inserted")
    for row in report.rejected:
        print(f"  line {row.line} [{row.incident_id}]")
        for issue in row.issues:
            print(f"      - {issue.field}: {issue.message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=DEFAULT_CSV_PATH,
        type=Path,
        help=f"CSV export to load (default: {DEFAULT_CSV_PATH.relative_to(ROOT_DIR)})",
    )
    args = parser.parse_args()

    csv_path: Path = args.csv_path
    if not csv_path.is_file():
        print(f"error: no such file: {csv_path}", file=sys.stderr)
        return 1

    try:
        report = seed(csv_path)
    except SeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except UnicodeDecodeError:
        print(f"error: {csv_path} is not valid UTF-8.", file=sys.stderr)
        return 1

    print_report(report)
    # Invalid rows are a fact about the export, not a failure of the run, so
    # they are reported without failing the exit status - unless every row was
    # invalid. That means the file is not the export this script reads (a new
    # date format, a different delimiter), and automation must not read it as
    # a successful seed.
    if report.total_rows > 0 and report.rejected_count == report.total_rows:
        print(
            f"error: every row in {csv_path.name} was rejected, so nothing was seeded. "
            "Check that this is the helpdesk export in its usual format.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
