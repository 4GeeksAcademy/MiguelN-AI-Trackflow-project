"""`GET /auth/me`: what the signed-in account learns about itself.

Both apps build their session from this route, so it must reflect the account
as it is now and must cope with an account that has no profile row.
"""

from services.auth import router as auth_router
from services.auth.dependencies import get_current_user
from services.profiles import service as profiles_service
from services.users import service as users_service
from services.users.models import Role, UserUpdate
from tests.auth.conftest import session_refusal


def me(token: str):
    """The route as FastAPI runs it: the gate first, then the handler."""
    return auth_router.read_current_user(get_current_user(token))


def test_returns_the_account_and_its_profile(make_user, access_token_for):
    user = make_user(email="ana@trackflow.com", name="Ana Ruiz", phone="+52 81 5555 0000")

    account = me(access_token_for(user))

    assert (account.email, account.role) == ("ana@trackflow.com", Role.USER)
    assert set(account.model_dump()) == {"email", "role", "profile"}
    assert (account.profile.name, account.profile.phone) == ("Ana Ruiz", "+52 81 5555 0000")
    assert "user_id" not in account.profile.model_dump()


def test_an_account_without_a_profile_row_is_answered_with_no_profile(make_user, access_token_for):
    """The API always creates one, but a row edited or restored by hand may
    lack it - and the UI must not break."""
    user = make_user()
    profiles_service.delete_profile_by_user(user.id)

    assert me(access_token_for(user)).profile is None


def test_reflects_the_account_state_now_not_when_the_token_was_issued(
    make_user, access_token_for, issue_reset_token
):
    user = make_user()
    token = access_token_for(user)
    reset_link = issue_reset_token(user)
    users_service.update_user(user.id, UserUpdate(is_active=False))

    assert session_refusal(token).status_code == 403, "deactivated since sign-in"
    assert session_refusal(reset_link).status_code == 401, "a reset link is not a session"


def test_never_hands_back_the_password_hash(make_user, access_token_for):
    account = me(access_token_for(make_user()))

    assert "hashed_password" not in account.model_dump()
