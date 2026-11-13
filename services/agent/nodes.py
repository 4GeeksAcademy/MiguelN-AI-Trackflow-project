"""The agent's nodes: one step each, returning only the state fields they own.

Retrieval is the RAG project's own function, called as it is: `retrieve` calls
`rag.retrieve()`. Generation is the agent's own structured call,
`respond.generate_response()` (MEM-092): the answer from the evidence the
earlier nodes produced - under exactly the RAG rules - plus the memory
self-evaluation, in one model call. The monolithic `rag.query()` is never
called here: it would run retrieval again inside one opaque step.

Memory (MEM-092) is five more steps around the same flow, each reaching the
memory store through its explicit interface (`memory.store`):
`classify_memory_reply` and `resolve_memory_proposal` settle a pending
proposal before anything else; `recall_memory` reads the approved memories the
question needs; `propose_memory` offers what generation proposed - never
writing a memory; `compose_response` puts the fixed memory texts around the
answer.

Each live lookup is an MCP client node of its own - `lookup_ticket_mcp`,
`lookup_stock_mcp` - running the step `plan_sources` planned for it through the
TrackFlow MCP server (`tools.mcp_lookups`, tools loaded with
`langchain-mcp-adapters`). The agent has no other path to the Incidents Manager
or the inventory. A lookup never raises for a failing service: its result says
`not_found` or `unavailable`, and the routers send the run to an honest fixed
answer instead.

Everything external is reached through its module at call time (`rag`,
`planner`, `mcp_lookups`, `respond`, `classifier`, `memory_store`), so tests can
replace it with `monkeypatch.setattr`, as the RAG tests do.
"""

from typing import Any

from data.pipelines import rag
from services.agent import planner, respond
from services.agent.errors import AgentContractError
from services.agent.memory import classifier, policy
from services.agent.memory import store as memory_store
from services.agent.state import (
    ANSWER_LOOKUP_FAILED,
    COMPOSE_RESPONSE,
    EMPTY_QUESTION,
    GENERATE_ANSWER,
    INCIDENT_MANAGER,
    INCIDENTS_UNAVAILABLE_ANSWER,
    INVENTORY_MANAGER,
    LOOKUP_STOCK,
    LOOKUP_TICKET,
    MEMORY_ALREADY_RESOLVED,
    MEMORY_DISCARDED_TOPIC,
    MEMORY_DISCARDED_UNCLEAR,
    MEMORY_QUESTION,
    MEMORY_REFUSED,
    MEMORY_REJECTED,
    MEMORY_REVISED_QUESTION,
    MEMORY_SAVED,
    MEMORY_UPDATE_QUESTION,
    NO_INFORMATION_ANSWER,
    PROPOSE_MEMORY,
    STOCK_NOT_FOUND_ANSWER,
    STOCK_UNAVAILABLE_ANSWER,
    TICKET_NOT_FOUND_ANSWER,
    TICKET_UNAVAILABLE_ANSWER,
    AgentState,
)
from services.agent.tools import mcp_lookups
from services.agent.tools.contracts import (
    StockLookupInput,
    StockLookupResult,
    TicketLookupInput,
    TicketLookupResult,
)
from services.agent.tools.passages import stock_passages, ticket_passages

# A product name from the request is quoted back in a fallback answer; it is cut
# here so a long one cannot flood the answer.
_MAX_QUOTED_PRODUCT = 80


def receive_question(state: AgentState) -> dict[str, Any]:
    """Take the caller's question as the graph's input, stripped; refuse an empty one."""
    raw = state.get("question")
    question = raw.strip() if isinstance(raw, str) else ""
    if not question:
        return {"question": question, "error": EMPTY_QUESTION}
    return {"question": question}


# --- Memory: a pending proposal is settled first --------------------------------------


def classify_memory_reply(state: AgentState) -> dict[str, Any]:
    """Classify the message against the pending proposal: approve, reject, edit or neither.

    An explicit label from the model, then deterministic rules (`classifier.decide`):
    approval is never assumed. The question becomes what remains of the message.
    """
    reply = classifier.classify_reply(state["question"], state["pending_proposal"])
    return {"memory_reply": reply, "question": reply["remaining_question"]}


