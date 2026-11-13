"""The agent's response: the answer the user sees and its memory self-evaluation, in one call.

MEM-092 asks for "the same agent with one extra self-evaluation step", solved
with structured output in a single model call: the response plus a
`memory_proposal` field. `generate_response()` is that call. The generation
model must call one tool, `respond` (`tool_choice="required"`, the mechanism
the planner already uses on this gateway), whose arguments are:

    answer               the reply, from the evidence only (the RAG rules)
    memory_check         propose | nothing_to_remember | already_remembered |
                         must_not_be_remembered
    memory_check_reason  why, in one sentence
    memory_proposal      for `propose`: one fact, with its scope (policy.MemoryDraft)

The model only proposes. Whether the proposal may be shown, and whether it is
ever stored, is decided outside the model: `policy.check_draft()`, the store,
and the user.

The prompt is the agent's own identity - an internal assistant for TrackFlow's
support and commercial staff, which consults the knowledge base as one of its
tools - followed by exactly the knowledge base's answer and business rules
(`rag.ANSWER_RULES`, `rag.BUSINESS_RULES`), the rules for team memory, and the
memory criterion rendered from `policy.py`. `/knowledge/query` keeps using
`rag.generate_answer` and its own prompt, unchanged.

A reply without the tool call, or with malformed arguments, still answers the
user when it carries an answer: the memory step fails closed (no proposal).
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final, Literal

from openai import OpenAIError
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from data.pipelines import rag
from data.process.knowledge_base import KnowledgeBaseError
from services.agent.memory import policy
from services.agent.planner import _inline_refs

logger = logging.getLogger("api.agent")

RESPOND_TOOL: Final = "respond"

MemoryCheck = Literal[
    "propose", "nothing_to_remember", "already_remembered", "must_not_be_remembered"
]

AGENT_IDENTITY: Final = """\
You are TrackFlow's internal operations assistant. Support (CX) agents on Valentina \
Cruz's team in Los Angeles and Zaragoza, and account managers on Miguel Torres's \
commercial team, ask you about TrackFlow's policies, carriers, tickets and stock. You \
answer from what your tools found - TrackFlow's knowledge base, the incident manager, \
the inventory - and from TEAM MEMORY: facts TrackFlow staff confirmed in earlier \
conversations. Be courteous, precise and brief, with exact data, and never promise \
terms that do not exist in TrackFlow's standard agreements.
"""

MEMORY_RULES: Final = """\
Team memory and updates:
- TEAM MEMORY notes ([M1], [M2], ...) were approved by TrackFlow staff on the date \
shown. Use them as evidence, like CONTEXT passages, and in the "Sources:" line name \
each one you used as "team memory — <scope>, approved <date>".
- When a team memory note contradicts a CONTEXT passage, say what the documentation \
states and what the team confirmed on that date. Never merge them silently and never \
drop either.
- When the message is marked USER UPDATE, the user is telling you something, not \
only asking. Acknowledge it as what the user reports; never present it as a confirmed \
TrackFlow fact unless CONTEXT or TEAM MEMORY confirms it.
"""

OUTPUT_RULES: Final = """\
Reply only by calling the respond tool, once: the answer the user sees, and your \
memory self-evaluation. Write the answer in the language the QUESTION is written in - \
an English question gets an English answer, even when it names Spanish places, \
carriers or people.
"""

NO_MEMORY: Final = "(No team memory is relevant to this message.)"
USER_UPDATE_NOTICE: Final = (
    "USER UPDATE\nThe user is telling you an update (a correction, the cause of recurring "
    "incidents, or a client's preference), not only asking a question."
)


def system_prompt() -> str:
    """Built at call time, so a change to the shared rules reaches the agent too."""
    return "\n".join(
        (
            AGENT_IDENTITY,
            rag.ANSWER_RULES,
            rag.BUSINESS_RULES,
            MEMORY_RULES,
            policy.memory_prompt(),
            OUTPUT_RULES,
        )
    )


class AgentReply(BaseModel):
    """Your reply to the user, and your memory self-evaluation of this interaction."""

    model_config = ConfigDict(extra="ignore")

    answer: str = Field(description="The answer the user sees, following every rule above.")
    memory_check: MemoryCheck = Field(
        description="Is there something new or corrected worth remembering? See the criterion."
    )
    memory_check_reason: str = Field(
        default="", max_length=300, description="Why, in one short sentence."
    )
    memory_proposal: policy.MemoryDraft | None = Field(
        default=None, description='Only when memory_check is "propose": the fact to remember.'
    )


def tool_definitions() -> list[ChatCompletionToolParam]:
    return [
        {
            "type": "function",
            "function": {
                "name": RESPOND_TOOL,
                "description": AgentReply.__doc__ or RESPOND_TOOL,
                "parameters": _inline_refs(AgentReply.model_json_schema()),
            },
        }
    ]


def format_memory(number: int, item: dict[str, Any]) -> str:
    approved = str(item.get("approved_at", ""))[:10]
    category = str(item.get("category", "")).replace("_", " ")
    scope = item.get("scope", "")
    header = f"[M{number}] team memory — {category} ({scope}), approved {approved}"
    return f"{header}\n{item.get('content', '').strip()}"


def build_messages(
    question: str,
    passages: list[dict[str, Any]],
    *,
    memories: list[dict[str, Any]],
    user_update: bool,
    today: date | None = None,
) -> list[ChatCompletionMessageParam]:
    team_memory = (
        "\n\n".join(format_memory(number, item) for number, item in enumerate(memories, 1))
        if memories
        else NO_MEMORY
    )
    blocks = [
        f"TODAY\n{(today or datetime.now(UTC).date()).isoformat()}",
        f"CONTEXT\n{rag.format_context(passages)}",
        f"TEAM MEMORY\n{team_memory}",
    ]
    if user_update:
        blocks.append(USER_UPDATE_NOTICE)
    blocks.append(f"QUESTION\n{question.strip()}")
    return [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": "\n\n".join(blocks)},
    ]


@dataclass(frozen=True)
class Response:
    """What `generate_response` returns: the answer, and the self-evaluation for the state."""

    answer: str
    memory_check: str
    memory_check_reason: str
    proposal: policy.MemoryDraft | None
    # False when the model answered without a usable `respond` call.
    structured: bool

    def candidate(self) -> dict[str, Any]:
        """The `memory_candidate` state field."""
        return {
            "memory_check": self.memory_check,
            "reason": self.memory_check_reason,
            "proposal": None if self.proposal is None else self.proposal.model_dump(mode="json"),
            "structured": self.structured,
        }


def _parse(message: Any) -> Response:
    content = (getattr(message, "content", None) or "").strip()
    for call in getattr(message, "tool_calls", None) or []:
        function = getattr(call, "function", None)
        if getattr(function, "name", None) != RESPOND_TOOL:
            continue
        raw = getattr(function, "arguments", None) or "{}"
        try:
            reply = AgentReply.model_validate(json.loads(raw))
        except (ValueError, ValidationError) as error:
            logger.warning("The respond call was malformed: %s", type(error).__name__)
            answer = _salvage_answer(raw) or content
            return Response(answer, "invalid", "the respond call was malformed", None, False)
        proposal = reply.memory_proposal if reply.memory_check == "propose" else None
        check: str = reply.memory_check
        reason = reply.memory_check_reason
        if check == "propose" and proposal is None:
            check, reason = "invalid", "memory_check was propose without a proposal"
        return Response(reply.answer.strip(), check, reason, proposal, True)
    return Response(content, "invalid", "the model answered without the respond tool", None, False)


def _salvage_answer(raw: str) -> str:
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    answer = data.get("answer") if isinstance(data, dict) else None
    return answer.strip() if isinstance(answer, str) else ""


def generate_response(
    question: str,
    passages: list[dict[str, Any]],
    *,
    memories: list[dict[str, Any]] | None = None,
    user_update: bool = False,
) -> Response:
    """Answer `question` from `passages` and `memories`, and self-evaluate, in one call.

    Raises KnowledgeBaseError when the model cannot be reached or gives no
    answer at all (retryable, as for `rag.generate_answer`).
    """
    # Through `rag`, like `rag.generate_answer`: one place to point the agent at a model.
    model, _, _ = rag.get_rag_settings().generation_endpoint()
    try:
        completion = rag._generation_client().chat.completions.create(
            model=model,
            messages=build_messages(
                question, passages, memories=memories or [], user_update=user_update
            ),
            tools=tool_definitions(),
            tool_choice="required",
            temperature=rag.GENERATION_TEMPERATURE,
        )
    except OpenAIError as error:
        logger.error("Generation request failed: %s", type(error).__name__)
        raise KnowledgeBaseError("The generation model could not be reached") from error
    if not completion.choices:
        raise KnowledgeBaseError("The generation model returned an empty answer")
    response = _parse(completion.choices[0].message)
    if not response.answer:
        raise KnowledgeBaseError("The generation model returned an empty answer")
    return response
