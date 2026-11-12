"""The state the agent's nodes share, and the agent's fixed answers.

The state holds only what a node needs to do its job or what a router needs to
choose the next step: the question, why it was refused, which sources the
question needs, what each source returned, and the answer. There is no
conversation history: one question gets one answer, and nothing in this graph
reads an earlier turn. A new field has to come with the node that reads it.

Tool results are stored as JSON-mode dicts (`model_dump(mode="json")`), so the
checkpointer and the run trace store them as they are.
"""

from typing import Any, Final, Literal, NotRequired, TypedDict


class AgentState(TypedDict):
    # The caller's question; `receive_question` replaces it with the stripped form.
    question: str
    # Set by `receive_question` when the question cannot be processed (it is empty).
    error: NotRequired[str]
    # `plan_sources` output (`planner.Plan`): the sources the question needs, in
    # the order they run, with each tool's arguments.
    plan: NotRequired[dict[str, Any]]
    # `lookup_ticket` output (`tools.contracts.TicketLookupResult`).
    ticket_lookup: NotRequired[dict[str, Any]]
    # `lookup_stock` output (`tools.contracts.StockLookupResult`).
    stock_lookup: NotRequired[dict[str, Any]]
    # `rag.retrieve()` output: chunk payloads plus `score`, best first; may be [].
    context: NotRequired[list[dict[str, Any]]]
    # The final answer: generated from the evidence, or one of the fixed answers.
    answer: NotRequired[str]


STATE_KEYS: Final = frozenset(AgentState.__required_keys__ | AgentState.__optional_keys__)

# Node names. They are also the `name` of the node rows in the run trace.
RECEIVE_QUESTION: Final = "receive_question"
PLAN_SOURCES: Final = "plan_sources"
LOOKUP_TICKET: Final = "lookup_ticket"
LOOKUP_STOCK: Final = "lookup_stock"
RETRIEVE: Final = "retrieve"
GENERATE_ANSWER: Final = "generate_answer"
ANSWER_LOOKUP_FAILED: Final = "answer_lookup_failed"
ANSWER_NO_INFORMATION: Final = "answer_no_information"

# The sources a question can need. Planned sources always run in this order -
# live data first, then the knowledge base - so a trace reads the same way for
# the same plan.
INCIDENT_MANAGER: Final = "incident_manager"
INVENTORY_MANAGER: Final = "inventory_manager"
KNOWLEDGE_BASE: Final = "knowledge_base"
SOURCE_ORDER: Final = (INCIDENT_MANAGER, INVENTORY_MANAGER, KNOWLEDGE_BASE)

# Which node consults which source, and the state field it writes.
NODE_OF_SOURCE: Final = {
    INCIDENT_MANAGER: LOOKUP_TICKET,
    INVENTORY_MANAGER: LOOKUP_STOCK,
    KNOWLEDGE_BASE: RETRIEVE,
}
SOURCE_OF_NODE: Final = {node: source for source, node in NODE_OF_SOURCE.items()}
RESULT_FIELD_OF_SOURCE: Final = {
    INCIDENT_MANAGER: "ticket_lookup",
    INVENTORY_MANAGER: "stock_lookup",
    KNOWLEDGE_BASE: "context",
}
# The tool lookups, in source order: (source, state field).
LOOKUP_FIELDS: Final = ((INCIDENT_MANAGER, "ticket_lookup"), (INVENTORY_MANAGER, "stock_lookup"))

Outcome = Literal["answered", "no_information", "rejected"]

EMPTY_QUESTION: Final = "The question is empty. Please type a question."

# Returned without calling the generation model when no chunk passes the
# threshold. It says what the system prompt asks the model to say in that case.
NO_INFORMATION_ANSWER: Final = (
    "I don't have information about that in TrackFlow's knowledge base, so I can't "
    "answer it reliably. Please confirm it with the responsible TrackFlow team."
)

# The fallback answers for a lookup that failed. They are written here, never by
# the model, so a ticket's status or a product's stock is never made up: the
# model only ever sees lookups that succeeded.
TICKET_NOT_FOUND_ANSWER: Final = (
    "I couldn't confirm the status of ticket {reference}: the incident manager has no "
    "ticket with that reference. Please check the reference and try again."
)
TICKET_UNAVAILABLE_ANSWER: Final = (
    "I couldn't confirm the status of ticket {reference} right now: the incident manager "
    "is not responding. Please try again in a few minutes."
)
INCIDENTS_UNAVAILABLE_ANSWER: Final = (
    "I couldn't check the incident manager right now: it is not responding. Please try "
    "again in a few minutes."
)
STOCK_NOT_FOUND_ANSWER: Final = (
    'I couldn\'t find a product matching "{product}" in the inventory manager, so I '
    "can't confirm its stock."
)
STOCK_UNAVAILABLE_ANSWER: Final = (
    'I couldn\'t check the stock of "{product}" right now: the inventory manager is not '
    "responding. Please try again in a few minutes."
)


def lookup_succeeded(result: dict[str, Any] | None) -> bool:
    """True for a lookup that returned live data (a search with no match included)."""
    return result is not None and result.get("outcome") == "ok"


def derive_outcome(state: dict[str, Any]) -> Outcome:
    """How a finished run ended, read from its final state alone.

    `answered` means the answer was generated from evidence: retrieved chunks or
    a lookup that succeeded. A run that could only give a fixed answer - nothing
    in the knowledge base, or a lookup that failed - is `no_information`.
    """
    if state.get("error"):
        return "rejected"
    if state.get("context") or any(lookup_succeeded(state.get(f)) for _, f in LOOKUP_FIELDS):
        return "answered"
    return "no_information"
