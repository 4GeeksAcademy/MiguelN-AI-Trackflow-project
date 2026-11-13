"""TrackFlow knowledge base (Milestone 7, RAG): corpus, chunking, embeddings, indexing.

What gets indexed is fixed by the briefing (`contexts/RAG-briefing.md`, sections
2-3): four English documents, copied verbatim from the course's
`00-general-contexts/trackflow/` into `docs/company-knowledge-base/` (identical
copies also live in `contexts/`). Each file maps to one `source_document` value
of the Qdrant payload, and the points go to the `trackflow_knowledge` collection.

One function per responsibility, so any piece can be swapped on its own:

- `load_source_documents` reads the corpus;
- `split_into_chunks` cuts one document into self-contained chunks;
- `embed` turns one text into a vector with the embeddings model (never the
  generation model), the same way for chunks and for questions;
- `setup` runs the three and upserts the result into Qdrant, idempotently.

Retrieval and generation live in `data/pipelines/rag.py`. Design notes:
`docs/rag/rag-design.md`.

Run `python -m data.process.knowledge_base` to index, `--dry-run` to print the
chunks without calling any model or database.
"""

import argparse
import logging
import re
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from openai import OpenAI, OpenAIError
from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from qdrant_client import QdrantClient, models

logger = logging.getLogger("data.knowledge_base")

ROOT_DIR: Final = Path(__file__).resolve().parents[2]
KNOWLEDGE_BASE_DIR: Final = ROOT_DIR / "docs" / "company-knowledge-base"

COLLECTION_NAME: Final = "trackflow_knowledge"  # BRIEFING section 2, exact name
COMPANY: Final = "trackflow"
LANGUAGE: Final = "en"
DISTANCE: Final = models.Distance.COSINE

# File name -> payload `source_document` (BRIEFING section 3), in briefing order.
SOURCE_DOCUMENTS: Final[dict[str, str]] = {
    "trackflow-sla-delivery.en.md": "sla-delivery",
    "trackflow-returns-policy.en.md": "returns-policy",
    "trackflow-carrier-coverage.en.md": "carrier-coverage",
    "trackflow-storage-pricing.en.md": "storage-pricing",
}

# Fixed namespace for point IDs: the same chunk position always gets the same ID.
POINT_ID_NAMESPACE: Final = uuid.uuid5(uuid.NAMESPACE_URL, "https://trackflow.com/knowledge-base")

# Per model request, before the SDK's 2 retries. The default suits the API (a
# person is waiting); `LLM_TIMEOUT_SECONDS` raises it for the batch CLIs when
# the gateway is slow.
MODEL_TIMEOUT_SECONDS: Final = 30.0
QDRANT_TIMEOUT_SECONDS: Final = 10


# --- Errors and settings -----------------------------------------------------------


class KnowledgeBaseError(RuntimeError):
    """The knowledge base cannot answer right now: configuration, Qdrant or a model failed.

    Messages name the failing piece, never a URL, key or response body, so a
    caller may log them. The API still answers its own generic sentence.
    """


class RagConfigurationError(KnowledgeBaseError):
    """A required RAG setting is missing or contradictory."""


_OPTIONAL_TEXT_SETTINGS: Final = (
    "qdrant_api_key",
    "llm_base_url",
    "llm_api_key",
    "embedding_model",
    "embedding_base_url",
    "embedding_api_key",
    "generation_model",
)


