"""`services/reporting`: status codes and shapes, with the `data/pipelines` functions mocked."""

import ast
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from prefect.settings import PREFECT_API_URL, temporary_settings
from sqlalchemy.exc import OperationalError

from data.pipelines import run_log
from data.pipelines.weekly_warehouse_client_performance import queries, trigger
from services.core import security
from services.reporting import router as reporting_router

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)  # last closed week: 2026-10-12

ENTRY = {
    "warehouse": "Monterrey",
    "client_id": "stride-athletics",
    "inbound_units_count": 4200,
    "outbound_orders_count": 980,
    "stockout_events_count": 3,
    "discrepancy_events_count": 2,
    "discrepancy_rate": Decimal("0.0020"),
}

RUN = {
    "pipeline_name": "weekly_warehouse_client_performance",
    "status": "completed",
    "trigger": "schedule",
    "started_at": datetime(2026, 10, 19, 11, 0, 3, tzinfo=timezone.utc),
    "finished_at": datetime(2026, 10, 19, 11, 0, 41, tzinfo=timezone.utc),
    "weeks_from": date(2026, 10, 5),
    "weeks_to": date(2026, 10, 12),
    "arrival_cutoff": datetime(2026, 10, 19, 11, 0, 3, tzinfo=timezone.utc),
    "events_read": 15234,
    "events_excluded": {"missing_client_id": 0, "duplicate_business_key": 1},
    "rows_inserted": 6,
    "rows_updated": 1,
    "rows_unchanged": 5,
    "rows_deleted": 0,
    "weeks_revised": [date(2026, 10, 5)],
    "late_events_outside_window": 0,
    "telemetry_dropped": 0,
    "min_capture_coverage": Decimal("1.0"),
    "quality_status": "ok",
    "error_stage": None,
    "error_class": None,
}


@pytest.fixture
def api(client, monkeypatch):
    """A signed-in client; the database engine and the pipeline functions are fakes."""
    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)
    monkeypatch.setattr(reporting_router, "get_engine", lambda: "engine")
    monkeypatch.setattr(reporting_router, "_today", lambda: NOW)
    credentials = {"email": "reporting@trackflow.com", "password": "reporting-password-42"}
    registered = client.post("/users", json=credentials)
    token = client.post("/auth/login", json=credentials).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}, registered.json()["id"]


def fake(monkeypatch, module, name, value=None, *, raises=None):
    calls = []

    def replacement(*args, **kwargs):
        calls.append((args, kwargs))
        if raises is not None:
            raise raises
        return value

    monkeypatch.setattr(module, name, replacement)
    return calls


