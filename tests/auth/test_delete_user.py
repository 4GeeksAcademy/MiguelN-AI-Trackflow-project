"""`DELETE /users/{user_id}`: closing an account.

Self or admin only. A closed account must stay closed: its session, its reset
links and its id must all die with it, even if someone registers the same
email again.
"""

import pytest

from services.auth.dependencies import get_current_user
from services.profiles import service as profiles_service
from services.users import router as users_router
from services.users import service as users_service
from services.users.models import Role, UserCreate
from tests.auth.conftest import PASSWORD, refusal, reset_link_is_dead, session_refusal


def delete(target_id: str, caller):
    return users_router.delete_user(target_id, caller)


def test_closing_your_own_account_removes_it_and_its_profile(make_user):
    ana = make_user(email="ana@trackflow.com", name="Ana Ruiz")

    delete(ana.id, ana)

    assert users_service.get_user(ana.id) is None
    assert profiles_service.get_profile_by_user(ana.id) is None


@pytest.mark.parametrize("target", ["another account", "an id that does not exist"])
def test_a_non_admin_cannot_close_anyone_else(target, make_user):
    ana = make_user(email="ana@trackflow.com")
    ben = make_user(email="ben@trackflow.com")
    target_id = ben.id if target == "another account" else "no-such-id"

    assert refusal(lambda: delete(target_id, ana)).status_code == 403
    assert users_service.get_user(ben.id) is not None


def test_a_closed_account_stays_closed(make_user, access_token_for, issue_reset_token):
    """Its session and reset link die with it, and re-registering the same email
    creates a new account the old token cannot reach."""
    ana = make_user(email="ana@trackflow.com")
    old_session = access_token_for(ana)
    old_link = issue_reset_token(ana)

    delete(ana.id, ana)
    newcomer = users_service.create_user(UserCreate(email="ana@trackflow.com", password=PASSWORD))

    assert session_refusal(old_session).status_code == 401
    assert reset_link_is_dead(old_link)
    assert newcomer.id != ana.id


def test_an_admin_can_close_another_account(make_user):
    boss = make_user(email="boss@trackflow.com", role=Role.ADMIN)
    ana = make_user(email="ana@trackflow.com")

    delete(ana.id, boss)

    assert users_service.get_user(ana.id) is None


def test_an_admin_closing_an_unknown_account_gets_not_found(make_user):
    boss = make_user(role=Role.ADMIN)

    assert refusal(lambda: delete("no-such-id", boss)).status_code == 404


def test_closing_twice_is_refused_the_second_time(make_user, access_token_for):
    """Your own second attempt fails at the session gate; an admin's is a 404."""
    boss = make_user(email="boss@trackflow.com", role=Role.ADMIN)
    ana = make_user(email="ana@trackflow.com")
    ana_session = access_token_for(ana)

    delete(ana.id, get_current_user(ana_session))

    assert session_refusal(ana_session).status_code == 401
    assert refusal(lambda: delete(ana.id, boss)).status_code == 404


def test_an_account_can_be_closed_with_the_session_alone(make_user):
    """Pinned, not endorsed: the borrowed-session risk BUG-3 closed for email and
    password still applies here, and to an admin closing the last admin
    (TESTING.md, open questions)."""
    boss = make_user(email="boss@trackflow.com", role=Role.ADMIN)

    delete(boss.id, boss)

    assert users_service.list_users() == []
