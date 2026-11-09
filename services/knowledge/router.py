"""`POST /knowledge/query`: a question in natural language, a generated answer out.

HTTP only: validate the body, call `query()` from `data/pipelines/rag.py`, map
failures to a status code. Retrieval and generation are not repeated here. The
response is `{"answer": "..."}`, the model's text and nothing else. Retrieved
chunks and similarity scores are logged server-side by `data.rag`, never
returned.

Requires a bearer token, like `/reporting`: the assistant is for TrackFlow's
commercial team, and every question costs two model calls.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from data.pipelines import rag
from data.process.knowledge_base import KnowledgeBaseError
from services.auth.dependencies import CurrentUser
from services.knowledge.models import KnowledgeAnswer, KnowledgeQuestion

logger = logging.getLogger("api.knowledge")

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

UNAVAILABLE = "The knowledge assistant is unavailable right now. Please try again in a moment."


@router.post("/query", response_model=KnowledgeAnswer)
def query_knowledge_base(body: KnowledgeQuestion, user: CurrentUser) -> KnowledgeAnswer:
    # A sync route: FastAPI runs it in its thread pool, so the blocking Qdrant
    # and model calls never stall the event loop.
    try:
        answer = rag.query(body.question)
    except KnowledgeBaseError as error:
        # The message names the failing piece (Qdrant, a model, a setting) and
        # never a URL or key; the client gets a generic sentence.
        logger.error("Knowledge query failed for user %s: %s", user.id, error)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from error
    return KnowledgeAnswer(answer=answer)
