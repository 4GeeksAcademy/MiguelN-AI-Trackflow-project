"""The state the agent's nodes share, and the agent's fixed answers.

The state holds only what a node needs to do its job or what a router needs to
choose the next step: the question, why it was refused, which sources the
question needs, what each source returned, and the answer. There is still no
conversation history: nothing in this graph reads an earlier turn's question
or answer. A conversation carries one thing between turns - its pending memory
proposal (MEM-092), which the runner loads from the memory store into
`pending_proposal`, so the user's next message is evaluated against it first.
A new field has to come with the node that reads it.

Tool results are stored as JSON-mode dicts (`model_dump(mode="json")`), so the
checkpointer and the run trace store them as they are.
"""

from typing import Any, Final, Literal, NotRequired, TypedDict


class AgentState(TypedDict):
    # The caller's question; `receive_question` replaces it with the stripped form,
    # and `classify_memory_reply` with what remains once the reply to a pending
    # proposal is taken out of it ("" when nothing else was asked).
    question: str
    # Set by the runner: this run's id (also its trace key and LangGraph thread),
    # the conversation it belongs to, and who is asking. The memory store records
    # them on every proposal and decision.
    run_id: NotRequired[str]
    conversation_id: NotRequired[str]
    user_id: NotRequired[str | None]
    # Set by the runner when the conversation has an open memory proposal
    # (`memory.store.Proposal.to_state()`).
    pending_proposal: NotRequired[dict[str, Any]]
    # `classify_memory_reply` output: the classifier's label, the decision it led
    # to, and the question that remains.
    memory_reply: NotRequired[dict[str, Any]]
    # `resolve_memory_proposal` output (`memory.store.Resolution.to_state()`).
    memory_resolution: NotRequired[dict[str, Any]]
    # `recall_memory` output (`memory.store.RecallResult.to_state()`): the approved
    # memories relevant to the question, at most three.
    memory_recall: NotRequired[dict[str, Any]]
    # Set by `receive_question` when the question cannot be processed (it is empty).
    error: NotRequired[str]
    # `plan_sources` output (`planner.Plan`): the sources the question needs, in
    # the order they run, with each tool's arguments.
    plan: NotRequired[dict[str, Any]]
    # `lookup_ticket_mcp` output (`tools.contracts.TicketLookupResult`).
    ticket_lookup: NotRequired[dict[str, Any]]
    # `lookup_stock_mcp` output (`tools.contracts.StockLookupResult`).
    stock_lookup: NotRequired[dict[str, Any]]
    # `rag.retrieve()` output: chunk payloads plus `score`, best first; may be [].
    context: NotRequired[list[dict[str, Any]]]
    # The final answer: generated from the evidence, or one of the fixed answers;
    # `compose_response` adds the memory notices around it.
    answer: NotRequired[str]
    # `generate_answer`'s memory self-evaluation (`respond.Response.candidate()`).
    memory_candidate: NotRequired[dict[str, Any]]
    # `propose_memory` output (`memory.store.ProposalResult.to_state()`).
    memory_proposal: NotRequired[dict[str, Any]]


STATE_KEYS: Final = frozenset(AgentState.__required_keys__ | AgentState.__optional_keys__)

# Node names. They are also the `name` of the node rows in the run trace.
RECEIVE_QUESTION: Final = "receive_question"
PLAN_SOURCES: Final = "plan_sources"
# The live lookups are MCP client nodes: they read the Incidents Manager and the
# inventory through the MCP server, never directly (they replaced the direct
# `lookup_ticket` / `lookup_stock` nodes).
LOOKUP_TICKET: Final = "lookup_ticket_mcp"
LOOKUP_STOCK: Final = "lookup_stock_mcp"
RETRIEVE: Final = "retrieve"
GENERATE_ANSWER: Final = "generate_answer"
ANSWER_LOOKUP_FAILED: Final = "answer_lookup_failed"
ANSWER_NO_INFORMATION: Final = "answer_no_information"
# The memory nodes (MEM-092).
CLASSIFY_MEMORY_REPLY: Final = "classify_memory_reply"
RESOLVE_MEMORY_PROPOSAL: Final = "resolve_memory_proposal"
RECALL_MEMORY: Final = "recall_memory"
PROPOSE_MEMORY: Final = "propose_memory"
COMPOSE_RESPONSE: Final = "compose_response"
MEMORY_NODES: Final = frozenset(
    {
        CLASSIFY_MEMORY_REPLY,
        RESOLVE_MEMORY_PROPOSAL,
        RECALL_MEMORY,
        PROPOSE_MEMORY,
        COMPOSE_RESPONSE,
    }
)

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

Outcome = Literal["answered", "no_information", "rejected", "memory_reply"]

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


# --- Memory (MEM-092): the fixed texts around the answer -------------------------------
# Written here, never by the model, so the user reads exactly the text that would
# be stored, and the outcome of their decision exactly as it was applied.

MEMORY_QUESTION: Final = (
    'Want me to remember this for next time? "{content}" Reply yes, no, or tell me what to change.'
)
MEMORY_UPDATE_QUESTION: Final = (
    'Want me to remember this for next time? "{content}" It would replace what I remember '
    'now: "{replaces}" Reply yes, no, or tell me what to change.'
)
MEMORY_REVISED_QUESTION: Final = (
    'Here is the corrected version. Want me to remember it? "{content}" Reply yes, no, or '
    "tell me what to change."
)
MEMORY_SAVED: Final = 'Saved to team memory: "{content}" I\'ll use it in future conversations.'
MEMORY_REJECTED: Final = "Understood: I won't remember it."
MEMORY_DISCARDED_UNCLEAR: Final = (
    "I didn't save the earlier suggestion: I couldn't tell whether you wanted me to."
)
MEMORY_DISCARDED_TOPIC: Final = (
    "I didn't save the earlier suggestion, since you moved on without confirming it."
)
MEMORY_REFUSED: Final = "I can't keep that in memory: {why}. Nothing was saved."
MEMORY_ALREADY_RESOLVED: Final = "That suggestion was already settled, so nothing changed."


def lookup_succeeded(result: dict[str, Any] | None) -> bool:
    """True for a lookup that returned live data (a search with no match included)."""
    return result is not None and result.get("outcome") == "ok"


def derive_outcome(state: dict[str, Any]) -> Outcome:
    """How a finished run ended, read from its final state alone.

    `answered` means the answer was generated: from evidence (retrieved chunks,
    a lookup that succeeded, a recalled team memory), or acknowledging an
    update the user gave. A run that could only give a fixed answer - nothing
    in the knowledge base, or a lookup that failed - is `no_information`. A
    message that only answered a pending memory proposal is `memory_reply`.
    """
    if state.get("error"):
        return "rejected"
    if "memory_resolution" in state and "plan" not in state:
        return "memory_reply"
    recalled = (state.get("memory_recall") or {}).get("items")
    if (
        "memory_candidate" in state
        or state.get("context")
        or recalled
        or any(lookup_succeeded(state.get(f)) for _, f in LOOKUP_FIELDS)
    ):
        return "answered"
    return "no_information"
