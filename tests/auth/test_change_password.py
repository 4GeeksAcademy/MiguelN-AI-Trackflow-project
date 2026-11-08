"""`POST /auth/change-password`: a signed-in user choosing a new password.

A session alone is not enough - the current password is asked for, so a
browser left signed in cannot be used to lock the owner out. A wrong current
password is a 400, not a 401: a 401 would sign the UI out, and the session is
fine.
"""

import pytest
from pydantic import ValidationError
from tinydb import Query

from services.auth import router as auth_router
from services.auth.dependencies import get_current_user
from services.auth.models import ChangePasswordRequest
from services.core import security
from services.core.db import get_table
from services.users import service as users_service
from tests.auth.conftest import NEW_PASSWORD, PASSWORD, refusal, reset_link_is_dead, signs_in


def change(token: str, current: str, new: str):
    """The route as FastAPI runs it: the gate first, then the handler."""
    caller = get_current_user(token)
    request = ChangePasswordRequest(current_password=current, new_password=new)
    return auth_router.change_password(request, caller)


def test_the_right_current_password_changes_it_and_keeps_the_session(make_user, access_token_for):
    user = make_user(email="ana@trackflow.com")
    token = access_token_for(user)

    change(token, PASSWORD, NEW_PASSWORD)

    assert signs_in("ana@trackflow.com", NEW_PASSWORD)
    assert not signs_in("ana@trackflow.com", PASSWORD)
    assert get_current_user(token).id == user.id, "documented: the session survives"


def test_a_wrong_current_password_is_a_400_and_changes_nothing(make_user, access_token_for):
    token = access_token_for(make_user(email="ana@trackflow.com"))

    refused = refusal(lambda: change(token, "not-the-password", NEW_PASSWORD))

    assert refused.status_code == 400
    assert refused.detail == "Current password is incorrect"
    assert signs_in("ana@trackflow.com", PASSWORD)


def test_changing_the_password_spends_outstanding_reset_links(
    make_user, access_token_for, issue_reset_token
):
    """A pending link would otherwise let someone undo the change."""
    user = make_user(email="ana@trackflow.com")
    link = issue_reset_token(user)

    change(access_token_for(user), PASSWORD, NEW_PASSWORD)

    assert reset_link_is_dead(link)
    assert signs_in("ana@trackflow.com", NEW_PASSWORD)


@pytest.mark.parametrize(
    "current, new, field",
    [
        ("", NEW_PASSWORD, "current_password"),
        (PASSWORD, "short7!", "new_password"),
        (PASSWORD, "x" * 73, "new_password"),
        (PASSWORD, "Contraseña" + "x" * 62, "new_password"),
        (PASSWORD, "correct\x00horse", "new_password"),
    ],
    ids=["empty current", "7-character new", "73-character new", "73-byte new", "NUL new"],
)
def test_an_invalid_payload_is_refused_by_validation(current, new, field):
    with pytest.raises(ValidationError) as refused:
        ChangePasswordRequest(current_password=current, new_password=new)

    assert refused.value.errors()[0]["loc"] == (field,)


def test_an_account_with_a_password_shorter_than_todays_rule_can_still_change_it(
    make_user, access_token_for
):
    """`current_password` has no length floor on purpose: older accounts must be
    able to move to a compliant password."""
    user = make_user(email="ana@trackflow.com")
    get_table(users_service.TABLE_NAME).update(
        {"hashed_password": security.hash_password("abc123")}, Query().id == user.id
    )

    change(access_token_for(user), "abc123", NEW_PASSWORD)

    assert signs_in("ana@trackflow.com", NEW_PASSWORD)


def test_an_account_deleted_after_the_session_check_is_not_written_back(
    make_user, access_token_for
):
    """A race with a deletion: the gate resolved the account, then it vanished."""
    user = make_user(email="ana@trackflow.com")
    caller = get_current_user(access_token_for(user))
    users_service.delete_user(user.id)
    request = ChangePasswordRequest(current_password=PASSWORD, new_password=NEW_PASSWORD)

    refused = refusal(lambda: auth_router.change_password(request, caller))

    assert refused.status_code == 400
    assert users_service.get_user(user.id) is None


def test_the_same_password_again_is_accepted(make_user, access_token_for):
    """Pinned, not endorsed (TESTING.md, open questions)."""
    token = access_token_for(make_user(email="ana@trackflow.com"))

    change(token, PASSWORD, PASSWORD)

    assert signs_in("ana@trackflow.com", PASSWORD)