class RagSettings(BaseSettings):
    """RAG configuration from the environment or the root `.env` (see `.env.example`).

    The 4Geeks models are served through an OpenAI-compatible API. One endpoint
    and key (`LLM_BASE_URL`, `LLM_API_KEY`) serve both models unless the
    embeddings model needs its own (`EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`).
    The two model IDs are separate settings and must differ.
    """

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr | None = None

    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None

    embedding_model: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None

    generation_model: str | None = None

    llm_timeout_seconds: float = Field(default=MODEL_TIMEOUT_SECONDS, gt=0, le=600)

    @field_validator(*_OPTIONAL_TEXT_SETTINGS, mode="before")
    @classmethod
    def blank_is_unset(cls, value: Any) -> Any:
        # Compose passes `${VAR:-}` through as an empty string.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("llm_timeout_seconds", mode="before")
    @classmethod
    def blank_timeout_is_default(cls, value: Any) -> Any:
        return MODEL_TIMEOUT_SECONDS if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def models_are_distinct(self) -> "RagSettings":
        if (
            self.embedding_model
            and self.generation_model
            and self.embedding_model.strip() == self.generation_model.strip()
        ):
            raise ValueError(
                "EMBEDDING_MODEL and GENERATION_MODEL must name different models: "
                "the generation model is never used for embeddings."
            )
        return self

    def embedding_endpoint(self) -> tuple[str, str | None, str]:
        """(model, base URL, API key) for embeddings."""
        if not self.embedding_model:
            raise RagConfigurationError("EMBEDDING_MODEL is not set")
        key = self.embedding_api_key or self.llm_api_key
        if key is None:
            raise RagConfigurationError("EMBEDDING_API_KEY or LLM_API_KEY is not set")
        return (
            self.embedding_model,
            self.embedding_base_url or self.llm_base_url,
            key.get_secret_value(),
        )

    def generation_endpoint(self) -> tuple[str, str | None, str]:
        """(model, base URL, API key) for answer generation."""
        if not self.generation_model:
            raise RagConfigurationError("GENERATION_MODEL is not set")
        if self.llm_api_key is None:
            raise RagConfigurationError("LLM_API_KEY is not set")
        return self.generation_model, self.llm_base_url, self.llm_api_key.get_secret_value()


@lru_cache(maxsize=1)
def get_rag_settings() -> RagSettings:
    try:
        return RagSettings()
    except ValidationError as error:
        raise RagConfigurationError(f"Invalid RAG settings: {error}") from error


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    settings = get_rag_settings()
    api_key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
    return QdrantClient(url=settings.qdrant_url, api_key=api_key, timeout=QDRANT_TIMEOUT_SECONDS)


@lru_cache(maxsize=1)
def _embedding_client() -> OpenAI:
    settings = get_rag_settings()
    _, base_url, api_key = settings.embedding_endpoint()
    return OpenAI(
        api_key=api_key, base_url=base_url, timeout=settings.llm_timeout_seconds, max_retries=2
    )


# --- Corpus ------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceDocument:
    """One briefing document as read from disk, before chunking."""

    source_document: str
    path: Path
    text: str


def load_source_documents(directory: Path = KNOWLEDGE_BASE_DIR) -> list[SourceDocument]:
    """Read the four briefing documents from `directory`, in briefing order.

    The files are listed explicitly instead of globbing the folder, so nothing is
    indexed or skipped silently. Raises FileNotFoundError when a document is
    missing, and ValueError when one is empty or when the folder holds a
    Markdown file the briefing does not list.
    """
    unexpected = sorted(
        path.name for path in directory.glob("*.md") if path.name not in SOURCE_DOCUMENTS
    )
    if unexpected:
        raise ValueError(
            f"{directory} holds Markdown files with no source_document value: "
            f"{', '.join(unexpected)}. Add them to SOURCE_DOCUMENTS or move them out."
        )

    documents = []
    for file_name, source_document in SOURCE_DOCUMENTS.items():
        path = directory / file_name
        if not path.is_file():
            raise FileNotFoundError(f"Knowledge-base document missing: {path}")
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"Knowledge-base document is empty: {path}")
        documents.append(SourceDocument(source_document=source_document, path=path, text=text))
    return documents


# --- Chunking ----------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    """One self-contained unit of a document, ready to embed and index."""

    source_document: str
    section: str
    chunk_index: int
    text: str

    def payload(self) -> dict[str, Any]:
        """The Qdrant payload: BRIEFING section 3's fields, plus `text` for the prompt."""
        return {
            "company": COMPANY,
            "source_document": self.source_document,
            "section": self.section,
            "language": LANGUAGE,
            "chunk_index": self.chunk_index,
            "text": self.text,
        }


