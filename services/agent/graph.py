"""The agent graph: node contracts, transitions, compilation and its checks.

    START -> receive_question
               --route_question--> END                                    (empty question)
               --route_question--> classify_memory_reply       (a memory proposal is pending)
               --route_question--> recall_memory
    classify_memory_reply -> resolve_memory_proposal
               --route_after_memory_reply--> recall_memory (the message says more)
                                             | compose_response
    recall_memory -> plan_sources
    plan_sources      --route_after_plan-->     lookup_ticket_mcp | lookup_stock_mcp | retrieve
    lookup_ticket_mcp --route_after_ticket-->   lookup_stock_mcp | retrieve | generate_answer
                                                | answer_lookup_failed
    lookup_stock_mcp  --route_after_stock-->    retrieve | generate_answer | answer_lookup_failed
    retrieve      --route_after_retrieve--> generate_answer | answer_lookup_failed
                                            | answer_no_information
    generate_answer --route_after_answer--> propose_memory | compose_response
    propose_memory, answer_lookup_failed, answer_no_information -> compose_response -> END

Memory (MEM-092) wraps the same flow without changing a decision in it: a
pending proposal is settled first, the approved memories the question needs
are recalled before planning (they count as evidence), generation
self-evaluates in the same call that answers, a proposal is offered (never
stored) and `compose_response` is the single exit, putting the fixed memory
texts around the answer.

`plan_sources` asks the model which sources the question needs; the planned
sources then run in `SOURCE_ORDER` (ticket, stock, knowledge base). The two
live lookups are MCP client nodes: they read the Incidents Manager and the
inventory through the TrackFlow MCP server, with tools loaded by
`langchain-mcp-adapters` (`tools/mcp_client.py`). After the
last one, the evidence decides the answer:

- something to answer from (chunks, a lookup that succeeded, a recalled team
  memory) -> generate it, with the fixed notice of any lookup that failed put
  in front;
- no evidence, but the user gave an update (`note_team_update`) -> generate,
  to acknowledge it and self-evaluate whether it is worth remembering;
- nothing, because a lookup failed -> `answer_lookup_failed`, a fixed answer;
- nothing at all -> `answer_no_information`, a fixed answer.

Every transition after a node is a decision with an explicit `path_map`, and
every decision is written to the run's stream with its reason, so the trace
shows why each step ran, not only that it did.

`build_agent_graph()` compiles the graph and refuses a structurally wrong one
before any run (`AgentGraphBuildError`). LangGraph's own `compile()` already
rejects an edge to or from an unknown node and a missing entry point; it does
**not** reject a node nobody routes to, a node that never reaches END, or a
node writing a field the state does not have (LangGraph silently drops it).
Those three are checked here, from the declarations below.
"""

import hashlib
import json
import sqlite3
from collections import defaultdict, deque
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final, Protocol, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from data.pipelines import rag
from services.agent import nodes, planner, respond
from services.agent.config import get_agent_settings
from services.agent.errors import AgentContractError, AgentGraphBuildError
from services.agent.memory import classifier, policy
from services.agent.memory import store as memory_store
from services.agent.state import (
    ANSWER_LOOKUP_FAILED,
    ANSWER_NO_INFORMATION,
    CLASSIFY_MEMORY_REPLY,
    COMPOSE_RESPONSE,
    EMPTY_QUESTION,
    GENERATE_ANSWER,
    INCIDENT_MANAGER,
    INCIDENTS_UNAVAILABLE_ANSWER,
    INVENTORY_MANAGER,
    KNOWLEDGE_BASE,
    LOOKUP_FIELDS,
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
    NODE_OF_SOURCE,
    PLAN_SOURCES,
    PROPOSE_MEMORY,
    RECALL_MEMORY,
    RECEIVE_QUESTION,
    RESOLVE_MEMORY_PROPOSAL,
    RESULT_FIELD_OF_SOURCE,
    RETRIEVE,
    SOURCE_ORDER,
    STATE_KEYS,
    STOCK_NOT_FOUND_ANSWER,
    STOCK_UNAVAILABLE_ANSWER,
    TICKET_NOT_FOUND_ANSWER,
    TICKET_UNAVAILABLE_ANSWER,
    AgentState,
    lookup_succeeded,
)
from services.agent.tools import mcp_client
from services.agent.tools.contracts import MAX_PRODUCTS, MAX_TICKETS