DB_DOWN = OperationalError("SELECT 1", {}, Exception("postgresql://user:secret@host/db"))


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/reporting/weekly-warehouse-client-performance"),
        ("get", "/reporting/pipeline-runs/latest"),
        ("post", "/reporting/pipeline-runs"),
    ],
)
def test_every_route_requires_a_bearer_token(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


# --- KPI query -----------------------------------------------------------------------


def test_kpis_default_to_the_most_recent_computed_week(api, monkeypatch):
    client, headers, _ = api
    fake(monkeypatch, run_log, "latest_completed_week", date(2026, 10, 12))
    computed = fake(monkeypatch, run_log, "is_week_computed", True)
    rows = fake(monkeypatch, queries, "get_weekly_performance", [ENTRY])

    response = client.get("/reporting/weekly-warehouse-client-performance", headers=headers)

    assert response.status_code == 200
    # The BRIEFING section 6 contract, with the monorepo's warehouse values.
    assert response.json() == {
        "week_start": "2026-10-12",
        "entries": [{**ENTRY, "discrepancy_rate": 0.002}],
    }
    assert computed[0][0] == ("engine", date(2026, 10, 12))
    assert rows[0][0] == ("engine", date(2026, 10, 12))


def test_a_computed_week_without_activity_returns_its_rows(api, monkeypatch):
    client, headers, _ = api
    fake(monkeypatch, run_log, "is_week_computed", True)
    fake(monkeypatch, queries, "get_weekly_performance", [])
    response = client.get(
        "/reporting/weekly-warehouse-client-performance",
        params={"week_start": "2026-10-05"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"week_start": "2026-10-05", "entries": []}


def test_a_week_never_computed_is_404_not_an_empty_week(api, monkeypatch):
    client, headers, _ = api
    fake(monkeypatch, run_log, "is_week_computed", False)
    rows = fake(monkeypatch, queries, "get_weekly_performance", [])
    response = client.get(
        "/reporting/weekly-warehouse-client-performance",
        params={"week_start": "2026-10-19"},
        headers=headers,
    )
    assert response.status_code == 404
    assert rows == []


def test_no_completed_run_at_all_is_404(api, monkeypatch):
    client, headers, _ = api
    fake(monkeypatch, run_log, "latest_completed_week", None)
    response = client.get("/reporting/weekly-warehouse-client-performance", headers=headers)
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("week_start", "message"),
    [("2026-10-13", "Monday"), ("2026-10-26", "future"), ("not-a-date", None)],
)
def test_invalid_week_start_is_422(api, monkeypatch, week_start, message):
    client, headers, _ = api
    computed = fake(monkeypatch, run_log, "is_week_computed", True)
    response = client.get(
        "/reporting/weekly-warehouse-client-performance",
        params={"week_start": week_start},
        headers=headers,
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["query", "week_start"]
    if message:
        assert message in detail[0]["msg"]
    assert computed == []


def test_database_failure_is_503_without_details(api, monkeypatch, caplog):
    client, headers, _ = api
    fake(monkeypatch, run_log, "latest_completed_week", raises=DB_DOWN)
    response = client.get("/reporting/weekly-warehouse-client-performance", headers=headers)
    assert response.status_code == 503
    assert response.json() == {"detail": "Reporting storage unavailable"}
    assert "secret" not in caplog.text


# --- Status query --------------------------------------------------------------------


def test_latest_run_exposes_status_and_derived_freshness(api, monkeypatch):
    client, headers, _ = api
    fake(monkeypatch, run_log, "get_latest_run", RUN)
    fake(monkeypatch, run_log, "latest_completed_week", date(2026, 10, 12))

    response = client.get("/reporting/pipeline-runs/latest", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["weeks_from"] == "2026-10-05" and body["weeks_to"] == "2026-10-12"
    assert body["last_closed_week"] == "2026-10-12"
    assert body["stale"] is False
    assert body["weeks_revised"] == ["2026-10-05"]
    assert body["min_capture_coverage"] == 1.0
    assert not {"flow_run_id", "triggered_by", "error_message"} & body.keys()


def test_latest_run_is_stale_when_the_newest_closed_week_is_missing(api, monkeypatch):
    client, headers, _ = api
    failed = {**RUN, "status": "failed", "error_stage": "load", "error_class": "OperationalError"}
    fake(monkeypatch, run_log, "get_latest_run", failed)
    fake(monkeypatch, run_log, "latest_completed_week", date(2026, 10, 5))
    body = client.get("/reporting/pipeline-runs/latest", headers=headers).json()
    assert (body["status"], body["stale"], body["error_stage"]) == ("failed", True, "load")


def test_latest_run_404_when_the_pipeline_never_ran(api, monkeypatch):
    client, headers, _ = api
    fake(monkeypatch, run_log, "get_latest_run", None)
    assert client.get("/reporting/pipeline-runs/latest", headers=headers).status_code == 404


def test_latest_run_503_when_the_database_is_down(api, monkeypatch):
    client, headers, _ = api
    fake(monkeypatch, run_log, "get_latest_run", raises=DB_DOWN)
    assert client.get("/reporting/pipeline-runs/latest", headers=headers).status_code == 503


# --- Manual trigger ------------------------------------------------------------------


def test_trigger_schedules_a_run_for_the_caller(api, monkeypatch):
    client, headers, user_id = api
    flow_run_id = uuid4()
    fake(monkeypatch, run_log, "is_running", False)
    calls = fake(
        monkeypatch,
        trigger,
        "trigger_run",
        trigger.TriggeredRun(flow_run_id, "weekly_warehouse_client_performance", None),
    )

    response = client.post("/reporting/pipeline-runs", headers=headers)

    assert response.status_code == 202
    assert response.json() == {
        "flow_run_id": str(flow_run_id),
        "pipeline_name": "weekly_warehouse_client_performance",
        "week_start": None,
        "status": "scheduled",
    }
    assert calls[0][0] == (None, UUID(user_id))


def test_trigger_accepts_one_closed_week(api, monkeypatch):
    client, headers, _ = api
    fake(monkeypatch, run_log, "is_running", False)
    calls = fake(
        monkeypatch,
        trigger,
        "trigger_run",
        trigger.TriggeredRun(uuid4(), "weekly_warehouse_client_performance", date(2026, 10, 5)),
    )
    response = client.post(
        "/reporting/pipeline-runs", json={"week_start": "2026-10-05"}, headers=headers
    )
    assert response.status_code == 202
    assert calls[0][0][0] == date(2026, 10, 5)


@pytest.mark.parametrize(
    ("body", "message"),
    [({"week_start": "2026-10-06"}, "Monday"), ({"week_start": "2026-10-19"}, "closed")],
)
def test_trigger_refuses_a_week_the_flow_would_reject(api, monkeypatch, body, message):
    client, headers, _ = api
    calls = fake(monkeypatch, trigger, "trigger_run")
    response = client.post("/reporting/pipeline-runs", json=body, headers=headers)
    assert response.status_code == 422
    assert message in response.json()["detail"][0]["msg"]
    assert calls == []


def test_trigger_refuses_unknown_fields(api, monkeypatch):
    client, headers, _ = api
    response = client.post("/reporting/pipeline-runs", json={"weeks": 3}, headers=headers)
    assert response.status_code == 422


def test_trigger_is_409_while_a_run_is_in_progress(api, monkeypatch):
    client, headers, _ = api
    fake(monkeypatch, run_log, "is_running", True)
    calls = fake(monkeypatch, trigger, "trigger_run")
    response = client.post("/reporting/pipeline-runs", headers=headers)
    assert response.status_code == 409
    assert calls == []


def test_trigger_is_503_when_prefect_is_unavailable(api, monkeypatch):
    client, headers, _ = api
    fake(monkeypatch, run_log, "is_running", False)
    fake(monkeypatch, trigger, "trigger_run", raises=trigger.PrefectUnavailable("ConnectError"))
    response = client.post("/reporting/pipeline-runs", headers=headers)
    assert response.status_code == 503
    assert response.json() == {"detail": "The pipeline scheduler is unavailable"}


def test_trigger_is_503_when_the_run_log_is_unreachable(api, monkeypatch):
    client, headers, _ = api
    fake(monkeypatch, run_log, "is_running", raises=DB_DOWN)
    assert client.post("/reporting/pipeline-runs", headers=headers).status_code == 503


# --- trigger_run itself --------------------------------------------------------------


def test_trigger_run_refuses_without_a_configured_prefect_api():
    with (
        temporary_settings(updates={PREFECT_API_URL: None}),
        pytest.raises(trigger.PrefectUnavailable, match="PREFECT_API_URL"),
    ):
        trigger.trigger_run(None, uuid4())


def test_trigger_run_starts_the_weekly_deployment_without_waiting(monkeypatch):
    import prefect.deployments

    seen = {}

    class FlowRun:
        id = uuid4()

    def run_deployment(name, **kwargs):
        seen.update(name=name, **kwargs)
        return FlowRun()

    monkeypatch.setattr(prefect.deployments, "run_deployment", run_deployment)
    user = uuid4()
    with temporary_settings(updates={PREFECT_API_URL: "http://127.0.0.1:4200/api"}):
        scheduled = trigger.trigger_run(date(2026, 10, 5), user)
    assert scheduled.flow_run_id == FlowRun.id
    assert seen["name"] == "weekly-warehouse-client-performance/weekly"
    assert seen["timeout"] == 0 and seen["as_subflow"] is False
    assert seen["parameters"] == {
        "trigger": "manual",
        "triggered_by": str(user),
        "week_start": "2026-10-05",
    }


def test_trigger_run_reports_prefect_errors_by_class_only(monkeypatch):
    import prefect.deployments

    def run_deployment(name, **kwargs):
        raise ConnectionError("http://user:secret@prefect")

    monkeypatch.setattr(prefect.deployments, "run_deployment", run_deployment)
    with (
        temporary_settings(updates={PREFECT_API_URL: "http://127.0.0.1:4200/api"}),
        pytest.raises(trigger.PrefectUnavailable) as raised,
    ):
        trigger.trigger_run(None, uuid4())
    assert str(raised.value) == "ConnectionError"


# --- Module boundaries ---------------------------------------------------------------


def imported_modules(directory):
    for path in directory.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                yield path, node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    yield path, alias.name


def test_reporting_never_imports_telemetry():
    offenders = [
        (path.name, module)
        for path, module in imported_modules(ROOT / "services" / "reporting")
        if module.startswith("services.telemetry")
    ]
    assert offenders == []


def test_the_data_layer_never_imports_services():
    offenders = [
        (str(path.relative_to(ROOT)), module)
        for path, module in imported_modules(ROOT / "data")
        if module.split(".")[0] == "services"
    ]
    assert offenders == []


def test_pure_transforms_do_no_io():
    forbidden = ("sqlalchemy", "prefect", "psycopg2", "requests", "httpx", "os", "pathlib")
    offenders = [
        (path.name, module)
        for path, module in imported_modules(ROOT / "data" / "process")
        if module.split(".")[0] in forbidden
    ]
    assert offenders == []
