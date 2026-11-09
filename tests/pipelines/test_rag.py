"""Phase 2 of the RAG: `retrieve()` and `query()`, with no live Qdrant or model.

`retrieve()` runs against the in-memory Qdrant client and a stub client;
`query()` runs with `retrieve()` and the generation LLM mocked. Run on its own
with `python -m pytest tests/pipelines/test_rag.py`.
"""

import re
from types import SimpleNamespace
from typing import Any

import pytest
from openai import APIConnectionError
from qdrant_client import QdrantClient, models

from data.pipelines import rag
from data.process.knowledge_base import COLLECTION_NAME, KnowledgeBaseError
from tests.pipelines.rag_support import (
    TEST_GENERATION_MODEL,
    FakeOpenAI,
    offline_rag,  # noqa: F401  (fixture)
)

pytestmark = pytest.mark.usefixtures("offline_rag")

RETURN_WINDOW = {
    "company": "trackflow",
    "source_document": "returns-policy",
    "section": "Returns Policy › Standard return window",
    "language": "en",
    "chunk_index": 1,
    "text": "Returns Policy\nStandard return window: 30 days from delivery.",
}
RURAL_ARAGON = {
    "company": "trackflow",
    "source_document": "carrier-coverage",
    "section": "Carrier Coverage › Spain (Zaragoza)",
    "language": "en",
    "chunk_index": 1,
    "text": "Carrier Coverage\nSEUR: best coverage in rural areas of Aragón.",
}
STORAGE_RATE = {
    "company": "trackflow",
    "source_document": "storage-pricing",
    "section": "Storage Pricing",
    "language": "en",
    "chunk_index": 0,
    "text": "Storage Pricing\nStandard rate: 16 EUR per cubic meter per month (Zaragoza).",
}

# Unit vectors: a question embedded as RETURNS scores 1.0 against the returns
# chunk, ~0.71 against the mixed one and 0.0 against the unrelated one.
RETURNS = [1.0, 0.0, 0.0]
CARRIERS = [0.0, 1.0, 0.0]
MIXED = [1.0, 0.0, 1.0]


@pytest.fixture
def qdrant() -> QdrantClient:
    client = QdrantClient(":memory:")
    client.create_collection(
        COLLECTION_NAME,
        vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE),
    )
    client.upsert(
        COLLECTION_NAME,
        points=[
            models.PointStruct(id=1, vector=RETURNS, payload=RETURN_WINDOW),
            models.PointStruct(id=2, vector=CARRIERS, payload=RURAL_ARAGON),
            models.PointStruct(id=3, vector=MIXED, payload=STORAGE_RATE),
        ],
    )
    return client


def embed_as(vector: list[float]):
    calls: list[str] = []

    def embedder(text: str) -> list[float]:
        calls.append(text)
        return vector

    embedder.calls = calls  # type: ignore[attr-defined]
    return embedder


# --- retrieve() ------------------------------------------------------------------------


def test_retrieve_excludes_results_below_min_score(qdrant):
    hits = rag.retrieve(
        "standard return window?", k=5, min_score=0.5, client=qdrant, embedder=embed_as(RETURNS)
    )

    assert [hit["source_document"] for hit in hits] == ["returns-policy", "storage-pricing"]
    assert all(hit["score"] >= 0.5 for hit in hits)


def test_retrieve_can_return_fewer_than_k_results(qdrant):
    hits = rag.retrieve(
        "standard return window?", k=5, min_score=0.9, client=qdrant, embedder=embed_as(RETURNS)
    )

    assert len(hits) == 1  # k was 5
    assert hits[0]["text"] == RETURN_WINDOW["text"]


def test_retrieve_returns_nothing_when_no_hit_clears_the_threshold(qdrant):
    hits = rag.retrieve(
        "weather in Zaragoza?", min_score=0.99, client=qdrant, embedder=embed_as([0.0, 0.0, 1.0])
    )

    assert hits == []