def resolve_memory_proposal(state: AgentState) -> dict[str, Any]:
    """Apply the decision to the pending proposal and log it, in one transaction."""
    pending = state["pending_proposal"]
    reply = state["memory_reply"]
    resolution = memory_store.get_memory_store().resolve_proposal(
        pending["proposal_id"],
        classifier.decision_from_state(reply),
        user_id=state.get("user_id"),
        run_id=state.get("run_id"),
        message=reply["message"],
    )
    return {"memory_resolution": resolution.to_state()}


def recall_memory(state: AgentState) -> dict[str, Any]:
    """Read the approved memories this question needs: at most three, never the whole store."""
    return {"memory_recall": memory_store.get_memory_store().recall(state["question"]).to_state()}


# --- Planning and the sources -----------------------------------------------------------


def plan_sources(state: AgentState) -> dict[str, Any]:
    """Decide which sources the question needs: a tool, the knowledge base, or both."""
    return {"plan": planner.choose_sources(state["question"])}


def _planned_arguments(state: AgentState, source: str, node: str) -> dict[str, Any]:
    for step in (state.get("plan") or {}).get("steps", []):
        if step.get("source") == source:
            return dict(step.get("arguments") or {})
    # The routers only send a run here when its plan has this step.
    raise AgentContractError(f"'{node}' ran but the plan has no '{source}' step")


def lookup_ticket_mcp(state: AgentState) -> dict[str, Any]:
    """Read the ticket(s) the plan names, live, through the MCP server's incident tools."""
    arguments = _planned_arguments(state, INCIDENT_MANAGER, LOOKUP_TICKET)
    result = mcp_lookups.lookup_ticket(TicketLookupInput.model_validate(arguments))
    return {"ticket_lookup": result.model_dump(mode="json")}


def lookup_stock_mcp(state: AgentState) -> dict[str, Any]:
    """Read the stock of the product the plan names, live, through the MCP server's
    read-only inventory tool."""
    arguments = _planned_arguments(state, INVENTORY_MANAGER, LOOKUP_STOCK)
    result = mcp_lookups.lookup_stock(StockLookupInput.model_validate(arguments))
    return {"stock_lookup": result.model_dump(mode="json")}


def retrieve(state: AgentState) -> dict[str, Any]:
    """Search the knowledge base: the chunks at or above `rag.MIN_SCORE`, possibly none."""
    return {"context": rag.retrieve(state["question"])}


def _ticket(state: AgentState) -> TicketLookupResult | None:
    raw = state.get("ticket_lookup")
    return None if raw is None else TicketLookupResult.model_validate(raw)


def _stock(state: AgentState) -> StockLookupResult | None:
    raw = state.get("stock_lookup")
    return None if raw is None else StockLookupResult.model_validate(raw)


def evidence_passages(state: AgentState) -> list[dict[str, Any]]:
    """What generation may use: live records that were found, then retrieved chunks."""
    passages: list[dict[str, Any]] = []
    if (ticket := _ticket(state)) is not None:
        passages.extend(ticket_passages(ticket))
    if (stock := _stock(state)) is not None:
        passages.extend(stock_passages(stock))
    passages.extend(state.get("context") or [])
    return passages


def failure_notices(state: AgentState) -> list[str]:
    """The fixed, honest sentence for each lookup that did not succeed."""
    notices: list[str] = []
    ticket = _ticket(state)
    if ticket is not None and ticket.outcome == "not_found":
        notices.append(TICKET_NOT_FOUND_ANSWER.format(reference=ticket.reference))
    elif ticket is not None and ticket.outcome == "unavailable":
        if ticket.reference is None:
            notices.append(INCIDENTS_UNAVAILABLE_ANSWER)
        else:
            notices.append(TICKET_UNAVAILABLE_ANSWER.format(reference=ticket.reference))
    stock = _stock(state)
    if stock is not None and stock.outcome != "ok":
        product = stock.request.product[:_MAX_QUOTED_PRODUCT]
        template = (
            STOCK_NOT_FOUND_ANSWER if stock.outcome == "not_found" else STOCK_UNAVAILABLE_ANSWER
        )
        notices.append(template.format(product=product))
    return notices


def recalled_memories(state: AgentState) -> list[dict[str, Any]]:
    return list((state.get("memory_recall") or {}).get("items") or [])


def user_update(state: AgentState) -> bool:
    return bool((state.get("plan") or {}).get("user_update"))