class NodeAction(Protocol):
    """A node function: the state in, the fields it updates out."""

    def __call__(self, state: AgentState) -> dict[str, Any]: ...


Router = Callable[[AgentState], str]


@dataclass(frozen=True)
class NodeSpec:
    """A node and its contract: the state fields it reads and the ones it may write."""

    name: str
    action: NodeAction
    reads: frozenset[str]
    writes: frozenset[str]


@dataclass(frozen=True)
class ConditionalEdge:
    """A decision after `source`: the router returns a key of `path_map`."""

    source: str
    router: Router
    path_map: Mapping[str, str]


# --- Routing -----------------------------------------------------------------------

ROUTE_QUESTION: Final = "route_question"
ROUTE_AFTER_PLAN: Final = "route_after_plan"
ROUTE_AFTER_TICKET: Final = "route_after_ticket"
ROUTE_AFTER_STOCK: Final = "route_after_stock"
ROUTE_AFTER_RETRIEVE: Final = "route_after_retrieve"
ROUTE_AFTER_MEMORY_REPLY: Final = "route_after_memory_reply"
ROUTE_AFTER_ANSWER: Final = "route_after_answer"
# The routers that only exist for memory: the routing baseline ignores them.
MEMORY_EDGES: Final = frozenset({ROUTE_AFTER_MEMORY_REPLY, ROUTE_AFTER_ANSWER})

# Decisions: a source's name sends the run to the node that consults it; the
# other three are the ways a run can be answered.
GENERATE: Final = "generate"
LOOKUP_FAILED: Final = "lookup_failed"
NO_INFORMATION: Final = "no_information"


def _sources_after(source: str | None) -> tuple[str, ...]:
    return SOURCE_ORDER if source is None else SOURCE_ORDER[SOURCE_ORDER.index(source) + 1 :]


def _paths_after(source: str | None) -> dict[str, str]:
    """Every destination possible once `source` (None: the plan) has run."""
    paths = {later: NODE_OF_SOURCE[later] for later in _sources_after(source)}
    if source is not None:
        # The answer decisions a run can reach from here: no_information needs
        # the knowledge base to have been searched, and a lookup to have failed
        # needs a lookup to have run.
        paths[GENERATE] = GENERATE_ANSWER
        paths[LOOKUP_FAILED] = ANSWER_LOOKUP_FAILED
        if source == KNOWLEDGE_BASE:
            paths[NO_INFORMATION] = ANSWER_NO_INFORMATION
    return paths


# "plan" keeps its name: the run goes on to planning, after recalling memory.
QUESTION_PATHS: Final[Mapping[str, str]] = {
    "plan": RECALL_MEMORY,
    "memory_reply": CLASSIFY_MEMORY_REPLY,
    "rejected": END,
}
MEMORY_REPLY_PATHS: Final[Mapping[str, str]] = {
    "continue": RECALL_MEMORY,
    "respond": COMPOSE_RESPONSE,
}
ANSWER_PATHS: Final[Mapping[str, str]] = {"propose": PROPOSE_MEMORY, "respond": COMPOSE_RESPONSE}
PLAN_PATHS: Final[Mapping[str, str]] = _paths_after(None)
TICKET_PATHS: Final[Mapping[str, str]] = _paths_after(INCIDENT_MANAGER)
STOCK_PATHS: Final[Mapping[str, str]] = _paths_after(INVENTORY_MANAGER)
RETRIEVE_PATHS: Final[Mapping[str, str]] = _paths_after(KNOWLEDGE_BASE)


