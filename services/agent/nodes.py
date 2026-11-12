"""The agent's nodes: one step each, returning only the state fields they own.

Retrieval and generation are the RAG project's own functions, called as they
are: `retrieve` calls `rag.retrieve()`, and `generate_answer` calls
`rag.generate_answer(question, context)` with the context `retrieve` already
produced. The monolithic `rag.query()` is never called here: it would run
retrieval again inside one opaque step.

The functions are reached through the `rag` module at call time, so tests can
replace them with `monkeypatch.setattr(rag, ...)` as the RAG tests do.
"""

from typing import Any

from data.pipelines import rag
from services.agent.errors import AgentContractError
from services.agent.state import (
    EMPTY_QUESTION,
    GENERATE_ANSWER,
    NO_INFORMATION_ANSWER,
    AgentState,
)


def receive_question(state: AgentState) -> dict[str, Any]:
    """Take the caller's question as the graph's input, stripped; refuse an empty one."""
    raw = state.get("question")
    question = raw.strip() if isinstance(raw, str) else ""
    if not question:
        return {"question": question, "error": EMPTY_QUESTION}
    return {"question": question}


def retrieve(state: AgentState) -> dict[str, Any]:
    """Search the knowledge base: the chunks at or above `rag.MIN_SCORE`, possibly none."""
    return {"context": rag.retrieve(state["question"])}


def generate_answer(state: AgentState) -> dict[str, Any]:
    """Answer from the context the `retrieve` node produced, and only from it."""
    context = state.get("context") or []
    if not context:
        # The router sends an empty context to `answer_no_information`; reaching
        # this node without context means the graph was wired wrongly.
        raise AgentContractError(f"'{GENERATE_ANSWER}' needs the context 'retrieve' produced")
    return {"answer": rag.generate_answer(state["question"], context)}


def answer_no_information(state: AgentState) -> dict[str, Any]:
    """Say honestly that the knowledge base has nothing on it, without calling a model."""
    return {"answer": NO_INFORMATION_ANSWER}
