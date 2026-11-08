"""Endpoint behaviour for /api/incidents."""

import pytest

from tests.conftest import VALID_INCIDENT


def create_response(client, **overrides):
    payload = {**VALID_INCIDENT, **overrides}
    response = client.post("/api/incidents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def create(client, **overrides):
    created = create_response(client, **overrides)
    return client.get(f"/api/incidents/{created['id']}").json()


# --- POST -------------------------------------------------------------------


def test_create_returns_only_confirmation_fields(client):
    body = create_response(client)

    assert set(body) == {"id", "title", "status", "branch"}
    assert body["status"] == "open", "a new incident starts unassigned"


def test_create_accepts_an_explicit_status(client):
    assert create_response(client, status="in_progress")["status"] == "in_progress"


@pytest.mark.parametrize("missing_field", ["title", "description", "category", "origin", "branch"])
def test_create_missing_required_field_returns_400_naming_it(client, missing_field):
    payload = {key: value for key, value in VALID_INCIDENT.items() if key != missing_field}
    response = client.post("/api/incidents", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "field": missing_field,
        "message": "This field is required.",
    }


@pytest.mark.parametrize("blank_field", ["title", "description"])
def test_create_blank_text_field_returns_400(client, blank_field):
    response = client.post("/api/incidents", json={**VALID_INCIDENT, blank_field: "   "})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["field"] == blank_field
    assert detail["message"] == "This field cannot be empty."


@pytest.mark.parametrize("field", ["category", "origin", "branch", "status"])
def test_create_rejects_a_value_outside_the_allowed_list(client, field):
    response = client.post("/api/incidents", json={**VALID_INCIDENT, field: "nonsense"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["field"] == field
    assert detail["message"].startswith("Must be one of:")


def test_create_reports_every_bad_field_at_once(client):
    response = client.post("/api/incidents", json={"category": "lost_parcel"})

    assert response.status_code == 400
    named = {problem["field"] for problem in response.json()["errors"]}
    assert named == {"title", "description", "origin", "branch"}


def test_create_rejects_a_title_longer_than_the_column(client):
    response = client.post("/api/incidents", json={**VALID_INCIDENT, "title": "x" * 121})

    assert response.status_code == 400
    assert response.json()["detail"]["field"] == "title"


def test_create_ignores_a_client_supplied_id_and_timestamp(client):
    body = create_response(client, id="pick-me", created_at="1999-01-01T00:00:00Z")

    assert body["id"] != "pick-me"
    detail = client.get(f"/api/incidents/{body['id']}").json()
    assert not detail["created_at"].startswith("1999")


# --- GET list ---------------------------------------------------------------


def test_list_on_empty_database_returns_an_empty_list(client):
    response = client.get("/api/incidents")

    assert response.status_code == 200
    assert response.json() == []


def test_list_returns_newest_first(client):
    create(client, title="First")
    create(client, title="Second")

    titles = [item["title"] for item in client.get("/api/incidents").json()]
    assert titles == ["Second", "First"]


@pytest.mark.parametrize(
    ("filter_field", "match", "miss"),
    [
        ("status", "open", "resolved"),
        ("origin", "branch", "customer"),
        ("branch", "la_warehouse", "zaragoza_office"),
        ("category", "lost_parcel", "system_failure"),
    ],
)
def test_list_filters_on_each_supported_parameter(client, filter_field, match, miss):
    create(client)

    assert len(client.get(f"/api/incidents?{filter_field}={match}").json()) == 1
    assert client.get(f"/api/incidents?{filter_field}={miss}").json() == []


def test_list_filters_combine_with_and(client):
    create(client, category="lost_parcel", branch="la_warehouse")
    create(client, category="carrier_issue", branch="la_warehouse")

    both = client.get("/api/incidents?category=lost_parcel&branch=la_warehouse")
    assert len(both.json()) == 1

    contradiction = client.get("/api/incidents?category=lost_parcel&branch=la_office")
    assert contradiction.json() == []


def test_list_rejects_an_unknown_filter_value_with_400(client):
    response = client.get("/api/incidents?status=nonsense")

    assert response.status_code == 400
    assert response.json()["detail"]["field"] == "status"


# --- GET detail -------------------------------------------------------------


def test_get_detail_returns_the_incident(client):
    created = create(client)

    response = client.get(f"/api/incidents/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_get_unknown_id_returns_404_with_a_readable_message(client):
    response = client.get("/api/incidents/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["field"] == "id"
    assert "No incident exists" in detail["message"]


def test_summary_path_is_not_captured_by_the_detail_route(client):
    """`/summary` is a sibling of `/{incident_id}`, and it has to win."""
    response = client.get("/api/incidents/summary")

    assert response.status_code == 200
    assert "by_status" in response.json()


# --- PATCH status -----------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "target"),
    [
        ("open", "in_progress"),
        ("open", "discarded"),
        ("in_progress", "resolved"),
        ("in_progress", "discarded"),
    ],
)
def test_allowed_transitions_succeed(client, start, target):
    created = create(client, status=start)

    response = client.patch(f"/api/incidents/{created['id']}/status", json={"status": target})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == target


@pytest.mark.parametrize(
    ("start", "target"),
    [
        ("open", "resolved"),
        ("open", "open"),
        ("in_progress", "open"),
        ("in_progress", "in_progress"),
        ("resolved", "open"),
        ("resolved", "in_progress"),
        ("resolved", "discarded"),
        ("discarded", "open"),
        ("discarded", "in_progress"),
        ("discarded", "resolved"),
    ],
)
def test_forbidden_transitions_return_400_and_change_nothing(client, start, target):
    created = create(client, status=start)

    response = client.patch(f"/api/incidents/{created['id']}/status", json={"status": target})
    assert response.status_code == 400
    assert response.json()["detail"]["field"] == "status"

    unchanged = client.get(f"/api/incidents/{created['id']}").json()
    assert unchanged["status"] == start
    assert unchanged["updated_at"] == created["updated_at"]


def test_final_states_say_they_are_final(client):
    created = create(client, status="resolved")

    response = client.patch(f"/api/incidents/{created['id']}/status", json={"status": "open"})
    assert "final state" in response.json()["detail"]["message"]


def test_a_successful_transition_moves_updated_at_but_not_created_at(client):
    created = create(client)

    updated = client.patch(
        f"/api/incidents/{created['id']}/status", json={"status": "in_progress"}
    ).json()

    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] >= created["updated_at"]


def test_patch_status_on_unknown_id_returns_404(client):
    response = client.patch("/api/incidents/does-not-exist/status", json={"status": "in_progress"})
    assert response.status_code == 404


def test_patch_status_rejects_a_missing_or_invalid_status(client):
    created = create(client)

    missing = client.patch(f"/api/incidents/{created['id']}/status", json={})
    assert missing.status_code == 400
    assert missing.json()["detail"]["field"] == "status"

    invalid = client.patch(f"/api/incidents/{created['id']}/status", json={"status": "closed"})
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["field"] == "status"


# --- GET summary ------------------------------------------------------------


def test_summary_on_empty_database_returns_zero_metrics(client):
    body = client.get("/api/incidents/summary").json()

    assert body["total"] == 0
    assert set(body["by_status"]) == {"open", "in_progress", "resolved", "discarded"}
    assert set(body["by_origin"]) == {"customer", "branch", "internal"}
    assert len(body["by_category"]) == 9
    assert len(body["by_branch"]) == 5
    assert all(
        count == 0
        for bucket in ("by_status", "by_category", "by_origin", "by_branch")
        for count in body[bucket].values()
    ), "an empty database reports zeros, not an empty object"


def test_summary_counts_each_dimension(client):
    create(client, category="lost_parcel", origin="branch", branch="la_warehouse")
    create(client, category="lost_parcel", origin="customer", branch="central")
    create(
        client,
        category="carrier_issue",
        origin="internal",
        branch="zaragoza_office",
        status="in_progress",
    )

    body = client.get("/api/incidents/summary").json()

    assert body["total"] == 3
    assert body["by_status"] == {
        "open": 2,
        "in_progress": 1,
        "resolved": 0,
        "discarded": 0,
    }
    assert body["by_category"]["lost_parcel"] == 2
    assert body["by_category"]["carrier_issue"] == 1
    assert body["by_category"]["other"] == 0
    assert body["by_origin"] == {"customer": 1, "branch": 1, "internal": 1}
    assert body["by_branch"]["la_warehouse"] == 1
    assert body["by_branch"]["central"] == 1
    assert body["by_branch"]["zaragoza_office"] == 1
    assert body["by_branch"]["la_office"] == 0


def test_summary_totals_stay_consistent_with_the_list(client):
    for category in ("lost_parcel", "carrier_issue", "returns_issue"):
        create(client, category=category)

    body = client.get("/api/incidents/summary").json()
    assert body["total"] == len(client.get("/api/incidents").json())
    assert sum(body["by_category"].values()) == body["total"]
    assert sum(body["by_status"].values()) == body["total"]


# --- Error handling ---------------------------------------------------------


def test_malformed_json_body_returns_400_not_422(client):
    response = client.post(
        "/api/incidents",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["message"]


def test_an_unhandled_exception_returns_500_without_a_stack_trace(raw_client, monkeypatch):
    from services.incidents import service

    def explode(*_args, **_kwargs):
        raise RuntimeError("TinyDB handle exploded at services/incidents/db.py:14")

    monkeypatch.setattr(service, "summarize", explode)

    response = raw_client.get("/api/incidents/summary")

    assert response.status_code == 500
    body = response.text
    assert "Traceback" not in body
    assert "RuntimeError" not in body
    assert "services/incidents/db.py" not in body
    message = response.json()["detail"]["message"]
    assert message.startswith("Something went wrong")
    assert "saved" not in message, "the handler covers reads too, so it claims nothing"
