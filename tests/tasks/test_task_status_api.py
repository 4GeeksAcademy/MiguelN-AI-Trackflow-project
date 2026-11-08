"""`GET /tasks/{task_id}`: every Celery state, unknown ids, malformed ids, Redis outages.

States are written straight into the in-memory result backend, as a worker
would write them.
"""

from uuid import uuid4

import pytest
from celery.backends.cache import CacheBackend
from redis.exceptions import ConnectionError as RedisConnectionError

from services.tasks.celery_app import celery_app
from services.tasks.service import SENT
from services.telemetry.report import ReportUnavailable

REPORT = {
    "period": {"from": "2026-10-13T00:00:00Z", "to": "2026-10-20T00:00:00Z"},
    "metrics": {
        "events_per_day": [],
        "error_rate_by_type": [],
        "latency_by_route": [],
        "event_type_share": [],
    },
}


def stored(state, result=None):
    task_id = str(uuid4())
    celery_app.backend.store_result(task_id, result, state)
    return task_id


@pytest.mark.parametrize(
    ("state", "result", "status"),
    [
        (SENT, None, "pending"),
        ("RECEIVED", None, "pending"),
        ("STARTED", {"pid": 4242, "hostname": "worker@host"}, "started"),
        ("RETRY", ReportUnavailable("OperationalError"), "started"),
        ("FAILURE", ReportUnavailable("OperationalError"), "failure"),
        ("REVOKED", None, "failure"),
        ("REJECTED", None, "failure"),
    ],
)
def test_each_celery_state_maps_to_a_status_and_hides_its_payload(client, state, result, status):
    task_id = stored(state, result)

    response = client.get(f"/tasks/{task_id}")

    assert response.status_code == 200
    assert response.json() == {"task_id": task_id, "status": status, "result": None}
    assert "OperationalError" not in response.text
    assert "pid" not in response.text


def test_a_successful_task_returns_its_result(client):
    task_id = stored("SUCCESS", REPORT)

    response = client.get(f"/tasks/{task_id}")

    assert response.status_code == 200
    assert response.json() == {"task_id": task_id, "status": "success", "result": REPORT}


def test_an_unexpected_state_reads_as_pending_and_is_logged(client, caplog):
    task_id = stored("PROGRESS")

    response = client.get(f"/tasks/{task_id}")

    assert response.json()["status"] == "pending"
    assert f"task {task_id} is in unexpected state PROGRESS" in caplog.text


def test_an_unknown_or_expired_task_is_not_found(client):
    response = client.get(f"/tasks/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Task not found"}


@pytest.mark.parametrize("task_id", ["not-a-uuid", "123", "4f0c2b8e"])
def test_a_malformed_task_id_is_refused(client, task_id):
    assert client.get(f"/tasks/{task_id}").status_code == 422


def test_redis_outage_answers_503_without_details(client, monkeypatch):
    def unreachable(self, task_id, cache=True):
        raise RedisConnectionError("Error 111 connecting to redis.internal:6379")

    monkeypatch.setattr(CacheBackend, "get_task_meta", unreachable)

    response = client.get(f"/tasks/{uuid4()}")

    assert response.status_code == 503
    assert response.json() == {"detail": "Task queue unavailable"}
    assert "redis.internal" not in response.text


def test_the_status_route_is_public(client):
    """Like the POST that enqueues: see the router docstring before adding a sensitive task."""
    task_id = stored(SENT)

    assert client.get(f"/tasks/{task_id}").status_code == 200