def _decide(edge: str, paths: Mapping[str, str], decision: str, reason: str) -> str:
    """Return `decision`, after writing it and its reason to the run's stream."""
    try:
        write = get_stream_writer()
    except RuntimeError:  # called outside a run, e.g. a router tested on its own
        return decision
    write({"edge": edge, "decision": decision, "to": paths[decision], "reason": reason})
    return decision


def route_question(state: AgentState) -> str:
    """An empty question ends the run; a pending memory proposal is settled first;
    anything else goes on to planning."""
    if state.get("error"):
        return _decide(
            ROUTE_QUESTION,
            QUESTION_PATHS,
            "rejected",
            f"{RECEIVE_QUESTION} refused the question: {state['error']}",
        )
    pending = state.get("pending_proposal")
    if pending:
        return _decide(
            ROUTE_QUESTION,
            QUESTION_PATHS,
            "memory_reply",
            f"memory proposal {pending.get('proposal_id')} is pending: the message is "
            "evaluated against it first",
        )
    return _decide(ROUTE_QUESTION, QUESTION_PATHS, "plan", "the question is not empty")


def route_after_memory_reply(state: AgentState) -> str:
    """Once the pending proposal is settled: go on with what remains of the message, if any."""
    resolution = state.get("memory_resolution") or {}
    settled = f"proposal {resolution.get('outcome', 'settled')} ({resolution.get('reason', '')})"
    remaining = (state.get("question") or "").strip()
    if remaining:
        return _decide(
            ROUTE_AFTER_MEMORY_REPLY,
            MEMORY_REPLY_PATHS,
            "continue",
            f"{settled}; the message goes on: {remaining[:120]!r}",
        )
    return _decide(
        ROUTE_AFTER_MEMORY_REPLY,
        MEMORY_REPLY_PATHS,
        "respond",
        f"{settled}; the message asks nothing else",
    )


def route_after_answer(state: AgentState) -> str:
    """The self-evaluation's verdict: offer a proposal, or nothing to remember."""
    candidate = state.get("memory_candidate") or {}
    check = candidate.get("memory_check")
    why = candidate.get("reason") or "no reason given"
    if check == "propose" and candidate.get("proposal"):
        category = candidate["proposal"].get("category")
        return _decide(
            ROUTE_AFTER_ANSWER,
            ANSWER_PATHS,
            "propose",
            f"self-evaluation proposes a {category}: {why}",
        )
    return _decide(ROUTE_AFTER_ANSWER, ANSWER_PATHS, "respond", f"self-evaluation: {check}: {why}")


def _planned_sources(state: AgentState) -> list[str]:
    steps = (state.get("plan") or {}).get("steps") or []
    planned = {step.get("source") for step in steps}
    return [source for source in SOURCE_ORDER if source in planned]


def _describe_step(step: dict[str, Any]) -> str:
    arguments = ", ".join(f"{key}={value!r}" for key, value in step.get("arguments", {}).items())
    return f"{step.get('source')} ({step.get('tool')}{f': {arguments}' if arguments else ''})"


def route_after_plan(state: AgentState) -> str:
    """Start with the first planned source; the plan always has at least one."""
    plan = state.get("plan") or {}
    planned = _planned_sources(state)
    if not planned:
        return _decide(
            ROUTE_AFTER_PLAN, PLAN_PATHS, KNOWLEDGE_BASE, "the plan is empty: knowledge base"
        )
    chosen = " then ".join(_describe_step(step) for step in plan.get("steps") or [])
    if plan.get("decided_by") == "fallback":
        why = "; ".join(plan.get("ignored") or []) or "no reason recorded"
        reason = f"planner fell back to {chosen}: {why}"
    else:
        reason = f"planner chose {chosen}"
        if plan.get("ignored"):
            reason += f" (ignored: {'; '.join(plan['ignored'])})"
    if plan.get("user_update"):
        reason += "; the user gives an update"
    return _decide(ROUTE_AFTER_PLAN, PLAN_PATHS, planned[0], reason)


