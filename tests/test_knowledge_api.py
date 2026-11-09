"""`POST /knowledge/query`: shapes and status codes, with `data.pipelines.rag.query` mocked."""

import ast
from pathlib import Path

import pytest

from data.pipelines import rag
from data.process.knowledge_base import KnowledgeBaseError
from services.core import security
from services.knowledge.models import MAX_QUESTION_LENGTH

ROUTER_SOURCE = (Path(__file__).resolve().parents[1] / "services/knowledge/router.py").read_text()


@pytest.fixture
def api(client, monkeypatch):
    """A signed-in client."""
    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)
    credentials = {"email": "sales@trackflow.com", "password": "commercial-password-42"}
    client.post("/users", json=credentials)
    token = client.post("/auth/login", json=credentials).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def mock_query(monkeypatch, answer: str = "", *, raises: Exception | None = None) -> list[str]:
    questions: list[str] = []

    def fake_query(question: str) -> str:
        questions.append(question)
        if raises is not None:
            raise raises
        return answer

    monkeypatch.setattr(rag, "query", fake_query)
    return questions


def test_requires_a_bearer_token(client, monkeypatch):
    questions = mock_query(monkeypatch, "never")

    response = client.post("/knowledge/query", json={"question": "Return window?"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert questions == []


def test_answers_with_the_generated_string_only(api, monkeypatch):
    client, headers = api
    questions = mock_query(monkeypatch, "The standard return window is 30 days from delivery.")

    response = client.post(
        "/knowledge/query",
        json={"question": "  What's the standard return window?  "},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"answer": "The standard return window is 30 days from delivery."}
    assert questions == ["What's the standard return window?"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": ""},
        {"question": "   "},
        {"question": "x" * (MAX_QUESTION_LENGTH + 1)},
        {"question": 42},
        {"question": "Return window?", "k": 50},
    ],
)
def test_invalid_body_is_422_and_never_reaches_the_pipeline(api, monkeypatch, body):
    client, headers = api
    questions = mock_query(monkeypatch, "never")

    response = client.post("/knowledge/query", json=body, headers=headers)

    assert response.status_code == 422
    assert questions == []


def test_knowledge_base_failure_is_503_without_details(api, monkeypatch, caplog):
    client, headers = api
    mock_query(monkeypatch, raises=KnowledgeBaseError("The vector database could not be searched"))

    response = client.post("/knowledge/query", json={"question": "Return window?"}, headers=headers)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "The knowledge assistant is unavailable right now. Please try again in a moment."
    }
    assert "vector database" in caplog.text  # the cause stays in the server log


def code_identifiers(source: str) -> set[str]:
    """Every name, attribute and imported module in the code (docstrings and comments excluded)."""
    identifiers: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            identifiers.update({node.module or "", *(alias.name for alias in node.names)})
        elif isinstance(node, ast.Import):
            identifiers.update(alias.name for alias in node.names)
    return identifiers


def test_router_delegates_to_query_and_holds_no_rag_logic():
    identifiers = code_identifiers(ROUTER_SOURCE)

    assert {"rag", "query"} <= identifiers
    forbidden = {"retrieve", "generate_answer", "build_messages", "embed", "QdrantClient", "OpenAI"}
    assert not identifiers & forbidden
    assert not any(name.startswith(("qdrant_client", "openai")) for name in identifiers)
