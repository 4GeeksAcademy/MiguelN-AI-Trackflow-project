"""The agent graph: node contracts, transitions, compilation and its checks.

    START -> receive_question
               --route_question--> END                                  (empty question)
               --route_question--> retrieve
                                     --route_context--> generate_answer -> END        (context)
                                     --route_context--> answer_no_information -> END  (none)

Every transition after `receive_question` and `retrieve` is a decision with an
explicit `path_map`, and every decision is written to the run's stream with
its reason, so the trace shows why each step ran, not only that it did.

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
from typing import Any, Final, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from data.pipelines import rag
from services.agent import nodes
from services.agent.config import get_agent_settings
from services.agent.errors import AgentContractError, AgentGraphBuildError
from services.agent.state import (
    ANSWER_NO_INFORMATION,
    EMPTY_QUESTION,
    GENERATE_ANSWER,
    NO_INFORMATION_ANSWER,
    RECEIVE_QUESTION,
    RETRIEVE,
    STATE_KEYS,
    AgentState,
)


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
ROUTE_CONTEXT: Final = "route_context"

QUESTION_PATHS: Final[Mapping[str, str]] = {"retrieve": RETRIEVE, "rejected": END}
CONTEXT_PATHS: Final[Mapping[str, str]] = {
    "generate": GENERATE_ANSWER,
    "no_information": ANSWER_NO_INFORMATION,
}


def _decide(edge: str, paths: Mapping[str, str], decision: str, reason: str) -> str:
    """Return `decision`, after writing it and its reason to the run's stream."""
    try:
        write = get_stream_writer()
    except RuntimeError:  # called outside a run, e.g. a router tested on its own
        return decision
    write({"edge": edge, "decision": decision, "to": paths[decision], "reason": reason})
    return decision


def route_question(state: AgentState) -> str:
    """An empty question ends the run; anything else goes to retrieval."""
    if state.get("error"):
        return _decide(
            ROUTE_QUESTION,
            QUESTION_PATHS,
            "rejected",
            f"{RECEIVE_QUESTION} refused the question: {state['error']}",
        )
    return _decide(ROUTE_QUESTION, QUESTION_PATHS, "retrieve", "the question is not empty")


def route_context(state: AgentState) -> str:
    """Generate only from retrieved context; with none, answer honestly instead."""
    hits = len(state.get("context") or [])
    threshold = f"{rag.MIN_SCORE:.2f}"
    if hits:
        return _decide(
            ROUTE_CONTEXT,
            CONTEXT_PATHS,
            "generate",
            f"{hits} chunk(s) scored at least {threshold}",
        )
    return _decide(
        ROUTE_CONTEXT,
        CONTEXT_PATHS,
        "no_information",
        f"no chunk scored at least {threshold}",
    )


# --- Declarations ------------------------------------------------------------------

NODES: Final[tuple[NodeSpec, ...]] = (
    NodeSpec(
        RECEIVE_QUESTION,
        nodes.receive_question,
        reads=frozenset({"question"}),
        writes=frozenset({"question", "error"}),
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
        reads=frozenset({"question", "context"}),
        writes=frozenset({"answer"}),
    ),
    NodeSpec(
        ANSWER_NO_INFORMATION,
        nodes.answer_no_information,
        reads=frozenset(),
        writes=frozenset({"answer"}),
    ),
)

EDGES: Final[tuple[tuple[str, str], ...]] = (
    (START, RECEIVE_QUESTION),
    (GENERATE_ANSWER, END),
    (ANSWER_NO_INFORMATION, END),
)

CONDITIONAL_EDGES: Final[tuple[ConditionalEdge, ...]] = (
    ConditionalEdge(RECEIVE_QUESTION, route_question, QUESTION_PATHS),
    ConditionalEdge(RETRIEVE, route_context, CONTEXT_PATHS),
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
    traces recorded by a different graph, prompt or threshold.
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
        "system_prompt": rag.SYSTEM_PROMPT,
        "min_score": rag.MIN_SCORE,
        "top_k": rag.DEFAULT_TOP_K,
        "temperature": rag.GENERATION_TEMPERATURE,
        "empty_question": EMPTY_QUESTION,
        "no_information_answer": NO_INFORMATION_ANSWER,
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