def _describe_lookup(source: str, result: dict[str, Any]) -> str:
    outcome = result.get("outcome")
    if outcome == "ok":
        found = result.get("total", 0)
        unit = "ticket(s)" if source == INCIDENT_MANAGER else "product(s)"
        return f"{source} ok ({found} {unit})"
    failure = result.get("failure")
    return f"{source} {outcome}{f' ({failure})' if failure else ''}"


def answer_decision(state: AgentState) -> tuple[str, str]:
    """(decision, reason) once every planned source has run."""
    chunks = len(state.get("context") or [])
    lookups = [
        (source, cast("dict[str, Any] | None", state.get(field))) for source, field in LOOKUP_FIELDS
    ]
    ran = [(source, result) for source, result in lookups if result is not None]
    found = [source for source, result in ran if lookup_succeeded(result)]
    failed = [source for source, result in ran if not lookup_succeeded(result)]

    memories = len((state.get("memory_recall") or {}).get("items") or [])
    update = bool((state.get("plan") or {}).get("user_update"))

    parts = [_describe_lookup(source, result) for source, result in ran]
    if "context" in state:
        parts.append(f"{chunks} chunk(s) scored at least {rag.MIN_SCORE:.2f}")
    if memories:
        parts.append(f"{memories} team memory note(s)")
    summary = "; ".join(parts)

    notice = f"; the failed lookup of {', '.join(failed)} is stated as such" if failed else ""
    if chunks or found or memories:
        return GENERATE, f"evidence to answer from: {summary}{notice}"
    if update:
        return (
            GENERATE,
            f"no evidence, but the user gives an update to acknowledge: {summary}{notice}",
        )
    if failed:
        return LOOKUP_FAILED, f"no evidence and a lookup failed: {summary}"
    return NO_INFORMATION, f"no evidence: {summary}"


def _route_after(edge: str, source: str, paths: Mapping[str, str]) -> Router:
    def route(state: AgentState) -> str:
        pending = [
            later
            for later in _sources_after(source)
            if later in _planned_sources(state) and RESULT_FIELD_OF_SOURCE[later] not in state
        ]
        if pending:
            return _decide(edge, paths, pending[0], f"the plan still needs {', '.join(pending)}")
        decision, reason = answer_decision(state)
        return _decide(edge, paths, decision, reason)

    route.__name__ = edge
    route.__doc__ = f"After {NODE_OF_SOURCE[source]}: the next planned source, or the answer."
    return route


route_after_ticket = _route_after(ROUTE_AFTER_TICKET, INCIDENT_MANAGER, TICKET_PATHS)
route_after_stock = _route_after(ROUTE_AFTER_STOCK, INVENTORY_MANAGER, STOCK_PATHS)
route_after_retrieve = _route_after(ROUTE_AFTER_RETRIEVE, KNOWLEDGE_BASE, RETRIEVE_PATHS)


# --- Declarations ------------------------------------------------------------------

