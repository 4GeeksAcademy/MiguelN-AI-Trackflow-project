"""Access tokens and `POST /auth/token`.

AUTH-088 exists because a refactor broke token expiry and no test noticed, so
the lifetime cases come first. Sessions are issued by `/auth/login` and by
`/auth/token` (the OAuth2 form behind the /docs Authorize button); whether a
token is accepted is decided by `get_current_user`, the gate in front of every
protected route.
"""

import json
from datetime import timedelta

import pytest
from fastapi.security import OAuth2PasswordRequestForm
from jose import jwt
from tinydb import Query

from services.auth import router as auth_router
from services.auth.dependencies import get_current_user
from services.auth.models import LoginRequest
from services.core import config
from services.core.db import get_table
from services.users import service as users_service
from tests.auth.conftest import (
    NOON,
    PASSWORD,
    expires_in,
    refusal,
    session_refusal,
    unsigned_token,
    with_payload,
)

OTHER_SECRET = "a-different-secret-that-is-also-32-characters"


def token_endpoint(username: str, password: str):
    """`POST /auth/token`, called with the form it would receive."""
    form = OAuth2PasswordRequestForm(username=username, password=password)
    return auth_router.login_for_access_token(form)


# --- Lifetime: the incident class ----------------------------------------------


def test_a_session_is_refused_once_its_lifetime_has_passed(
    make_user, access_token_for, time_machine
):
    time_machine.move_to(NOON, tick=False)
    user = make_user()
    token = access_token_for(user)
    assert get_current_user(token).id == user.id, "valid while fresh"

    time_machine.move_to(NOON + timedelta(minutes=60, seconds=1), tick=False)

    assert session_refusal(token).status_code == 401


@pytest.mark.parametrize("minutes", [1, 15, 60])
def test_a_session_lasts_exactly_the_configured_lifetime(
    minutes, monkeypatch, make_user, access_token_for, time_machine
):
    """Catches off-by-one and seconds-versus-minutes mistakes at any setting."""
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", str(minutes))
    config.get_settings.cache_clear()
    lifetime = timedelta(minutes=minutes)

    time_machine.move_to(NOON, tick=False)
    user = make_user()
    token = access_token_for(user)

    time_machine.move_to(NOON + lifetime - timedelta(seconds=1), tick=False)
    assert get_current_user(token).id == user.id

    time_machine.move_to(NOON + lifetime + timedelta(seconds=1), tick=False)
    assert session_refusal(token).status_code == 401


def test_a_signed_token_without_an_expiry_is_refused(make_user, craft_token):
    """BUG-1. A refactor that stops setting `exp` must fail loudly - not mint
    sessions that never end."""
    user = make_user()
    token = craft_token({"sub": user.id, "role": "user", "typ": "access"})

    assert session_refusal(token).status_code == 401


# --- Issuing through POST /auth/token -------------------------------------------


def test_the_token_endpoint_issues_a_session_for_the_account(make_user):
    user = make_user(email="ana@trackflow.com")

    issued = token_endpoint("ana@trackflow.com", PASSWORD)

    claims = jwt.get_unverified_claims(issued.access_token)
    assert claims["sub"] == user.id
    assert claims["typ"] == "access"
    assert claims["exp"] - claims["iat"] == 60 * 60, "the configured lifetime"
    assert issued.expires_in == 60 * 60, "and the client is told the same"
    assert get_current_user(issued.access_token).id == user.id


def test_the_token_endpoint_refuses_an_account_whose_stored_hash_is_corrupt(make_user, caplog):
    """Corruption must be a refusal and a log line - never a crash, never a way in."""
    user = make_user(email="ana@trackflow.com")
    get_table(users_service.TABLE_NAME).update(
        {"hashed_password": "not-a-bcrypt-hash"}, Query().id == user.id
    )

    refused = refusal(lambda: token_endpoint("ana@trackflow.com", PASSWORD))

    assert refused.status_code == 401
    assert "could not be read" in caplog.text


@pytest.mark.parametrize("username", ["  ANA@TrackFlow.com ", "ana@trackflow.com"])
def test_the_token_endpoint_accepts_the_email_in_any_case_or_spacing(username, make_user):
    user = make_user(email="ana@trackflow.com")

    issued = token_endpoint(username, PASSWORD)

    assert get_current_user(issued.access_token).id == user.id


@pytest.mark.parametrize("username", ["admin", ""])
def test_the_token_endpoint_treats_a_username_that_is_not_an_account_as_wrong_credentials(
    username, make_user
):
    """`username` is free text in the OAuth2 form: no email validation to hide behind."""
    make_user(email="ana@trackflow.com")

    assert refusal(lambda: token_endpoint(username, PASSWORD)).status_code == 401


