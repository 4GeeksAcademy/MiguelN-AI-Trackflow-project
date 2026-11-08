"""`POST /auth/reset-password`: finishing password recovery.

A reset link is a credential that works exactly once, for 30 minutes, for one
account. Every way a link can fail gets the same answer - the caller must not
learn whether a link they did not receive was ever real - while the log keeps
the reason. And a weak new password must not burn the link: the UI reads any
400 as "this link is dead".
"""

import logging
import uuid
from datetime import timedelta

import pytest
from jose import jwt
from pydantic import ValidationError
from tinydb import Query
from tinydb.table import Document

from services.auth import router as auth_router
from services.auth import service as auth_service
from services.auth.models import ResetPasswordRequest
from services.core import security
from services.core.db import get_table
from services.users import service as users_service
from services.users.models import UserUpdate
from tests.auth.conftest import (
    NEW_PASSWORD,
    NOON,
    PASSWORD,
    expires_in,
    refusal,
    signs_in,
    with_payload,
)

OTHER_SECRET = "a-different-secret-that-is-also-32-characters"


def reset(token: str, new_password: str = NEW_PASSWORD):
    return auth_router.reset_password(ResetPasswordRequest(token=token, new_password=new_password))


def reset_refusal(token: str, new_password: str = NEW_PASSWORD):
    return refusal(lambda: reset(token, new_password))


def registry_row(token: str) -> Document:
    jti = jwt.get_unverified_claims(token)["jti"]
    row = get_table(auth_service.TABLE_NAME).get(Query().jti == jti)
    assert isinstance(row, Document), "every issued link has a registry row"
    return row


def test_a_valid_link_sets_the_new_password_and_is_spent(make_user, issue_reset_token):
    make_user(email="ana@trackflow.com")
    link = issue_reset_token(users_service.get_user_by_email("ana@trackflow.com"))

    reset(link)

    assert signs_in("ana@trackflow.com", NEW_PASSWORD)
    assert not signs_in("ana@trackflow.com", PASSWORD)
    assert registry_row(link)["used_at"] is not None


def test_a_link_works_only_once(make_user, issue_reset_token):
    ana = make_user(email="ana@trackflow.com")
    link = issue_reset_token(ana)
    reset(link)

    replay = reset_refusal(link, "a-third-password")

    assert replay.status_code == 400
    assert replay.detail == auth_router.INVALID_RESET_TOKEN_MESSAGE
    assert signs_in("ana@trackflow.com", NEW_PASSWORD), "the password did not change again"


def test_a_link_is_refused_once_its_30_minutes_have_passed(
    make_user, issue_reset_token, time_machine
):
    time_machine.move_to(NOON, tick=False)
    ana = make_user(email="ana@trackflow.com")
    link = issue_reset_token(ana)

    time_machine.move_to(NOON + timedelta(minutes=30, seconds=1), tick=False)

    assert reset_refusal(link).status_code == 400
    assert signs_in("ana@trackflow.com", PASSWORD)


def test_the_registry_expiry_is_a_second_independent_check(make_user, issue_reset_token):
    """A link whose JWT is still valid is refused when its registry row says it
    expired - neither check relies on the other."""
    ana = make_user(email="ana@trackflow.com")
    link = issue_reset_token(ana)
    get_table(auth_service.TABLE_NAME).update(
        {"expires_at": "2000-01-01T00:00:00Z"}, Query().jti == registry_row(link)["jti"]
    )

    assert security.decode_password_reset_token(link) is not None, "the JWT itself is live"
    assert reset_refusal(link).status_code == 400
    assert signs_in("ana@trackflow.com", PASSWORD)


