"""The knowledge agent graph, offline: structure, nodes, routes, checkpoints, resume and trace.

`rag.retrieve` and `rag.generate_answer` are replaced by fakes, `rag.query` is
made to fail (the graph must never call it), and `offline_rag` makes any real
Qdrant or model client fail loudly. The live behaviour is covered by the
recorded-trace evals in `test_agent_evals.py`.
"""

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START

from data.pipelines import rag
from data.process.knowledge_base import KnowledgeBaseError
from services.agent import graph as agent_graph
from services.agent import nodes, runner
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
    ConditionalEdge,
    NodeSpec,
    build_agent_graph,
    graph_fingerprint,
    route_context,
    route_question,
)
from services.agent.runner import resume_run, run_agent
from services.agent.state import (
    ANSWER_NO_INFORMATION,
    EMPTY_QUESTION,
    GENERATE_ANSWER,
    NO_INFORMATION_ANSWER,
    RECEIVE_QUESTION,
    RETRIEVE,
)
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


class FakeRag:
    """Stands in for `rag.retrieve` and `rag.generate_answer`, recording every call."""

    def __init__(self) -> None:
        self.context: list[dict[str, Any]] = [dict(CHUNK)]
        self.answer = ANSWER
        self.retrieve_error: Exception | None = None
        self.generate_error: Exception | None = None
        self.retrieve_calls: list[str] = []
        self.generate_calls: list[tuple[str, list[dict[str, Any]]]] = []

    def retrieve(self, question: str) -> list[dict[str, Any]]:
        self.retrieve_calls.append(question)
        if self.retrieve_error is not None:
            raise self.retrieve_error
        return [dict(chunk) for chunk in self.context]

    def generate_answer(self, question: str, context: list[dict[str, Any]]) -> str:
        self.generate_calls.append((question, context))
        if self.generate_error is not None:
            raise self.generate_error
        return self.answer


@pytest.fixture
def fake_rag(monkeypatch, offline_rag):  # noqa: F811
    fake = FakeRag()
    monkeypatch.setattr(rag, "retrieve", fake.retrieve)
    monkeypatch.setattr(rag, "generate_answer", fake.generate_answer)

    def monolithic_query(question: str) -> str:
        raise AssertionError("The graph must never call rag.query()")

    monkeypatch.setattr(rag, "query", monolithic_query)
    return fake


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


def test_the_shipped_graph_compiles_with_its_four_nodes():
    drawn = build_agent_graph().get_graph()

    assert set(drawn.nodes) == {
        START,
        RECEIVE_QUESTION,
        RETRIEVE,
        GENERATE_ANSWER,
        ANSWER_NO_INFORMATION,
        END,
    }


