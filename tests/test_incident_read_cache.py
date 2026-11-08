from services.incidents.read_cache import IncidentReadCache
from tests.conftest import VALID_INCIDENT


def test_cache_expires_entries_after_ttl(monkeypatch):
    current_time = 10.0
    monkeypatch.setattr(
        "services.incidents.read_cache.time.monotonic", lambda: current_time
    )
    cache = IncidentReadCache(ttl_seconds=15, max_entries=2)
    key = ("summary",)

    cache.set(key, b"payload")
    assert cache.get(key) == b"payload"

    current_time += 15
    assert cache.get(key) is None
    assert cache.stats() == (0, 0)


def test_cache_evicts_least_recently_used_entry_at_capacity():
    cache = IncidentReadCache(ttl_seconds=15, max_entries=2)
    first = ("list", "open")
    second = ("list", "resolved")
    third = ("summary",)

    cache.set(first, b"one")
    cache.set(second, b"two")
    assert cache.get(first) == b"one"
    cache.set(third, b"three")

    assert cache.get(first) == b"one"
    assert cache.get(second) is None
    assert cache.stats() == (2, 8)


def test_invalidation_prevents_an_inflight_read_from_repopulating_stale_data():
    cache = IncidentReadCache(ttl_seconds=15, max_entries=2)
    generation = cache.generation

    cache.clear()
    cache.set(("summary",), b"stale", expected_generation=generation)

    assert cache.get(("summary",)) is None


def test_cache_enforces_total_payload_budget_and_skips_oversized_response():
    cache = IncidentReadCache(ttl_seconds=15, max_entries=8, max_bytes=5)
    first = ("list", "open")
    second = ("list", "resolved")

    cache.set(first, b"123")
    cache.set(second, b"456")
    assert cache.get(first) is None
    assert cache.get(second) == b"456"
    assert cache.stats() == (1, 3)

    cache.set(("list", "all"), b"123456")
    assert cache.get(("list", "all")) is None
    assert cache.stats() == (1, 3)


def test_incident_list_is_cached_per_filter_and_invalidated_after_create(
    client, monkeypatch
):
    from services.incidents import service

    original = service.list_incidents
    calls = 0

    def counted_list(**filters):
        nonlocal calls
        calls += 1
        return original(**filters)

    monkeypatch.setattr(service, "list_incidents", counted_list)

    assert client.get("/api/incidents?status=open").json() == []
    assert client.get("/api/incidents?status=open").json() == []
    assert client.get("/api/incidents?status=resolved").json() == []
    assert calls == 2

    created = client.post("/api/incidents", json=VALID_INCIDENT)
    assert created.status_code == 201
    listed = client.get("/api/incidents?status=open")

    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert calls == 3


def test_incident_summary_is_invalidated_after_status_change(client):
    from tests.conftest import VALID_INCIDENT

    assert client.get("/api/incidents/summary").json()["total"] == 0
    created = client.post("/api/incidents", json=VALID_INCIDENT)
    incident_id = created.json()["id"]

    assert client.get("/api/incidents/summary").json()["total"] == 1
    updated = client.patch(
        f"/api/incidents/{incident_id}/status",
        json={"status": "in_progress"},
    )

    assert updated.status_code == 200
    summary = client.get("/api/incidents/summary").json()
    assert summary["by_status"]["open"] == 0
    assert summary["by_status"]["in_progress"] == 1