NODES: Final[tuple[NodeSpec, ...]] = (
    NodeSpec(
        RECEIVE_QUESTION,
        nodes.receive_question,
        reads=frozenset({"question"}),
        writes=frozenset({"question", "error"}),
    ),
    NodeSpec(
        CLASSIFY_MEMORY_REPLY,
        nodes.classify_memory_reply,
        reads=frozenset({"question", "pending_proposal"}),
        writes=frozenset({"memory_reply", "question"}),
    ),
    NodeSpec(
        RESOLVE_MEMORY_PROPOSAL,
        nodes.resolve_memory_proposal,
        reads=frozenset({"pending_proposal", "memory_reply", "user_id", "run_id"}),
        writes=frozenset({"memory_resolution"}),
    ),
    NodeSpec(
        RECALL_MEMORY,
        nodes.recall_memory,
        reads=frozenset({"question"}),
        writes=frozenset({"memory_recall"}),
    ),
    NodeSpec(
        PLAN_SOURCES,
        nodes.plan_sources,
        reads=frozenset({"question"}),
        writes=frozenset({"plan"}),
    ),
    NodeSpec(
        LOOKUP_TICKET,
        nodes.lookup_ticket_mcp,
        reads=frozenset({"plan"}),
        writes=frozenset({"ticket_lookup"}),
    ),
    NodeSpec(
        LOOKUP_STOCK,
        nodes.lookup_stock_mcp,
        reads=frozenset({"plan"}),
        writes=frozenset({"stock_lookup"}),
    ),
    NodeSpec(
        RETRIEVE,
        nodes.retrieve,
        reads=frozenset({"question"}),
        writes=frozenset({"context"}),
    ),
    NodeSpec(
        GENERATE_ANSWER,
        nodes.generate_answer,
        reads=frozenset(
            {"question", "context", "ticket_lookup", "stock_lookup", "memory_recall", "plan"}
        ),
        writes=frozenset({"answer", "memory_candidate"}),
    ),
    NodeSpec(
        PROPOSE_MEMORY,
        nodes.propose_memory,
        reads=frozenset({"question", "memory_candidate", "conversation_id", "user_id", "run_id"}),
        writes=frozenset({"memory_proposal"}),
    ),
    NodeSpec(
        ANSWER_LOOKUP_FAILED,
        nodes.answer_lookup_failed,
        reads=frozenset({"ticket_lookup", "stock_lookup"}),
        writes=frozenset({"answer"}),
    ),
    NodeSpec(
        ANSWER_NO_INFORMATION,
        nodes.answer_no_information,
        reads=frozenset(),
        writes=frozenset({"answer"}),
    ),
    NodeSpec(
        COMPOSE_RESPONSE,
        nodes.compose_response,
        reads=frozenset({"answer", "memory_resolution", "memory_reply", "memory_proposal"}),
        writes=frozenset({"answer"}),
    ),
)

EDGES: Final[tuple[tuple[str, str], ...]] = (
    (START, RECEIVE_QUESTION),
    (CLASSIFY_MEMORY_REPLY, RESOLVE_MEMORY_PROPOSAL),
    (RECALL_MEMORY, PLAN_SOURCES),
    (PROPOSE_MEMORY, COMPOSE_RESPONSE),
    (ANSWER_LOOKUP_FAILED, COMPOSE_RESPONSE),
    (ANSWER_NO_INFORMATION, COMPOSE_RESPONSE),
    (COMPOSE_RESPONSE, END),
)

CONDITIONAL_EDGES: Final[tuple[ConditionalEdge, ...]] = (
    ConditionalEdge(RECEIVE_QUESTION, route_question, QUESTION_PATHS),
    ConditionalEdge(RESOLVE_MEMORY_PROPOSAL, route_after_memory_reply, MEMORY_REPLY_PATHS),
    ConditionalEdge(PLAN_SOURCES, route_after_plan, PLAN_PATHS),
    ConditionalEdge(LOOKUP_TICKET, route_after_ticket, TICKET_PATHS),
    ConditionalEdge(LOOKUP_STOCK, route_after_stock, STOCK_PATHS),
    ConditionalEdge(RETRIEVE, route_after_retrieve, RETRIEVE_PATHS),
    ConditionalEdge(GENERATE_ANSWER, route_after_answer, ANSWER_PATHS),
)

# What each node reads: the run trace stores only these fields as a node's input.
NODE_READS: Final[Mapping[str, frozenset[str]]] = {spec.name: spec.reads for spec in NODES}


# --- Compilation -------------------------------------------------------------------


def _check_contracts(specs: Iterable[NodeSpec]) -> None:
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            raise AgentGraphBuildError(f"Node '{spec.name}' is declared twice")
        seen.add(spec.name)
        if spec.name in STATE_KEYS:
            raise AgentGraphBuildError(
                f"Node '{spec.name}' has the name of a state field; rename the node"
            )
        unknown = sorted((spec.reads | spec.writes) - STATE_KEYS)
        if unknown:
            raise AgentGraphBuildError(
                f"Node '{spec.name}' declares {unknown}, which AgentState does not define "
                f"(fields: {sorted(STATE_KEYS)})"
            )
        if not spec.writes:
            raise AgentGraphBuildError(f"Node '{spec.name}' declares no state field it writes")


