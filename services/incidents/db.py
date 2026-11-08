from pathlib import Path

from tinydb.table import Table

from services.core import db as core_db

TABLE_NAME = "incidents"


def get_db_path() -> Path:
    return core_db.get_db_path()


def get_table() -> Table:
    return core_db.get_table(TABLE_NAME)
