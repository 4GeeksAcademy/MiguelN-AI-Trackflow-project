import os
from functools import lru_cache
from pathlib import Path

from tinydb import TinyDB
from tinydb.table import Table

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT_DIR / "data" / "suppliers.json"


def get_db_path() -> Path:
    return Path(os.getenv("DB_PATH", DEFAULT_DB_PATH))


@lru_cache(maxsize=1)
def get_db() -> TinyDB:
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return TinyDB(path, indent=2, ensure_ascii=False)


def get_table(name: str) -> Table:
    """Return a TinyDB table. Each domain owns its own table name."""
    return get_db().table(name)
