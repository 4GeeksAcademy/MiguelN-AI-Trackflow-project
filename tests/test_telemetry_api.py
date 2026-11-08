import json
from uuid import uuid4

import pytest

from services.telemetry import service
from services.telemetry.models import MAX_BATCH_SIZE


def make_event(**overrides):
    event = {
        "eventId": str(uuid4()),
        "timestamp": "2026-10-15T10:00:00Z",
        "sessionId": str(uuid4()),
        "userId": None,
        "event_type": "section_viewed",
        "schemaVersion": "1.0.0",
        "requestId": str(uuid4()),
        "properties": {"section": "login"},
    }
    return {**event, **overrides}


@pytest.fixture
def stored(monkeypatch):
    """Each `store_events` call, as the list of event ids it was given."""
    calls = []

    def record(events):
        calls.append([str(event.eventId) for event in events])
        return len(events)

    monkeypatch.setattr(service, "store_events", record)
    return calls


def test_accepts_batch_without_auth(client, stored):
    events = [make_event(), make_event()]
    response = client.post("/telemetry/events", json={"events": events})

    assert response.status_code == 200
    assert response.json() == {"received": 2, "stored": 2, "rejected": 0}
    assert stored == [[event["eventId"] for event in events]]


def test_accepts_text_plain_body_as_sent_by_beacon(client, stored):
    response = client.post(
        "/telemetry/events",
        content=json.dumps({"events": [make_event()]}),
        headers={"Content-Type": "text/plain;charset=UTF-8"},
    )

    assert response.status_code == 200
    assert response.json() == {"received": 1, "stored": 1, "rejected": 0}


def test_invalid_events_are_rejected_and_the_rest_stored_in_one_insert(client, stored):
    first, last = make_event(), make_event()
    events = [
        first,
        make_event(eventId="not-a-uuid"),
        {key: value for key, value in make_event().items() if key != "requestId"},
        make_event(unexpected="field"),
        make_event(event_type="Section Viewed"),
        make_event(schemaVersion="v1"),
        make_event(properties=["not", "an", "object"]),
        "not an event",
        None,
        last,
    ]
    response = client.post("/telemetry/events", json={"events": events})

    assert response.status_code == 200
    assert response.json() == {"received": 10, "stored": 2, "rejected": 8}
    assert stored == [[first["eventId"], last["eventId"]]]


def test_values_jsonb_cannot_hold_are_rejected_per_event(client, stored):
    kept = make_event()
    nan = make_event(properties={"marker": "nan"})
    infinity = make_event(properties={"marker": "infinity"})
    nul = make_event(properties={"section": "lo\u0000gin"})
    # json.dumps cannot write NaN or Infinity into a payload, so they are spliced in.
    body = json.dumps({"events": [kept, nan, infinity, nul]})
    body = body.replace('{"marker": "nan"}', '{"value": NaN}')
    body = body.replace('{"marker": "infinity"}', '{"nested": [Infinity]}')

    response = client.post("/telemetry/events", content=body)

    assert response.json() == {"received": 4, "stored": 1, "rejected": 3}
    assert stored == [[kept["eventId"]]]


def test_a_repeated_event_id_in_one_batch_is_stored_once(client, stored):
    event = make_event()
    response = client.post("/telemetry/events", json={"events": [event, dict(event)]})

    assert response.json() == {"received": 2, "stored": 1, "rejected": 1}
    assert stored == [[event["eventId"]]]


def test_a_batch_with_no_valid_event_answers_200_and_stores_nothing(client, stored):
    response = client.post("/telemetry/events", json={"events": [make_event(userId="nope")]})

    assert response.status_code == 200
    assert response.json() == {"received": 1, "stored": 0, "rejected": 1}
    assert stored == []


def test_storage_failure_answers_503_so_the_frontend_retries(client, monkeypatch):
    def fail(events):
        raise service.TelemetryStorageUnavailable("APIError 42501")

    monkeypatch.setattr(service, "store_events", fail)
    response = client.post("/telemetry/events", json={"events": [make_event()]})

    assert response.status_code == 503
    assert response.json() == {"detail": "Telemetry storage unavailable"}
    assert "42501" not in response.text


def test_rejects_empty_batch(client, stored):
    response = client.post("/telemetry/events", json={"events": []})

    assert response.status_code == 422
    assert stored == []


def test_rejects_oversized_batch(client, stored):
    events = [make_event() for _ in range(MAX_BATCH_SIZE + 1)]

    assert client.post("/telemetry/events", json={"events": events}).status_code == 422
    assert stored == []


@pytest.mark.parametrize(
    "body",
    ["{not json", "[]", '{"events": {}}', '{"batch": []}', '{"events": [{}], "extra": 1}'],
)
def test_rejects_a_malformed_envelope(client, stored, body):
    assert client.post("/telemetry/events", content=body).status_code == 422
    assert stored == []


def test_rejects_oversized_body(client, stored):
    padding = "x" * (256 * 1024)
    response = client.post(
        "/telemetry/events", json={"events": [make_event(properties={"p": padding})]}
    )

    assert response.status_code == 413
    assert stored == []


def test_logs_counts_and_types_but_never_rejected_values(client, stored, caplog):
    secret_key = "a-sender-chosen-key"
    events = [make_event(), make_event(eventId="leaked-value"), make_event(**{secret_key: 1})]
    with caplog.at_level("INFO", logger="api.telemetry"):
        client.post("/telemetry/events", json={"events": events})

    assert "received=3 stored=1 rejected=2" in caplog.text
    assert "section_viewed" in caplog.text
    assert "eventId:uuid_parsing" in caplog.text
    assert "envelope:extra_forbidden" in caplog.text
    assert "leaked-value" not in caplog.text
    assert secret_key not in caplog.text
