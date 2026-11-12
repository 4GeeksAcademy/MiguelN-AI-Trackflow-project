"""The state the agent's nodes share, and the agent's fixed answers.

The state holds only what a node needs to do its job or what a router needs to
choose the next step: the question, why it was refused, the retrieved
context and the answer. There is no conversation history: one question gets
one answer, and nothing in this graph reads an earlier turn. A future field
(Part 2: tool results, a history) has to come with the node that reads it.
"""

from typing import Any, Final, Literal, NotRequired, TypedDict


class AgentState(TypedDict):
    # The caller's question; `receive_question` replaces it with the stripped form.
    question: str
    # Set by `receive_question` when the question cannot be processed (it is empty).
    error: NotRequired[str]
    # `rag.retrieve()` output: chunk payloads plus `score`, best first; may be [].
    context: NotRequired[list[dict[str, Any]]]
    # The final answer: generated from `context`, or the honest no-information one.
    answer: NotRequired[str]


STATE_KEYS: Final = frozenset(AgentState.__required_keys__ | AgentState.__optional_keys__)

# Node names. They are also the `name` of the node rows in the run trace.
RECEIVE_QUESTION: Final = "receive_question"
RETRIEVE: Final = "retrieve"
GENERATE_ANSWER: Final = "generate_answer"
ANSWER_NO_INFORMATION: Final = "answer_no_information"

Outcome = Literal["answered", "no_information", "rejected"]

EMPTY_QUESTION: Final = "The question is empty. Please type a question."

# Returned without calling the generation model when no chunk passes the
# threshold. It says what the system prompt asks the model to say in that case.
NO_INFORMATION_ANSWER: Final = (
    "I don't have information about that in TrackFlow's knowledge base, so I can't "
    "answer it reliably. Please confirm it with the responsible TrackFlow team."
)


def derive_outcome(state: dict[str, Any]) -> Outcome:
    """How a finished run ended, read from its final state alone."""
    if state.get("error"):
        return "rejected"
    if state.get("context"):
        return "answered"
    return "no_information"