def _reachable(start: str, links: Mapping[str, set[str]]) -> set[str]:
    found = {start}
    queue = deque([start])
    while queue:
        for nxt in links.get(queue.popleft(), set()):
            if nxt not in found:
                found.add(nxt)
                queue.append(nxt)
    return found


def _check_topology(
    specs: Iterable[NodeSpec],
    edges: Iterable[tuple[str, str]],
    conditional_edges: Iterable[ConditionalEdge],
) -> None:
    # Checked on the declarations, not on `compiled.get_graph()`: the drawn graph
    # adds an implicit edge to END for a node with no way out, hiding a dead end.
    forward: dict[str, set[str]] = defaultdict(set)
    backward: dict[str, set[str]] = defaultdict(set)
    links = [*edges] + [(c.source, t) for c in conditional_edges for t in c.path_map.values()]
    for source, target in links:
        forward[source].add(target)
        backward[target].add(source)
    from_start = _reachable(START, forward)
    to_end = _reachable(END, backward)
    for spec in specs:
        if spec.name not in from_start:
            raise AgentGraphBuildError(
                f"Node '{spec.name}' is not connected: no path from START reaches it"
            )
        if spec.name not in to_end:
            raise AgentGraphBuildError(
                f"Node '{spec.name}' is a dead end: no path from it reaches END"
            )


def _bind(spec: NodeSpec) -> NodeAction:
    """The node as LangGraph runs it: `spec.action`, held to its declared writes."""

    def node(state: AgentState) -> dict[str, Any]:
        update = spec.action(state)
        if not isinstance(update, dict):
            raise AgentContractError(
                f"Node '{spec.name}' returned {type(update).__name__}, not a dict of state fields"
            )
        undeclared = sorted(set(update) - spec.writes)
        if undeclared:
            raise AgentContractError(
                f"Node '{spec.name}' wrote {undeclared}, outside its declared writes "
                f"{sorted(spec.writes)}"
            )
        return update

    node.__name__ = spec.name
    node.__doc__ = spec.action.__doc__
    return node


def build_agent_graph(
    checkpointer: BaseCheckpointSaver | None = None,
    *,
    node_specs: Iterable[NodeSpec] = NODES,
    edges: Iterable[tuple[str, str]] = EDGES,
    conditional_edges: Iterable[ConditionalEdge] = CONDITIONAL_EDGES,
) -> CompiledStateGraph:
    """Validate and compile the graph. Raises AgentGraphBuildError naming the problem.

    With a `checkpointer`, LangGraph saves the state after every step under the
    run's `thread_id`, so a run can be inspected or resumed later.
    """
    specs = tuple(node_specs)
    edge_list = tuple(edges)
    conditionals = tuple(conditional_edges)
    _check_contracts(specs)

    builder = StateGraph(AgentState)
    try:
        for spec in specs:
            builder.add_node(spec.name, _bind(spec))
        for source, target in edge_list:
            builder.add_edge(source, target)
        for conditional in conditionals:
            path_map: dict[Hashable, str] = dict(conditional.path_map.items())
            builder.add_conditional_edges(conditional.source, conditional.router, path_map)
        compiled = builder.compile(checkpointer=checkpointer)
    except ValueError as error:
        raise AgentGraphBuildError(f"LangGraph rejected the graph: {error}") from error

    _check_topology(specs, edge_list, conditionals)
    return compiled


