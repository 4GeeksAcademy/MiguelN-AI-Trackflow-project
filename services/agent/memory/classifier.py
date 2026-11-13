"""Classify the user's reply to a pending memory proposal, explicitly.

When a proposal is pending, the user's next message is first evaluated against
it - not by looking for "yes" in the text, but with a structured label from the
model (one forced tool call, temperature 0):

    decision        approve | reject | edit | unclear | unrelated
    confidence      0..1
    decision_span   the exact words of the message that express the decision
    edited_content  for an edit, the memory text the user wants instead
    other_request   the exact part of the message that asks or says something else

The model only labels. `decide()` then applies deterministic rules, and
approval is never assumed:

- approve / reject / edit count only with confidence >= MIN_CONFIDENCE and a
  `decision_span` that really is in the message; an edit also needs the new text;
- anything else - `unclear` (the user hesitates), `unrelated` (the user moved
  on), low confidence, a span the message does not contain, a malformed reply
  or an unreachable model - discards the proposal;
- `other_request` must also be in the message; it becomes the question the
  conversation continues with, so "yes, and what's the return window?" is
  resolved and then answered. After a change of topic the whole message
  continues.
"""

import json
import logging
import re
from functools import lru_cache
from typing import Any, Final, Literal

from openai import OpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from data.process import knowledge_base
from data.process.knowledge_base import KnowledgeBaseError
from services.agent.memory import policy
from services.agent.memory.store import ReplyDecision
from services.agent.planner import _inline_refs

logger = logging.getLogger("api.agent.memory")

CLASSIFY_TOOL: Final = "classify_reply"
CLASSIFIER_TEMPERATURE: Final = 0.0
MIN_CONFIDENCE: Final = 0.8

CLASSIFIER_PROMPT: Final = """\
In the previous turn, TrackFlow's assistant asked the user whether to remember a fact \
for next time (the PENDING PROPOSAL). Classify what the user's new MESSAGE says about \
that proposal. You only classify: call classify_reply once.

- approve: the user clearly agrees to remember it as written ("yes", "sure, save it", \
"correct, remember that").
- reject: the user clearly declines ("no", "don't save that", "not needed").
- edit: the user agrees only with a change, or corrects the fact; edited_content is the \
full corrected fact as one sentence, in the language of the PENDING PROPOSAL, written \
from the proposal and the user's correction only.
- unclear: the user responds to the proposal but neither clearly agrees nor declines - \
hesitation such as "maybe", "I'm not sure", "later", "let me check".
- unrelated: the message does not answer the proposal at all - a new question or topic.

decision_span: copy, exactly as written, the words of the MESSAGE that express the \
decision; empty for unrelated. other_request: copy, exactly as written, a part of the \
MESSAGE that asks a new question or gives new information on another subject; empty if \
there is none. A reason or comment about the decision itself ("it was a one-off", "we \
don't need it") belongs to the decision, not to other_request. confidence: how sure you \
are of the decision, from 0 to 1. Never treat silence, hesitation or a change of topic \
as approval.\
"""


class ReplyClassification(BaseModel):
    """Classify the user's message against the pending memory proposal."""

    model_config = ConfigDict(extra="ignore")

    decision: Literal["approve", "reject", "edit", "unclear", "unrelated"]
    confidence: float = Field(ge=0.0, le=1.0)
    decision_span: str = Field(default="", max_length=500)
    edited_content: str | None = Field(default=None, max_length=1000)
    other_request: str | None = Field(default=None, max_length=4000)


def tool_definitions() -> list[ChatCompletionToolParam]:
    return [
        {
            "type": "function",
            "function": {
                "name": CLASSIFY_TOOL,
                "description": ReplyClassification.__doc__ or CLASSIFY_TOOL,
                "parameters": _inline_refs(ReplyClassification.model_json_schema()),
            },
        }
    ]


def build_messages(message: str, proposal: dict[str, Any]) -> list[ChatCompletionMessageParam]:
    pending = f'"{proposal["content"]}"'
    if proposal.get("replaces_content"):
        pending += f' (it would replace: "{proposal["replaces_content"]}")'
    return [
        {"role": "system", "content": CLASSIFIER_PROMPT},
        {"role": "user", "content": f"PENDING PROPOSAL\n{pending}\n\nMESSAGE\n{message}"},
    ]


@lru_cache(maxsize=1)
def _classifier_client() -> OpenAI:
    # The generation endpoint and its timeout and retries, as the planner's.
    settings = knowledge_base.get_rag_settings()
    _, base_url, api_key = settings.generation_endpoint()
    return OpenAI(
        api_key=api_key, base_url=base_url, timeout=settings.llm_timeout_seconds, max_retries=2
    )


def _comparable(text: str) -> str:
    """Case-, accent-, quote- and spacing-insensitive, without edge punctuation."""
    normalised = policy.normalise_text(text.replace("’", "'").replace("‘", "'"))
    return normalised.strip(" .,;:!?¡¿\"'()-")


