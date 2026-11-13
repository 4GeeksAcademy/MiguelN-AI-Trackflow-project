"""The agent's nodes: one step each, returning only the state fields they own.

Retrieval and generation are the RAG project's own functions, called as they
are: `retrieve` calls `rag.retrieve()`, and `generate_answer` calls
`rag.generate_answer(question, passages)` with the evidence the earlier nodes
produced. The monolithic `rag.query()` is never called here: it would run
retrieval again inside one opaque step.

Each live lookup is an MCP client node of its own - `lookup_ticket_mcp`,
`lookup_stock_mcp` - running the step `plan_sources` planned for it through the
TrackFlow MCP server (`tools.mcp_lookups`, tools loaded with
`langchain-mcp-adapters`). The agent has no other path to the Incidents Manager
or the inventory. A lookup never raises for a failing service: its result says
`not_found` or `unavailable`, and the routers send the run to an honest fixed
answer instead.

Everything external is reached through its module at call time (`rag`,
`planner`, `mcp_lookups`), so tests can replace it with `monkeypatch.setattr`,
as the RAG tests do.
"""

from typing import Any

from data.pipelines import rag
from services.agent import planner
from services.agent.errors import AgentContractError
from services.agent.state import (
    ANSWER_LOOKUP_FAILED,
    EMPTY_QUESTION,
    GENERATE_ANSWER,
    INCIDENT_MANAGER,
    INCIDENTS_UNAVAILABLE_ANSWER,
    INVENTORY_MANAGER,
    LOOKUP_STOCK,
    LOOKUP_TICKET,
    NO_INFORMATION_ANSWER,
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


def generate_answer(state: AgentState) -> dict[str, Any]:
    """Answer from the evidence the earlier nodes produced, and only from it.

    A lookup that failed is never shown to the model: its fixed notice is put
    in front of the generated answer instead.
    """
    passages = evidence_passages(state)
    if not passages:
        # The routers send a run without evidence to a fixed answer; reaching
        # this node without any means the graph was wired wrongly.
        raise AgentContractError(
            f"'{GENERATE_ANSWER}' needs evidence: retrieved context or a lookup that succeeded"
        )
    answer = rag.generate_answer(state["question"], passages)
    return {"answer": "\n\n".join([*failure_notices(state), answer])}


def answer_lookup_failed(state: AgentState) -> dict[str, Any]:
    """Say honestly that a lookup failed, without calling a model: never a made-up status."""
    notices = failure_notices(state)
    if not notices:
        raise AgentContractError(f"'{ANSWER_LOOKUP_FAILED}' ran but no lookup failed")
    return {"answer": "\n\n".join(notices)}


def answer_no_information(state: AgentState) -> dict[str, Any]:
    """Say honestly that the knowledge base has nothing on it, without calling a model."""
    return {"answer": NO_INFORMATION_ANSWER}
