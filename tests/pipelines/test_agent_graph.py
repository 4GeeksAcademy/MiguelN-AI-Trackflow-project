"""The knowledge agent graph, offline: structure, nodes, routes, checkpoints, resume and trace.

`rag.retrieve`, the agent's generation call (`respond.generate_response`), the
planner and both tools are replaced by fakes, `rag.query` and
`rag.generate_answer` are made to fail (the graph must call neither), and
`offline_rag` makes any real Qdrant or model client fail loudly. By default the
fake planner plans the knowledge base alone, the Part 1 route. The live
behaviour is covered by the recorded-trace evals in `test_agent_evals.py`.
"""

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START

from data.pipelines import rag
from data.process.knowledge_base import KnowledgeBaseError
from services.agent import graph as agent_graph
from services.agent import nodes, planner, respond, runner
from services.agent.config import get_agent_settings
from services.agent.errors import (
    AgentContractError,
    AgentGraphBuildError,
    AgentRunError,
    AgentRunNotFound,
    AgentRunNotResumable,
)
from services.agent.graph import (
    CONDITIONAL_EDGES,
    EDGES,
    NODES,
    RETRIEVE_PATHS,
    ConditionalEdge,
    NodeSpec,
    build_agent_graph,
    graph_fingerprint,
    route_after_plan,
    route_after_retrieve,
    route_after_stock,
    route_after_ticket,
    route_question,
)
from services.agent.runner import resume_run, run_agent
from services.agent.state import (
    ANSWER_LOOKUP_FAILED,
    ANSWER_NO_INFORMATION,
    CLASSIFY_MEMORY_REPLY,
    COMPOSE_RESPONSE,
    EMPTY_QUESTION,
    GENERATE_ANSWER,
    INCIDENTS_UNAVAILABLE_ANSWER,
    LOOKUP_STOCK,
    LOOKUP_TICKET,
    NO_INFORMATION_ANSWER,
    PLAN_SOURCES,
    PROPOSE_MEMORY,
    RECALL_MEMORY,
    RECEIVE_QUESTION,
    RESOLVE_MEMORY_PROPOSAL,
    RETRIEVE,
    STOCK_NOT_FOUND_ANSWER,
    STOCK_UNAVAILABLE_ANSWER,
    TICKET_NOT_FOUND_ANSWER,
    TICKET_UNAVAILABLE_ANSWER,
    derive_outcome,
)
from services.agent.tools import mcp_lookups
from services.agent.tools.contracts import (
    StockLookupInput,
    StockLookupResult,
    TicketLookupInput,
    TicketLookupResult,
)
from services.agent.tools.passages import stock_passages, ticket_passages
from services.agent.tracing import TraceStore
from tests.pipelines.rag_support import offline_rag  # noqa: F401  (fixture)

CHUNK: dict[str, Any] = {
    "company": "trackflow",
    "source_document": "returns-policy",
    "section": "Returns Policy › Standard return window",
    "language": "en",
    "chunk_index": 1,
    "text": "Standard return window: 30 days from delivery.",
    "score": 0.754,
}
ANSWER = "The standard return window is 30 days from delivery."

TICKET_RECORD: dict[str, Any] = {
    "id": "6c7f3b0e-5a1d-4a8e-9a43-0f5f0e7d2a11",
    "reference": "TRF-000012",
    "title": "Wrong postcode assigned during label generation",
    "description": "The label shows the wrong postcode.",
    "category": "delivery_failure",
    "origin": "customer",
    "branch": "central",
    "status": "in_progress",
    "created_at": "2026-10-20T04:21:51Z",
    "updated_at": "2026-10-21T09:00:00Z",
}
PRODUCT_RECORD: dict[str, Any] = {
    "id": "395dfc8e-bb1e-5f97-b0d5-ea06f9af7b0e",
    "name": "Black Running Shoes - Size 42",
    "sku": "SHOE-BLK-42",
    "warehouse": "Monterrey",
    "current_stock": 45,
}


def step(source: str, **arguments: Any) -> dict[str, Any]:
    return {"source": source, "tool": planner.TOOL_OF_SOURCE[source], "arguments": arguments}


def plan(*steps: dict[str, Any], decided_by: str = "model", ignored=()) -> dict[str, Any]:
    return {"decided_by": decided_by, "steps": list(steps), "ignored": list(ignored)}


KNOWLEDGE_PLAN = plan(step("knowledge_base"))

# What `recall_memory` returns while the memory is empty (every test starts so).
EMPTY_RECALL: dict[str, Any] = {
    "outcome": "ok",
    "items": [],
    "reason": "the memory holds no active entries",
}

# The self-evaluation the fake generation returns by default.
NOTHING_TO_REMEMBER: dict[str, Any] = {
    "memory_check": "nothing_to_remember",
    "reason": "fake self-evaluation",
    "proposal": None,
    "structured": True,
}


def ticket_result(
    outcome: str = "ok", *, reference: str | None = "12", failure: str | None = None
) -> TicketLookupResult:
    """A ticket lookup's result as `mcp_lookups.lookup_ticket` returns it."""
    request = (
        TicketLookupInput(reference=reference) if reference else TicketLookupInput(status="open")
    )
    found = outcome == "ok"
    return TicketLookupResult.model_validate(
        {
            "request": request,
            "outcome": outcome,
            "endpoint": "MCP get_incident(reference=TRF-000012)",
            "reference": "TRF-000012" if reference == "12" else reference,
            "tickets": [TICKET_RECORD] if found else [],
            "total": 1 if found else 0,
            "failure": failure,
            "http_status": None if failure in {"timeout", "unreachable"} else 200,
            "duration_ms": 3.0,
        }
    )