def test_retrieve_returns_plain_payload_dicts_best_first(qdrant):
    hits = rag.retrieve("returns", k=2, min_score=0.0, client=qdrant, embedder=embed_as(RETURNS))

    assert all(type(hit) is dict for hit in hits)
    assert hits[0] == {**RETURN_WINDOW, "score": pytest.approx(1.0)}
    assert hits[1]["score"] == pytest.approx(2**-0.5)
    assert len(hits) == 2


def test_retrieve_embeds_the_question_with_the_given_embedder(qdrant):
    embedder = embed_as(CARRIERS)

    hits = rag.retrieve(
        "Which carrier best covers rural Aragón?", min_score=0.5, client=qdrant, embedder=embedder
    )

    assert embedder.calls == ["Which carrier best covers rural Aragón?"]
    assert [hit["source_document"] for hit in hits] == ["carrier-coverage"]


class StubQdrant:
    """Ignores the threshold it is given, as a misbehaving client or server might."""

    def __init__(self, scored: list[tuple[float, dict]]):
        self.scored = scored
        self.calls: list[dict[str, Any]] = []

    def query_points(self, collection_name: str, **kwargs: Any) -> Any:
        self.calls.append({"collection_name": collection_name, **kwargs})
        points = [SimpleNamespace(score=score, payload=payload) for score, payload in self.scored]
        return SimpleNamespace(points=points)


def test_retrieve_filters_below_min_score_even_if_qdrant_does_not():
    stub = StubQdrant([(0.82, RURAL_ARAGON), (0.12, STORAGE_RATE)])

    hits = rag.retrieve(
        "rural Aragón", k=3, min_score=0.4, client=stub, embedder=embed_as(CARRIERS)
    )

    assert [hit["source_document"] for hit in hits] == ["carrier-coverage"]
    assert stub.calls == [
        {
            "collection_name": "trackflow_knowledge",
            "query": CARRIERS,
            "limit": 3,
            "score_threshold": 0.4,
            "with_payload": True,
        }
    ]


