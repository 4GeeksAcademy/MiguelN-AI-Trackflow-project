"""Request and response contracts for `/agent`."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

from services.agent.tracing import RunTrace, TraceEvent
from services.knowledge.models import MAX_QUESTION_LENGTH

# Only the transport limit: whether a question is usable (not empty) is the
# graph's decision, made in its `receive_question` node.
AgentQuestionText = Annotated[str, StringConstraints(max_length=MAX_QUESTION_LENGTH)]


class AgentQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: AgentQuestionText


class AgentAnswer(BaseModel):
    """The answer, and the id under which the run's trace can be read."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    answer: str


class AgentTraceEvent(BaseModel):
    seq: int
    attempt: int
    kind: str
    name: str
    status: str
    started_at: str
    finished_at: str | None
    duration_ms: float | None
    input: Any
    output: Any
    error_type: str | None
    error_message: str | None

    @classmethod
    def from_event(cls, event: TraceEvent) -> "AgentTraceEvent":
        return cls(
            seq=event.seq,
            attempt=event.attempt,
            kind=event.kind,
            name=event.name,
            status=event.status,
            started_at=event.started_at,
            finished_at=event.finished_at,
            duration_ms=event.duration_ms,
            input=event.input,
            output=event.output,
            error_type=event.error_type,
            error_message=event.error_message,
        )


class AgentRunTrace(BaseModel):
    """One run: how it ended and every node and routing decision, in order."""

    run_id: str
    graph_version: str
    question: str
    status: str
    outcome: str | None
    answer: str | None
    attempts: int
    started_at: str
    finished_at: str | None
    error_node: str | None
    error_type: str | None
    events: list[AgentTraceEvent]

    @classmethod
    def from_trace(cls, trace: RunTrace) -> "AgentRunTrace":
        return cls(
            run_id=trace.run_id,
            graph_version=trace.graph_version,
            question=trace.question,
            status=trace.status,
            outcome=trace.outcome,
            answer=trace.answer,
            attempts=trace.attempts,
            started_at=trace.started_at,
            finished_at=trace.finished_at,
            error_node=trace.error_node,
            error_type=trace.error_type,
            events=[AgentTraceEvent.from_event(event) for event in trace.events],
        )
