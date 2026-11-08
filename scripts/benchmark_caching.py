"""Measure representative TinyDB API reads against an isolated scratch DB."""

import argparse
import json
import math
import os
import statistics
import time
from pathlib import Path
from uuid import uuid4

from tinydb import TinyDB

from services.core.db import DEFAULT_DB_PATH

_SAFE_SETTINGS = {
    "APP_ENV": "development",
    "JWT_SECRET_KEY": "local-performance-benchmark-secret-key",
    "DATABASE_URL": "postgresql://benchmark:benchmark@127.0.0.1:5432/unused?sslmode=disable",
    "JWT_ALGORITHM": "HS256",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "60",
    "PASSWORD_RESET_TOKEN_EXPIRE_MINUTES": "30",
    "RESEND_API_KEY": "",
    "EMAIL_FROM": "TrackFlow <onboarding@resend.dev>",
    "FRONTEND_BASE_URL": "http://localhost:3000",
}


def _percentile(values: list[float], percentile: float) -> float:
    return sorted(values)[max(0, math.ceil(percentile * len(values)) - 1)]


def _validate_scratch_path(db_path: Path) -> Path:
    resolved_path = db_path.expanduser().resolve()
    if resolved_path == DEFAULT_DB_PATH.resolve():
        raise ValueError("Refusing to benchmark the default database.")
    if not resolved_path.is_file():
        raise ValueError("The scratch database must already exist; seed it first.")
    return resolved_path


def benchmark(
    db_path: Path, *, requests: int = 40, warmups: int = 3
) -> dict[str, object]:
    if requests < 1 or warmups < 0:
        raise ValueError("Requests must be positive and warmups cannot be negative.")
    resolved_path = _validate_scratch_path(db_path)

    for name, value in _SAFE_SETTINGS.items():
        os.environ[name] = value
    os.environ["DB_PATH"] = str(resolved_path)

    from services.core import config
    from services.core import db as core_db
    from services.incidents.read_cache import clear_read_cache, read_cache_stats
    from services.inventory import database as inventory_database

    config.get_settings.cache_clear()
    core_db.get_db.cache_clear()

    # This benchmark only calls TinyDB-backed routes. Never open a PostgreSQL
    # connection, even if a developer machine has a live DATABASE_URL in .env.
    inventory_database.check_connection = lambda: None
    inventory_database.initialize_schema = lambda: None
    inventory_database.dispose_engine = lambda: None

    with TinyDB(resolved_path) as database:
        initial_counts = {
            "incidents": len(database.table("incidents")),
            "suppliers": len(database.table("suppliers")),
        }

    from fastapi.testclient import TestClient

    from services.main import app

    routes = {
        "incident_list": ("GET", "/api/incidents", {}),
        "filtered_incident_list": ("GET", "/api/incidents?status=open", {}),
        "incident_summary": ("GET", "/api/incidents/summary", {}),
        "supplier_list": ("GET", "/suppliers", {}),
    }
    measurements: dict[str, dict[str, object]] = {}
    benchmark_email = f"performance-benchmark-{uuid4().hex}@example.com"

    with TestClient(app) as client:
        registered = client.post(
            "/users",
            json={
                "email": benchmark_email,
                "password": "local-benchmark-password-4812",
                "name": "Synthetic Benchmark User",
            },
        )
        if registered.status_code != 201:
            raise RuntimeError(
                f"Could not create scratch benchmark account: {registered.status_code}"
            )

        login = client.post(
            "/auth/login",
            json={
                "email": benchmark_email,
                "password": "local-benchmark-password-4812",
            },
        )
        if login.status_code != 200:
            raise RuntimeError(
                f"Could not authenticate benchmark account: {login.status_code}"
            )
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

        for name, (method, path, kwargs) in routes.items():
            clear_read_cache()
            started = time.perf_counter()
            cold_response = client.request(method, path, **kwargs)
            cold_ms = (time.perf_counter() - started) * 1000
            if cold_response.status_code != 200:
                raise RuntimeError(f"{name} returned {cold_response.status_code}")

            for _ in range(warmups):
                client.request(method, path, **kwargs)

            durations = []
            last_response = cold_response
            for _ in range(requests):
                started = time.perf_counter()
                last_response = client.request(method, path, **kwargs)
                durations.append((time.perf_counter() - started) * 1000)
                if last_response.status_code != 200:
                    raise RuntimeError(f"{name} returned {last_response.status_code}")

            measurements[name] = {
                "method": method,
                "path": path,
                "calls_in_timed_window": requests,
                "cold_ms": round(cold_ms, 3),
                "median_ms": round(statistics.median(durations), 3),
                "p95_ms": round(_percentile(durations, 0.95), 3),
                "response_bytes": len(last_response.content),
                "cache_entries_after_warmup": read_cache_stats()[0],
                "cache_payload_bytes_after_warmup": read_cache_stats()[1],
            }

    return {
        "database": str(resolved_path),
        "environment": "in-process FastAPI TestClient; PostgreSQL hooks stubbed",
        "warmups_per_route": warmups,
        "rows": initial_counts,
        "routes": measurements,
        "notes": [
            "Durations include TestClient overhead and measure request completion in-process.",
            "Repeated calls are a controlled workload, not observed production traffic.",
            "The benchmark account is added after row counts are captured.",
        ],
    }


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--requests", type=_positive_int, default=40)
    parser.add_argument("--warmups", type=int, default=3)
    args = parser.parse_args()

    try:
        result = benchmark(args.db_path, requests=args.requests, warmups=args.warmups)
    except ValueError as error:
        parser.error(str(error))

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
