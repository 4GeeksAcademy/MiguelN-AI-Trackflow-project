"""TrackFlow knowledge assistant (Milestone 7, RAG): retrieval and answer generation.

`query()` is the only function external consumers call. It is literally
`generate_answer(question, retrieve(question))`, and the two halves stay
separate so a later agent can run retrieval and generation as its own steps:

- `retrieve` embeds the question with the same `embed()` used at index time,
  searches `trackflow_knowledge` and keeps only hits at or above `min_score`;
- `build_messages` assembles the prompt from the surviving chunks;
- `generate_answer` sends it to the generation model (a chat model, never the
  embeddings model) and returns its text.

The caller only ever gets the model's answer: raw Qdrant points, chunk lists and
similarity scores stay on the server, in the `data.rag` log. The corpus and the
indexing side are in `data/process/knowledge_base.py`; the design and the
threshold choice are in `docs/rag/rag-design.md`.
"""

import logging
from collections.abc import Callable
from functools import lru_cache
from typing import Any, Final

from openai import OpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageParam
from qdrant_client import QdrantClient

from data.process.knowledge_base import (
    COLLECTION_NAME,
    KnowledgeBaseError,
    embed,
    get_qdrant_client,
    get_rag_settings,
)

logger = logging.getLogger("data.rag")

DEFAULT_TOP_K: Final = 5

# Cosine similarity a chunk needs before it may reach the prompt. Below it, the
# chunk is treated as unrelated, and with nothing left the model is told so and
# answers that the knowledge base has no information. The value depends on the
# embeddings model; it is set from the score distribution of the questions in
# `data/eval/test-queries.json` (scripts/evaluate_rag_retrieval.py), and the
# reasoning is recorded in docs/rag/rag-design.md. Measured on 2026-11-08 with
# pplx-embed-v1-0.6b: correct chunks score >= 0.3285 (a paraphrased question),
# while business-sounding out-of-scope questions reach 0.4489, so no value
# separates them. Recall first: 0.30 keeps every correct chunk and still drops
# unrelated questions (<= 0.20); the prompt handles the overlap.
MIN_SCORE: Final = 0.30

# Answers are lookups of contractual facts: no creativity wanted.
GENERATION_TEMPERATURE: Final = 0.0

NO_CONTEXT: Final = "(No passage in the knowledge base is relevant enough to this question.)"

SYSTEM_PROMPT: Final = """\
You are TrackFlow's commercial knowledge assistant. Account managers and \
business-development staff on Miguel Torres's commercial team use you while they \
talk to prospect and current client brands. Answer the way an experienced \
TrackFlow salesperson would answer that client on a call: courteous, confident \
and precise, with exact data, and never promising terms that do not exist in \
TrackFlow's standard agreements.

How to answer:
- Every fact about TrackFlow's services, prices, times, carriers and policies \
must come from the CONTEXT passages. Do not add outside knowledge or assumptions.
- Copy every number, percentage, price, currency, weight, time frame and name \
exactly as the CONTEXT states it. Never round, convert currencies, estimate, or \
combine figures into new ones.
- If the CONTEXT does not answer the question, or there is no CONTEXT, say \
clearly that the knowledge base does not have enough information to answer it \
and that it must be confirmed with the responsible TrackFlow team. Never fill \
the gap with a guess.
- Lead with the direct answer, then the conditions and exceptions that apply. \
Keep it brief: a few sentences or a short list.
- Answer in the language of the question. Translate the wording, never the figures.
- Finish with a line "Sources:" naming each passage you used as \
"source document — section". If you used no passage, leave that line out.

Business rules that always apply, even if the client insists:
- Delivery SLA: never promise or guarantee an SLA for declared high-demand dates \
(Black Friday, Christmas, January Sales in Spain). Whenever the answer gives \
delivery times or the SLA, add that the SLA is not guaranteed on those dates, \
with the conditions the CONTEXT gives for them.
- International returns (bought in one country, returned in another): never \
describe them as automatic. Always say they are handled manually by Sofía \
Ramos's Returns team.
- Storage discounts and preferential rates: never offer or promise one. Always \
say that any storage discount requires Miguel Torres's approval.
- Carriers: the system assigns the carrier for each shipment. Any carrier \
exception or condition the CONTEXT does not document requires approval from \
Carlos Vega (Head of Carrier Operations); never invent one.
"""


