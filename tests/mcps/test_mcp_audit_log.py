"""Every tool invocation is logged: which tool, which client, what result."""

import json

from mcps.trackflow_tools.audit import AuditSink, loggable_arguments
from tests.conftest import VALID_INCIDENT
from tests.mcps.conftest import ALL, McpHarness, issue_token


def tool_calls(harness: McpHarness) -> list[dict]:
    return [record for record in harness.sink.records if record["event"] == "tool_call"]


def test_a_successful_call_names_the_tool_the_client_and_the_requests_made(harness):
    token = issue_token(["incidents:read"], client_id="trackflow-agent")

    harness.call_sync(token, "search_incidents", {"status": "open", "limit": 3})

    (record,) = tool_calls(harness)
    assert record["tool"] == "search_incidents"
    assert record["client_id"] == "trackflow-agent"
    assert record["subject"] == "trackflow-agent@clients"
    assert record["scopes"] == ["incidents:read"]
    assert record["arguments"] == {"limit": 3, "status": "open"}
    assert record["outcome"] == "ok"
    assert record["error"] is None
    assert record["upstream"] == [{"method": "GET", "path": "/api/incidents", "status": 200}]
    assert record["duration_ms"] >= 0
    assert record["ts"].endswith("+00:00")


def test_refusals_and_failures_are_logged_with_their_code(harness):
    async def scenario(h: McpHarness):
        await h.call(issue_token([]), "get_incident", {"reference": "1"})
        await h.call(
            issue_token(ALL), "query_inventory", {"operation": "record_inbound", "quantity": 2}
        )
        await h.call(issue_token(ALL), "get_incident", {"reference": "999999"})
        await h.call(issue_token(ALL), "create_incident", {"title": ""})

    harness.run(scenario)

    assert [(r["tool"], r["outcome"], r["error"]) for r in tool_calls(harness)] == [
        ("get_incident", "error", "insufficient_scope"),
        ("query_inventory", "error", "inventory_read_only"),
        ("get_incident", "error", "incident_not_found"),
        ("create_incident", "error", "invalid_arguments"),
    ]


def test_free_text_and_tokens_never_reach_the_log(harness):
    token = issue_token(ALL)

    harness.call_sync(token, "create_incident", VALID_INCIDENT)

    (record,) = tool_calls(harness)
    logged = json.dumps(record)
    assert VALID_INCIDENT["title"] not in logged
    assert VALID_INCIDENT["description"] not in logged
    assert token not in logged
    assert record["arguments"]["title"] == f"<{len(VALID_INCIDENT['title'])} chars>"
    assert record["arguments"]["category"] == "lost_parcel"


def test_identifying_arguments_are_logged_by_value():
    assert loggable_arguments(
        {
            "incident_id": "abc",
            "reference": "482",
            "operation": "list_products",
            "name": "black shoes",
        }
    ) == {
        "incident_id": "abc",
        "name": "<11 chars>",
        "operation": "list_products",
        "reference": "482",
    }


def test_the_sink_appends_json_lines(tmp_path):
    path = tmp_path / "nested" / "audit.jsonl"
    sink = AuditSink(path)

    sink.write({"event": "tool_call", "tool": "a"})
    sink.write({"event": "tool_call", "tool": "b"})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["tool"] for line in lines] == ["a", "b"]
