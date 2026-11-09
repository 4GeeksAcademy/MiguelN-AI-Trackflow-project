"""Phase 1 of the RAG: corpus, chunking, `embed()` and an idempotent `setup()`.

Qdrant is the in-memory client and every model call is a fake (`rag_support`).
"""

import re
import uuid
from pathlib import Path

import pytest
from openai import APIConnectionError
from qdrant_client import QdrantClient, models

from data.pipelines import rag
from data.process import knowledge_base
from data.process.knowledge_base import (
    COLLECTION_NAME,
    KNOWLEDGE_BASE_DIR,
    ROOT_DIR,
    SOURCE_DOCUMENTS,
    KnowledgeBaseError,
    RagConfigurationError,
    SourceDocument,
    embed,
    load_source_documents,
    normalise_for_embedding,
    point_id,
    setup,
    split_into_chunks,
)
from tests.pipelines.rag_support import (
    TEST_EMBEDDING_MODEL,
    FakeOpenAI,
    hashed_vector,
    offline_rag,  # noqa: F401  (fixture)
    pinned_rag_settings,
)

pytestmark = pytest.mark.usefixtures("offline_rag")

# Taken at import, before offline_rag swaps them for clients that refuse to run.
_REAL_EMBEDDING_CLIENT = knowledge_base._embedding_client
_REAL_GENERATION_CLIENT = rag._generation_client

BRIEFING_SLUGS = ["sla-delivery", "returns-policy", "carrier-coverage", "storage-pricing"]
BRIEFING_PAYLOAD_KEYS = {"company", "source_document", "section", "language", "chunk_index", "text"}


def write_corpus(directory: Path) -> None:
    for file_name in SOURCE_DOCUMENTS:
        (directory / file_name).write_text(f"# {file_name}\n\nBody.\n", encoding="utf-8")


def document(text: str) -> SourceDocument:
    return SourceDocument(source_document="returns-policy", path=Path("doc.md"), text=text)


def shipped_chunks():
    return [chunk for doc in load_source_documents() for chunk in split_into_chunks(doc)]


def body(chunk_text: str) -> str:
    """The chunk text without its leading title line."""
    return chunk_text.split("\n", 1)[1]


# --- Corpus ----------------------------------------------------------------------------


def test_shipped_corpus_loads_the_four_briefing_documents_in_order():
    documents = load_source_documents()

    assert [document.source_document for document in documents] == BRIEFING_SLUGS
    assert all(document.path.parent == KNOWLEDGE_BASE_DIR for document in documents)
    assert all(document.text.lstrip().startswith("# ") for document in documents)


def test_shipped_corpus_matches_the_copies_in_contexts():
    for file_name in SOURCE_DOCUMENTS:
        indexed = (KNOWLEDGE_BASE_DIR / file_name).read_bytes()
        assert indexed == (ROOT_DIR / "contexts" / file_name).read_bytes(), file_name


def test_missing_document_is_an_error(tmp_path):
    write_corpus(tmp_path)
    (tmp_path / "trackflow-returns-policy.en.md").unlink()

    with pytest.raises(FileNotFoundError, match="returns-policy"):
        load_source_documents(tmp_path)