def in_message(message: str, span: str | None) -> bool:
    """True when `span` (non-empty) is part of `message`, ignoring case and spacing."""
    needle = _comparable(span or "")
    return bool(needle) and needle in _comparable(message)


def call_classifier(message: str, proposal: dict[str, Any]) -> ReplyClassification:
    """One structured model call. Raises on a model failure or a malformed reply."""
    model, _, _ = knowledge_base.get_rag_settings().generation_endpoint()
    response = _classifier_client().chat.completions.create(
        model=model,
        messages=build_messages(message, proposal),
        tools=tool_definitions(),
        tool_choice="required",
        temperature=CLASSIFIER_TEMPERATURE,
    )
    calls = response.choices[0].message.tool_calls if response.choices else None
    for call in calls or []:
        function = getattr(call, "function", None)
        if getattr(function, "name", None) == CLASSIFY_TOOL:
            return ReplyClassification.model_validate(
                json.loads(getattr(function, "arguments", None) or "{}")
            )
    raise ValueError("the model did not call classify_reply")


def decide(
    message: str, classification: ReplyClassification | None, *, failure: str | None = None
) -> tuple[ReplyDecision, str]:
    """(the decision on the proposal, the question the conversation continues with).

    Deterministic: the same classification always gives the same decision.
    """
    if classification is None:
        why = f"the reply could not be classified ({failure or 'no classification'}): discarded"
        return ReplyDecision("discard", why, label="unavailable"), message.strip()

    label = classification.decision
    confidence = classification.confidence
    other = (classification.other_request or "").strip()
    remaining = other if other and in_message(message, other) else ""
    if other and not remaining:
        # The model's "other request" is not the user's words: keep the whole message.
        remaining = message.strip()

    if label == "unrelated":
        return (
            ReplyDecision(
                "discard",
                "the message does not answer the proposal (change of topic): discarded",
                label=label,
                confidence=confidence,
            ),
            message.strip(),
        )
    if label == "unclear":
        return (
            ReplyDecision(
                "discard",
                "the user did not clearly agree or decline: discarded",
                label=label,
                confidence=confidence,
            ),
            remaining,
        )
    if confidence < MIN_CONFIDENCE:
        why = f"{label} with confidence {confidence:.2f} < {MIN_CONFIDENCE}: discarded"
        return ReplyDecision("discard", why, label=label, confidence=confidence), remaining
    if not in_message(message, classification.decision_span):
        why = f"{label}, but its decision span is not in the message: discarded"
        return ReplyDecision("discard", why, label=label, confidence=confidence), remaining
    span = re.sub(r"\s+", " ", classification.decision_span.strip())
    if label == "edit":
        edited = (classification.edited_content or "").strip()
        if not edited:
            why = "edit without the corrected text: discarded"
            return ReplyDecision("discard", why, label=label, confidence=confidence), remaining
        return (
            ReplyDecision(
                "edit",
                f'the user asked for a change ("{span}")',
                label=label,
                confidence=confidence,
                edited_content=edited,
            ),
            remaining,
        )
    verb = "approved" if label == "approve" else "rejected"
    return (
        ReplyDecision(
            "approve" if label == "approve" else "reject",
            f'the user {verb} it ("{span}")',
            label=label,
            confidence=confidence,
        ),
        remaining,
    )


def classify_reply(message: str, proposal: dict[str, Any]) -> dict[str, Any]:
    """The `memory_reply` state field: the label, the decision and what remains. Never raises."""
    classification: ReplyClassification | None = None
    failure: str | None = None
    try:
        classification = call_classifier(message, proposal)
    except (OpenAIError, KnowledgeBaseError) as error:
        failure = f"model unavailable: {type(error).__name__}"
        logger.warning("Memory reply classifier unavailable: %s", type(error).__name__)
    except (ValueError, ValidationError) as error:  # json.JSONDecodeError is a ValueError
        failure = f"malformed classification: {type(error).__name__}"
    decision, remaining = decide(message, classification, failure=failure)
    return {
        # The whole message, as the user wrote it: the decision is logged against it.
        "message": message,
        "classification": None
        if classification is None
        else classification.model_dump(mode="json"),
        "decision": {
            "outcome": decision.outcome,
            "reason": decision.reason,
            "label": decision.label,
            "confidence": decision.confidence,
            "edited_content": decision.edited_content,
        },
        "remaining_question": remaining,
    }


def decision_from_state(reply: dict[str, Any]) -> ReplyDecision:
    raw = reply["decision"]
    return ReplyDecision(
        outcome=raw["outcome"],
        reason=raw["reason"],
        label=raw.get("label"),
        confidence=raw.get("confidence"),
        edited_content=raw.get("edited_content"),
    )
