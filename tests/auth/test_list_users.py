"""`GET /users`: every account, as any signed-in user sees it.

The route's only guard is the session gate (`test_current_user.py` pins that
it is attached). What it decides is what the list contains - and that one
damaged record cannot take the whole list down.
"""

from services.auth.dependencies import get_current_user
from services.core.db import get_table
from services.users import router as users_router
from services.users import service as users_service
from services.users.models import Role


def test_lists_every_account_without_any_password_hash(make_user):
    make_user(email="ana@trackflow.com")
    make_user(email="ben@trackflow.com", role=Role.MANAGER)

    listed = users_router.list_users()

    assert {user.email for user in listed} == {"ana@trackflow.com", "ben@trackflow.com"}
    assert all("hashed_password" not in user.model_dump() for user in listed)


def test_a_deactivated_account_is_listed_as_inactive(make_user):
    make_user(email="former@trackflow.com", is_active=False)

    [listed] = users_router.list_users()

    assert listed.is_active is False


def test_an_empty_table_lists_nothing():
    assert users_router.list_users() == []


def test_one_unreadable_row_is_skipped_and_logged_not_a_500(make_user, caplog):
    """F-7. A row edited by hand, or written before a rule changed, used to make
    the whole list fail. The incident list already skips such rows."""
    make_user(email="ana@trackflow.com")
    get_table(users_service.TABLE_NAME).insert({"id": "broken-row", "email": "x@trackflow.com"})

    listed = users_router.list_users()

    assert [user.email for user in listed] == ["ana@trackflow.com"]
    assert "broken-row" in caplog.text


def test_any_signed_in_user_can_list_every_account(make_user, access_token_for):
    """Pinned, not endorsed: whether this should be admin-only is an open
    question (TESTING.md). The session gate is the route's only check."""
    plain = make_user(email="plain@trackflow.com")
    make_user(email="boss@trackflow.com", role=Role.ADMIN)

    get_current_user(access_token_for(plain))
    listed = users_router.list_users()

    assert {user.email for user in listed} == {"plain@trackflow.com", "boss@trackflow.com"}
