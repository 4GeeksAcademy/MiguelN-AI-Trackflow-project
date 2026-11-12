"""Run or resume the agent graph, writing its trace as it goes.

`run_agent()` is the one entry point callers use (the API, the CLIs). It gives
the run an id, which is also its LangGraph `thread_id`, so the trace and the
checkpoints of a run share one key. It streams the graph with two modes:

- `tasks`: a start and an end event per node, with its input and its result or
  error, which become the trace's `node` rows;
- `custom`: the routers' decisions (`graph._decide`), which become `edge` rows;

plus `values`, whose last event is the final state.

A failure at any node marks the run `failed` with the node's name and raises
`AgentRunError`; the checkpoints keep the state up to that node, so
`resume_run()` continues from it without repeating the steps that succeeded.
"""

import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StreamMode

from data.process.knowledge_base import KnowledgeBaseError
from services.agent.errors import (
    AgentContractError,
    AgentRunError,
    AgentRunNotFound,
    AgentRunNotResumable,
)
from services.agent.graph import NODE_READS, get_agent_graph, graph_fingerprint
from services.agent.state import Outcome, derive_outcome
from services.agent.tracing import TraceStore, get_trace_store, utc_now

logger = logging.getLogger("api.agent")

STREAM_MODES: Final[list[StreamMode]] = ["tasks", "custom", "values"]


@dataclass(frozen=True)
class AgentResult:
    run_id: str
    outcome: Outcome
    answer: str | None
    # Why the question was refused, for a `rejected` run.
    error: str | None = None


def safe_error_message(error: BaseException) -> str | None:
    """The exception text, only for errors whose messages are written to be logged.

    KnowledgeBaseError messages name the failing piece, never a URL or key;
    AgentContractError messages name a node and a field. Anything else keeps
    only its class in the trace.
    """
    return str(error) if isinstance(error, (KnowledgeBaseError, AgentContractError)) else None


class _TraceRecorder:
    """Turns the graph's stream into trace rows, in the order things happened."""

    def __init__(
        self,
        store: TraceStore,
        run_id: str,
        attempt: int,
        reads: Mapping[str, frozenset[str]] = NODE_READS,
    ) -> None:
        self._store = store
        self._run_id = run_id
        self._attempt = attempt
        self._reads = reads
        self._open: dict[str, tuple[str, float, Any]] = {}
        self._pending_edges: list[tuple[str, dict[str, Any]]] = []
        self.running_node: str | None = None
        self.failed_node: str | None = None
        self.final_state: dict[str, Any] = {}

    def handle(self, mode: str, event: Any) -> None:
        if mode == "tasks":
            if "input" in event:
                self._node_started(event)
            else:
                self._node_finished(event)
        elif mode == "custom" and isinstance(event, dict) and "edge" in event:
            # A router runs as the last part of its source node: its decision
            # is written after that node's row, in `_node_finished`.
            self._pending_edges.append((utc_now(), event))
        elif mode == "values" and isinstance(event, dict):
            self.final_state = event

    def _inputs(self, name: str, state: Any) -> Any:
        reads = self._reads.get(name)
        if reads is None or not isinstance(state, dict):
            return state
        return {key: state[key] for key in sorted(reads) if key in state}

    def _node_started(self, event: dict[str, Any]) -> None:
        self.running_node = event["name"]
        self._open[event["id"]] = (
            utc_now(),
            time.perf_counter(),
            self._inputs(event["name"], event.get("input")),
        )

    def _node_finished(self, event: dict[str, Any]) -> None:
        started_at, began, inputs = self._open.pop(
            event["id"], (utc_now(), time.perf_counter(), None)
        )
        error = event.get("error")
        common: dict[str, Any] = {
            "attempt": self._attempt,
            "kind": "node",
            "name": event["name"],
            "started_at": started_at,
            "finished_at": utc_now(),
            "duration_ms": round((time.perf_counter() - began) * 1000, 3),
            "input": inputs,
        }
        if error is None:
            self._store.add_event(self._run_id, status="ok", output=event.get("result"), **common)
        else:
            self.failed_node = event["name"]
            self._store.add_event(
                self._run_id,
                status="error",
                error_type=type(error).__name__,
                error_message=safe_error_message(error),
                **common,
            )
        self.running_node = None
        self.flush_edges()

    def flush_edges(self) -> None:
        for at, edge in self._pending_edges:
            self._store.add_event(
                self._run_id,
                attempt=self._attempt,
                kind="edge",
                name=edge["edge"],
                status="ok",
                started_at=at,
                finished_at=at,
                output={key: edge.get(key) for key in ("decision", "to", "reason")},
            )
        self._pending_edges.clear()


