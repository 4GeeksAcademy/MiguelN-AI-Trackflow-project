"""Errors raised while building or running the knowledge agent."""


class AgentGraphBuildError(RuntimeError):
    """The graph is structurally wrong and was not compiled.

    Raised by `build_agent_graph()` before any run: an unconnected node, an
    edge to an unknown node, a node contract naming a field the state does
    not have. The message names the culprit.
    """


class AgentContractError(RuntimeError):
    """A node broke its declared contract while running.

    For example, it returned a state field outside its declared writes, which
    LangGraph would otherwise drop without a word, or the generation node was
    reached without context.
    """


class AgentRunNotFound(LookupError):
    """No run with that id is recorded in the trace store."""


class AgentRunNotResumable(RuntimeError):
    """The run cannot be resumed: it is still running, was recorded by another graph
    version, or has no checkpoint left to resume from."""


class AgentRunError(RuntimeError):
    """A run stopped at a node. The trace and the checkpoints keep where.

    `retryable` is true when the failure was the knowledge base being
    unavailable (Qdrant, a model, a missing setting): the same run can be
    resumed from the failed node once it is back.
    """

    def __init__(self, *, run_id: str, node: str, retryable: bool) -> None:
        self.run_id = run_id
        self.node = node
        self.retryable = retryable
        super().__init__(f"Agent run {run_id} failed at node '{node}'")

    @property
    def public_message(self) -> str:
        """What a caller may be told: the step that failed, never the exception text."""
        if self.retryable:
            return (
                f"The knowledge agent could not complete the request: the '{self.node}' step "
                "is unavailable right now. Please try again in a moment."
            )
        return (
            f"The knowledge agent could not complete the request: the '{self.node}' step "
            "failed unexpectedly."
        )