def test_the_compiled_graph_has_exactly_the_declared_transitions():
    drawn = build_agent_graph().get_graph()

    assert {(edge.source, edge.target) for edge in drawn.edges} == {
        (START, RECEIVE_QUESTION),
        (RECEIVE_QUESTION, RETRIEVE),
        (RECEIVE_QUESTION, END),
        (RETRIEVE, GENERATE_ANSWER),
        (RETRIEVE, ANSWER_NO_INFORMATION),
        (GENERATE_ANSWER, END),
        (ANSWER_NO_INFORMATION, END),
    }
    conditional = {(e.source, e.target) for e in drawn.edges if e.conditional}
    assert conditional == {
        (RECEIVE_QUESTION, RETRIEVE),
        (RECEIVE_QUESTION, END),
        (RETRIEVE, GENERATE_ANSWER),
        (RETRIEVE, ANSWER_NO_INFORMATION),
    }


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
        {"edges": ((START, RECEIVE_QUESTION), (ANSWER_NO_INFORMATION, END))},
        "'generate_answer' is a dead end",
    ),
    "edge to an unknown node": (
        {"edges": EDGES + ((GENERATE_ANSWER, "summarise"),)},
        "LangGraph rejected the graph.*summarise",
    ),
    "decision to an unknown node": (
        {
            "conditional_edges": (
                CONDITIONAL_EDGES[0],
                ConditionalEdge(
                    RETRIEVE,
                    route_context,
                    {"generate": "generate_v2", "no_information": ANSWER_NO_INFORMATION},
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

    assert update == {"answer": ANSWER}
    assert fake_rag.generate_calls == [("Return window?", [CHUNK])]
    assert fake_rag.retrieve_calls == []


@pytest.mark.parametrize("state", [{"question": "q"}, {"question": "q", "context": []}])
def test_generate_answer_refuses_to_generate_without_context(fake_rag, state):
    with pytest.raises(AgentContractError, match="needs the context"):
        nodes.generate_answer(state)
    assert fake_rag.generate_calls == []


def test_answer_no_information_answers_without_any_model(fake_rag):
    assert nodes.answer_no_information({"question": "q", "context": []}) == {
        "answer": NO_INFORMATION_ANSWER
    }
    assert fake_rag.generate_calls == []


def test_the_routers_decide_from_the_state_alone():
    assert route_question({"question": "q"}) == "retrieve"
    assert route_question({"question": "", "error": EMPTY_QUESTION}) == "rejected"
    assert route_context({"question": "q", "context": [CHUNK]}) == "generate"
    assert route_context({"question": "q", "context": []}) == "no_information"


# --- Routes --------------------------------------------------------------------------------


@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
def test_an_empty_question_ends_the_run_before_retrieval(fake_rag, graph, store, question):
    result = ask(question, graph, store)

    assert result.outcome == "rejected"
    assert result.error == EMPTY_QUESTION
    assert result.answer is None
    assert fake_rag.retrieve_calls == []
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
    assert trace.node_names() == [RECEIVE_QUESTION, RETRIEVE, ANSWER_NO_INFORMATION]
    assert trace.edge_events()[-1].output == {
        "decision": "no_information",
        "to": ANSWER_NO_INFORMATION,
        "reason": f"no chunk scored at least {rag.MIN_SCORE:.2f}",
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
        (1, (RETRIEVE,)),
        (2, (GENERATE_ANSWER,)),
        (3, ()),
    ]
    assert history[0].values["context"] == [CHUNK]
    assert history[0].values["answer"] == ANSWER


def test_checkpoints_and_traces_outlive_the_process(fake_rag):
    result = run_agent("Return window?")
    agent_graph.close_agent_graph()  # as on API shutdown

    reopened = agent_graph.get_agent_graph()

    assert reopened.get_state(config(result.run_id)).values["answer"] == ANSWER
    trace = TraceStore(get_agent_settings().agent_store_path).get_trace(result.run_id)
    assert trace.node_names() == [RECEIVE_QUESTION, RETRIEVE, GENERATE_ANSWER]


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
    assert len(fake_rag.generate_calls) == 2
    resumed = store.get_trace(run_id)
    assert resumed.status == "completed"
    assert resumed.attempts == 2
    assert resumed.error_node is None
    assert [(e.attempt, e.name, e.status) for e in resumed.events if e.kind == "node"] == [
        (1, RECEIVE_QUESTION, "ok"),
        (1, RETRIEVE, "ok"),
        (1, GENERATE_ANSWER, "error"),
        (2, GENERATE_ANSWER, "ok"),
    ]


def test_a_retrieval_failure_stops_the_run_at_retrieve(fake_rag, graph, store):
    fake_rag.retrieve_error = KnowledgeBaseError("The vector database could not be searched")

    with pytest.raises(AgentRunError) as failure:
        ask("Return window?", graph, store)

    assert failure.value.node == RETRIEVE
    assert failure.value.retryable is True
    assert fake_rag.generate_calls == []
    trace = store.get_trace(failure.value.run_id)
    assert trace.node_names() == [RECEIVE_QUESTION, RETRIEVE]
    assert trace.edge_events()[-1].name == "route_question"  # no decision after the failure


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
        (3, "node", RETRIEVE),
        (4, "edge", "route_context"),
        (5, "node", GENERATE_ANSWER),
    ]
    receive, decide_question, retrieved, decide_context, generated = trace.events
    assert receive.input == {"question": "  Return window? "}
    assert receive.output == {"question": "Return window?"}
    assert decide_question.output == {
        "decision": "retrieve",
        "to": RETRIEVE,
        "reason": "the question is not empty",
    }
    assert retrieved.input == {"question": "Return window?"}  # only what the node reads
    assert retrieved.output == {"context": [CHUNK]}
    assert decide_context.output["reason"] == f"1 chunk(s) scored at least {rag.MIN_SCORE:.2f}"
    assert generated.input == {"context": [CHUNK], "question": "Return window?"}
    assert generated.output == {"answer": ANSWER}
    assert all(e.duration_ms >= 0 for e in trace.events if e.kind == "node")


def test_traces_are_queryable_after_the_run(fake_rag, graph, store):
    first = ask("Return window?", graph, store, user_id="user-1")
    second = ask("   ", graph, store, user_id="user-2")

    reopened = TraceStore(store.path)

    assert [run.run_id for run in reopened.list_runs()] == [second.run_id, first.run_id]
    assert [run.run_id for run in reopened.list_runs(user_id="user-1")] == [first.run_id]
    assert reopened.get_trace(first.run_id).node_names() == [
        RECEIVE_QUESTION,
        RETRIEVE,
        GENERATE_ANSWER,
    ]


def test_a_trace_is_only_returned_to_its_owner(fake_rag, graph, store):
    result = ask("Return window?", graph, store, user_id="user-1")

    assert store.get_trace(result.run_id, owner="user-1") is not None
    assert store.get_trace(result.run_id, owner="user-2") is None


def test_the_fingerprint_changes_with_the_prompt_or_the_threshold(monkeypatch):
    shipped = graph_fingerprint()
    assert graph_fingerprint() == shipped

    prompt = rag.SYSTEM_PROMPT
    monkeypatch.setattr(rag, "SYSTEM_PROMPT", prompt + "\nBe brief.")
    assert graph_fingerprint() != shipped

    monkeypatch.setattr(rag, "SYSTEM_PROMPT", prompt)
    monkeypatch.setattr(rag, "MIN_SCORE", 0.48)
    assert graph_fingerprint() != shipped
