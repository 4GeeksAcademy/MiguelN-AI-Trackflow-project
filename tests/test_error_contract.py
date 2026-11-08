"""Which routes answer a bad payload with 400, and which still answer 422.

The incident manager owes its callers a 400 naming one field. Every other
domain was built against FastAPI's 422 and its `detail` array, and the two UIs
branch on the difference: `resetPassword` in `uis/*/services/auth-service.ts`
reads any 400 as a spent reset token and replaces the form with "request a new
link". Remapping validation errors app-wide would therefore tell someone whose
new password was simply too short that their reset link had expired.

These tests pin both halves of that boundary, and what an error body is
allowed to carry on either side of it.
"""

import pytest

from tests.conftest import VALID_INCIDENT


def test_incident_route_answers_validation_with_400_and_one_field(client):
    response = client.post("/api/incidents", json={})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail == {"field": "title", "message": "This field is required."}


def test_users_route_still_answers_validation_with_422(client):
    """`POST /users` predates the incident manager and keeps FastAPI's default."""
    response = client.post("/users", json={"email": "not-an-email", "password": "x"})

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list), "the pydantic array shape, unchanged"


def test_reset_password_keeps_422_for_a_short_password(client):
    """The case the app-wide remap broke.

    A 400 here means "this token is dead, ask for a new link". A password that
    is merely too short must not produce one, or the user is sent to restart a
    recovery that was working.
    """
    response = client.post(
        "/auth/reset-password",
        json={"token": "irrelevant-the-password-fails-first", "new_password": "short"},
    )

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_a_bad_query_parameter_on_an_incident_route_is_a_400(client):
    response = client.get("/api/incidents?status=nonsense")

    assert response.status_code == 400
    assert response.json()["detail"]["field"] == "status"


def test_the_incident_prefix_is_read_from_the_router(client):
    """The scope follows the router, so moving the prefix moves the behaviour."""
    from services.incidents.router import router

    response = client.post(f"{router.prefix}", json={})
    assert response.status_code == 400


# --- What an error body may carry ---------------------------------------------


def test_a_422_never_echoes_the_submitted_value(client):
    """FastAPI's default handler returns every rejected value under `input`,
    a password included. The status and the array stay; the value does not."""
    secret = "Sup3rSecretPw"
    response = client.post("/users", json={"password": secret})

    assert response.status_code == 422
    assert secret not in response.text
    assert all("input" not in problem for problem in response.json()["detail"])


def test_a_422_keeps_loc_and_words_msg_for_a_reader(client):
    response = client.post("/auth/reset-password", json={"token": "t", "new_password": "short"})

    problem = response.json()["detail"][0]
    assert problem["loc"] == ["body", "new_password"]
    assert problem["msg"] == "Must be at least 8 characters long."


def test_an_invalid_email_is_described_without_the_parser_diagnosis(client):
    response = client.post("/users", json={"email": "not-an-email", "password": "long-enough"})

    problem = response.json()["detail"][0]
    assert problem["loc"] == ["body", "email"]
    assert problem["msg"] == "Enter a valid email address."


def test_a_taken_email_is_a_409_that_does_not_repeat_the_address(client):
    payload = {"email": "taken@trackflow.com", "password": "long-enough"}
    assert client.post("/users", json=payload).status_code == 201

    response = client.post("/users", json=payload)

    assert response.status_code == 409
    assert "taken@trackflow.com" not in response.text
    assert response.json()["detail"] == "An account with this email address already exists."


def test_a_configuration_error_names_the_setting_but_never_its_value(monkeypatch):
    from services.core import config

    monkeypatch.setenv("JWT_SECRET_KEY", "tooshort")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(config.ConfigurationError) as raised:
            config.get_settings()
    finally:
        config.get_settings.cache_clear()

    message = str(raised.value)
    assert "JWT_SECRET_KEY" in message
    assert "tooshort" not in message
    assert raised.value.__suppress_context__, (
        "pydantic's message, which quotes the value, is not chained"
    )


# --- One bad row does not take a list down ------------------------------------


def test_an_unreadable_stored_incident_is_skipped_not_a_500(client):
    from services.incidents.db import get_table

    assert client.post("/api/incidents", json=VALID_INCIDENT).status_code == 201
    get_table().insert({"id": "broken", "title": "No other fields"})

    listed = client.get("/api/incidents")
    summary = client.get("/api/incidents/summary")

    assert listed.status_code == 200
    assert [incident["title"] for incident in listed.json()] == [VALID_INCIDENT["title"]]
    assert summary.status_code == 200
    assert summary.json()["total"] == 1


# --- The static site server ---------------------------------------------------


@pytest.fixture
def static_client():
    pytest.importorskip("flask")
    from services.server import app

    return app.test_client()


@pytest.mark.parametrize(
    "path",
    [
        "/.env",
        "/data/suppliers.json",
        "/data/raw/incidents-trackflow.csv",
        "/services/core/config.py",
        "/pyproject.toml",
    ],
)
def test_the_static_server_never_serves_repository_files(static_client, path):
    response = static_client.get(path)

    assert response.status_code == 404
    assert path.strip("/") not in response.get_data(as_text=True), "the path is not echoed back"


def test_the_static_server_still_serves_the_shared_logo(static_client):
    assert static_client.get("/workflows/TrackFlowLogo.png").status_code == 200


def test_the_static_server_answers_a_form_post_with_clean_json(static_client):
    response = static_client.post("/signup.html")

    assert response.status_code == 405
    assert response.get_json()["message"]


# --- The console email backend ------------------------------------------------


@pytest.fixture
def console_email(monkeypatch):
    """Settings with no provider key, rebuilt for each test."""
    from services.core import config

    monkeypatch.setenv("RESEND_API_KEY", "")
    config.get_settings.cache_clear()
    yield monkeypatch
    config.get_settings.cache_clear()


def test_the_console_backend_logs_the_message_on_a_local_setup(console_email, caplog):
    from services.core.email import send_email

    console_email.setenv("FRONTEND_BASE_URL", "http://localhost:3000")

    assert send_email("person@example.com", "Subject", "<p>html</p>", "the-reset-link") == "console"
    assert "the-reset-link" in caplog.text


def test_the_console_backend_never_logs_a_reset_link_outside_a_local_setup(console_email, caplog):
    from services.core.email import EmailDeliveryError, send_email

    console_email.setenv("FRONTEND_BASE_URL", "https://app.trackflow.com")

    with pytest.raises(EmailDeliveryError):
        send_email("person@example.com", "Subject", "<p>html</p>", "the-reset-link")
    assert "the-reset-link" not in caplog.text
    assert "person@example.com" not in caplog.text
