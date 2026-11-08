"""The technical report's analysis pipeline, against an in-memory event table.

`load_events` is the only function that touches PostgreSQL; here it is replaced
by one that serves rows from a list, the way the table would. Its SQL is
covered by `tests/test_telemetry_postgres.py`.
"""

import ast
import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from services.telemetry import analysis

METRICS = (
    analysis.events_per_day,
    analysis.event_type_share,
    analysis.error_rate_by_type,
    analysis.latency_by_route,
)
COLUMNS = ["timestamp", "event_type", "session_id", "payload"]

START = datetime(2026, 10, 18, tzinfo=timezone.utc)
END = datetime(2026, 10, 21, tzinfo=timezone.utc)


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def event(event_type, timestamp, session="s1", **payload):
    return {
        "timestamp": timestamp,
        "event_type": event_type,
        "session_id": session,
        "payload": payload,
    }


def call(timestamp, route="/auth/login", ms=100.0, status="2xx", method="POST", rate=1, **kw):
    return event(
        "api_latency_recorded",
        timestamp,
        route_template=route,
        method=method,
        status_class=status,
        duration_ms=ms,
        sample_rate=rate,
        **kw,
    )


def failure(timestamp, error_class="auth", route="/auth/login", status=401):
    payload = {"route_template": route, "method": "POST", "status_code": status}
    if error_class is not None:
        payload["error_class"] = error_class
    return event("api_error_recorded", timestamp, **payload)


@pytest.fixture
def table(monkeypatch):
    """The rows `load_events` serves, and every call it received.

    Like the real loader it filters by type and drops `payload` unless asked;
    unlike it, it ignores the window, so the metrics' own window check is what
    the window tests exercise.
    """
    state = SimpleNamespace(rows=[], calls=[])

    def load_events(start, end, event_types=None, *, payload=False):
        state.calls.append(
            {"start": start, "end": end, "event_types": event_types, "payload": payload}
        )
        frame = pd.DataFrame(state.rows, columns=COLUMNS)
        if event_types is not None:
            frame = frame[frame["event_type"].isin(event_types)]
        return frame if payload else frame.drop(columns="payload")

    monkeypatch.setattr(analysis, "load_events", load_events)
    return state


# --- events_per_day ---------------------------------------------------------


def test_events_per_day_lists_every_day_of_the_window_including_empty_ones(table):
    table.rows += [
        event("section_viewed", "2026-10-18T09:00:00Z", session="a"),
        event("login_succeeded", "2026-10-18T09:01:00Z", session="b"),
        event("section_viewed", "2026-10-20T10:00:00Z", session="a"),
        event("section_viewed", "2026-10-20T11:00:00Z", session="a"),
        event("page_load_measured", "2026-10-20T11:00:01Z", session="a"),
    ]

    assert analysis.events_per_day(START, END) == [
        {"date": "2026-10-18", "events": 2, "sessions": 2, "event_types": 2, "partial_day": False},
        {"date": "2026-10-19", "events": 0, "sessions": 0, "event_types": 0, "partial_day": False},
        {"date": "2026-10-20", "events": 3, "sessions": 1, "event_types": 2, "partial_day": False},
    ]


def test_timestamps_are_grouped_by_their_utc_day_not_their_written_one(table):
    """The bug the tech lead warned about: grouped as text, this event lands on the 20th."""
    table.rows.append(event("section_viewed", "2026-10-20T01:30:00+05:00"))

    by_day = {row["date"]: row["events"] for row in analysis.events_per_day(START, END)}

    assert by_day == {"2026-10-18": 0, "2026-10-19": 1, "2026-10-20": 0}


def test_window_end_is_exclusive_and_events_outside_it_are_ignored(table):
    table.rows += [
        event("section_viewed", "2026-10-17T23:59:59.999999Z"),
        event("section_viewed", "2026-10-18T00:00:00Z"),
        event("section_viewed", "2026-10-21T00:00:00Z"),
    ]

    rows = analysis.events_per_day(START, END)

    assert [row["date"] for row in rows] == ["2026-10-18", "2026-10-19", "2026-10-20"]
    assert sum(row["events"] for row in rows) == 1


