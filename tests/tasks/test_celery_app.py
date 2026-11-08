"""The Celery app and its settings: REDIS_URL, the static configuration, lazy loading."""

import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from services.core import config
from services.tasks import base
from services.tasks import celery_app as celery_module
from services.tasks.celery_app import VISIBILITY_TIMEOUT, celery_app

ROOT = Path(__file__).resolve().parents[2]


def settings_with(monkeypatch, value):
    monkeypatch.setenv("REDIS_URL", value)
    config.get_settings.cache_clear()
    return config.get_settings()


@pytest.mark.parametrize(
    "url",
    [
        "redis://localhost:6379/0",
        "redis://redis:6379/0",
        "rediss://default:s3cret@cache.internal:6380/2",
        "redis://localhost",
    ],
)
def test_redis_url_accepts_redis_and_tls_urls(monkeypatch, url):
    assert settings_with(monkeypatch, url).redis_url.get_secret_value() == url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:6379/0",
        "redis://",
        "redis://:hunter2@:6379/0",
        "redis://localhost:99999/0",
        "redis://localhost:port/0",
        "localhost:6379",
    ],
)
def test_an_invalid_redis_url_fails_at_startup_without_echoing_it(monkeypatch, url):
    monkeypatch.setenv("REDIS_URL", url)
    config.get_settings.cache_clear()

    with pytest.raises(config.ConfigurationError) as raised:
        config.get_settings()

    # The fixed reason only: the rejected value, password included, is never echoed.
    assert str(raised.value) == (
        "Invalid configuration - REDIS_URL: Value error, Must be a redis:// or rediss:// "
        "URL with a host. Compare .env with .env.example."
    )


def test_redis_url_defaults_to_a_local_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL")
    config.get_settings.cache_clear()

    assert config.get_settings().redis_url.get_secret_value() == "redis://localhost:6379/0"


def test_redis_url_is_kept_secret_in_reprs(monkeypatch):
    settings = settings_with(monkeypatch, "redis://default:s3cret@cache.internal:6380/0")

    assert "s3cret" not in repr(settings)


def test_the_broker_and_the_result_backend_are_the_same_redis(monkeypatch):
    settings_with(monkeypatch, "redis://cache.internal:6380/2")

    assert celery_module._connection_settings() == {
        "broker_url": "redis://cache.internal:6380/2",
        "result_backend": "redis://cache.internal:6380/2",
    }


def test_the_tests_use_the_in_memory_transport_never_redis():
    assert celery_app.conf.broker_url == "memory://"
    assert celery_app.conf.result_backend == "cache+memory://"


def test_static_configuration():
    conf = celery_app.conf

    assert conf.task_serializer == "json"
    assert conf.result_serializer == "json"
    assert conf.accept_content == ["json"]
    assert conf.enable_utc is True
    assert conf.task_track_started is True
    assert conf.task_acks_late is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_send_sent_event is True
    assert conf.worker_send_task_events is True
    assert conf.result_expires == timedelta(hours=24)
    assert conf.broker_transport_options["visibility_timeout"] == VISIBILITY_TIMEOUT


def test_the_tasks_are_registered_under_stable_names():
    assert {"telemetry.generate_report", "tasks.record_dead_letter"} <= set(celery_app.tasks)


def test_every_retry_countdown_stays_under_the_visibility_timeout():
    """A countdown past it would make Redis deliver the waiting message twice."""
    assert base.BACKOFF_MAX_SECONDS < VISIBILITY_TIMEOUT
    assert base.RECORDER_BACKOFF_MAX_SECONDS < VISIBILITY_TIMEOUT


def test_importing_the_api_reads_no_settings():
    """Test collection imports the app before any fixture pins the settings."""
    code = (
        "from services.core import config\n"
        "def refuse():\n"
        "    raise AssertionError('settings were read at import')\n"
        "config.get_settings = refuse\n"
        "import services.main\n"
        "from services.tasks.celery_app import celery_app\n"
        "assert not celery_app.configured\n"
    )
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in {"JWT_SECRET_KEY", "DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY"}
    }

    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True
    )

    assert completed.returncode == 0, completed.stderr