def test_empty_document_is_an_error(tmp_path):
    write_corpus(tmp_path)
    (tmp_path / "trackflow-storage-pricing.en.md").write_text("  \n", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        load_source_documents(tmp_path)


def test_unlisted_markdown_file_is_an_error_not_skipped(tmp_path):
    write_corpus(tmp_path)
    (tmp_path / "README.md").write_text("# Notes\n", encoding="utf-8")
    (tmp_path / ".DS_Store").write_bytes(b"\x00")  # non-Markdown files are ignored

    with pytest.raises(ValueError, match="README.md"):
        load_source_documents(tmp_path)


# --- Chunking --------------------------------------------------------------------------


def test_every_briefing_document_yields_at_least_three_chunks():
    counts = {slug: 0 for slug in BRIEFING_SLUGS}
    for chunk in shipped_chunks():
        counts[chunk.source_document] += 1

    assert all(count >= 3 for count in counts.values()), counts


def test_chunks_cover_each_document_exactly_once():
    # Nothing dropped, nothing duplicated: the chunk bodies put back together
    # are the document body, whitespace aside.
    for doc in load_source_documents():
        chunks = split_into_chunks(doc)
        rebuilt = " ".join(body(chunk.text) for chunk in chunks)
        original = doc.text.split("\n", 1)[1]
        assert normalise_for_embedding(rebuilt) == normalise_for_embedding(original)


def test_no_chunk_ends_in_the_middle_of_a_sentence():
    for chunk in shipped_chunks():
        assert body(chunk.text).rstrip().endswith((".", "!", "?", ")")), chunk


def test_chunk_indexes_count_from_zero_within_each_document():
    for doc in load_source_documents():
        assert [chunk.chunk_index for chunk in split_into_chunks(doc)] == list(
            range(len(split_into_chunks(doc)))
        )


def test_each_chunk_starts_with_its_document_title():
    for chunk in shipped_chunks():
        title = chunk.section.split(knowledge_base.SECTION_SEPARATOR)[0]
        assert chunk.text.startswith(f"{title}\n")


def test_an_intro_sentence_stays_with_the_list_it_introduces():
    chunks = split_into_chunks(
        document(
            "# Returns Policy\n\nThe process works as follows:\n\n"
            "1. The customer asks.\n2. TrackFlow generates the\n   label.\n\nAnother rule.\n"
        )
    )

    assert [chunk.text for chunk in chunks] == [
        (
            "Returns Policy\nThe process works as follows:\n1. The customer asks.\n"
            "2. TrackFlow generates the label."
        ),
        "Returns Policy\nAnother rule.",
    ]


def test_hard_wrapped_lines_are_joined_into_one_paragraph():
    (chunk,) = split_into_chunks(document("# T\n\nA rule that wraps\nonto a second line.\n"))

    assert chunk.text == "T\nA rule that wraps onto a second line."


def test_inline_label_names_the_section():
    chunks = split_into_chunks(
        document("# Returns Policy\n\nReturn costs: paid by the brand.\n\nPlain paragraph.\n")
    )

    assert [chunk.section for chunk in chunks] == [
        "Returns Policy › Return costs",
        "Returns Policy",
    ]


def test_long_lead_in_is_not_mistaken_for_a_label():
    (chunk,) = split_into_chunks(
        document("# T\n\nThis sentence has far too many words before its colon: yes.\n")
    )

    assert chunk.section == "T"


def test_document_without_a_title_is_rejected():
    with pytest.raises(ValueError, match="Title"):
        split_into_chunks(document("No heading here.\n"))


def test_payload_has_exactly_the_briefing_fields():
    for chunk in shipped_chunks():
        payload = chunk.payload()
        assert set(payload) == BRIEFING_PAYLOAD_KEYS
        assert payload["company"] == "trackflow"
        assert payload["language"] == "en"
        assert payload["source_document"] in BRIEFING_SLUGS


def test_point_ids_are_deterministic_and_unique():
    chunks = shipped_chunks()
    ids = [point_id(chunk) for chunk in chunks]

    assert ids == [point_id(chunk) for chunk in shipped_chunks()]
    assert len(set(ids)) == len(ids)
    assert all(uuid.UUID(value) for value in ids)


# --- Embeddings ------------------------------------------------------------------------


def test_normalisation_unifies_unicode_and_whitespace():
    decomposed = "Aragón"  # "o" + combining acute, as some keyboards type it

    assert normalise_for_embedding(f"  rural\n areas of\t{decomposed} ") == "rural areas of Aragón"


def test_embed_uses_the_embeddings_model_with_normalised_text(monkeypatch):
    fake = FakeOpenAI()
    monkeypatch.setattr(knowledge_base, "_embedding_client", lambda: fake)

    vector = embed("  What is the\nreturn window?  ")

    assert vector == hashed_vector("What is the return window?")
    assert fake.embedding_calls == [
        {"model": TEST_EMBEDDING_MODEL, "input": "What is the return window?"}
    ]
    assert fake.chat_calls == []


def test_embed_refuses_blank_text(monkeypatch):
    monkeypatch.setattr(knowledge_base, "_embedding_client", lambda: FakeOpenAI())

    with pytest.raises(ValueError):
        embed(" \n ")


def test_embeddings_api_failure_becomes_a_knowledge_base_error(monkeypatch):
    failure = APIConnectionError(request=None)  # type: ignore[arg-type]
    monkeypatch.setattr(knowledge_base, "_embedding_client", lambda: FakeOpenAI(error=failure))

    with pytest.raises(KnowledgeBaseError, match="embeddings model"):
        embed("question")


def test_embed_without_an_embedding_model_is_a_configuration_error(monkeypatch):
    settings = pinned_rag_settings(embedding_model=None)
    monkeypatch.setattr(knowledge_base, "get_rag_settings", lambda: settings)

    with pytest.raises(RagConfigurationError, match="EMBEDDING_MODEL"):
        embed("question")


# --- Settings --------------------------------------------------------------------------


def test_the_generation_model_can_never_be_the_embeddings_model():
    with pytest.raises(ValueError, match="different models"):
        pinned_rag_settings(embedding_model="same-model", generation_model="same-model")


def test_blank_settings_count_as_unset():
    settings = pinned_rag_settings(embedding_model="  ", llm_base_url="")

    assert settings.embedding_model is None
    assert settings.llm_base_url is None


def test_model_timeout_defaults_and_can_be_raised(monkeypatch):
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    assert pinned_rag_settings().llm_timeout_seconds == knowledge_base.MODEL_TIMEOUT_SECONDS

    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "120")
    assert pinned_rag_settings().llm_timeout_seconds == 120.0

    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", " ")
    assert pinned_rag_settings().llm_timeout_seconds == knowledge_base.MODEL_TIMEOUT_SECONDS


