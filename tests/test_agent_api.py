"""`POST /agent/query` and `GET /agent/runs/{run_id}`: the graph over HTTP.

Most tests run the real compiled graph inside the app (built by the lifespan,
checkpointed in the per-test store) with `rag.retrieve` / `rag.generate_answer`,
the planner and the tools faked, so a status code is checked against what the
graph actually decided.
"""

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from data.process.knowledge_base import KnowledgeBaseError
from services.agent import graph as agent_graph
from services.agent import nodes, runner
from services.agent.errors import AgentGraphBuildError
from services.agent.graph import NodeSpec
from services.agent.state import EMPTY_QUESTION, NO_INFORMATION_ANSWER, TICKET_UNAVAILABLE_ANSWER
from services.agent.tracing import get_trace_store
from services.core import security
from services.knowledge.models import MAX_QUESTION_LENGTH
from services.main import app
from tests.pipelines.test_agent_graph import (
    ANSWER,
    CHUNK,
    FakeRag,
    install_fakes,
    plan,
    step,
    ticket_result,
)
from tests.test_knowledge_api import code_identifiers

ROUTER_SOURCE = (Path(__file__).resolve().parents[1] / "services/agent/router.py").read_text()


def sign_in(client: TestClient, email: str) -> tuple[dict[str, str], str]:
    """Register and log in; returns the bearer header and the user's id."""
    credentials = {"email": email, "password": "commercial-password-42"}
    user_id = client.post("/users", json=credentials).json()["id"]
    token = client.post("/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, user_id


@pytest.fixture
def api(client, monkeypatch):
    """A signed-in client."""
    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)
    headers, _ = sign_in(client, "sales@trackflow.com")
    return client, headers


@pytest.fixture
def fake_rag(monkeypatch):
    return install_fakes(monkeypatch, FakeRag())


def ask(client: TestClient, headers: dict[str, str], body: Any) -> Any:
    return client.post("/agent/query", json=body, headers=headers)


# --- POST /agent/query ---------------------------------------------------------------


def test_requires_a_bearer_token(client, fake_rag):
    response = client.post("/agent/query", json={"question": "Return window?"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert fake_rag.retrieve_calls == []


def test_answers_with_the_answer_and_the_run_id(api, fake_rag):
    client, headers = api

    response = ask(client, headers, {"question": "  What's the standard return window?  "})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"run_id", "conversation_id", "answer", "memory"}
    assert body["answer"] == ANSWER
    assert body["memory"] is None  # nothing proposed, nothing pending
    uuid.UUID(body["run_id"])
    uuid.UUID(body["conversation_id"])
    assert fake_rag.retrieve_calls == ["What's the standard return window?"]
    assert fake_rag.generate_calls == [("What's the standard return window?", [CHUNK])]


def test_no_context_is_answered_honestly(api, fake_rag):
    client, headers = api
    fake_rag.context = []

    response = ask(client, headers, {"question": "Who won the last football World Cup?"})

    assert response.status_code == 200
    assert response.json()["answer"] == NO_INFORMATION_ANSWER
    assert fake_rag.generate_calls == []


def test_the_endpoint_only_invokes_the_graph(client, monkeypatch):
    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)
    headers, user_id = sign_in(client, "sales@trackflow.com")
    calls: list[tuple[str, str | None, str | None]] = []

    def fake_run_agent(
        question: str, *, user_id: str | None = None, conversation_id: str | None = None
    ) -> runner.AgentResult:
        calls.append((question, user_id, conversation_id))
        return runner.AgentResult(
            run_id="run-1", outcome="answered", answer="From the graph.", conversation_id="c-1"
        )

    monkeypatch.setattr(runner, "run_agent", fake_run_agent)

    response = ask(client, headers, {"question": " as typed "})

    assert response.json() == {
        "run_id": "run-1",
        "conversation_id": "c-1",
        "answer": "From the graph.",
        "memory": None,
    }
    assert calls == [(" as typed ", user_id, None)]  # unchanged: the graph normalises it


def test_router_holds_no_rag_or_graph_logic():
    identifiers = code_identifiers(ROUTER_SOURCE)

    assert {"runner", "run_agent"} <= identifiers
    forbidden = {
        "rag",
        "retrieve",
        "generate_answer",
        "query",
        "build_agent_graph",
        "get_agent_graph",
        "StateGraph",
        "embed",
        "planner",
        "choose_sources",
        "lookup_ticket",
        "lookup_stock",
        "mcp_lookups",
        "mcp_client",
        "call_tool",
        "httpx",
        "incidents",
        "inventory",
    }
    assert not identifiers & forbidden


def test_a_ticket_question_is_answered_from_the_live_lookup(api, fake_rag):
    client, headers = api
    fake_rag.plan = plan(step("incident_manager", reference="12"))

    response = ask(client, headers, {"question": "What's the status of ticket 12?"})

    assert response.status_code == 200
    assert fake_rag.retrieve_calls == []
    trace = client.get(f"/agent/runs/{response.json()['run_id']}", headers=headers).json()
    assert trace["sources"] == ["incident_manager"]


