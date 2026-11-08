"""Profile persistence. One profile per user, linked through `user_id`.

Stored in TinyDB only. PostgreSQL tables reference the TinyDB user id as
`user_uuid` and never duplicate this data.
"""

from uuid import uuid4

from tinydb import Query
from tinydb.table import Table

from services.core.db import get_table
from services.profiles.models import ProfileInDB, ProfileUpdate

TABLE_NAME = "profiles"


def _table() -> Table:
    return get_table(TABLE_NAME)


def create_profile(
    user_id: str,
    name: str | None = None,
    phone: str | None = None,
    address: str | None = None,
) -> ProfileInDB:
    record = ProfileInDB(
        id=str(uuid4()),
        user_id=user_id,
        name=name,
        phone=phone,
        address=address,
    )
    _table().insert(record.model_dump(mode="json"))
    return record


def get_profile_by_user(user_id: str) -> ProfileInDB | None:
    doc = _table().get(Query().user_id == user_id)
    return None if doc is None else ProfileInDB(**doc)


def update_profile_by_user(user_id: str, payload: ProfileUpdate) -> ProfileInDB | None:
    profile = get_profile_by_user(user_id)
    if profile is None:
        return None

    changes = payload.model_dump(mode="json", exclude_unset=True)
    if changes:
        _table().update(changes, Query().user_id == user_id)
    return get_profile_by_user(user_id)


def delete_profile_by_user(user_id: str) -> bool:
    removed = _table().remove(Query().user_id == user_id)
    return bool(removed)