@pytest.mark.parametrize(
    "kind",
    [
        "garbage",
        "whitespace",
        "signed with another secret",
        "tampered subject",
        "a session token",
        "legacy token without a type",
        "missing jti",
        "missing subject",
        "jti never issued",
        "another account's jti",
    ],
)
def test_every_unusable_link_gets_the_same_answer_and_changes_nothing(
    kind, make_user, issue_reset_token, craft_token, access_token_for
):
    ana = make_user(email="ana@trackflow.com")
    ben = make_user(email="ben@trackflow.com")
    real = issue_reset_token(ana)
    real_claims = jwt.get_unverified_claims(real)
    ben_jti = jwt.get_unverified_claims(issue_reset_token(ben))["jti"]
    valid = {"sub": ana.id, "typ": "password_reset", "exp": expires_in(600)}
    links = {
        "garbage": "not-a-token",
        "whitespace": "   ",
        "signed with another secret": craft_token(
            {**valid, "jti": real_claims["jti"]}, key=OTHER_SECRET
        ),
        "tampered subject": with_payload(real, {**real_claims, "sub": ben.id}),
        "a session token": access_token_for(ana),
        "legacy token without a type": craft_token(
            {"sub": ana.id, "jti": real_claims["jti"], "exp": expires_in(600)}
        ),
        "missing jti": craft_token(valid),
        "missing subject": craft_token(
            {"typ": "password_reset", "jti": real_claims["jti"], "exp": expires_in(600)}
        ),
        "jti never issued": craft_token({**valid, "jti": str(uuid.uuid4())}),
        "another account's jti": craft_token({**valid, "jti": ben_jti}),
    }

    refused = reset_refusal(links[kind])

    assert refused.status_code == 400
    assert refused.detail == auth_router.INVALID_RESET_TOKEN_MESSAGE
    assert signs_in("ana@trackflow.com", PASSWORD)
    assert signs_in("ben@trackflow.com", PASSWORD)


def test_setting_the_password_kills_every_other_outstanding_link(make_user, issue_reset_token):
    ana = make_user(email="ana@trackflow.com")
    older = issue_reset_token(ana)
    newer = issue_reset_token(ana)

    reset(newer)

    assert reset_refusal(older, "a-third-password").status_code == 400
    assert signs_in("ana@trackflow.com", NEW_PASSWORD)


@pytest.mark.parametrize("change", ["deleted", "deactivated"])
def test_a_link_cannot_revive_a_closed_account(change, make_user, issue_reset_token):
    ana = make_user(email="ana@trackflow.com")
    link = issue_reset_token(ana)
    if change == "deleted":
        users_service.delete_user(ana.id)
    else:
        users_service.update_user(ana.id, UserUpdate(is_active=False))

    assert reset_refusal(link).status_code == 400
    if change == "deleted":
        assert users_service.get_user(ana.id) is None, "nothing was written back"
    else:
        stored = users_service.get_user(ana.id)
        assert security.verify_password(PASSWORD, stored.hashed_password), "unchanged"


@pytest.mark.parametrize(
    "weak_password",
    ["", "short7!", "x" * 73, "Contraseña" + "x" * 62, "correct\x00horse"],
    ids=["empty", "7 characters", "73 characters", "73 bytes (BUG-4)", "NUL (BUG-5)"],
)
def test_a_rejected_new_password_does_not_burn_the_link(
    weak_password, make_user, issue_reset_token
):
    """The UI reads a 400 as "link expired"; a password that is merely invalid
    must be refused by validation and leave the link usable."""
    ana = make_user(email="ana@trackflow.com")
    link = issue_reset_token(ana)

    with pytest.raises(ValidationError) as refused:
        ResetPasswordRequest(token=link, new_password=weak_password)

    assert refused.value.errors()[0]["loc"] == ("new_password",)
    reset(link)
    assert signs_in("ana@trackflow.com", NEW_PASSWORD)


def test_an_empty_link_is_refused_by_validation():
    with pytest.raises(ValidationError) as refused:
        ResetPasswordRequest(token="", new_password=NEW_PASSWORD)

    assert refused.value.errors()[0]["loc"] == ("token",)


def test_the_reason_is_logged_but_never_told(make_user, issue_reset_token, caplog):
    """Support can tell a spent link from a forged one; the caller cannot."""
    caplog.set_level(logging.INFO, logger="services.auth.router")
    link = issue_reset_token(make_user())
    reset(link)

    spent = reset_refusal(link)
    forged = reset_refusal("not-a-token")

    assert spent.detail == forged.detail
    assert "has already been used" in caplog.text
    assert "is not valid or has expired" in caplog.text