def stock_result(
    outcome: str = "ok", *, product: str = "black running shoes", failure: str | None = None
) -> StockLookupResult:
    found = outcome == "ok"
    return StockLookupResult.model_validate(
        {
            "request": StockLookupInput(product=product),
            "outcome": outcome,
            "endpoint": "MCP query_inventory(operation=list_products, name=black running shoes)",
            "products": [PRODUCT_RECORD] if found else [],
            "total": 1 if found else 0,
            "failure": failure,
            "http_status": 200,
            "duration_ms": 4.0,
        }
    )


class FakeRag:
    """Stands in for `rag.retrieve`, `rag.generate_answer`, the planner and both tools,
    recording every call."""

    def __init__(self) -> None:
        self.context: list[dict[str, Any]] = [dict(CHUNK)]
        self.answer = ANSWER
        self.retrieve_error: Exception | None = None
        self.generate_error: Exception | None = None
        self.retrieve_calls: list[str] = []
        self.generate_calls: list[tuple[str, list[dict[str, Any]]]] = []
        self.plan: dict[str, Any] = KNOWLEDGE_PLAN
        self.ticket: TicketLookupResult = ticket_result()
        self.stock: StockLookupResult = stock_result()
        self.plan_calls: list[str] = []
        self.ticket_calls: list[TicketLookupInput] = []
        self.stock_calls: list[StockLookupInput] = []
        # The memory self-evaluation the fake generation returns.
        self.memory_check = "nothing_to_remember"
        self.proposal: Any = None
        self.generate_memories: list[list[dict[str, Any]]] = []

    def choose_sources(self, question: str) -> dict[str, Any]:
        self.plan_calls.append(question)
        return dict(self.plan)

    def lookup_ticket(self, request: TicketLookupInput, **_: Any) -> TicketLookupResult:
        self.ticket_calls.append(request)
        return self.ticket

    def lookup_stock(self, request: StockLookupInput, **_: Any) -> StockLookupResult:
        self.stock_calls.append(request)
        return self.stock

    def retrieve(self, question: str) -> list[dict[str, Any]]:
        self.retrieve_calls.append(question)
        if self.retrieve_error is not None:
            raise self.retrieve_error
        return [dict(chunk) for chunk in self.context]

    def generate_response(
        self,
        question: str,
        context: list[dict[str, Any]],
        *,
        memories: list[dict[str, Any]] | None = None,
        user_update: bool = False,
    ) -> respond.Response:
        self.generate_calls.append((question, context))
        self.generate_memories.append(list(memories or []))
        if self.generate_error is not None:
            raise self.generate_error
        return respond.Response(
            self.answer, self.memory_check, "fake self-evaluation", self.proposal, True
        )


def install_fakes(monkeypatch, fake: FakeRag) -> FakeRag:
    """Route every external call the graph makes to `fake`."""
    monkeypatch.setattr(rag, "retrieve", fake.retrieve)
    monkeypatch.setattr(respond, "generate_response", fake.generate_response)

    def rag_generation(*_: Any, **__: Any) -> str:
        raise AssertionError("The agent answers through respond.generate_response")

    monkeypatch.setattr(rag, "generate_answer", rag_generation)
    monkeypatch.setattr(planner, "choose_sources", fake.choose_sources)
    monkeypatch.setattr(mcp_lookups, "lookup_ticket", fake.lookup_ticket)
    monkeypatch.setattr(mcp_lookups, "lookup_stock", fake.lookup_stock)

    def monolithic_query(question: str) -> str:
        raise AssertionError("The graph must never call rag.query()")

    monkeypatch.setattr(rag, "query", monolithic_query)
    return fake


@pytest.fixture
def fake_rag(monkeypatch, offline_rag):  # noqa: F811
    return install_fakes(monkeypatch, FakeRag())


@pytest.fixture
def store(tmp_path) -> TraceStore:
    return TraceStore(tmp_path / "traces.sqlite")


@pytest.fixture
def graph():
    return build_agent_graph(InMemorySaver())


def ask(question: str, graph, store, **kwargs: Any) -> runner.AgentResult:
    return run_agent(question, graph=graph, store=store, **kwargs)