def generate_answer(state: AgentState) -> dict[str, Any]:
    """Answer from the evidence the earlier nodes produced, and only from it, and
    self-evaluate whether the message holds something worth remembering - one call.

    A lookup that failed is never shown to the model: its fixed notice is put
    in front of the generated answer instead.
    """
    passages = evidence_passages(state)
    memories = recalled_memories(state)
    update = user_update(state)
    if not passages and not memories and not update:
        # The routers send a run without evidence to a fixed answer; reaching
        # this node without any means the graph was wired wrongly.
        raise AgentContractError(
            f"'{GENERATE_ANSWER}' needs evidence: retrieved context, a lookup that "
            "succeeded, a recalled memory, or an update from the user"
        )
    response = respond.generate_response(
        state["question"], passages, memories=memories, user_update=update
    )
    return {
        "answer": "\n\n".join([*failure_notices(state), response.answer]),
        "memory_candidate": response.candidate(),
    }


def propose_memory(state: AgentState) -> dict[str, Any]:
    """Offer what the self-evaluation proposed - after the policy, duplicate and
    one-at-a-time checks. Never writes a memory: only the user's approval does."""
    candidate = state.get("memory_candidate") or {}
    raw = candidate.get("proposal")
    if raw is None:
        raise AgentContractError(f"'{PROPOSE_MEMORY}' ran but nothing was proposed")
    result = memory_store.get_memory_store().open_proposal(
        policy.MemoryDraft.model_validate(raw),
        conversation_id=state["conversation_id"],
        run_id=state.get("run_id"),
        user_id=state.get("user_id"),
        message=state["question"],
    )
    return {"memory_proposal": result.to_state()}


def answer_lookup_failed(state: AgentState) -> dict[str, Any]:
    """Say honestly that a lookup failed, without calling a model: never a made-up status."""
    notices = failure_notices(state)
    if not notices:
        raise AgentContractError(f"'{ANSWER_LOOKUP_FAILED}' ran but no lookup failed")
    return {"answer": "\n\n".join(notices)}


def answer_no_information(state: AgentState) -> dict[str, Any]:
    """Say honestly that the knowledge base has nothing on it, without calling a model."""
    return {"answer": NO_INFORMATION_ANSWER}


# --- The response ---------------------------------------------------------------------


def resolution_notice(
    resolution: dict[str, Any] | None, reply: dict[str, Any] | None
) -> str | None:
    """The fixed sentence saying what happened to the pending proposal."""
    if resolution is None:
        return None
    outcome = resolution["outcome"]
    if outcome == "approved":
        return MEMORY_SAVED.format(content=resolution["content"])
    if outcome == "rejected":
        return MEMORY_REJECTED
    if outcome == "blocked":
        reasons = resolution.get("refusal") or []
        why = (
            "; ".join(policy.REFUSAL_REASONS.get(r, r) for r in reasons)
            or "it breaks the memory policy"
        )
        return MEMORY_REFUSED.format(why=why)
    if outcome == "already_resolved":
        return MEMORY_ALREADY_RESOLVED
    if outcome == "discarded":
        label = ((reply or {}).get("decision") or {}).get("label")
        return MEMORY_DISCARDED_TOPIC if label == "unrelated" else MEMORY_DISCARDED_UNCLEAR
    return None  # revised: the corrected version is asked at the end


def proposal_question(proposal: dict[str, Any], *, revised: bool = False) -> str:
    if revised:
        return MEMORY_REVISED_QUESTION.format(content=proposal["content"])
    if proposal.get("replaces_content"):
        return MEMORY_UPDATE_QUESTION.format(
            content=proposal["content"], replaces=proposal["replaces_content"]
        )
    return MEMORY_QUESTION.format(content=proposal["content"])


def compose_response(state: AgentState) -> dict[str, Any]:
    """The response the user reads: what happened to the pending proposal, the answer,
    and the question about a new proposal - the memory texts are fixed, never generated."""
    resolution = state.get("memory_resolution")
    parts: list[str] = []
    notice = resolution_notice(resolution, state.get("memory_reply"))
    if notice:
        parts.append(notice)
    if state.get("answer"):
        parts.append(state["answer"])
    revised = (resolution or {}).get("revised")
    proposal = state.get("memory_proposal") or {}
    if revised:
        parts.append(proposal_question(revised, revised=True))
    elif proposal.get("status") == "pending" and proposal.get("proposal"):
        parts.append(proposal_question(proposal["proposal"]))
    if not parts:
        raise AgentContractError(f"'{COMPOSE_RESPONSE}' has nothing to say")
    return {"answer": "\n\n".join(parts)}
