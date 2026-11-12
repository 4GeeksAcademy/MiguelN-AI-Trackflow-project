"""`/agent`: the knowledge agent graph over HTTP. No business logic lives here.

- `POST /agent/query` hands the question to `runner.run_agent()` and translates
  how the run ended into a status code. Whether the question is usable, what
  to retrieve, whether to generate: the graph decides all of it.
- `GET /agent/runs/{run_id}` returns that run's trace (nodes in order, what each
  produced, the routing decisions and their reasons) to the user who ran it.

Coexists with `POST /knowledge/query`, which stays as it was. Both require a
bearer token: every question costs model calls, and questions may carry
client details.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from services.agent import runner
from services.agent.errors import AgentRunError
from services.agent.models import AgentAnswer, AgentQuestion, AgentRunTrace
from services.agent.tracing import get_trace_store
from services.auth.dependencies import CurrentUser

logger = logging.getLogger("api.agent")

router = APIRouter(prefix="/agent", tags=["agent"])

RUN_NOT_FOUND = "Agent run not found"


@router.post("/query", response_model=AgentAnswer)
def query_agent(body: AgentQuestion, user: CurrentUser) -> AgentAnswer:
    # A sync route: FastAPI runs it in its thread pool, so the blocking Qdrant
    # and model calls inside the graph never stall the event loop.
    try:
        result = runner.run_agent(body.question, user_id=user.id)
    except AgentRunError as error:
        # The trace keeps the exception class; the caller gets the failed step
        # and the run id to quote, never the exception text or a traceback.
        code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if error.retryable
            else status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        raise HTTPException(
            code, {"message": error.public_message, "run_id": error.run_id}
        ) from error
    if result.outcome == "rejected" or result.answer is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "message": result.error or "The question could not be processed.",
                "run_id": result.run_id,
            },
        )
    return AgentAnswer(run_id=result.run_id, answer=result.answer)


@router.get("/runs/{run_id}", response_model=AgentRunTrace)
def get_agent_run(run_id: UUID, user: CurrentUser) -> AgentRunTrace:
    # Another user's run is reported as missing, not forbidden: whether a run
    # id exists is not something to reveal.
    trace = get_trace_store().get_trace(str(run_id), owner=user.id)
    if trace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, RUN_NOT_FOUND)
    return AgentRunTrace.from_trace(trace)
