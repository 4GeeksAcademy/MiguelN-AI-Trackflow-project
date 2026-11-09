"""Fakes shared by the RAG tests: no test may reach Qdrant, the embeddings model or the LLM."""

import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from data.pipelines import rag
from data.process import knowledge_base
from data.process.knowledge_base import RagSettings

TEST_EMBEDDING_MODEL = "test-embedding-model"
TEST_GENERATION_MODEL = "test-generation-model"


def pinned_rag_settings(**overrides: Any) -> RagSettings:
    """Settings that ignore the developer's `.env` and shell."""
    values: dict[str, Any] = {
        "qdrant_url": "http://qdrant.invalid:6333",
        "llm_base_url": "http://llm.invalid/v1",
        "llm_api_key": "test-key",
        "embedding_model": TEST_EMBEDDING_MODEL,
        "generation_model": TEST_GENERATION_MODEL,
        **overrides,
    }
    return RagSettings(_env_file=None, **values)  # type: ignore[call-arg]


def hashed_vector(text: str, size: int = 8) -> list[float]:
    """A deterministic, non-zero stand-in for an embedding."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [digest[i] / 255 + 0.01 for i in range(size)]


class FakeOpenAI:
    """Records calls to `embeddings.create` and `chat.completions.create`."""

    def __init__(self, *, answer: str | None = "MODEL ANSWER", error: Exception | None = None):
        self.embedding_calls: list[dict[str, Any]] = []
        self.chat_calls: list[dict[str, Any]] = []
        self._answer = answer
        self._error = error
        self.embeddings = SimpleNamespace(create=self._embed)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

    def _embed(self, **kwargs: Any) -> Any:
        self.embedding_calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(data=[SimpleNamespace(embedding=hashed_vector(kwargs["input"]))])

    def _chat(self, **kwargs: Any) -> Any:
        self.chat_calls.append(kwargs)
        if self._error is not None:
            raise self._error
        message = SimpleNamespace(content=self._answer)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _forbidden(name: str):
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(f"Unit tests must not create a real {name}")

    return fail


@pytest.fixture
def offline_rag(monkeypatch):
    """Pinned settings, and every real client replaced by one that fails loudly."""
    settings = pinned_rag_settings()
    monkeypatch.setattr(knowledge_base, "get_rag_settings", lambda: settings)
    monkeypatch.setattr(rag, "get_rag_settings", lambda: settings)
    monkeypatch.setattr(knowledge_base, "get_qdrant_client", _forbidden("Qdrant client"))
    monkeypatch.setattr(rag, "get_qdrant_client", _forbidden("Qdrant client"))
    monkeypatch.setattr(knowledge_base, "_embedding_client", _forbidden("embeddings client"))
    monkeypatch.setattr(rag, "_generation_client", _forbidden("generation client"))
    return settings
