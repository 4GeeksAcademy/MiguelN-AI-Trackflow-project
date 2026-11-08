"""`POST /telemetry/report`: period resolution, the 202 contract, and failures (DEV-55).

The endpoint resolves the period and enqueues `telemetry.generate_report`;
nothing is calculated during the request. `enqueue` is replaced by a recorder
here; the task itself is covered by `tests/tasks/test_telemetry_report_task.py`
and the metrics by `tests/test_telemetry_analysis.py`. The last test runs the
whole path, POST -> task -> GET /tasks/{id}, eagerly.
"""

from datetime import UTC, datetime

import pytest
import time_machine

from services.tasks import service as task_service
from services.tasks.service import TaskQueueUnavailable
from services.telemetry import analysis, router
from services.telemetry.tasks import generate_telemetry_report

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
TASK_ID = "4f0c2b8e-0000-4000-8000-000000000001"


@pytest.fixture
def queued(monkeypatch):
    """Every enqueue call, as `(task, args)`; the request gets TASK_ID back."""
    calls = []

    def record(task, *args):
        calls.append((task, args))
        return TASK_ID

    monkeypatch.setattr(router, "enqueue", record)
    return calls


@pytest.fixture
def metrics(monkeypatch):
    """Fails the test if a metric is calculated during the request."""
    calls = []

    def recorder(name):
        def metric(start, end):
            calls.append(name)
            return []

        return metric

    for name in ("events_per_day", "error_rate_by_type", "latency_by_route", "event_type_share"):
        monkeypatch.setattr(analysis, name, recorder(name))
    return calls


def period(queued):
    [(task, args)] = queued
    assert task is generate_telemetry_report
    return args


def test_the_report_is_queued_and_answered_with_202_and_a_task_id(client, queued, metrics):
    response = client.post(
        "/telemetry/report",
        params={"start_date": "2026-10-13T00:00:00Z", "end_date": "2026-10-20T00:00:00Z"},
    )

    assert response.status_code == 202
    assert response.json() == {"task_id": TASK_ID}
    assert response.headers["location"] == f"/tasks/{TASK_ID}"
    assert period(queued) == ("2026-10-13T00:00:00+00:00", "2026-10-20T00:00:00+00:00")
    assert metrics == []  # the work happens in the worker, not in the request


def test_the_endpoint_is_public(client, queued):
    assert client.post("/telemetry/report").status_code == 202


def test_get_is_no_longer_served(client, queued):
    assert client.get("/telemetry/report").status_code == 405
    assert queued == []


@time_machine.travel(NOW, tick=False)
def test_without_parameters_the_period_is_the_seven_days_before_the_request(client, queued):
    client.post("/telemetry/report")

    assert period(queued) == ("2026-10-13T12:00:00+00:00", "2026-10-20T12:00:00+00:00")


@pytest.mark.parametrize(
    "start_date",
    [
        "2026-10-13T00:00:00Z",
        "2026-10-13T02:00:00+02:00",
        "2026-10-12T18:00:00-06:00",
        "2026-10-13T00:00:00",
        "2026-10-13",
    ],
)
def test_iso_8601_dates_are_converted_to_utc(client, queued, start_date):
    response = client.post(
        "/telemetry/report",
        params={"start_date": start_date, "end_date": "2026-10-20T00:00:00Z"},
    )

    assert response.status_code == 202
    assert period(queued)[0] == "2026-10-13T00:00:00+00:00"


def test_an_end_alone_reports_the_seven_days_before_it(client, queued):
    client.post("/telemetry/report", params={"end_date": "2026-10-10T00:00:00Z"})

    assert period(queued) == ("2026-10-03T00:00:00+00:00", "2026-10-10T00:00:00+00:00")


@time_machine.travel(NOW, tick=False)
def test_a_start_alone_reports_up_to_the_request(client, queued):
    client.post("/telemetry/report", params={"start_date": "2026-10-01T00:00:00Z"})

    assert period(queued) == ("2026-10-01T00:00:00+00:00", "2026-10-20T12:00:00+00:00")


@pytest.mark.parametrize(
    "params",
    [
        {"start_date": "last tuesday"},
        {"end_date": "2026-13-01T00:00:00Z"},
        {"start_date": "2026-10-20T00:00:00Z", "end_date": "2026-10-20T00:00:00Z"},
        {"start_date": "2026-10-21T00:00:00Z", "end_date": "2026-10-20T00:00:00Z"},
    ],
)
def test_an_unreadable_or_empty_period_is_refused_before_anything_is_queued(client, queued, params):
    response = client.post("/telemetry/report", params=params)

    assert response.status_code == 422
    assert queued == []


def test_a_reversed_period_names_the_parameter(client, queued):
    response = client.post(
        "/telemetry/report",
        params={"start_date": "2026-10-21T00:00:00Z", "end_date": "2026-10-20T00:00:00Z"},
    )

    assert response.json() == {
        "detail": [
            {
                "loc": ["query", "start_date"],
                "msg": "start_date must be before end_date",
                "type": "value_error",
            }
        ]
    }


def test_a_queue_outage_answers_503_without_details(client, monkeypatch):
    def unavailable(task, *args):
        raise TaskQueueUnavailable("ConnectionError: Error 111 connecting to redis.internal")

    monkeypatch.setattr(router, "enqueue", unavailable)

    response = client.post("/telemetry/report")

    assert response.status_code == 503
    assert response.json() == {"detail": "Task queue unavailable"}
    assert "redis.internal" not in response.text


def test_post_then_poll_returns_the_report_once_the_task_ran(client, metrics, monkeypatch):
    """The whole path on Celery's in-memory transport, with the task run in-process."""
    # Eager runs keep no result unless asked; a worker always stores it.
    monkeypatch.setattr(generate_telemetry_report, "store_eager_result", True)
    sent = []

    def enqueue_and_remember(task, *args):
        task_id = task_service.enqueue(task, *args)
        sent.append((task_id, args))
        return task_id

    monkeypatch.setattr(router, "enqueue", enqueue_and_remember)

    accepted = client.post(
        "/telemetry/report",
        params={"start_date": "2026-10-13T00:00:00Z", "end_date": "2026-10-20T00:00:00Z"},
    )
    task_id = accepted.json()["task_id"]

    assert client.get(f"/tasks/{task_id}").json() == {
        "task_id": task_id,
        "status": "pending",
        "result": None,
    }

    [(sent_id, args)] = sent
    generate_telemetry_report.apply(args=list(args), task_id=sent_id)  # what a worker does

    assert client.get(f"/tasks/{task_id}").json() == {
        "task_id": task_id,
        "status": "success",
        "result": {
            "period": {"from": "2026-10-13T00:00:00Z", "to": "2026-10-20T00:00:00Z"},
            "metrics": {
                "events_per_day": [],
                "error_rate_by_type": [],
                "latency_by_route": [],
                "event_type_share": [],
            },
        },
    }