def config(run_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": run_id}}


def with_node(spec: NodeSpec) -> tuple[NodeSpec, ...]:
    """The shipped nodes with `spec` replacing the node of the same name (or added)."""
    return tuple(s for s in NODES if s.name != spec.name) + (spec,)


# --- Compilation -----------------------------------------------------------------------


def test_the_shipped_graph_compiles_with_its_thirteen_nodes():
    drawn = build_agent_graph().get_graph()

    assert set(drawn.nodes) == {
        START,
        RECEIVE_QUESTION,
        CLASSIFY_MEMORY_REPLY,
        RESOLVE_MEMORY_PROPOSAL,
        RECALL_MEMORY,
        PLAN_SOURCES,
        LOOKUP_TICKET,
        LOOKUP_STOCK,
        RETRIEVE,
        GENERATE_ANSWER,
        PROPOSE_MEMORY,
        ANSWER_LOOKUP_FAILED,
        ANSWER_NO_INFORMATION,
        COMPOSE_RESPONSE,
        END,
    }


DECISIONS = {
    (RECEIVE_QUESTION, RECALL_MEMORY),
    (RECEIVE_QUESTION, CLASSIFY_MEMORY_REPLY),
    (RECEIVE_QUESTION, END),
    (RESOLVE_MEMORY_PROPOSAL, RECALL_MEMORY),
    (RESOLVE_MEMORY_PROPOSAL, COMPOSE_RESPONSE),
    (PLAN_SOURCES, LOOKUP_TICKET),
    (PLAN_SOURCES, LOOKUP_STOCK),
    (PLAN_SOURCES, RETRIEVE),
    (LOOKUP_TICKET, LOOKUP_STOCK),
    (LOOKUP_TICKET, RETRIEVE),
    (LOOKUP_TICKET, GENERATE_ANSWER),
    (LOOKUP_TICKET, ANSWER_LOOKUP_FAILED),
    (LOOKUP_STOCK, RETRIEVE),
    (LOOKUP_STOCK, GENERATE_ANSWER),
    (LOOKUP_STOCK, ANSWER_LOOKUP_FAILED),
    (RETRIEVE, GENERATE_ANSWER),
    (RETRIEVE, ANSWER_LOOKUP_FAILED),
    (RETRIEVE, ANSWER_NO_INFORMATION),
    (GENERATE_ANSWER, PROPOSE_MEMORY),
    (GENERATE_ANSWER, COMPOSE_RESPONSE),
}


def test_the_compiled_graph_has_exactly_the_declared_transitions():
    drawn = build_agent_graph().get_graph()

    assert {(edge.source, edge.target) for edge in drawn.edges} == DECISIONS | {
        (START, RECEIVE_QUESTION),
        (CLASSIFY_MEMORY_REPLY, RESOLVE_MEMORY_PROPOSAL),
        (RECALL_MEMORY, PLAN_SOURCES),
        (PROPOSE_MEMORY, COMPOSE_RESPONSE),
        (ANSWER_LOOKUP_FAILED, COMPOSE_RESPONSE),
        (ANSWER_NO_INFORMATION, COMPOSE_RESPONSE),
        (COMPOSE_RESPONSE, END),
    }
    conditional = {(e.source, e.target) for e in drawn.edges if e.conditional}
    assert conditional == DECISIONS


def test_each_tool_is_its_own_node_reached_only_through_a_decision():
    tool_nodes = {LOOKUP_TICKET, LOOKUP_STOCK}
    by_name = {spec.name: spec for spec in NODES}

    assert {spec.writes for name, spec in by_name.items() if name in tool_nodes} == {
        frozenset({"ticket_lookup"}),
        frozenset({"stock_lookup"}),
    }
    assert not any(target in tool_nodes for _, target in EDGES)


def test_the_application_graph_is_checkpointed_in_the_agent_store():
    compiled = agent_graph.get_agent_graph()

    assert isinstance(compiled.checkpointer, SqliteSaver)
    assert get_agent_settings().agent_store_path.exists()
    assert agent_graph.get_agent_graph() is compiled  # compiled once, then reused


def _orphan() -> NodeSpec:
    return NodeSpec("summarise", nodes.answer_no_information, frozenset(), frozenset({"answer"}))


BROKEN_GRAPHS = {
    "orphan node": (
        {"node_specs": NODES + (_orphan(),)},
        "'summarise' is not connected",
    ),
    "dead-end node": (
        {"edges": tuple(edge for edge in EDGES if edge != (PROPOSE_MEMORY, COMPOSE_RESPONSE))},
        "'propose_memory' is a dead end",
    ),
    "edge to an unknown node": (
        {"edges": EDGES + ((GENERATE_ANSWER, "summarise"),)},
        "LangGraph rejected the graph.*summarise",
    ),
    "decision to an unknown node": (
        {
            "conditional_edges": tuple(c for c in CONDITIONAL_EDGES if c.source != RETRIEVE)
            + (
                ConditionalEdge(
                    RETRIEVE,
                    route_after_retrieve,
                    {**RETRIEVE_PATHS, "generate": "generate_v2"},
                ),
            )
        },
        "LangGraph rejected the graph.*generate_v2",
    ),
    "no entry point": (
        {"edges": ((GENERATE_ANSWER, END), (ANSWER_NO_INFORMATION, END))},
        "LangGraph rejected the graph",
    ),
    "a field the state does not have": (
        {
            "node_specs": with_node(
                NodeSpec(RETRIEVE, nodes.retrieve, frozenset({"question"}), frozenset({"chunks"}))
            )
        },
        r"'retrieve' declares \['chunks'\], which AgentState does not define",
    ),
    "a node named like a state field": (
        {
            "node_specs": NODES
            + (NodeSpec("answer", nodes.answer_no_information, frozenset(), frozenset({"answer"})),)
        },
        "'answer' has the name of a state field",
    ),
    "a node declared twice": (
        {"node_specs": NODES + (NODES[0],)},
        "'receive_question' is declared twice",
    ),
}


@pytest.mark.parametrize(("overrides", "message"), BROKEN_GRAPHS.values(), ids=BROKEN_GRAPHS)
def test_a_structural_error_fails_compilation_with_a_clear_message(overrides, message):
    with pytest.raises(AgentGraphBuildError, match=message):
        build_agent_graph(InMemorySaver(), **overrides)


def test_a_node_writing_an_undeclared_field_fails_the_run_instead_of_being_dropped(fake_rag, store):
    def leaky_retrieve(state):
        return {"context": [CHUNK], "chunks": [CHUNK]}

    leaky = build_agent_graph(
        InMemorySaver(),
        node_specs=with_node(
            NodeSpec(RETRIEVE, leaky_retrieve, frozenset({"question"}), frozenset({"context"}))
        ),
    )

    with pytest.raises(AgentRunError) as failure:
        ask("Return window?", leaky, store)

    assert failure.value.node == RETRIEVE
    assert failure.value.retryable is False
    assert isinstance(failure.value.__cause__, AgentContractError)
    trace = store.get_trace(failure.value.run_id)
    assert trace.status == "failed"
    assert trace.error_type == "AgentContractError"
    assert "['chunks']" in trace.node_events(RETRIEVE)[0].error_message


# --- Nodes on their own ------------------------------------------------------------------


@pytest.mark.parametrize("question", ["", "   ", "\n\t ", None, 42])
def test_receive_question_refuses_an_empty_or_non_text_question(question):
    assert nodes.receive_question({"question": question}) == {
        "question": "",
        "error": EMPTY_QUESTION,
    }


def test_receive_question_strips_the_question():
    assert nodes.receive_question({"question": "  Return window?\n"}) == {
        "question": "Return window?"
    }


def test_retrieve_calls_the_rag_retrieve_once_with_the_question(fake_rag):
    assert nodes.retrieve({"question": "Return window?"}) == {"context": [CHUNK]}
    assert fake_rag.retrieve_calls == ["Return window?"]


def test_generate_answer_uses_the_context_it_is_given(fake_rag):
    update = nodes.generate_answer({"question": "Return window?", "context": [CHUNK]})

    assert update == {"answer": ANSWER, "memory_candidate": NOTHING_TO_REMEMBER}
    assert fake_rag.generate_calls == [("Return window?", [CHUNK])]
    assert fake_rag.retrieve_calls == []


@pytest.mark.parametrize(
    "state",
    [
        {"question": "q"},
        {"question": "q", "context": []},
        {"question": "q", "context": [], "ticket_lookup": "not_found"},
    ],
)
def test_generate_answer_refuses_to_generate_without_evidence(fake_rag, state):
    if state.get("ticket_lookup"):
        state["ticket_lookup"] = ticket_result("not_found", reference="482").model_dump(mode="json")
    with pytest.raises(AgentContractError, match="needs evidence"):
        nodes.generate_answer(state)
    assert fake_rag.generate_calls == []


def test_answer_no_information_answers_without_any_model(fake_rag):
    assert nodes.answer_no_information({"question": "q", "context": []}) == {
        "answer": NO_INFORMATION_ANSWER
    }
    assert fake_rag.generate_calls == []


def _dumped(result) -> dict[str, Any]:
    return result.model_dump(mode="json")


def test_the_routers_decide_from_the_state_alone():
    both = plan(step("incident_manager", reference="12"), step("knowledge_base"))
    found = _dumped(ticket_result())
    gone = _dumped(ticket_result("not_found", reference="482"))

    assert route_question({"question": "q"}) == "plan"
    assert route_question({"question": "", "error": EMPTY_QUESTION}) == "rejected"
    assert route_after_plan({"question": "q", "plan": KNOWLEDGE_PLAN}) == "knowledge_base"
    assert route_after_plan({"question": "q", "plan": both}) == "incident_manager"
    assert route_after_ticket({"question": "q", "plan": both, "ticket_lookup": gone}) == (
        "knowledge_base"
    )
    ticket_only = plan(step("incident_manager", reference="12"))
    assert route_after_ticket({"question": "q", "plan": ticket_only, "ticket_lookup": found}) == (
        "generate"
    )
    assert route_after_ticket({"question": "q", "plan": ticket_only, "ticket_lookup": gone}) == (
        "lookup_failed"
    )
    stock_only = plan(step("inventory_manager", product="shoes"))
    assert route_after_stock(
        {"question": "q", "plan": stock_only, "stock_lookup": _dumped(stock_result("not_found"))}
    ) == ("lookup_failed")
    assert route_after_retrieve({"question": "q", "context": [CHUNK]}) == "generate"
    assert route_after_retrieve({"question": "q", "context": []}) == "no_information"
    assert route_after_retrieve(
        {"question": "q", "plan": both, "ticket_lookup": gone, "context": [CHUNK]}
    ) == ("generate")
    assert route_after_retrieve(
        {"question": "q", "plan": both, "ticket_lookup": gone, "context": []}
    ) == ("lookup_failed")


def test_the_outcome_counts_a_successful_lookup_as_an_answer():
    found = _dumped(ticket_result())
    gone = _dumped(ticket_result("unavailable", failure="timeout"))

    assert derive_outcome({"question": "q", "ticket_lookup": found, "answer": "a"}) == "answered"
    assert derive_outcome({"question": "q", "ticket_lookup": gone, "answer": "a"}) == (
        "no_information"
    )
    assert derive_outcome({"question": "", "error": EMPTY_QUESTION}) == "rejected"


# --- Routes --------------------------------------------------------------------------------


@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
def test_an_empty_question_ends_the_run_before_retrieval(fake_rag, graph, store, question):
    result = ask(question, graph, store)

    assert result.outcome == "rejected"
    assert result.error == EMPTY_QUESTION
    assert result.answer is None
    assert fake_rag.retrieve_calls == []
    assert fake_rag.plan_calls == [], "an empty question is never planned"
    trace = store.get_trace(result.run_id)
    assert trace.node_names() == [RECEIVE_QUESTION]
    (decision,) = trace.edge_events()
    assert decision.output["to"] == END


def test_no_context_routes_to_the_honest_answer_without_generating(fake_rag, graph, store):
    fake_rag.context = []

    result = ask("Who won the last football World Cup?", graph, store)

    assert result.outcome == "no_information"
    assert result.answer == NO_INFORMATION_ANSWER
    assert fake_rag.generate_calls == []
    trace = store.get_trace(result.run_id)
    assert trace.node_names() == [
        RECEIVE_QUESTION,
        RECALL_MEMORY,
        PLAN_SOURCES,
        RETRIEVE,
        ANSWER_NO_INFORMATION,
        COMPOSE_RESPONSE,
    ]
    assert trace.edge_events()[-1].output == {
        "decision": "no_information",
        "to": ANSWER_NO_INFORMATION,
        "reason": f"no evidence: 0 chunk(s) scored at least {rag.MIN_SCORE:.2f}",
    }


def test_generation_gets_exactly_the_context_retrieve_produced(fake_rag, graph, store):
    result = ask("  What's the standard return window?  ", graph, store)

    assert result.outcome == "answered"
    assert result.answer == ANSWER
    assert fake_rag.retrieve_calls == ["What's the standard return window?"]
    assert fake_rag.generate_calls == [("What's the standard return window?", [CHUNK])]


# --- Checkpoints and resume ------------------------------------------------------------


def test_every_transition_is_checkpointed(fake_rag, graph, store):
    result = ask("Return window?", graph, store)

    history = list(graph.get_state_history(config(result.run_id)))
    steps = [(snapshot.metadata["step"], snapshot.next) for snapshot in reversed(history)]
    assert steps == [
        (-1, (START,)),
        (0, (RECEIVE_QUESTION,)),
        (1, (RECALL_MEMORY,)),
        (2, (PLAN_SOURCES,)),
        (3, (RETRIEVE,)),
        (4, (GENERATE_ANSWER,)),
        (5, (COMPOSE_RESPONSE,)),
        (6, ()),
    ]
    assert history[0].values["context"] == [CHUNK]
    assert history[0].values["answer"] == ANSWER


def test_checkpoints_and_traces_outlive_the_process(fake_rag):
    result = run_agent("Return window?")
    agent_graph.close_agent_graph()  # as on API shutdown

    reopened = agent_graph.get_agent_graph()

    assert reopened.get_state(config(result.run_id)).values["answer"] == ANSWER
    trace = TraceStore(get_agent_settings().agent_store_path).get_trace(result.run_id)
    assert trace.node_names() == [
        RECEIVE_QUESTION,
        RECALL_MEMORY,
        PLAN_SOURCES,
        RETRIEVE,
        GENERATE_ANSWER,
        COMPOSE_RESPONSE,
    ]


def test_a_failed_run_resumes_from_the_failed_node_without_retrieving_again(fake_rag, graph, store):
    fake_rag.generate_error = KnowledgeBaseError("The generation model could not be reached")
    with pytest.raises(AgentRunError) as failure:
        ask("Return window?", graph, store)

    run_id = failure.value.run_id
    assert failure.value.node == GENERATE_ANSWER
    assert failure.value.retryable is True
    failed = store.get_trace(run_id)
    assert failed.status == "failed"
    assert failed.error_node == GENERATE_ANSWER
    assert failed.error_message == "The generation model could not be reached"
    assert graph.get_state(config(run_id)).next == (GENERATE_ANSWER,)

    fake_rag.generate_error = None
    result = resume_run(run_id, graph=graph, store=store)

    assert result.outcome == "answered"
    assert result.answer == ANSWER
    assert fake_rag.retrieve_calls == ["Return window?"]  # retrieval was not repeated
    assert fake_rag.plan_calls == ["Return window?"]  # nor was planning
    assert len(fake_rag.generate_calls) == 2
    resumed = store.get_trace(run_id)
    assert resumed.status == "completed"
    assert resumed.attempts == 2
    assert resumed.error_node is None
    assert [(e.attempt, e.name, e.status) for e in resumed.events if e.kind == "node"] == [
        (1, RECEIVE_QUESTION, "ok"),
        (1, RECALL_MEMORY, "ok"),
        (1, PLAN_SOURCES, "ok"),
        (1, RETRIEVE, "ok"),
        (1, GENERATE_ANSWER, "error"),
        (2, GENERATE_ANSWER, "ok"),
        (2, COMPOSE_RESPONSE, "ok"),
    ]


def test_a_retrieval_failure_stops_the_run_at_retrieve(fake_rag, graph, store):
    fake_rag.retrieve_error = KnowledgeBaseError("The vector database could not be searched")

    with pytest.raises(AgentRunError) as failure:
        ask("Return window?", graph, store)

    assert failure.value.node == RETRIEVE
    assert failure.value.retryable is True
    assert fake_rag.generate_calls == []
    trace = store.get_trace(failure.value.run_id)
    assert trace.node_names() == [RECEIVE_QUESTION, RECALL_MEMORY, PLAN_SOURCES, RETRIEVE]
    assert trace.edge_events()[-1].name == "route_after_plan"  # no decision after the failure


def test_an_unexpected_error_keeps_only_its_class_in_the_trace(fake_rag, graph, store):
    fake_rag.generate_error = RuntimeError("internal detail at http://10.0.0.5:6333?key=secret")

    with pytest.raises(AgentRunError) as failure:
        ask("Return window?", graph, store)

    assert failure.value.retryable is False
    trace = store.get_trace(failure.value.run_id)
    assert trace.error_type == "RuntimeError"
    assert trace.error_message is None
    assert trace.node_events(GENERATE_ANSWER)[0].error_message is None
    assert "secret" not in failure.value.public_message


def test_resuming_a_completed_run_returns_it_as_recorded(fake_rag, graph, store):
    result = ask("Return window?", graph, store)

    again = resume_run(result.run_id, graph=graph, store=store)

    assert again == result
    assert len(fake_rag.generate_calls) == 1


def test_resume_refuses_unknown_and_other_version_runs(fake_rag, graph, store, monkeypatch):
    with pytest.raises(AgentRunNotFound):
        resume_run("00000000-0000-0000-0000-000000000000", graph=graph, store=store)

    fake_rag.generate_error = KnowledgeBaseError("down")
    with pytest.raises(AgentRunError) as failure:
        ask("Return window?", graph, store)
    monkeypatch.setattr(runner, "graph_fingerprint", lambda: "another-version")

    with pytest.raises(AgentRunNotResumable, match="graph version"):
        resume_run(failure.value.run_id, graph=graph, store=store)


# --- Trace ----------------------------------------------------------------------------------


def test_the_trace_records_nodes_and_decisions_in_order(fake_rag, graph, store):
    result = ask("  Return window? ", graph, store, user_id="user-1")

    trace = store.get_trace(result.run_id)
    assert trace.status == "completed"
    assert trace.outcome == "answered"
    assert trace.question == "  Return window? "
    assert trace.answer == ANSWER
    assert trace.user_id == "user-1"
    assert trace.graph_version == graph_fingerprint()
    assert [(e.seq, e.kind, e.name) for e in trace.events] == [
        (1, "node", RECEIVE_QUESTION),
        (2, "edge", "route_question"),
        (3, "node", RECALL_MEMORY),
        (4, "node", PLAN_SOURCES),
        (5, "edge", "route_after_plan"),
        (6, "node", RETRIEVE),
        (7, "edge", "route_after_retrieve"),
        (8, "node", GENERATE_ANSWER),
        (9, "edge", "route_after_answer"),
        (10, "node", COMPOSE_RESPONSE),
    ]
    (
        receive,
        decide_question,
        recalled,
        planned,
        decide_plan,
        retrieved,
        decide_answer,
        generated,
        decide_memory,
        composed,
    ) = trace.events
    assert receive.input == {"question": "  Return window? "}
    assert receive.output == {"question": "Return window?"}
    assert decide_question.output == {
        "decision": "plan",
        "to": RECALL_MEMORY,
        "reason": "the question is not empty",
    }
    assert recalled.output == {"memory_recall": EMPTY_RECALL}
    assert planned.input == {"question": "Return window?"}
    assert planned.output == {"plan": KNOWLEDGE_PLAN}
    assert decide_plan.output == {
        "decision": "knowledge_base",
        "to": RETRIEVE,
        "reason": "planner chose knowledge_base (search_knowledge_base)",
    }
    assert retrieved.input == {"question": "Return window?"}  # only what the node reads
    assert retrieved.output == {"context": [CHUNK]}
    assert decide_answer.output["reason"] == (
        f"evidence to answer from: 1 chunk(s) scored at least {rag.MIN_SCORE:.2f}"
    )
    assert generated.input == {
        "context": [CHUNK],
        "memory_recall": EMPTY_RECALL,
        "plan": KNOWLEDGE_PLAN,
        "question": "Return window?",
    }
    assert trace.sources() == ["knowledge_base"]
    assert generated.output == {"answer": ANSWER, "memory_candidate": NOTHING_TO_REMEMBER}
    assert decide_memory.output == {
        "decision": "respond",
        "to": COMPOSE_RESPONSE,
        "reason": "self-evaluation: nothing_to_remember: fake self-evaluation",
    }
    assert composed.output == {"answer": ANSWER}
    assert all(e.duration_ms >= 0 for e in trace.events if e.kind == "node")


def test_traces_are_queryable_after_the_run(fake_rag, graph, store):
    first = ask("Return window?", graph, store, user_id="user-1")
    second = ask("   ", graph, store, user_id="user-2")

    reopened = TraceStore(store.path)

    assert [run.run_id for run in reopened.list_runs()] == [second.run_id, first.run_id]
    assert [run.run_id for run in reopened.list_runs(user_id="user-1")] == [first.run_id]
    assert reopened.get_trace(first.run_id).node_names() == [
        RECEIVE_QUESTION,
        RECALL_MEMORY,
        PLAN_SOURCES,
        RETRIEVE,
        GENERATE_ANSWER,
        COMPOSE_RESPONSE,
    ]


def test_a_trace_is_only_returned_to_its_owner(fake_rag, graph, store):
    result = ask("Return window?", graph, store, user_id="user-1")

    assert store.get_trace(result.run_id, owner="user-1") is not None
    assert store.get_trace(result.run_id, owner="user-2") is None


def test_the_fingerprint_changes_with_the_prompt_or_the_threshold(monkeypatch):
    shipped = graph_fingerprint()
    assert graph_fingerprint() == shipped

    prompt = rag.BUSINESS_RULES
    monkeypatch.setattr(rag, "BUSINESS_RULES", prompt + "\nBe brief.")
    assert graph_fingerprint() != shipped

    monkeypatch.setattr(rag, "BUSINESS_RULES", prompt)
    monkeypatch.setattr(rag, "MIN_SCORE", 0.48)
    assert graph_fingerprint() != shipped


# --- Tool routes -----------------------------------------------------------------------


def test_a_ticket_question_is_answered_from_the_live_ticket_without_the_rag(fake_rag, graph, store):
    fake_rag.plan = plan(step("incident_manager", reference="12"))

    result = ask("What's the status of ticket 12?", graph, store)

    assert result.outcome == "answered"
    assert result.answer == ANSWER
    assert fake_rag.retrieve_calls == [], "the knowledge base was not searched"
    assert fake_rag.ticket_calls == [TicketLookupInput(reference="12")]
    assert fake_rag.generate_calls == [
        ("What's the status of ticket 12?", ticket_passages(fake_rag.ticket))
    ]
    trace = store.get_trace(result.run_id)
    assert trace.node_names() == [
        RECEIVE_QUESTION,
        RECALL_MEMORY,
        PLAN_SOURCES,
        LOOKUP_TICKET,
        GENERATE_ANSWER,
        COMPOSE_RESPONSE,
    ]
    assert trace.sources() == ["incident_manager"]
    lookup = trace.node_events(LOOKUP_TICKET)[0]
    assert lookup.input == {"plan": fake_rag.plan}
    assert lookup.output["ticket_lookup"]["tickets"][0]["status"] == "in_progress"


@pytest.mark.parametrize(
    ("result", "answer"),
    [
        (
            ticket_result("not_found", reference="482"),
            TICKET_NOT_FOUND_ANSWER.format(reference="482"),
        ),
        (
            ticket_result("unavailable", failure="timeout"),
            TICKET_UNAVAILABLE_ANSWER.format(reference="TRF-000012"),
        ),
        (
            ticket_result("unavailable", reference=None, failure="unreachable"),
            INCIDENTS_UNAVAILABLE_ANSWER,
        ),
    ],
    ids=["not found", "timeout", "search unreachable"],
)
def test_a_failed_ticket_lookup_gets_an_honest_fixed_answer(fake_rag, graph, store, result, answer):
    fake_rag.plan = plan(step("incident_manager", reference="12"))
    fake_rag.ticket = result

    outcome = ask("What's the status of ticket 12?", graph, store)

    assert outcome.answer == answer
    assert outcome.outcome == "no_information"
    assert fake_rag.generate_calls == [], "no model writes a status it does not have"
    trace = store.get_trace(outcome.run_id)
    assert trace.node_names() == [
        RECEIVE_QUESTION,
        RECALL_MEMORY,
        PLAN_SOURCES,
        LOOKUP_TICKET,
        ANSWER_LOOKUP_FAILED,
        COMPOSE_RESPONSE,
    ]
    decision = trace.edge_events()[-1].output
    assert decision["decision"] == "lookup_failed"
    assert decision["reason"].startswith("no evidence and a lookup failed: incident_manager")


def test_a_question_needing_both_consults_the_ticket_then_the_knowledge_base(
    fake_rag, graph, store
):
    fake_rag.plan = plan(step("incident_manager", reference="12"), step("knowledge_base"))

    result = ask("Ticket 12: status and our lost parcel policy?", graph, store)

    assert result.outcome == "answered"
    assert fake_rag.generate_calls == [
        (
            "Ticket 12: status and our lost parcel policy?",
            ticket_passages(fake_rag.ticket) + [CHUNK],
        )
    ]
    trace = store.get_trace(result.run_id)
    assert trace.node_names() == [
        RECEIVE_QUESTION,
        RECALL_MEMORY,
        PLAN_SOURCES,
        LOOKUP_TICKET,
        RETRIEVE,
        GENERATE_ANSWER,
        COMPOSE_RESPONSE,
    ]
    assert trace.sources() == ["incident_manager", "knowledge_base"]
    after_ticket = [e for e in trace.edge_events() if e.name == "route_after_ticket"][0]
    assert after_ticket.output == {
        "decision": "knowledge_base",
        "to": RETRIEVE,
        "reason": "the plan still needs knowledge_base",
    }


def test_a_failed_lookup_is_stated_and_the_rest_still_answered(fake_rag, graph, store):
    fake_rag.plan = plan(step("incident_manager", reference="12"), step("knowledge_base"))
    fake_rag.ticket = ticket_result("unavailable", failure="timeout")

    result = ask("Ticket 12: status and our lost parcel policy?", graph, store)

    notice = TICKET_UNAVAILABLE_ANSWER.format(reference="TRF-000012")
    assert result.outcome == "answered"
    assert result.answer == f"{notice}\n\n{ANSWER}"
    ((_, passages),) = fake_rag.generate_calls
    assert passages == [CHUNK], "the failed lookup never reaches the model"


def test_a_failed_lookup_with_nothing_retrieved_gets_the_fixed_answer(fake_rag, graph, store):
    fake_rag.plan = plan(step("incident_manager", reference="482"), step("knowledge_base"))
    fake_rag.ticket = ticket_result("not_found", reference="482")
    fake_rag.context = []

    result = ask("Ticket 482 and the return policy?", graph, store)

    assert result.answer == TICKET_NOT_FOUND_ANSWER.format(reference="482")
    assert store.get_trace(result.run_id).node_names()[-2:] == [
        ANSWER_LOOKUP_FAILED,
        COMPOSE_RESPONSE,
    ]


def test_a_stock_question_is_answered_from_the_live_inventory(fake_rag, graph, store):
    fake_rag.plan = plan(step("inventory_manager", product="black running shoes"))

    result = ask("Do we have stock of the black running shoes?", graph, store)

    assert result.outcome == "answered"
    assert fake_rag.stock_calls == [StockLookupInput(product="black running shoes")]
    ((_, passages),) = fake_rag.generate_calls
    assert passages == stock_passages(fake_rag.stock)
    assert "Current stock: 45 units" in passages[0]["text"]
    trace = store.get_trace(result.run_id)
    assert trace.node_names() == [
        RECEIVE_QUESTION,
        RECALL_MEMORY,
        PLAN_SOURCES,
        LOOKUP_STOCK,
        GENERATE_ANSWER,
        COMPOSE_RESPONSE,
    ]
    assert trace.sources() == ["inventory_manager"]


@pytest.mark.parametrize(
    ("result", "answer"),
    [
        (stock_result("not_found", product="red umbrella"), STOCK_NOT_FOUND_ANSWER),
        (
            stock_result("unavailable", product="red umbrella", failure="timeout"),
            STOCK_UNAVAILABLE_ANSWER,
        ),
    ],
    ids=["not found", "unavailable"],
)
def test_a_failed_stock_lookup_gets_an_honest_fixed_answer(fake_rag, graph, store, result, answer):
    fake_rag.plan = plan(step("inventory_manager", product="red umbrella"))
    fake_rag.stock = result

    outcome = ask("Do we have red umbrellas?", graph, store)

    assert outcome.answer == answer.format(product="red umbrella")
    assert fake_rag.generate_calls == []


def test_every_planned_source_runs_once_in_the_fixed_order(fake_rag, graph, store):
    fake_rag.plan = plan(
        step("incident_manager", reference="12"),
        step("inventory_manager", product="shoes"),
        step("knowledge_base"),
    )

    result = ask("Ticket 12, shoe stock and the return policy?", graph, store)

    trace = store.get_trace(result.run_id)
    assert trace.node_names() == [
        RECEIVE_QUESTION,
        RECALL_MEMORY,
        PLAN_SOURCES,
        LOOKUP_TICKET,
        LOOKUP_STOCK,
        RETRIEVE,
        GENERATE_ANSWER,
        COMPOSE_RESPONSE,
    ]
    assert trace.sources() == ["incident_manager", "inventory_manager", "knowledge_base"]
    assert (
        len(fake_rag.ticket_calls),
        len(fake_rag.stock_calls),
        len(fake_rag.retrieve_calls),
    ) == (
        1,
        1,
        1,
    )


def test_a_planner_fallback_takes_the_knowledge_base_route_and_says_why(fake_rag, graph, store):
    fake_rag.plan = plan(
        step("knowledge_base"),
        decided_by="fallback",
        ignored=["the planner model is unavailable (APITimeoutError)"],
    )

    result = ask("What's the standard return window?", graph, store)

    assert result.outcome == "answered"
    decision = store.get_trace(result.run_id).edge_events()[1].output
    assert decision["to"] == RETRIEVE
    assert decision["reason"] == (
        "planner fell back to knowledge_base (search_knowledge_base): "
        "the planner model is unavailable (APITimeoutError)"
    )


def test_a_lookup_node_refuses_to_run_without_its_planned_step(fake_rag):
    with pytest.raises(AgentContractError, match="no 'incident_manager' step"):
        nodes.lookup_ticket_mcp({"question": "q", "plan": KNOWLEDGE_PLAN})
    assert fake_rag.ticket_calls == []


def test_the_fallback_node_refuses_to_run_when_nothing_failed(fake_rag):
    with pytest.raises(AgentContractError, match="no lookup failed"):
        nodes.answer_lookup_failed({"question": "q", "ticket_lookup": _dumped(ticket_result())})


def test_a_tool_bug_still_fails_the_run_instead_of_hiding_it(fake_rag, graph, store, monkeypatch):
    def broken_lookup(request, **_):
        raise TypeError("a programming error, not a service failure")

    monkeypatch.setattr(mcp_lookups, "lookup_ticket", broken_lookup)
    fake_rag.plan = plan(step("incident_manager", reference="12"))

    with pytest.raises(AgentRunError) as failure:
        ask("What's the status of ticket 12?", graph, store)

    assert failure.value.node == LOOKUP_TICKET
    assert failure.value.retryable is False
