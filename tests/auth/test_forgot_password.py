"""`POST /auth/forgot-password`: starting password recovery.

The route's hardest rule is to say nothing: a registered, an unregistered and a
deactivated address must get the same answer, or the route becomes a way to
test which emails have accounts. The email itself goes out after the answer,
so a delivery failure must stay in the log.
"""

import json
from datetime import timedelta

import pytest
import resend
from fastapi import BackgroundTasks
from pydantic import ValidationError
from resend.exceptions import ResendError
from tinydb.table import Document

from services.auth import router as auth_router
from services.auth import service as auth_service
from services.auth.emails import build_reset_email, build_reset_url, send_password_reset_email
from services.auth.models import ForgotPasswordRequest
from services.core import config, security
from services.core.db import get_table
from tests.auth.conftest import NOON


def forgot(email: str):
    """Call the route; return its answer and the work it queued for afterwards."""
    tasks = BackgroundTasks()
    answer = auth_router.forgot_password(ForgotPasswordRequest(email=email), tasks)
    return answer, tasks.tasks


def reset_rows() -> list[Document]:
    return get_table(auth_service.TABLE_NAME).all()


@pytest.fixture
def live_email(monkeypatch):
    """A provider key is configured and Resend is replaced by `sent`, so the
    real transport code runs without anything leaving the machine."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_not_a_real_key")
    config.get_settings.cache_clear()
    sent: list[dict] = []

    def accept(params):
        sent.append(params)
        return {"id": "msg_test_1"}

    monkeypatch.setattr(resend.Emails, "send", accept)
    return sent


def test_every_address_gets_the_same_answer_and_only_a_real_account_gets_mail(make_user):
    make_user(email="ana@trackflow.com")
    make_user(email="former@trackflow.com", is_active=False)

    registered, registered_tasks = forgot("ana@trackflow.com")
    unregistered, unregistered_tasks = forgot("nobody@trackflow.com")
    deactivated, deactivated_tasks = forgot("former@trackflow.com")

    assert registered == unregistered == deactivated
    assert len(registered_tasks) == 1
    assert unregistered_tasks == [] and deactivated_tasks == []
    assert len(reset_rows()) == 1, "no reset row for the other two"


def test_a_registered_address_gets_a_single_use_link_that_lasts_30_minutes(make_user, time_machine):
    time_machine.move_to(NOON, tick=False)
    user = make_user(email="ana@trackflow.com")

    _, [email_task] = forgot("ana@trackflow.com")

    assert email_task.func is send_password_reset_email
    recipient, token = email_task.args
    assert recipient == "ana@trackflow.com"
    claims = security.decode_password_reset_token(token)
    [row] = reset_rows()
    assert claims["sub"] == row["user_id"] == user.id
    assert claims["typ"] == "password_reset"
    assert claims["jti"] == row["jti"]
    assert claims["exp"] - claims["iat"] == 30 * 60
    assert row["used_at"] is None


def test_the_registry_never_stores_the_link_itself(make_user):
    """A leaked database file must not hand out working reset links."""
    make_user(email="ana@trackflow.com")

    _, [email_task] = forgot("ana@trackflow.com")

    token = email_task.args[1]
    assert token not in json.dumps(reset_rows())


def test_a_live_send_logs_neither_the_token_nor_the_link(live_email, caplog):
    caplog.set_level("DEBUG")

    assert send_password_reset_email("ana@trackflow.com", "the.reset.token") is True

    assert "the.reset.token" in live_email[0]["text"], "the link went to the provider"
    assert "the.reset.token" not in caplog.text
    assert "re_test_not_a_real_key" not in caplog.text


@pytest.mark.parametrize("email", ["ANA@TrackFlow.com", "  ana@trackflow.com  "])
def test_the_address_is_found_whatever_its_case_or_spacing(email, make_user):
    make_user(email="ana@trackflow.com")

    _, tasks = forgot(email)

    assert len(tasks) == 1


@pytest.mark.parametrize("failure", ["provider rejects", "no message id", "unreachable"])
def test_a_failed_send_is_logged_and_never_raised(
    failure, live_email, monkeypatch, make_user, caplog
):
    """The send runs after the answer: failing loudly there would crash a
    background task, and telling the caller would reveal the address exists."""

    def broken_send(params):
        if failure == "provider rejects":
            raise ResendError(
                code=422, error_type="validation_error", message="bad", suggested_action=""
            )
        if failure == "no message id":
            return {}
        raise ConnectionError("network unreachable")

    monkeypatch.setattr(resend.Emails, "send", broken_send)
    make_user(email="ana@trackflow.com")
    normal_answer, _ = forgot("nobody@trackflow.com")

    answer, [email_task] = forgot("ana@trackflow.com")

    assert answer == normal_answer
    assert email_task.func(*email_task.args) is False
    assert "Could not deliver the password reset email" in caplog.text


def test_issuing_a_link_purges_expired_rows_and_keeps_live_ones(make_user, time_machine):
    make_user(email="ana@trackflow.com")
    time_machine.move_to(NOON, tick=False)
    forgot("ana@trackflow.com")  # expires 12:30
    time_machine.move_to(NOON + timedelta(minutes=20), tick=False)
    forgot("ana@trackflow.com")  # expires 12:50
    kept_jti = reset_rows()[1]["jti"]

    time_machine.move_to(NOON + timedelta(minutes=31), tick=False)
    forgot("ana@trackflow.com")  # purges the 12:30 row

    rows = reset_rows()
    assert len(rows) == 2
    assert rows[0]["jti"] == kept_jti


def test_repeated_requests_leave_several_live_links(make_user):
    """Pinned: there is no rate limit (TESTING.md, known gaps)."""
    make_user(email="ana@trackflow.com")

    for _ in range(3):
        forgot("ana@trackflow.com")

    assert [row["used_at"] for row in reset_rows()] == [None, None, None]


@pytest.mark.parametrize("email", ["not-an-email", "", "ana@"])
def test_a_malformed_address_is_refused_before_any_lookup(email):
    with pytest.raises(ValidationError):
        ForgotPasswordRequest(email=email)


def test_the_link_drops_a_trailing_slash_and_encodes_the_token(monkeypatch):
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://app.trackflow.com/")
    config.get_settings.cache_clear()

    url = build_reset_url("header.pay+load.sig/nature")

    assert url == "https://app.trackflow.com/reset-password?token=header.pay%2Bload.sig%2Fnature"


@pytest.mark.parametrize("minutes, phrase", [(30, "30 minutes"), (60, "1 hour")])
def test_the_email_states_the_real_expiry_and_that_the_link_works_once(minutes, phrase):
    html, text = build_reset_email("https://app.trackflow.com/reset-password?token=t", minutes)

    for part in (html, text):
        assert f"expires in {phrase}" in part
        assert "can only be used once" in part


@pytest.mark.parametrize("minutes, accepted", [(14, False), (15, True), (60, True), (61, False)])
def test_the_reset_link_lifetime_must_stay_within_15_to_60_minutes(minutes, accepted, monkeypatch):
    monkeypatch.setenv("PASSWORD_RESET_TOKEN_EXPIRE_MINUTES", str(minutes))
    config.get_settings.cache_clear()

    if accepted:
        assert config.get_settings().password_reset_token_expire_minutes == minutes
    else:
        with pytest.raises(config.ConfigurationError, match="PASSWORD_RESET_TOKEN_EXPIRE_MINUTES"):
            config.get_settings()