@pytest.mark.parametrize("scenario", ["wrong password", "unknown user", "deactivated account"])
def test_the_token_endpoint_decides_exactly_like_login(scenario, make_user):
    """Two ways into the same account need the same checks."""
    make_user(email="ana@trackflow.com", is_active=scenario != "deactivated account")
    email = "nobody@trackflow.com" if scenario == "unknown user" else "ana@trackflow.com"
    password = "not-the-password" if scenario == "wrong password" else PASSWORD

    by_form = refusal(lambda: token_endpoint(email, password))
    by_json = refusal(lambda: auth_router.login(LoginRequest(email=email, password=password)))

    assert (by_form.status_code, by_form.detail) == (by_json.status_code, by_json.detail)


# --- Forged, confused and malformed tokens --------------------------------------


def test_forged_tokens_are_refused(make_user, access_token_for, craft_token):
    """Anyone can rewrite a JWT's payload: only the signature and the pinned
    algorithm stop them."""
    ana = make_user(email="ana@trackflow.com")
    ben = make_user(email="ben@trackflow.com")
    real = access_token_for(ana)
    real_claims = jwt.get_unverified_claims(real)
    assert get_current_user(real).id == ana.id, "the untouched token is valid"

    forged = {
        "signed with another secret": craft_token(real_claims, key=OTHER_SECRET),
        "role escalated to admin": with_payload(real, {**real_claims, "role": "admin"}),
        "subject swapped to another user": with_payload(real, {**real_claims, "sub": ben.id}),
        "alg none": unsigned_token(real_claims),
        "signature stripped": real.rsplit(".", 1)[0] + ".",
        "HS512 with the right secret": craft_token(real_claims, algorithm="HS512"),
    }

    decisions = {name: session_refusal(token).status_code for name, token in forged.items()}

    assert decisions == dict.fromkeys(forged, 401)


def test_a_reset_link_token_does_not_open_a_session(make_user, issue_reset_token):
    user = make_user()

    assert session_refusal(issue_reset_token(user)).status_code == 401


@pytest.mark.parametrize("token_type", ["password_reset", "ACCESS", "refresh", ""])
def test_a_token_of_any_other_type_is_refused(token_type, make_user, craft_token):
    user = make_user()
    token = craft_token({"sub": user.id, "role": "user", "typ": token_type, "exp": expires_in(600)})

    assert session_refusal(token).status_code == 401


def test_a_legacy_token_without_a_type_is_still_a_session(make_user, craft_token):
    """Documented promise: adding the `typ` claim signed nobody out."""
    user = make_user()
    token = craft_token({"sub": user.id, "role": "user", "exp": expires_in(600)})

    assert get_current_user(token).id == user.id


@pytest.mark.parametrize(
    "token",
    ["", "abc", "a.b.c", "!!!.@@@.###", "eyJhbGciOiJIUzI1NiJ9.not-base64.sig"],
    ids=["empty", "one word", "three dots", "symbols", "bad base64"],
)
def test_a_malformed_token_is_refused_not_a_crash(token):
    assert session_refusal(token).status_code == 401


def test_a_token_padded_with_whitespace_is_refused(make_user, access_token_for):
    token = access_token_for(make_user())

    assert session_refusal(f" {token} ").status_code == 401


def test_a_token_carries_no_personal_data(make_user, access_token_for):
    """Anyone holding a JWT can read it; it names the account and nothing more."""
    user = make_user(email="ana@trackflow.com", name="Ana Ruiz", phone="+52 81 5555 0000")

    claims = jwt.get_unverified_claims(access_token_for(user))

    assert set(claims) == {"sub", "role", "typ", "iat", "exp"}
    assert "ana@trackflow.com" not in json.dumps(claims)


# --- Configuration --------------------------------------------------------------


@pytest.mark.parametrize("minutes", ["0", "-5"])
def test_startup_refuses_a_session_lifetime_that_is_not_positive(minutes, monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", minutes)
    config.get_settings.cache_clear()

    with pytest.raises(config.ConfigurationError, match="ACCESS_TOKEN_EXPIRE_MINUTES"):
        config.get_settings()


@pytest.mark.parametrize("length, accepted", [(31, False), (32, True)])
def test_startup_refuses_a_signing_secret_shorter_than_32_characters(length, accepted, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "k" * length)
    config.get_settings.cache_clear()

    if accepted:
        assert config.get_settings().jwt_secret_key == "k" * length
    else:
        with pytest.raises(config.ConfigurationError, match="JWT_SECRET_KEY"):
            config.get_settings()
