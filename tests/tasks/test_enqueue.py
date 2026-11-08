"""`enqueue`: the `SENT` marker goes in before the message, and outages fail fast."""

import socket
import time
from uuid import UUID

import pytest
from celery import Celery
from kombu.exceptions import OperationalError as BrokerError
from redis.exceptions import ConnectionError as RedisConnectionError

from services.tasks import service
from services.tasks.celery_app import celery_app
from services.tasks.service import SENT, TaskQueueUnavailable, enqueue


class FakeBackend:
    def __init__(self, events, fail_store=None):
        self.events = events
        self.fail_store = fail_store

    def store_result(self, task_id, result, state):
        if self.fail_store:
            raise self.fail_store
        self.events.append(("marker", task_id, state))

    def forget(self, task_id):
        self.events.append(("forget", task_id))


class FakeTask:
    def __init__(self, events, fail_store=None, fail_publish=None):
        self.events = events
        self.backend = FakeBackend(events, fail_store)
        self.fail_publish = fail_publish

    def apply_async(self, args, task_id):
        if self.fail_publish:
            raise self.fail_publish
        self.events.append(("publish", task_id, args))


def test_the_marker_is_written_before_the_message_is_published():
    events = []

    task_id = enqueue(FakeTask(events), "2026-10-13T00:00:00+00:00", "2026-10-20T00:00:00+00:00")

    assert UUID(task_id).version == 4
    assert events == [
        ("marker", task_id, SENT),
        ("publish", task_id, ["2026-10-13T00:00:00+00:00", "2026-10-20T00:00:00+00:00"]),
    ]


def test_a_queued_task_reads_as_pending_at_once():
    from services.telemetry.tasks import generate_telemetry_report

    task_id = enqueue(generate_telemetry_report, "a", "b")

    assert service.get_status(task_id).status == "pending"


@pytest.mark.parametrize(
    "error", [RedisConnectionError("refused"), BrokerError("refused"), OSError("unreachable")]
)
def test_redis_down_before_the_marker_raises_unavailable(error):
    events = []

    with pytest.raises(TaskQueueUnavailable):
        enqueue(FakeTask(events, fail_store=error))

    assert not [event for event in events if event[0] == "publish"]


def test_a_publish_failure_forgets_the_marker_it_left():
    events = []

    with pytest.raises(TaskQueueUnavailable):
        enqueue(FakeTask(events, fail_publish=BrokerError("refused")))

    marker, forget = events
    assert marker[0] == "marker"
    assert forget == ("forget", marker[1])


def closed_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_an_unreachable_redis_fails_in_well_under_a_second():
    """The real Redis transport and backend with the app's own settings, on a closed port."""
    url = f"redis://127.0.0.1:{closed_port()}/0"
    app = Celery("probe")
    app.conf.update(
        {
            key: value
            for key, value in celery_app.conf.items()
            if key.startswith(
                ("task_publish_retry_policy", "broker_transport_options", "redis_socket")
            )
        },
        broker_url=url,
        result_backend=url,
    )

    @app.task(name="probe.noop")
    def noop():
        return None

    started = time.perf_counter()
    with pytest.raises(TaskQueueUnavailable):
        enqueue(noop)

    assert time.perf_counter() - started < 1