@lru_cache(maxsize=1)
def _generation_client() -> OpenAI:
    settings = get_rag_settings()
    _, base_url, api_key = settings.generation_endpoint()
    return OpenAI(
        api_key=api_key, base_url=base_url, timeout=settings.llm_timeout_seconds, max_retries=2
    )


def retrieve(
    query: str,
    *,
    k: int = DEFAULT_TOP_K,
    min_score: float = MIN_SCORE,
    client: QdrantClient | None = None,
    embedder: Callable[[str], list[float]] = embed,
) -> list[dict[str, Any]]:
    """The payloads of the top-`k` chunks scoring at least `min_score`, best first.

    Each result is a plain dict: the indexed payload (`company`,
    `source_document`, `section`, `language`, `chunk_index`, `text`) plus its
    `score`, never a Qdrant SDK object. It may hold fewer than `k` results, or
    none. The threshold is applied by Qdrant and again here, so a hit below it
    can never pass whatever the client does.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    vector = embedder(query)
    qdrant = client if client is not None else get_qdrant_client()
    try:
        response = qdrant.query_points(
            COLLECTION_NAME,
            query=vector,
            limit=k,
            score_threshold=min_score,
            with_payload=True,
        )
    except Exception as error:  # the SDK raises transport, HTTP and gRPC errors alike
        logger.error("Qdrant search failed: %s", type(error).__name__)
        raise KnowledgeBaseError("The vector database could not be searched") from error

    hits = sorted(
        (
            {**(point.payload or {}), "score": float(point.score)}
            for point in response.points
            if point.score >= min_score
        ),
        key=lambda hit: hit["score"],
        reverse=True,
    )[:k]
    logger.info(
        "retrieve k=%d min_score=%.2f -> %d hit(s): %s",
        k,
        min_score,
        len(hits),
        ", ".join(
            f"{hit.get('source_document')}#{hit.get('chunk_index')}={hit['score']:.3f}"
            for hit in hits
        )
        or "none",
    )
    return hits


def _format_passage(number: int, chunk: dict[str, Any]) -> str:
    # The score is deliberately left out: the model weighs passages by content.
    source = chunk.get("source_document", "unknown")
    section = chunk.get("section", "")
    return f"[{number}] {source} — {section}\n{chunk.get('text', '').strip()}"


def build_messages(
    question: str, context: list[dict[str, Any]]
) -> list[ChatCompletionMessageParam]:
    """Prompt assembly: the system rules, then the retrieved passages and the question."""
    passages = (
        "\n\n".join(_format_passage(number, chunk) for number, chunk in enumerate(context, 1))
        if context
        else NO_CONTEXT
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"CONTEXT\n{passages}\n\nQUESTION\n{question.strip()}"},
    ]


def generate_answer(question: str, context: list[dict[str, Any]]) -> str:
    """Ask the generation model to answer `question` from `context` only.

    Called even when `context` is empty: the model then says the knowledge
    base has no relevant information, so every answer is a generated one.
    """
    model, _, _ = get_rag_settings().generation_endpoint()
    try:
        response = _generation_client().chat.completions.create(
            model=model,
            messages=build_messages(question, context),
            temperature=GENERATION_TEMPERATURE,
        )
    except OpenAIError as error:
        logger.error("Generation request failed: %s", type(error).__name__)
        raise KnowledgeBaseError("The generation model could not be reached") from error
    answer = (response.choices[0].message.content or "").strip() if response.choices else ""
    if not answer:
        raise KnowledgeBaseError("The generation model returned an empty answer")
    return answer


def query(question: str) -> str:
    """Answer a natural-language question from the knowledge base.

    The only entry point for external consumers (the API). Returns the
    generation model's answer and nothing else. Raises KnowledgeBaseError when
    Qdrant or a model is unavailable or not configured.
    """
    return generate_answer(question, retrieve(question))