def graph_fingerprint() -> str:
    """A short hash of everything that decides what a run does.

    Stored on every run and in the recorded eval traces, so evals can refuse
    traces recorded by a different graph, prompt, threshold, tool schema,
    fallback text or memory policy. The MCP server's URL, the OAuth client, its scopes and the
    tool timeout are deployment settings and stay out of it; the recorder
    writes the timeout into the recording instead.
    """
    payload = {
        "nodes": [
            {
                "name": spec.name,
                "action": f"{spec.action.__module__}."
                f"{getattr(spec.action, '__qualname__', type(spec.action).__qualname__)}",
                "reads": sorted(spec.reads),
                "writes": sorted(spec.writes),
            }
            for spec in NODES
        ],
        "edges": sorted(list(edge) for edge in EDGES),
        "conditional_edges": [
            {"source": c.source, "router": c.router.__name__, "paths": dict(c.path_map)}
            for c in CONDITIONAL_EDGES
        ],
        "system_prompt": respond.system_prompt(),
        "respond_tool": respond.tool_definitions(),
        "min_score": rag.MIN_SCORE,
        "top_k": rag.DEFAULT_TOP_K,
        "temperature": rag.GENERATION_TEMPERATURE,
        "empty_question": EMPTY_QUESTION,
        "no_information_answer": NO_INFORMATION_ANSWER,
        "planner": {
            "prompt": planner.PLANNER_PROMPT,
            "temperature": planner.PLANNER_TEMPERATURE,
            "tools": planner.tool_definitions(),
        },
        "source_order": list(SOURCE_ORDER),
        "tools": {"max_tickets": MAX_TICKETS, "max_products": MAX_PRODUCTS},
        "mcp": {"server": mcp_client.SERVER_NAME, "tools": sorted(mcp_client.AGENT_TOOLS)},
        "memory": {
            "classifier": {
                "prompt": classifier.CLASSIFIER_PROMPT,
                "tools": classifier.tool_definitions(),
                "min_confidence": classifier.MIN_CONFIDENCE,
            },
            "memorable": [
                [kind.category, kind.ttl_days, kind.max_ttl_days, kind.cap]
                for kind in policy.MEMORABLE
            ],
            "forbidden": [list(rule.reasons) for rule in policy.FORBIDDEN],
            "pending_ttl_seconds": policy.PENDING_TTL.total_seconds(),
            "recall": [memory_store.RECALL_TOP_K, memory_store.MIN_MEMORY_SCORE],
            "texts": [
                MEMORY_QUESTION,
                MEMORY_UPDATE_QUESTION,
                MEMORY_REVISED_QUESTION,
                MEMORY_SAVED,
                MEMORY_REJECTED,
                MEMORY_DISCARDED_UNCLEAR,
                MEMORY_DISCARDED_TOPIC,
                MEMORY_REFUSED,
                MEMORY_ALREADY_RESOLVED,
            ],
        },
        "fallback_answers": [
            TICKET_NOT_FOUND_ANSWER,
            TICKET_UNAVAILABLE_ANSWER,
            INCIDENTS_UNAVAILABLE_ANSWER,
            STOCK_NOT_FOUND_ANSWER,
            STOCK_UNAVAILABLE_ANSWER,
        ],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()[:16]


def draw_mermaid() -> str:
    """The compiled graph as a Mermaid diagram (no checkpointer needed)."""
    return build_agent_graph().get_graph().draw_mermaid()


# --- The application's graph -------------------------------------------------------


@lru_cache(maxsize=1)
def get_checkpointer() -> SqliteSaver:
    """LangGraph's SQLite checkpointer, in the agent store file."""
    path = get_agent_settings().agent_store_path
    path.parent.mkdir(parents=True, exist_ok=True)
    # Shared across FastAPI's worker threads: SqliteSaver serialises access with its lock.
    connection = sqlite3.connect(path, check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    return saver


@lru_cache(maxsize=1)
def get_agent_graph() -> CompiledStateGraph:
    """The compiled, checkpointed graph. Built at API startup, before any request."""
    return build_agent_graph(get_checkpointer())


def close_agent_graph() -> None:
    """Drop the cached graph and close the checkpointer's connection."""
    if get_checkpointer.cache_info().currsize:
        get_checkpointer().conn.close()
    get_agent_graph.cache_clear()
    get_checkpointer.cache_clear()
