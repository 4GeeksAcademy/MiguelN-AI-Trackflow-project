import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
from postgrest import APIError
from postgrest.types import CountMethod, ReturnMethod

from services.core import supabase_client
from services.telemetry import service
from services.telemetry.models import TelemetryEvent
from services.telemetry.service import to_row


def make_event(**overrides):
    event = {
        "eventId": str(uuid4()),
        "timestamp": "2026-10-17T10:00:00Z",
        "sessionId": str(uuid4()),
        "userId": str(uuid4()),
        "event_type": "outbound_order_created",
        "schemaVersion": "1.0.0",
        "requestId": str(uuid4()),
        "properties": {"warehouse": "Monterrey", "sku": "SHOE-1", "quantity": 1},
    }
    return {**event, **overrides}


def row_for(**overrides):
    return to_row(TelemetryEvent.model_validate(make_event(**overrides)))


class FakeSupabase:
    """Records `table(...).upsert(...).execute()` the way the service calls it."""

    def __init__(self, count=None, error=None):
        self.calls = []
        self.count = count
        self.error = error

    def table(self, name):
        def upsert(rows, **options):
            self.calls.append({"table": name, "rows": rows, **options})
            return SimpleNamespace(execute=self.execute)

        return SimpleNamespace(upsert=upsert)

    def execute(self):
        if self.error:
            raise self.error
        return SimpleNamespace(count=self.count)


@pytest.fixture
def supabase(monkeypatch):
    fake = FakeSupabase()
    monkeypatch.setattr(supabase_client, "get_supabase", lambda: fake)
    return fake


# --- The row contract --------------------------------------------------------


def test_row_maps_the_event_onto_the_supabase_columns():
    raw = make_event()
    row = to_row(TelemetryEvent.model_validate(raw))

    assert row == {
        "id": raw["eventId"],
        "timestamp": "2026-10-17T10:00:00+00:00",
        "event_type": "outbound_order_created",
        "payload": raw["properties"],
        "tags": {
            "sessionId": raw["sessionId"],
            "userId": raw["userId"],
            "requestId": raw["requestId"],
            "schemaVersion": "1.0.0",
            "warehouse": "Monterrey",
        },
    }


def test_row_is_plain_json_for_the_supabase_client():
    row = row_for()

    assert json.loads(json.dumps(row, allow_nan=False)) == row


def test_payload_is_properties_verbatim():
    properties = {"incident_id": str(uuid4()), "branch": "zaragoza_office", "nested": {"kept": [1]}}

    assert row_for(properties=properties)["payload"] == properties


@pytest.mark.parametrize(
    ("properties", "dimensions"),
    [
        ({"warehouse": "Zaragoza", "sku": "BOX-9"}, {"warehouse": "Zaragoza"}),
        ({"branch": "la_office", "category": "other"}, {"branch": "la_office"}),
        ({"source_system": "monterrey_wms"}, {"source_system": "monterrey_wms"}),
        ({"client_app": "backoffice", "login_route": "/auth/login"}, {"client_app": "backoffice"}),
        ({"section": "incidents_list", "dwell_ms": 3}, {"section": "incidents_list"}),
        ({"warehouse": None, "sku": None}, {}),
        ({"route_template": "/suppliers"}, {}),
    ],
)
def test_tags_carry_the_context_dimensions_the_event_has(properties, dimensions):
    tags = row_for(properties=properties)["tags"]

    assert {key: tags[key] for key in tags if key not in service.TAG_ENVELOPE_FIELDS} == dimensions


def test_anonymous_event_keeps_a_null_user_in_tags():
    assert row_for(userId=None)["tags"]["userId"] is None


def test_timestamp_without_offset_is_read_as_utc():
    assert row_for(timestamp="2026-10-17T10:00:00")["timestamp"] == "2026-10-17T10:00:00+00:00"


def test_timestamp_with_offset_keeps_its_instant():
    assert (
        row_for(timestamp="2026-10-17T12:00:00+02:00")["timestamp"] == "2026-10-17T12:00:00+02:00"
    )


# --- Storage -----------------------------------------------------------------


def test_events_are_stored_with_one_upsert_that_skips_known_ids(supabase):
    supabase.count = 2
    events = [TelemetryEvent.model_validate(make_event()) for _ in range(2)]

    assert service.store_events(events) == 2

    [call] = supabase.calls
    assert call["table"] == "telemetry_events"
    assert call["rows"] == [to_row(event) for event in events]
    assert call["on_conflict"] == "id"
    assert call["ignore_duplicates"] is True
    assert call["returning"] == ReturnMethod.minimal
    assert call["count"] == CountMethod.exact


def test_without_a_count_every_row_is_assumed_new(supabase):
    events = [TelemetryEvent.model_validate(make_event())]

    assert service.store_events(events) == 1


@pytest.mark.parametrize(
    "error",
    [
        APIError({"message": "value 'secret-value' rejected", "code": "22P05"}),
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
    ],
)
def test_supabase_failures_become_storage_unavailable(supabase, error):
    supabase.error = error

    with pytest.raises(service.TelemetryStorageUnavailable) as raised:
        service.store_events([TelemetryEvent.model_validate(make_event())])

    assert "secret-value" not in str(raised.value)
    assert raised.value.__suppress_context__


def test_a_replayed_event_counts_as_stored(monkeypatch):
    monkeypatch.setattr(service, "store_events", lambda events: 0)

    receipt = service.ingest([make_event()])

    assert receipt.model_dump() == {"received": 1, "stored": 1, "rejected": 0}


def test_a_batch_without_valid_events_never_calls_supabase(monkeypatch):
    store = MagicMock()
    monkeypatch.setattr(service, "store_events", store)

    receipt = service.ingest([{"eventId": "x"}, 7])

    assert receipt.model_dump() == {"received": 2, "stored": 0, "rejected": 2}
    store.assert_not_called()


def test_rejection_reasons_name_the_field_and_never_the_value():
    _, rejected = service.validate_events(
        [make_event(eventId="secret-value"), "plain string", make_event(other=1)]
    )

    assert rejected == {
        "eventId:uuid_parsing": 1,
        "event:model_type": 1,
        "envelope:extra_forbidden": 1,
    }
