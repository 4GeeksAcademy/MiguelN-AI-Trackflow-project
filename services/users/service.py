"""User persistence and registration rules.

Users live in TinyDB only. The `id` is a UUID string because PostgreSQL tables
(inventory and friends) reference it as `user_uuid` — a TinyDB `doc_id` integer
would not survive that crossing.
"""

import logging
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError
from tinydb import Query
from tinydb.table import Table

from services.core.db import get_table
from services.core.errors import EmailAlreadyRegistered
from services.core.security import hash_password
from services.profiles import service as profiles_service
from services.users.models import Role, UserCreate, UserInDB, UserUpdate, normalize_email

TABLE_NAME = "users"

logger = logging.getLogger(__name__)


def _table() -> Table:
    return get_table(TABLE_NAME)


def create_user(payload: UserCreate) -> UserInDB:
    """Register a user and its linked profile as one operation.

    Raises `EmailAlreadyRegistered` when the address is taken. If the profile
    insert fails the user row is rolled back, so no account is left without one.
    """
    if get_user_by_email(payload.email) is not None:
        raise EmailAlreadyRegistered(payload.email)

    record = UserInDB(
        id=str(uuid4()),
        email=payload.email,
        hashed_password=hash_password(payload.password),
        is_active=True,
        role=Role.USER,
        created_at=datetime.now(timezone.utc),
    )
    _table().insert(record.model_dump(mode="json"))

    try:
        profiles_service.create_profile(
            user_id=record.id,
            name=payload.name,
            phone=payload.phone,
            address=payload.address,
        )
    except Exception:
        _table().remove(Query().id == record.id)
        raise

    return record


def get_user(user_id: str) -> UserInDB | None:
    doc = _table().get(Query().id == user_id)
    return None if doc is None else UserInDB(**doc)


def get_user_by_email(email: str) -> UserInDB | None:
    doc = _table().get(Query().email == normalize_email(email))
    return None if doc is None else UserInDB(**doc)


def list_users() -> list[UserInDB]:
    """Every stored user that still reads as one.

    A row that no longer fits the model - edited by hand, or written before a
    rule changed - is skipped and logged by id, so one bad row cannot turn the
    whole list into a 500. Same rule as the incident list.
    """
    users = []
    for doc in _table().all():
        try:
            users.append(UserInDB(**doc))
        except ValidationError:
            logger.warning(
                "Skipping stored user %s: it no longer matches the model", doc.get("id", "?")
            )
    return users


def update_user(user_id: str, payload: UserUpdate) -> UserInDB | None:
    """Apply credential changes. Returns None when the user does not exist."""
    if get_user(user_id) is None:
        return None

    # `current_password` is the caller's proof of identity, checked by the
    # router; it must never be written to the user row.
    changes = payload.model_dump(
        mode="json", exclude_unset=True, exclude_none=True, exclude={"current_password"}
    )

    new_email = changes.get("email")
    if new_email is not None:
        existing = get_user_by_email(new_email)
        if existing is not None and existing.id != user_id:
            raise EmailAlreadyRegistered(new_email)

    if "password" in changes:
        changes["hashed_password"] = hash_password(changes.pop("password"))

    if changes:
        _table().update(changes, Query().id == user_id)
    return get_user(user_id)


def delete_user(user_id: str) -> bool:
    """Delete the user and its linked profile. Returns False when not found."""
    if get_user(user_id) is None:
        return False

    _table().remove(Query().id == user_id)
    profiles_service.delete_profile_by_user(user_id)
    return True