_TITLE = re.compile(r"^#\s+(?P<title>.+?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
# "Return costs: the return shipping cost..." - a short lead-in naming the paragraph.
_INLINE_LABEL = re.compile(r"^(?P<label>[^:\n]{2,60}):\s+\S")
_MAX_LABEL_WORDS: Final = 6
SECTION_SEPARATOR: Final = " › "


def _blocks(lines: list[str]) -> list[list[str]]:
    """Group lines into blocks separated by blank lines (paragraphs and lists)."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _unwrap(block: list[str]) -> str:
    """Join hard-wrapped lines; every list item keeps a line of its own."""
    lines: list[str] = []
    for raw in block:
        line = " ".join(raw.split())
        if not lines or _LIST_ITEM.match(line):
            lines.append(line)
        else:
            lines[-1] = f"{lines[-1]} {line}"
    return "\n".join(lines)


def _is_intro(text: str) -> bool:
    """A lone sentence that announces what follows ("...works as follows:")."""
    return "\n" not in text and not _LIST_ITEM.match(text) and text.endswith(":")


def _inline_label(text: str) -> str | None:
    match = _INLINE_LABEL.match(text)
    if match is None or _LIST_ITEM.match(text):
        return None
    label = match.group("label").strip()
    return label if len(label.split()) <= _MAX_LABEL_WORDS else None


def split_into_chunks(document: SourceDocument) -> list[Chunk]:
    """Cut a document into self-contained chunks, never inside a sentence or a rule.

    The documents are short and carry a single `#` title, so headings cannot
    delimit sections. The semantic unit is the block: a paragraph or a list,
    as separated by blank lines in the source. An intro sentence ending in ":"
    is merged into the block it introduces, so a list never loses its lead-in.
    Each chunk's text starts with the document title, so it reads on its own in
    a prompt and embeds with its topic. `section` is the title, plus the
    paragraph's inline label when it opens with one ("Return costs: ...").
    """
    lines = document.text.splitlines()
    title_index = next((i for i, line in enumerate(lines) if line.strip()), None)
    if title_index is None or (title_match := _TITLE.match(lines[title_index].strip())) is None:
        raise ValueError(f"{document.path} must start with a '# Title' line")
    title = title_match.group("title")

    units: list[tuple[str, str | None]] = []  # (body, inline label)
    intro: str | None = None
    for block in _blocks(lines[title_index + 1 :]):
        text = _unwrap(block)
        if _is_intro(text) and intro is None:
            intro = text
            continue
        label = _inline_label(text)
        units.append((f"{intro}\n{text}" if intro else text, label))
        intro = None
    if intro is not None:  # an intro with nothing after it still carries content
        units.append((intro, None))

    return [
        Chunk(
            source_document=document.source_document,
            section=f"{title}{SECTION_SEPARATOR}{label}" if label else title,
            chunk_index=index,
            text=f"{title}\n{body}",
        )
        for index, (body, label) in enumerate(units)
    ]


def point_id(chunk: Chunk) -> str:
    """Deterministic point ID: re-indexing overwrites a chunk instead of duplicating it."""
    return str(uuid.uuid5(POINT_ID_NAMESPACE, f"{chunk.source_document}/{chunk.chunk_index}"))


# --- Embeddings --------------------------------------------------------------------


def normalise_for_embedding(text: str) -> str:
    """The one preprocessing step, identical for chunks and questions.

    NFC so "Aragón" typed on any keyboard matches the document's bytes, and
    whitespace collapsed so hard wraps and list layout do not shift the vector.
    Case and punctuation are kept: the embeddings model reads natural text.
    """
    return " ".join(unicodedata.normalize("NFC", text).split())


def embed(text: str) -> list[float]:
    """One vector for one text, from the dedicated embeddings model.

    Used for chunks at index time and for the question at query time, so both
    sides are always embedded by the same model with the same preprocessing.
    """
    cleaned = normalise_for_embedding(text)
    if not cleaned:
        raise ValueError("embed() needs non-empty text")
    model, _, _ = get_rag_settings().embedding_endpoint()
    try:
        response = _embedding_client().embeddings.create(model=model, input=cleaned)
    except OpenAIError as error:
        logger.error("Embedding request failed: %s", type(error).__name__)
        raise KnowledgeBaseError("The embeddings model could not be reached") from error
    if not response.data or not response.data[0].embedding:
        raise KnowledgeBaseError("The embeddings model returned no vector")
    return [float(value) for value in response.data[0].embedding]


# --- Indexing ----------------------------------------------------------------------


def _ensure_collection(client: QdrantClient, vector_size: int) -> None:
    """Create the collection, or recreate it when the vector size or metric changed.

    A different size means a different embeddings model, whose vectors cannot
    share a collection with the old ones.
    """
    if client.collection_exists(COLLECTION_NAME):
        vectors = client.get_collection(COLLECTION_NAME).config.params.vectors
        if isinstance(vectors, models.VectorParams) and (
            vectors.size == vector_size and vectors.distance == DISTANCE
        ):
            return
        logger.warning(
            "Recreating %s: the vector configuration changed (now size %d, %s)",
            COLLECTION_NAME,
            vector_size,
            DISTANCE.value,
        )
        client.delete_collection(COLLECTION_NAME)
    client.create_collection(
        COLLECTION_NAME,
        vectors_config=models.VectorParams(size=vector_size, distance=DISTANCE),
    )


def setup(
    directory: Path = KNOWLEDGE_BASE_DIR,
    *,
    client: QdrantClient | None = None,
    embedder: Callable[[str], list[float]] = embed,
) -> list[Chunk]:
    """Read, chunk, embed and upsert the corpus into `trackflow_knowledge`.

    Idempotent: point IDs derive from (source_document, chunk_index), so a
    re-run overwrites the same points, and points this run did not write (a
    document that now yields fewer chunks) are deleted afterwards. Every chunk
    is embedded before Qdrant is touched, so a model failure leaves the
    collection as it was. Returns the indexed chunks.
    """
    chunks = [
        chunk
        for document in load_source_documents(directory)
        for chunk in split_into_chunks(document)
    ]
    vectors = [embedder(chunk.text) for chunk in chunks]
    sizes = {len(vector) for vector in vectors}
    if len(sizes) != 1:
        raise KnowledgeBaseError(f"The embeddings model returned vectors of sizes {sorted(sizes)}")

    client = client if client is not None else get_qdrant_client()
    _ensure_collection(client, sizes.pop())
    points = [
        models.PointStruct(id=point_id(chunk), vector=vector, payload=chunk.payload())
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    client.upsert(COLLECTION_NAME, points=points, wait=True)
    client.delete(
        COLLECTION_NAME,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must_not=[models.HasIdCondition(has_id=[point.id for point in points])]
            )
        ),
        wait=True,
    )
    logger.info("Indexed %d chunks into %s", len(points), COLLECTION_NAME)
    return chunks


# --- CLI ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index the TrackFlow knowledge base in Qdrant.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the chunks and exit, without calling the embeddings model or Qdrant",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.dry_run:
        for document in load_source_documents():
            for chunk in split_into_chunks(document):
                words = len(chunk.text.split())
                print(
                    f"[{chunk.source_document} #{chunk.chunk_index}] {chunk.section} ({words} words)"
                )
                print(f"  {chunk.text.replace(chr(10), chr(10) + '  ')}\n")
        return 0

    try:
        chunks = setup()
    except KnowledgeBaseError as error:
        print(f"Indexing failed: {error}")
        return 2
    per_document: dict[str, int] = {}
    for chunk in chunks:
        per_document[chunk.source_document] = per_document.get(chunk.source_document, 0) + 1
    print(f"Indexed {len(chunks)} chunks into '{COLLECTION_NAME}': {per_document}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
