"""`POST /auth/login`: who gets a session.

The front door. Beyond "right password in, token out", what this route decides
is how much a refusal gives away: an unknown email and a wrong password must be
indistinguishable, in the answer and in the time it takes.
"""

import pytest
from jose import jwt
from pydantic import ValidationError
from tinydb import Query

from services.auth import router as auth_router
from services.auth.dependencies import get_current_user
from services.auth.models import LoginRequest
from services.core import security
from services.core.db import get_table
from services.users import service as users_service
from tests.auth.conftest import PASSWORD, refusal


def login(email: str, password: str):
    return auth_router.login(LoginRequest(email=email, password=password))


def login_refusal(email: str, password: str):
    return refusal(lambda: login(email, password))


@pytest.fixture
def bcrypt_checks(monkeypatch) -> list[str]:
    """Every hash a password is checked against, in order."""
    checked: list[str] = []
    real_verify = security.bcrypt.verify

    def spy(secret, hashed):
        checked.append(hashed)
        return real_verify(secret, hashed)

    monkeypatch.setattr(security.bcrypt, "verify", spy)
    return checked


def test_the_right_credentials_open_a_session_for_that_account(make_user):
    user = make_user(email="ana@trackflow.com")

    issued = login("ana@trackflow.com", PASSWORD)

    claims = jwt.get_unverified_claims(issued.access_token)
    assert claims["exp"] - claims["iat"] == 60 * 60, "the configured lifetime"
    assert get_current_user(issued.access_token).id == user.id


def test_a_wrong_password_and_an_unknown_email_are_refused_identically(make_user):
    """Anything different would tell an attacker which addresses are registered."""
    make_user(email="ana@trackflow.com")

    wrong_password = login_refusal("ana@trackflow.com", "not-the-password")
    unknown_email = login_refusal("nobody@trackflow.com", PASSWORD)

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.detail == unknown_email.detail


def test_an_unknown_email_costs_the_same_bcrypt_work_as_a_known_one(make_user, bcrypt_checks):
    """Otherwise the response time alone would reveal which emails exist."""
    make_user(email="ana@trackflow.com")

    login_refusal("nobody@trackflow.com", PASSWORD)
    unknown_email_checks = len(bcrypt_checks)
    bcrypt_checks.clear()
    login_refusal("ana@trackflow.com", "not-the-password")

    assert unknown_email_checks == len(bcrypt_checks) == 1


def test_a_deactivated_account_is_refused_and_only_the_owner_learns_why(make_user):
    """403 needs the right password; without it, a deactivated account looks like
    any other failed sign-in."""
    make_user(email="ana@trackflow.com", is_active=False)

    assert login_refusal("ana@trackflow.com", PASSWORD).status_code == 403
    assert login_refusal("ana@trackflow.com", "not-the-password").status_code == 401


def test_the_email_signs_in_whatever_its_case_or_spacing(make_user):
    user = make_user(email="ana@trackflow.com")

    issued = login("  ANA@TrackFlow.COM ", PASSWORD)

    assert get_current_user(issued.access_token).id == user.id


def test_an_empty_password_is_refused_before_anything_is_checked():
    with pytest.raises(ValidationError) as refused:
        LoginRequest(email="ana@trackflow.com", password="")

    assert refused.value.errors()[0]["loc"] == ("password",)


def test_a_whitespace_password_is_simply_a_wrong_password(make_user):
    make_user(email="ana@trackflow.com")

    assert login_refusal("ana@trackflow.com", "        ").status_code == 401


@pytest.mark.parametrize(
    "password",
    ["a" * 73, "ñ" * 40, "x" * 5000, "correct\x00horse"],
    ids=["73 bytes", "80 bytes of ñ", "5000 bytes", "NUL byte"],
)
def test_a_password_bcrypt_cannot_take_is_a_wrong_password_not_an_alarm(
    password, make_user, caplog
):
    """F-6. No stored hash can match such a password, so it is refused like any
    wrong one - without logging a data-corruption warning an anonymous caller
    could trigger at will."""
    make_user(email="ana@trackflow.com")

    assert login_refusal("ana@trackflow.com", password).status_code == 401
    assert "could not be read" not in caplog.text


def test_a_corrupt_stored_hash_refuses_sign_in_and_is_logged(make_user, caplog):
    """Real corruption must stay visible in the log - and never become a 500."""
    user = make_user(email="ana@trackflow.com")
    get_table(users_service.TABLE_NAME).update(
        {"hashed_password": "$2b$12$truncated"}, Query().id == user.id
    )

    assert login_refusal("ana@trackflow.com", PASSWORD).status_code == 401
    assert "could not be read" in caplog.text


@pytest.mark.parametrize("email", ["not-an-email", "ana@", "@trackflow.com", ""])
def test_a_malformed_email_is_refused_before_any_lookup(email):
    with pytest.raises(ValidationError) as refused:
        LoginRequest(email=email, password=PASSWORD)

    assert refused.value.errors()[0]["loc"] == ("email",)
