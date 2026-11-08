"""Create a deterministic, synthetic dataset in a new TinyDB scratch file."""

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from tinydb import TinyDB
from trackflow_shared.incidents import (
    IncidentBranch,
    IncidentCategory,
    IncidentOrigin,
    IncidentStatus,
)

from services.core.db import DEFAULT_DB_PATH
from services.incidents.models import IncidentInDB
from services.suppliers.models import (
    CURRENCY_BY_COUNTRY,
    Category,
    Country,
    SupplierInDB,
    SupplierStatus,
)

_BASE_TIME = datetime(2023, 1, 1, tzinfo=UTC)
_INCIDENT_AREAS = (
    "Receiving dock",
    "Outbound staging",
    "Returns inspection",
    "Carrier handoff",
    "Inventory aisle",
    "Customer escalation",
)


def _incident_rows(count: int) -> list[dict[str, object]]:
    statuses = tuple(IncidentStatus)
    categories = tuple(IncidentCategory)
    origins = tuple(IncidentOrigin)
    branches = tuple(IncidentBranch)
    rows = []

    for index in range(count):
        created_at = _BASE_TIME + timedelta(minutes=index * 17)
        area = _INCIDENT_AREAS[index % len(_INCIDENT_AREAS)]
        record = IncidentInDB(
            id=str(uuid5(NAMESPACE_URL, f"trackflow-performance-incident-{index}")),
            title=f"{area} exception {index:05d}",
            description=(
                f"Synthetic scale-test record {index:05d}: scan reconciliation, "
                f"package condition, and handoff details for {area.lower()}."
            ),
            category=categories[index % len(categories)],
            origin=origins[(index // 2) % len(origins)],
            branch=branches[(index // 3) % len(branches)],
            status=statuses[(index // 5) % len(statuses)],
            created_at=created_at,
            updated_at=created_at + timedelta(minutes=index % 90),
            source_incident_id=f"performance-fixture-{index:05d}",
        )
        rows.append(record.model_dump(mode="json"))

    return rows


def _supplier_rows(count: int) -> list[dict[str, object]]:
    countries = tuple(Country)
    categories = tuple(Category)
    statuses = tuple(SupplierStatus)
    rows = []

    for index in range(count):
        country = countries[index % len(countries)]
        category = categories[(index // 2) % len(categories)]
        record = SupplierInDB(
            name=f"Synthetic {category.value.replace('_', ' ')} partner {index:05d}",
            country=country,
            categories=[category, categories[(index + 3) % len(categories)]],
            rate_per_shipment=round(0.25 + ((index * 37) % 480) / 100, 2),
            currency=CURRENCY_BY_COUNTRY[country],
            status=statuses[(index // 4) % len(statuses)],
            service_zone=f"Synthetic zone {index % 24:02d}",
            updated_at=_BASE_TIME + timedelta(days=index % 1200),
        )
        rows.append(record.model_dump(mode="json"))

    return rows


def seed_performance_data(
    db_path: Path,
    *,
    incident_count: int = 5_000,
    supplier_count: int = 5_000,
) -> tuple[int, int]:
    """Populate only a new non-default DB file; existing files are never opened."""
    resolved_path = db_path.expanduser().resolve()
    if resolved_path == DEFAULT_DB_PATH.resolve():
        raise ValueError("Refusing to write performance data to the default database.")
    if resolved_path.exists():
        raise ValueError(f"Refusing to overwrite existing database: {resolved_path}")
    if incident_count < 0 or supplier_count < 0:
        raise ValueError("Row counts must be zero or greater.")

    incident_rows = _incident_rows(incident_count)
    supplier_rows = _supplier_rows(supplier_count)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    with TinyDB(resolved_path, indent=2, ensure_ascii=False) as database:
        database.table("incidents").insert_multiple(incident_rows)
        database.table("suppliers").insert_multiple(supplier_rows)

    return incident_count, supplier_count


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path", type=Path, required=True, help="new scratch TinyDB file"
    )
    parser.add_argument("--incidents", type=_non_negative_int, default=5_000)
    parser.add_argument("--suppliers", type=_non_negative_int, default=5_000)
    args = parser.parse_args()

    try:
        incident_count, supplier_count = seed_performance_data(
            args.db_path,
            incident_count=args.incidents,
            supplier_count=args.suppliers,
        )
    except ValueError as error:
        parser.error(str(error))

    print(
        f"Created {args.db_path}: incidents={incident_count}, suppliers={supplier_count}. "
        "Synthetic data only; remove this scratch file when finished."
    )


if __name__ == "__main__":
    main()