@pytest.mark.parametrize("value", ["0", "-5", "601", "soon"])
def test_an_unusable_model_timeout_is_refused(monkeypatch, value):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="llm_timeout_seconds"):
        pinned_rag_settings()


def test_both_model_clients_use_the_configured_timeout(monkeypatch):
    # The real factories (offline_rag replaces them); building a client makes no request.
    settings = pinned_rag_settings(llm_timeout_seconds=95)
    monkeypatch.setattr(knowledge_base, "get_rag_settings", lambda: settings)
    monkeypatch.setattr(rag, "get_rag_settings", lambda: settings)
    for factory in (_REAL_EMBEDDING_CLIENT, _REAL_GENERATION_CLIENT):
        factory.cache_clear()
        try:
            assert factory().timeout == 95
        finally:
            factory.cache_clear()


def test_embeddings_share_the_llm_endpoint_unless_overridden():
    shared = pinned_rag_settings()
    split = pinned_rag_settings(
        embedding_base_url="http://embeddings.invalid/v1", embedding_api_key="embedding-key"
    )

    assert shared.embedding_endpoint() == (
        TEST_EMBEDDING_MODEL,
        "http://llm.invalid/v1",
        "test-key",
    )
    assert split.embedding_endpoint() == (
        TEST_EMBEDDING_MODEL,
        "http://embeddings.invalid/v1",
        "embedding-key",
    )
    assert split.generation_endpoint()[1:] == ("http://llm.invalid/v1", "test-key")


# --- setup() ---------------------------------------------------------------------------


def indexed(client: QdrantClient) -> dict[str, dict]:
    points, _ = client.scroll(COLLECTION_NAME, limit=1000, with_payload=True)
    return {str(point.id): point.payload for point in points}


def test_setup_indexes_every_chunk_with_its_payload():
    client = QdrantClient(":memory:")
    embedded: list[str] = []

    def embedder(text: str) -> list[float]:
        embedded.append(text)
        return hashed_vector(text)

    chunks = setup(client=client, embedder=embedder)

    assert embedded == [chunk.text for chunk in chunks]  # vector = embed(chunk text)
    assert indexed(client) == {point_id(chunk): chunk.payload() for chunk in chunks}
    vectors = client.get_collection(COLLECTION_NAME).config.params.vectors
    assert vectors.size == 8
    assert vectors.distance == models.Distance.COSINE


def test_setup_is_idempotent():
    client = QdrantClient(":memory:")

    first = setup(client=client, embedder=hashed_vector)
    second = setup(client=client, embedder=hashed_vector)

    assert first == second
    assert client.count(COLLECTION_NAME).count == len(first)


def test_setup_removes_points_it_did_not_write():
    client = QdrantClient(":memory:")
    setup(client=client, embedder=hashed_vector)
    stale = str(uuid.uuid4())
    client.upsert(
        COLLECTION_NAME,
        points=[models.PointStruct(id=stale, vector=hashed_vector("old"), payload={"text": "old"})],
    )

    chunks = setup(client=client, embedder=hashed_vector)

    assert stale not in indexed(client)
    assert client.count(COLLECTION_NAME).count == len(chunks)


def test_setup_recreates_the_collection_when_the_vector_size_changes():
    client = QdrantClient(":memory:")
    setup(client=client, embedder=lambda text: hashed_vector(text, size=8))

    chunks = setup(client=client, embedder=lambda text: hashed_vector(text, size=16))

    assert client.get_collection(COLLECTION_NAME).config.params.vectors.size == 16
    assert client.count(COLLECTION_NAME).count == len(chunks)


def test_an_embedding_failure_leaves_the_collection_untouched():
    client = QdrantClient(":memory:")
    chunks = setup(client=client, embedder=hashed_vector)
    before = indexed(client)

    def failing(text: str) -> list[float]:
        raise KnowledgeBaseError("The embeddings model could not be reached")

    with pytest.raises(KnowledgeBaseError):
        setup(client=client, embedder=failing)

    assert indexed(client) == before
    assert len(before) == len(chunks)


def test_vectors_of_different_sizes_are_refused():
    sizes = iter([8, 9])

    with pytest.raises(KnowledgeBaseError, match="sizes"):
        setup(
            client=QdrantClient(":memory:"),
            embedder=lambda text: hashed_vector(text, next(sizes, 8)),
        )


def test_dry_run_prints_the_chunks_without_any_client(capsys):
    assert knowledge_base.main(["--dry-run"]) == 0

    output = capsys.readouterr().out
    assert len(re.findall(r"^\[(?:\w|-)+ #\d+\]", output, flags=re.MULTILINE)) == len(
        shipped_chunks()
    )
