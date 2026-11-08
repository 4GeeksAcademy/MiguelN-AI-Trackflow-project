import json
import sys
from datetime import datetime, timezone

from pydantic import ValidationError
from tinydb import Query

from services.suppliers.db import get_db_path, get_table
from services.suppliers.models import SupplierCreate, SupplierInDB
from services.suppliers.seed_data import SUPPLIERS_SEED

# What reading or writing the TinyDB file can raise: the file is missing its
# permissions or its disk, or it is not valid JSON any more.
STORAGE_ERRORS = (OSError, json.JSONDecodeError)


def main() -> None:
    # Every seed entry is validated before anything is written, so a bad entry
    # in `seed_data.py` stops the run with nothing half-inserted.
    payloads = []
    for position, raw in enumerate(SUPPLIERS_SEED, start=1):
        try:
            payloads.append(SupplierCreate(**raw))
        except ValidationError as error:
            name = raw.get("name") or f"entry {position}"
            print(
                f"error: seed supplier {name!r} is invalid. Nothing was inserted.", file=sys.stderr
            )
            for problem in error.errors(include_input=False, include_url=False):
                field = ".".join(str(part) for part in problem["loc"]) or "entry"
                print(f"  {field}: {problem['msg']}", file=sys.stderr)
            raise SystemExit(1) from error

    table = get_table()
    supplier = Query()
    inserted = 0
    skipped = 0

    for payload in payloads:
        try:
            if table.contains(supplier.name == payload.name):
                skipped += 1
                continue
            record = SupplierInDB(
                **payload.model_dump(),
                updated_at=datetime.now(timezone.utc),
            )
            table.insert(record.model_dump(mode="json"))
        except STORAGE_ERRORS as error:
            print(
                f"error: the supplier database at {get_db_path()} could not be read or written "
                f"({type(error).__name__}). {inserted} supplier(s) were inserted before the "
                "failure; running again is safe, as suppliers already present are skipped.",
                file=sys.stderr,
            )
            raise SystemExit(1) from error
        inserted += 1

    print(f"Database: {get_db_path()}")
    print(f"Inserted {inserted} suppliers ({skipped} skipped, already present).")
    print(f"Total suppliers in directory: {len(table)}")


if __name__ == "__main__":
    main()
