"""Fixtures for the task queue tests: a SQLite dead letter queue, and attempt recorders."""

import pytest
from celery.signals import task_retry
from sqlalchemy import create_engine

from services.tasks import base, dead_letters


@pytest.fixture
def dlq_engine(tmp_path, monkeypatch):
    """Dead letters go to a per-test SQLite file instead of DATABASE_URL."""
    engine = create_engine(f"sqlite:///{tmp_path / 'tasks.sqlite'}")
    dead_letters.ensure_schema(engine)
    monkeypatch.setattr(base, "dead_letter_engine", lambda: engine)
    yield engine
    engine.dispose()


@pytest.fixture
def countdowns():
    """The countdown of every retry Celery schedules, read from the `task_retry` signal.

    Eager execution runs a retry at once, but the signal still carries the
    countdown a worker would have waited.
    """
    seen = []

    def record(sender=None, reason=None, **kwargs):
        seen.append(reason.when)

    task_retry.connect(record, weak=False)
    yield seen
    task_retry.disconnect(record)