def test_qdrant_failure_becomes_a_knowledge_base_error():
    class Down:
        def query_points(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionRefusedError("http://qdrant:6333 refused")

    with pytest.raises(KnowledgeBaseError, match="vector database"):
        rag.retrieve("question", client=Down(), embedder=embed_as(RETURNS))


def test_missing_collection_becomes_a_knowledge_base_error():
    with pytest.raises(KnowledgeBaseError):
        rag.retrieve("question", client=QdrantClient(":memory:"), embedder=embed_as(RETURNS))


def test_k_must_be_positive(qdrant):
    with pytest.raises(ValueError):
        rag.retrieve("question", k=0, client=qdrant, embedder=embed_as(RETURNS))


# --- Prompt assembly -------------------------------------------------------------------


def test_prompt_carries_the_passages_and_their_sources_but_no_scores():
    context = [{**RETURN_WINDOW, "score": 0.873}]

    system, user = rag.build_messages("  What's the standard return window? ", context)

    assert system == {"role": "system", "content": rag.SYSTEM_PROMPT}
    assert "returns-policy — Returns Policy › Standard return window" in user["content"]
    assert RETURN_WINDOW["text"] in user["content"]
    assert user["content"].endswith("QUESTION\nWhat's the standard return window?")
    assert "0.873" not in user["content"]


def test_prompt_says_so_when_nothing_was_retrieved():
    _, user = rag.build_messages("Do you store frozen food?", [])

    assert rag.NO_CONTEXT in user["content"]


def test_system_prompt_states_the_briefing_business_rules():
    prompt = rag.SYSTEM_PROMPT

    assert "Black Friday" in prompt and "never promise" in prompt.lower()
    assert "never describe them as automatic" in prompt and "Sofía Ramos" in prompt
    assert "Miguel Torres's approval" in prompt
    assert "Carlos Vega" in prompt
    assert "not have enough information" in prompt


def test_system_prompt_holds_no_figures_of_its_own():
    # Every percentage, rate and time frame in an answer must come from the
    # retrieved chunks (BRIEFING section 4, faithfulness).
    assert not re.search(r"\d", rag.SYSTEM_PROMPT)


# --- generate_answer() / query() -------------------------------------------------------


@pytest.fixture
def llm(monkeypatch) -> FakeOpenAI:
    fake = FakeOpenAI(answer="The standard return window is 30 days from delivery.")
    monkeypatch.setattr(rag, "_generation_client", lambda: fake)
    return fake


def mock_retrieve(monkeypatch, hits: list[dict]) -> list[str]:
    questions: list[str] = []

    def fake_retrieve(question: str, **kwargs: Any) -> list[dict]:
        questions.append(question)
        return hits

    monkeypatch.setattr(rag, "retrieve", fake_retrieve)
    return questions


def test_query_returns_the_model_output(monkeypatch, llm):
    questions = mock_retrieve(monkeypatch, [{**RETURN_WINDOW, "score": 0.9}])

    answer = rag.query("What's the standard return window?")

    assert answer == "The standard return window is 30 days from delivery."
    assert questions == ["What's the standard return window?"]
    (call,) = llm.chat_calls
    assert call["model"] == TEST_GENERATION_MODEL
    assert call["temperature"] == 0.0
    assert RETURN_WINDOW["text"] in call["messages"][1]["content"]


def test_query_never_returns_raw_chunk_text(monkeypatch, llm):
    mock_retrieve(monkeypatch, [{**RURAL_ARAGON, "score": 0.9}, {**STORAGE_RATE, "score": 0.6}])
    llm._answer = "SEUR offers the best coverage in rural Aragón."

    answer = rag.query("Which carrier best covers rural Aragón?")

    assert answer == "SEUR offers the best coverage in rural Aragón."
    assert RURAL_ARAGON["text"] not in answer
    assert STORAGE_RATE["text"] not in answer
    assert "score" not in answer


def test_query_still_asks_the_model_when_nothing_was_retrieved(monkeypatch, llm):
    mock_retrieve(monkeypatch, [])
    llm._answer = "The knowledge base does not have enough information to answer that."

    answer = rag.query("Do you store frozen food?")

    assert answer == "The knowledge base does not have enough information to answer that."
    (call,) = llm.chat_calls
    assert rag.NO_CONTEXT in call["messages"][1]["content"]


def test_query_is_retrieve_then_generate(monkeypatch):
    hits = [{**RETURN_WINDOW, "score": 0.9}]
    mock_retrieve(monkeypatch, hits)
    seen: list[tuple[str, list[dict]]] = []

    def fake_generate(question: str, context: list[dict]) -> str:
        seen.append((question, context))
        return "generated"

    monkeypatch.setattr(rag, "generate_answer", fake_generate)

    assert rag.query("return window?") == "generated"
    assert seen == [("return window?", hits)]


def test_generation_failure_is_an_error_not_a_fallback_to_chunks(monkeypatch):
    mock_retrieve(monkeypatch, [{**RETURN_WINDOW, "score": 0.9}])
    failing = FakeOpenAI(error=APIConnectionError(request=None))  # type: ignore[arg-type]
    monkeypatch.setattr(rag, "_generation_client", lambda: failing)

    with pytest.raises(KnowledgeBaseError, match="generation model"):
        rag.query("What's the standard return window?")


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_an_empty_model_answer_is_an_error(monkeypatch, llm, empty):
    mock_retrieve(monkeypatch, [{**RETURN_WINDOW, "score": 0.9}])
    llm._answer = empty

    with pytest.raises(KnowledgeBaseError, match="empty answer"):
        rag.query("What's the standard return window?")


def test_generation_uses_the_generation_model_not_the_embeddings_model(llm):
    rag.generate_answer("question", [])

    assert [call["model"] for call in llm.chat_calls] == [TEST_GENERATION_MODEL]
    assert llm.embedding_calls == []