def _execute(
    graph: CompiledStateGraph,
    store: TraceStore,
    run_id: str,
    attempt: int,
    graph_input: dict[str, Any] | None,
) -> AgentResult:
    config: RunnableConfig = {"configurable": {"thread_id": run_id}}
    recorder = _TraceRecorder(store, run_id, attempt)
    try:
        for mode, event in graph.stream(graph_input, config, stream_mode=STREAM_MODES):
            recorder.handle(mode, event)
    except Exception as error:
        recorder.flush_edges()
        node = recorder.failed_node or recorder.running_node or "graph"
        store.finish_run(
            run_id,
            status="failed",
            error_node=node,
            error_type=type(error).__name__,
            error_message=safe_error_message(error),
        )
        logger.error("Agent run %s failed at %s: %s", run_id, node, type(error).__name__)
        raise AgentRunError(
            run_id=run_id, node=node, retryable=isinstance(error, KnowledgeBaseError)
        ) from error

    state = recorder.final_state
    outcome = derive_outcome(state)
    answer = state.get("answer")
    store.finish_run(run_id, status="completed", outcome=outcome, answer=answer)
    logger.info("Agent run %s completed: %s", run_id, outcome)
    return AgentResult(run_id=run_id, outcome=outcome, answer=answer, error=state.get("error"))


def run_agent(
    question: str,
    *,
    user_id: str | None = None,
    graph: CompiledStateGraph | None = None,
    store: TraceStore | None = None,
) -> AgentResult:
    """Run the graph on one question. Raises AgentRunError if a node fails.

    Every call leaves a trace, completed or failed; `run_id` is its key.
    """
    graph = graph if graph is not None else get_agent_graph()
    store = store if store is not None else get_trace_store()
    run_id = str(uuid.uuid4())
    store.start_run(run_id, question=question, user_id=user_id, graph_version=graph_fingerprint())
    return _execute(graph, store, run_id, attempt=1, graph_input={"question": question})


def resume_run(
    run_id: str,
    *,
    graph: CompiledStateGraph | None = None,
    store: TraceStore | None = None,
) -> AgentResult:
    """Continue a failed run from the node that failed, using its checkpoints.

    The nodes that already succeeded are not run again: their results are in
    the last checkpoint. A completed run is returned as recorded.
    """
    graph = graph if graph is not None else get_agent_graph()
    store = store if store is not None else get_trace_store()
    run = store.get_trace(run_id)
    if run is None:
        raise AgentRunNotFound(run_id)
    if run.status == "completed":
        state = graph.get_state({"configurable": {"thread_id": run_id}}).values
        return AgentResult(
            run_id=run_id,
            outcome=cast(Outcome, run.outcome),
            answer=run.answer,
            error=state.get("error"),
        )
    if run.status == "running":
        raise AgentRunNotResumable(f"Run {run_id} is still marked running")
    if run.graph_version != graph_fingerprint():
        raise AgentRunNotResumable(
            f"Run {run_id} was recorded by graph version {run.graph_version}, "
            f"not the current {graph_fingerprint()}"
        )
    snapshot = graph.get_state({"configurable": {"thread_id": run_id}})
    if not snapshot.next:
        raise AgentRunNotResumable(f"Run {run_id} has no checkpoint to resume from")
    attempt = store.start_attempt(run_id)
    return _execute(graph, store, run_id, attempt, graph_input=None)