def test_days_the_window_covers_in_part_are_marked(table):
    rows = analysis.events_per_day(utc(2026, 10, 19, 12), utc(2026, 10, 21, 6))

    assert [(row["date"], row["partial_day"]) for row in rows] == [
        ("2026-10-19", True),
        ("2026-10-20", False),
        ("2026-10-21", True),
    ]


def test_naive_datetimes_are_read_as_utc(table):
    table.rows.append(event("section_viewed", "2026-10-18T00:30:00Z"))

    naive = analysis.events_per_day(datetime(2026, 10, 18), datetime(2026, 10, 19))

    assert naive == analysis.events_per_day(utc(2026, 10, 18), utc(2026, 10, 19))
    assert naive[0]["events"] == 1


def test_offset_windows_are_converted_to_utc(table):
    mexico = timezone(timedelta(hours=-6))
    rows = analysis.events_per_day(
        datetime(2026, 10, 17, 18, tzinfo=mexico), datetime(2026, 10, 18, 18, tzinfo=mexico)
    )

    assert table.calls[0]["start"] == utc(2026, 10, 18)
    assert table.calls[0]["end"] == utc(2026, 10, 19)
    assert [row["date"] for row in rows] == ["2026-10-18"]


@pytest.mark.parametrize("metric", METRICS, ids=lambda metric: metric.__name__)
def test_an_empty_or_reversed_window_is_refused(table, metric):
    with pytest.raises(ValueError):
        metric(END, END)
    with pytest.raises(ValueError):
        metric(END, START)


# --- event_type_share -------------------------------------------------------


def test_event_type_share_ranks_types_per_day(table):
    table.rows += [
        event("page_load_measured", "2026-10-18T09:00:00Z"),
        event("page_load_measured", "2026-10-18T09:00:01Z"),
        event("section_viewed", "2026-10-18T09:00:02Z"),
        event("api_latency_recorded", "2026-10-18T09:00:03Z"),
        event("section_viewed", "2026-10-20T09:00:00Z"),
    ]

    assert analysis.event_type_share(START, END) == [
        {"date": "2026-10-18", "event_type": "page_load_measured", "events": 2, "share": 0.5},
        {"date": "2026-10-18", "event_type": "api_latency_recorded", "events": 1, "share": 0.25},
        {"date": "2026-10-18", "event_type": "section_viewed", "events": 1, "share": 0.25},
        {"date": "2026-10-20", "event_type": "section_viewed", "events": 1, "share": 1.0},
    ]


# --- error_rate_by_type -----------------------------------------------------


def test_error_rate_divides_each_class_by_the_days_requests(table):
    table.rows += [
        call("2026-10-18T09:00:00Z", status="4xx"),
        call("2026-10-18T09:00:01Z", status="5xx"),
        call("2026-10-18T09:00:02Z"),
        # Kept with probability 0.5, so it stands for two requests: five in all.
        call("2026-10-18T09:00:03Z", rate=0.5),
        failure("2026-10-18T09:00:00Z", "auth"),
        failure("2026-10-18T09:00:01Z", "unhandled", status=503),
    ]

    day = [row for row in analysis.error_rate_by_type(START, END) if row["date"] == "2026-10-18"]

    assert [row["error_class"] for row in day] == list(analysis.ERROR_CLASSES)
    assert {row["error_class"]: row["errors"] for row in day if row["errors"]} == {
        "auth": 1,
        "unhandled": 1,
    }
    assert {row["requests"] for row in day} == {5}
    assert {row["error_class"]: row["error_rate"] for row in day if row["errors"]} == {
        "auth": 0.2,
        "unhandled": 0.2,
    }
    assert sum(row["error_rate"] for row in day) == pytest.approx(0.4)