def test_an_incident_manager_outage_is_a_200_with_the_honest_answer(api, fake_rag):
    client, headers = api
    fake_rag.plan = plan(step("incident_manager", reference="12"))
    fake_rag.ticket = ticket_result("unavailable", failure="timeout")

    response = ask(client, headers, {"question": "What's the status of ticket 12?"})

    assert response.status_code == 200, "a failed tool is a recovery path, not an error"
    assert response.json()["answer"] == TICKET_UNAVAILABLE_ANSWER.format(reference="TRF-000012")
    assert fake_rag.generate_calls == []


@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
def test_an_empty_question_is_refused_by_the_graph_with_422(api, fake_rag, question):
    client, headers = api

    response = ask(client, headers, {"question": question})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["message"] == EMPTY_QUESTION
    trace = get_trace_store().get_trace(detail["run_id"])
    assert trace.outcome == "rejected"
    assert trace.node_names() == ["receive_question"]
    assert fake_rag.retrieve_calls == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": 42},
        {"question": "x" * (MAX_QUESTION_LENGTH + 1)},
        {"question": "Return window?", "k": 50},
    ],
)
def test_a_malformed_body_is_422_and_never_reaches_the_graph(api, fake_rag, body):
    client, headers = api

    response = ask(client, headers, body)

    assert response.status_code == 422
    assert get_trace_store().list_runs() == []
    assert fake_rag.retrieve_calls == []


def test_an_unavailable_knowledge_base_is_503_naming_the_step(api, fake_rag, caplog):
    client, headers = api
    fake_rag.retrieve_error = KnowledgeBaseError("The vector database could not be searched")

    response = ask(client, headers, {"question": "Return window?"})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "'retrieve' step is unavailable" in detail["message"]
    assert "vector database" not in response.text
    assert "Traceback" not in response.text
    trace = get_trace_store().get_trace(detail["run_id"])
    assert trace.status == "failed"
    assert trace.error_node == "retrieve"
    assert "failed at retrieve" in caplog.text


def test_an_unexpected_node_error_is_500_without_its_details(api, fake_rag):
    client, headers = api
    fake_rag.generate_error = RuntimeError("internal detail at http://10.0.0.5?key=secret")

    response = ask(client, headers, {"question": "Return window?"})

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "'generate_answer' step failed unexpectedly" in detail["message"]
    uuid.UUID(detail["run_id"])
    for leaked in ("secret", "10.0.0.5", "RuntimeError", "Traceback"):
        assert leaked not in response.text


# --- GET /agent/runs/{run_id} ----------------------------------------------------------


def test_the_trace_of_a_run_is_readable_by_its_owner(api, fake_rag):
    client, headers = api
    run_id = ask(client, headers, {"question": "Return window?"}).json()["run_id"]

    response = client.get(f"/agent/runs/{run_id}", headers=headers)

    assert response.status_code == 200
    trace = response.json()
    assert trace["run_id"] == run_id
    assert trace["status"] == "completed"
    assert trace["outcome"] == "answered"
    assert "user_id" not in trace
    assert trace["sources"] == ["knowledge_base"]
    assert [(e["kind"], e["name"]) for e in trace["events"]] == [
        ("node", "receive_question"),
        ("edge", "route_question"),
        ("node", "recall_memory"),
        ("node", "plan_sources"),
        ("edge", "route_after_plan"),
        ("node", "retrieve"),
        ("edge", "route_after_retrieve"),
        ("node", "generate_answer"),
        ("edge", "route_after_answer"),
        ("node", "compose_response"),
    ]
    assert trace["events"][5]["output"] == {"context": [CHUNK]}
    assert trace["events"][6]["output"]["to"] == "generate_answer"
    uuid.UUID(trace["conversation_id"])


def test_another_users_run_is_not_found(api, fake_rag):
    client, headers = api
    run_id = ask(client, headers, {"question": "Return window?"}).json()["run_id"]
    other, _ = sign_in(client, "other@trackflow.com")

    response = client.get(f"/agent/runs/{run_id}", headers=other)

    assert response.status_code == 404
    assert response.json() == {"detail": "Agent run not found"}


def test_an_unknown_or_malformed_run_id(api):
    client, headers = api

    assert client.get(f"/agent/runs/{uuid.uuid4()}", headers=headers).status_code == 404
    assert client.get("/agent/runs/not-a-uuid", headers=headers).status_code == 422
    assert client.get(f"/agent/runs/{uuid.uuid4()}").status_code == 401


# --- Startup -----------------------------------------------------------------------------


def test_the_graph_is_compiled_at_startup(db_path):
    assert agent_graph.get_agent_graph.cache_info().currsize == 0

    with TestClient(app):
        assert agent_graph.get_agent_graph.cache_info().currsize == 1

    assert agent_graph.get_agent_graph.cache_info().currsize == 0  # closed on shutdown


def test_a_structurally_broken_graph_stops_startup(db_path, monkeypatch):
    orphan = NodeSpec("summarise", nodes.answer_no_information, frozenset(), frozenset({"answer"}))
    shipped_build = agent_graph.build_agent_graph

    def build_with_an_orphan(checkpointer: Any) -> Any:
        return shipped_build(checkpointer, node_specs=agent_graph.NODES + (orphan,))

    monkeypatch.setattr(agent_graph, "build_agent_graph", build_with_an_orphan)

    with pytest.raises(AgentGraphBuildError, match="summarise"), TestClient(app):
        pass
