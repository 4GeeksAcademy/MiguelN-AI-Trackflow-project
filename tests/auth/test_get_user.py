"""`GET /users/{user_id}`: one account, by id.

The id comes straight from the path, so it is attacker-controlled: any shape of
id must end in a clean "not found", never a crash.
"""

import pytest

from services.auth.dependencies import get_current_user
from services.users import router as users_router
from services.users import service as users_service
from services.users.models import Role
from tests.auth.conftest import refusal


def test_an_existing_id_returns_that_account_without_its_hash(make_user):
    user = make_user(email="ana@trackflow.com")

    found = users_router.get_user(user.id)

    assert (found.id, found.email) == (user.id, "ana@trackflow.com")
    assert "hashed_password" not in found.model_dump()


@pytest.mark.parametrize(
    "user_id",
    ["does-not-exist", "../users", "x" * 1000, "ñandú-🦙", ""],
    ids=["unknown", "path traversal", "1000 characters", "unicode", "empty"],
)
def test_an_unknown_or_oddly_shaped_id_is_not_found(user_id, make_user):
    make_user()

    assert refusal(lambda: users_router.get_user(user_id)).status_code == 404


def test_an_account_deleted_after_it_was_listed_is_not_found(make_user):
    user = make_user()
    [listed] = users_router.list_users()
    users_service.delete_user(user.id)

    assert refusal(lambda: users_router.get_user(listed.id)).status_code == 404


def test_any_signed_in_user_can_read_any_account(make_user, access_token_for):
    """Pinned, not endorsed: an open question (TESTING.md)."""
    plain = make_user(email="plain@trackflow.com")
    boss = make_user(email="boss@trackflow.com", role=Role.ADMIN)

    get_current_user(access_token_for(plain))

    assert users_router.get_user(boss.id).email == "boss@trackflow.com"