def test_error_rate_is_none_on_a_day_without_requests(table):
    table.rows.append(failure("2026-10-19T09:00:00Z", "auth"))

    rows = analysis.error_rate_by_type(START, END)

    assert len(rows) == 3 * len(analysis.ERROR_CLASSES)
    assert {row["error_rate"] for row in rows} == {None}
    assert {row["requests"] for row in rows} == {0}
    assert [row["errors"] for row in rows if row["errors"]] == [1]


def test_classes_outside_the_catalogue_are_listed_after_it(table):
    table.rows += [
        call("2026-10-18T09:00:00Z", status="4xx"),
        call("2026-10-18T09:00:01Z", status="4xx"),
        failure("2026-10-18T09:00:00Z", "teapot", status=418),
        failure("2026-10-18T09:00:01Z", None),
    ]

    day = [row for row in analysis.error_rate_by_type(START, END) if row["date"] == "2026-10-18"]

    assert [row["error_class"] for row in day] == [
        *analysis.ERROR_CLASSES,
        "teapot",
        "unclassified",
    ]
    assert [row["errors"] for row in day[-2:]] == [1, 1]


def test_error_classes_match_the_event_catalogue():
    schemas = json.loads(Path("docs/telemetry/event-schemas.json").read_text())
    api_error = schemas["definitions"]["events"]["api_error_recorded"]["allOf"][1]
    enum = api_error["properties"]["properties"]["properties"]["error_class"]["enum"]

    assert tuple(enum) == analysis.ERROR_CLASSES


# --- latency_by_route -------------------------------------------------------


def test_latency_percentiles_per_route_and_day(table):
    table.rows += [
        call("2026-10-18T09:00:00Z", ms=400.0, status="4xx"),
        call("2026-10-18T09:00:01Z", ms=100.0),
        call("2026-10-18T09:00:02Z", ms=300.0),
        call("2026-10-18T09:00:03Z", ms=200.0),
        call("2026-10-18T09:00:04Z", route="/inventory/products", method="GET", ms=900.0),
    ]

    assert analysis.latency_by_route(START, END) == [
        {
            "date": "2026-10-18",
            "method": "GET",
            "route_template": "/inventory/products",
            "samples": 1,
            "requests": 1,
            "p50_ms": 900.0,
            "p95_ms": 900.0,
            "mean_ms": 900.0,
            "max_ms": 900.0,
            "error_rate": 0.0,
        },
        {
            "date": "2026-10-18",
            "method": "POST",
            "route_template": "/auth/login",
            "samples": 4,
            "requests": 4,
            "p50_ms": 200.0,
            "p95_ms": 400.0,
            "mean_ms": 250.0,
            "max_ms": 400.0,
            "error_rate": 0.25,
        },
    ]


def test_sampled_calls_weigh_as_the_requests_they_stand_for(table):
    """Ten fast calls kept at 10% must outweigh one slow call kept in full."""
    table.rows += [
        call("2026-10-18T09:00:00Z", ms=50.0, rate=0.1),
        call("2026-10-18T09:00:01Z", ms=900.0, status="5xx"),
    ]

    [route] = analysis.latency_by_route(START, END)

    assert (route["samples"], route["requests"]) == (2, 11)
    assert (route["p50_ms"], route["p95_ms"], route["max_ms"]) == (50.0, 900.0, 900.0)
    assert route["mean_ms"] == pytest.approx(127.3)
    assert route["error_rate"] == pytest.approx(round(1 / 11, 4))


def test_latency_rows_are_split_by_day_and_ignore_malformed_measurements(table):
    table.rows += [
        call("2026-10-18T09:00:00Z", ms=100.0),
        call("2026-10-20T09:00:00Z", ms=120.0),
        call("2026-10-20T09:00:01Z", ms="slow"),
        event("api_latency_recorded", "2026-10-20T09:00:02Z", method="GET", duration_ms=5),
    ]

    rows = analysis.latency_by_route(START, END)

    assert [(row["date"], row["samples"], row["p50_ms"]) for row in rows] == [
        ("2026-10-18", 1, 100.0),
        ("2026-10-20", 1, 120.0),
    ]


