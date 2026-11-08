"""`POST /users`: who may open an account, and what it is opened with.

Registration is public, so it is where privilege escalation, duplicate
accounts and half-written records would start. The password rule lives on the
shared `Password` type, so the boundaries pinned here hold for reset, change
and admin edits too.
"""

import json
import uuid
from datetime import timedelta

import pytest
from pydantic import ValidationError
from tinydb.table import Document

from services.core import security
from services.core.db import get_table
from services.profiles import service as profiles_service
from services.users import router as users_router
from services.users import service as users_service
from services.users.models import Role, UserCreate
from tests.auth.conftest import PASSWORD, refusal

# 72 characters, but 73 bytes: the one "ñ" takes two.
ACCENTED_72_CHARS = "Contraseña" + "x" * 62


def register(**fields):
    payload = {"email": "ana@trackflow.com", "password": PASSWORD, **fields}
    return users_router.register_user(UserCreate(**payload))


def stored_rows() -> list[Document]:
    return get_table(users_service.TABLE_NAME).all()


def test_a_new_account_is_a_plain_active_user_with_a_hashed_password_and_a_profile():
    account = register(
        name="Ana Ruiz", phone="+52 81 5555 0000", address="Av. Constitución 100, Monterrey"
    )

    stored = users_service.get_user(account.id)
    assert stored.role is Role.USER
    assert stored.is_active
    assert uuid.UUID(account.id).version == 4
    assert account.created_at.utcoffset() == timedelta(0), "stamped in UTC"
    assert stored.hashed_password.startswith("$2b$")
    assert security.verify_password(PASSWORD, stored.hashed_password)
    assert PASSWORD not in json.dumps(stored_rows()), "the password is never stored"
    assert "hashed_password" not in account.model_dump(), "the hash is never handed back"
    profile = profiles_service.get_profile_by_user(account.id)
    assert (profile.name, profile.phone, profile.address) == (
        "Ana Ruiz",
        "+52 81 5555 0000",
        "Av. Constitución 100, Monterrey",
    )


def test_nobody_can_grant_themselves_privileges_at_sign_up():
    account = register(
        role="admin",
        is_active=False,
        id="chosen-id",
        hashed_password="$2b$12$attacker-chosen-hash",
        created_at="1999-01-01T00:00:00Z",
    )

    stored = users_service.get_user(account.id)
    assert stored.role is Role.USER
    assert stored.is_active
    assert account.id != "chosen-id"
    assert stored.hashed_password != "$2b$12$attacker-chosen-hash"
    assert stored.created_at.year != 1999


@pytest.mark.parametrize(
    "second_email",
    [
        "ana@trackflow.com",
        "ANA@TrackFlow.com",
        "  ana@trackflow.com  ",
        "Ana Ruiz <ana@trackflow.com>",
    ],
    ids=["exact", "other case", "spaces", "display name"],
)
def test_a_second_account_for_the_same_email_is_refused(second_email):
    register(email="ana@trackflow.com")

    refused = refusal(lambda: register(email=second_email))

    assert refused.status_code == 409
    assert "ana@trackflow.com" not in str(refused.detail), "the address is not echoed back"
    assert len(stored_rows()) == 1


def test_a_plus_tag_address_is_a_separate_account():
    """Not an alias: some teams use `+ops` addresses as shared inboxes."""
    register(email="ana@trackflow.com")
    register(email="ana+ops@trackflow.com")

    assert len(stored_rows()) == 2


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"password": PASSWORD}, "email"),
        ({"email": "ana@trackflow.com"}, "password"),
        ({"email": "", "password": PASSWORD}, "email"),
        ({"email": "ana@trackflow.com", "password": ""}, "password"),
    ],
    ids=["email missing", "password missing", "email empty", "password empty"],
)
def test_an_empty_or_missing_credential_is_refused(payload, field):
    with pytest.raises(ValidationError) as refused:
        UserCreate(**payload)

    assert refused.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize(
    "length, accepted",
    [(7, False), (8, True), (72, True), (73, False)],
)
def test_the_password_length_boundaries(length, accepted):
    password = "p" * length

    if accepted:
        assert security.verify_password(
            password, users_service.get_user(register(password=password).id).hashed_password
        )
    else:
        with pytest.raises(ValidationError):
            UserCreate(email="ana@trackflow.com", password=password)


@pytest.mark.parametrize(
    "password",
    [ACCENTED_72_CHARS, "ñ" * 37, "🔐" * 19],
    ids=["72 chars, 73 bytes", "37 ñ, 74 bytes", "19 emoji, 76 bytes"],
)
def test_a_password_over_72_bytes_is_refused_by_validation_not_a_crash(password):
    """BUG-4. bcrypt reads bytes, the old rule counted characters. Accented
    passwords are ordinary for users in Mexico and Spain; past 72 bytes bcrypt
    raises, which used to surface as a 500."""
    assert len(password) <= 72 < len(password.encode("utf-8"))

    with pytest.raises(ValidationError) as refused:
        UserCreate(email="ana@trackflow.com", password=password)

    assert refused.value.errors()[0]["loc"] == ("password",)


def test_a_password_containing_nul_is_refused_by_validation_not_a_crash():
    """BUG-5. bcrypt refuses NUL outright; JSON can carry it as `\\u0000`."""
    with pytest.raises(ValidationError) as refused:
        UserCreate(email="ana@trackflow.com", password="correct\x00horse")

    assert refused.value.errors()[0]["loc"] == ("password",)


def test_a_failed_profile_insert_rolls_the_account_back(monkeypatch):
    """A user row without its profile would block that email for good."""

    def unavailable(**_fields):
        raise RuntimeError("profiles table unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(profiles_service, "create_profile", unavailable)
        with pytest.raises(RuntimeError):
            register()

    assert stored_rows() == []
    assert register().email == "ana@trackflow.com", "the address is free again"


def test_a_password_bcrypt_cannot_hash_leaves_no_account_behind():
    """The hash is computed before anything is written. Reaching the service
    requires skipping validation, which is what `model_construct` does here."""
    payload = UserCreate.model_construct(
        email="ana@trackflow.com", password="ñ" * 72, name=None, phone=None, address=None
    )

    with pytest.raises(ValueError):
        users_service.create_user(payload)

    assert stored_rows() == []


@pytest.mark.parametrize("field, limit", [("name", 120), ("phone", 40), ("address", 255)])
def test_profile_fields_are_capped_at_their_column_size(field, limit):
    register(**{field: "x" * limit})

    with pytest.raises(ValidationError) as refused:
        UserCreate(email="ben@trackflow.com", password=PASSWORD, **{field: "x" * (limit + 1)})

    assert refused.value.errors()[0]["loc"] == (field,)


def test_omitted_profile_fields_are_stored_as_null():
    profile = profiles_service.get_profile_by_user(register().id)

    assert (profile.name, profile.phone, profile.address) == (None, None, None)


def test_an_all_spaces_password_is_accepted():
    """Pinned, not endorsed: there is no complexity rule (TESTING.md, open
    questions). Changing this should be a decision, not an accident."""
    account = register(password=" " * 8)

    assert security.verify_password(" " * 8, users_service.get_user(account.id).hashed_password)


def test_an_international_address_is_accepted_and_lower_cased():
    account = register(email="José.Pérez@Almacén.es")

    assert account.email == "josé.pérez@almacén.es"
