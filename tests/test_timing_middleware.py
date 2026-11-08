import logging


def _timing_messages(caplog):
    return [
        record.getMessage() for record in caplog.records if record.name == "api.timing"
    ]


def test_timing_logs_route_template_and_status_without_query_values(client, caplog):
    with caplog.at_level(logging.INFO, logger="api.timing"):
        response = client.get("/api/incidents?status=not-a-status")

    assert response.status_code == 400
    messages = _timing_messages(caplog)
    assert len(messages) == 1
    assert messages[0].startswith("GET /api/incidents -> 400 | ")
    assert "not-a-status" not in messages[0]
    assert messages[0].endswith("ms")


def test_timing_logs_successful_response(client, caplog):
    with caplog.at_level(logging.INFO, logger="api.timing"):
        response = client.get("/api/incidents/summary")

    assert response.status_code == 200
    assert any(
        message.startswith("GET /api/incidents/summary -> 200 | ")
        for message in _timing_messages(caplog)
    )


def test_timing_logs_unhandled_failure_as_500(raw_client, monkeypatch, caplog):
    from services.incidents import service

    def fail_list(**kwargs):
        raise RuntimeError("synthetic timing failure")

    monkeypatch.setattr(service, "list_incidents", fail_list)

    with caplog.at_level(logging.INFO, logger="api.timing"):
        response = raw_client.get("/api/incidents")

    assert response.status_code == 500
    assert "synthetic timing failure" not in response.text
    assert any(
        message.startswith("GET /api/incidents -> 500 | ")
        for message in _timing_messages(caplog)
    )