# --- rules every metric follows ---------------------------------------------


@pytest.fixture
def busy_table(table):
    table.rows += [
        event("section_viewed", "2026-10-18T09:00:00Z", session="a"),
        event("page_load_measured", "2026-10-19T09:00:00Z", session="b"),
        call("2026-10-19T10:00:00Z", status="4xx"),
        call("2026-10-20T10:00:00Z", ms=80.5, rate=0.1),
        failure("2026-10-19T10:00:00Z", "auth"),
    ]
    return table


@pytest.mark.parametrize("metric", METRICS, ids=lambda metric: metric.__name__)
def test_same_window_gives_the_same_result(busy_table, metric):
    assert metric(START, END) == metric(START, END)


@pytest.mark.parametrize("metric", METRICS, ids=lambda metric: metric.__name__)
def test_metrics_do_not_modify_what_the_loader_returns(busy_table, monkeypatch, metric):
    loaded = []
    load = analysis.load_events

    def keep(*args, **kwargs):
        frame = load(*args, **kwargs)
        loaded.append((frame, frame.copy(deep=True)))
        return frame

    monkeypatch.setattr(analysis, "load_events", keep)
    metric(START, END)

    [(frame, before)] = loaded
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("metric", METRICS, ids=lambda metric: metric.__name__)
def test_each_metric_loads_its_own_events_and_needs_no_other_metric(
    busy_table, monkeypatch, metric
):
    def must_not_be_called(*args, **kwargs):
        raise AssertionError("a metric function called another one")

    for other in METRICS:
        if other is not metric:
            monkeypatch.setattr(analysis, other.__name__, must_not_be_called)

    metric(START, END)

    assert len(busy_table.calls) == 1
    assert busy_table.calls[0]["start"] == START
    assert busy_table.calls[0]["end"] == END


def test_each_metric_asks_only_for_the_events_it_uses(busy_table):
    requested = {}
    for metric in METRICS:
        busy_table.calls.clear()
        metric(START, END)
        requested[metric.__name__] = (
            busy_table.calls[0]["event_types"],
            busy_table.calls[0]["payload"],
        )

    assert requested == {
        "events_per_day": (None, False),
        "event_type_share": (None, False),
        "error_rate_by_type": (("api_latency_recorded", "api_error_recorded"), True),
        "latency_by_route": (("api_latency_recorded",), True),
    }


@pytest.mark.parametrize("metric", METRICS, ids=lambda metric: metric.__name__)
def test_results_are_strict_json(busy_table, metric):
    rows = metric(START, END)

    assert rows
    assert json.loads(json.dumps(rows, allow_nan=False)) == rows


@pytest.mark.parametrize("metric", METRICS, ids=lambda metric: metric.__name__)
def test_an_empty_table_gives_empty_or_zero_rows(table, metric):
    rows = metric(START, END)

    assert json.dumps(rows, allow_nan=False)
    assert all(row.get("events", 0) == 0 and row.get("errors", 0) == 0 for row in rows)


@pytest.mark.parametrize("metric", METRICS, ids=lambda metric: metric.__name__)
def test_metrics_have_no_default_window(metric):
    parameters = inspect.signature(metric).parameters

    assert list(parameters) == ["start", "end"]
    assert all(p.default is inspect.Parameter.empty for p in parameters.values())


LOOP_NODES = (
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Lambda,
)
ROW_BY_ROW_METHODS = {"apply", "applymap", "iterrows", "itertuples", "items"}


def test_the_pipeline_calculates_with_pandas_operations_only():
    """No Python loop, comprehension or row-by-row callback anywhere in the module."""
    tree = ast.parse(Path(analysis.__file__).read_text())

    loops = [
        f"{type(node).__name__} at line {node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, LOOP_NODES)
    ]
    row_by_row = [
        f".{node.attr} at line {node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in ROW_BY_ROW_METHODS
    ]

    assert loops == []
    assert row_by_row == []
