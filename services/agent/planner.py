"""Source planning: which sources a question needs, decided by the model.

The generation model is shown the three tools - `lookup_ticket`,
`lookup_stock`, `search_knowledge_base` - with JSON schemas generated from
their typed contracts, and must call at least one (`tool_choice="required"`).
It only *chooses*: the graph runs each chosen source as its own node.

What the model returns is checked before anything runs, and every call that is
dropped is recorded with its reason in `Plan.ignored`:

- an unknown tool, arguments that are not JSON or break the contract, or a
  second call to the same tool;
- a ticket reference that does not appear in the question, so the model cannot
  invent a ticket to look up.

The surviving steps run in `SOURCE_ORDER` (live data first). With no usable
step, or when the model cannot be reached, the plan falls back to the
knowledge base alone - the Part 1 behaviour - with the reason recorded. The
planner never raises for a model failure.
"""

import inspect
import json
import re
from collections.abc import Iterable
from functools import lru_cache
from typing import Any, Final, Literal

from openai import OpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from trackflow_shared.incidents import normalize_reference

from data.process.knowledge_base import KnowledgeBaseError, get_rag_settings
from services.agent.state import INCIDENT_MANAGER, INVENTORY_MANAGER, KNOWLEDGE_BASE, SOURCE_ORDER
from services.agent.tools.contracts import StockLookupInput, TicketLookupInput

TICKET_TOOL: Final = "lookup_ticket"
STOCK_TOOL: Final = "lookup_stock"
KNOWLEDGE_TOOL: Final = "search_knowledge_base"

PLANNER_TEMPERATURE: Final = 0.0

PLANNER_PROMPT: Final = """\
You route questions from TrackFlow's support and commercial staff to the sources \
that can answer them. You never answer the question yourself: you only call tools.

- lookup_ticket: the live state of support tickets (incidents) in the incident \
manager - a ticket's status, category, dates or details, or which tickets match a \
status, category, origin or branch.
- lookup_stock: the live stock of a product in the inventory manager - whether \
there is stock and how many units, optionally in one warehouse.
- search_knowledge_base: TrackFlow's documented knowledge - policies, procedures, \
services, prices, carriers, delivery times, contacts and who is responsible for what.

Call every tool the question needs, and only those: a question about a ticket's \
status and about a policy needs both lookup_ticket and search_knowledge_base. Live \
data such as a ticket's status or current stock is never in the knowledge base, and \
policies or procedures are never in the incident or inventory manager. Copy ticket \
references and product names exactly as the user wrote them, and never invent a \
reference the question does not contain. If no tool fits, call search_knowledge_base.\
"""


class KnowledgeSearchInput(BaseModel):
    """Search TrackFlow's knowledge base with the user's question: policies,
    procedures, services, prices, carriers, delivery times and contacts."""

    # The `retrieve` node searches with the question itself, the way the
    # threshold was calibrated, so any argument the model adds is dropped.
    model_config = ConfigDict(extra="ignore")


Source = Literal["incident_manager", "inventory_manager", "knowledge_base"]

TOOL_INPUTS: Final[dict[str, type[BaseModel]]] = {
    TICKET_TOOL: TicketLookupInput,
    STOCK_TOOL: StockLookupInput,
    KNOWLEDGE_TOOL: KnowledgeSearchInput,
}
SOURCE_OF_TOOL: Final[dict[str, Source]] = {
    TICKET_TOOL: INCIDENT_MANAGER,
    STOCK_TOOL: INVENTORY_MANAGER,
    KNOWLEDGE_TOOL: KNOWLEDGE_BASE,
}
TOOL_OF_SOURCE: Final = {source: tool for tool, source in SOURCE_OF_TOOL.items()}


class PlanStep(BaseModel):
    source: Source
    tool: str
    # The validated contract input, JSON-mode, without unset fields.
    arguments: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    # `model`: the model's choice, validated. `fallback`: the knowledge base alone.
    decided_by: Literal["model", "fallback"]
    steps: list[PlanStep] = Field(min_length=1)
    # Every tool call that was dropped, with its reason.
    ignored: list[str] = Field(default_factory=list)


# --- Tool schemas ------------------------------------------------------------------


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema with every `$ref` replaced by its definition, and titles dropped.

    Pydantic puts enums in `$defs`; not every model behind the gateway resolves
    references, so the schema the model sees is self-contained.
    """
    definitions = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = resolve(definitions[node["$ref"].rsplit("/", 1)[-1]])
                return {**target, **{k: resolve(v) for k, v in node.items() if k != "$ref"}}
            return {
                key: resolve(value)
                for key, value in node.items()
                if key != "$defs" and not (key == "title" and isinstance(value, str))
            }
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    return resolved


def tool_definitions() -> list[ChatCompletionToolParam]:
    """The tools as the chat API takes them: name, description, JSON schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": inspect.cleandoc(model.__doc__ or name),
                "parameters": _inline_refs(model.model_json_schema()),
            },
        }
        for name, model in TOOL_INPUTS.items()
    ]


def build_planner_messages(question: str) -> list[ChatCompletionMessageParam]:
    return [
        {"role": "system", "content": PLANNER_PROMPT},
        {"role": "user", "content": question},
    ]


# --- Validating the model's choice -------------------------------------------------

# A reference in the question: 'TRF-000482', 'TRF 482' or a bare number.
_REFERENCE_MENTION: Final = re.compile(r"TRF[\s-]*\d+|\d+", re.IGNORECASE)


def mentions_reference(question: str, reference: str) -> bool:
    """True when `reference` is in the question, in any of its written forms."""
    canonical = normalize_reference(reference)
    if canonical is None:
        return reference.casefold() in question.casefold()
    return any(normalize_reference(m) == canonical for m in _REFERENCE_MENTION.findall(question))


def _why_invalid(error: ValueError) -> str:
    if isinstance(error, ValidationError):
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "arguments"
        return f"{where}: {first['msg']}"
    return "arguments are not valid JSON"


def _knowledge_step() -> PlanStep:
    return PlanStep(source=KNOWLEDGE_BASE, tool=KNOWLEDGE_TOOL)


def fallback_plan(reasons: Iterable[str]) -> Plan:
    return Plan(decided_by="fallback", steps=[_knowledge_step()], ignored=list(reasons))


def plan_from_calls(question: str, calls: Iterable[Any]) -> Plan:
    """The plan the model's tool calls describe, after validating each one."""
    steps: dict[str, PlanStep] = {}
    ignored: list[str] = []
    for call in calls:
        function = getattr(call, "function", None)
        name = getattr(function, "name", None)
        if not isinstance(name, str) or name not in TOOL_INPUTS:
            ignored.append(f"unknown tool {name!r}")
            continue
        source = SOURCE_OF_TOOL[name]
        if source in steps:
            ignored.append(f"{name}: called again; only the first call is used")
            continue
        try:
            arguments = TOOL_INPUTS[name].model_validate(
                json.loads(getattr(function, "arguments", None) or "{}")
            )
        except ValueError as error:  # json.JSONDecodeError and ValidationError
            ignored.append(f"{name}: {_why_invalid(error)}")
            continue
        reference = getattr(arguments, "reference", None)
        if reference is not None and not mentions_reference(question, reference):
            ignored.append(f"{name}: reference {reference!r} does not appear in the question")
            continue
        steps[source] = PlanStep(
            source=source,
            tool=name,
            arguments=arguments.model_dump(mode="json", exclude_none=True),
        )

    if not steps:
        return fallback_plan([*ignored, "no usable source was chosen"])
    return Plan(
        decided_by="model",
        steps=[steps[source] for source in SOURCE_ORDER if source in steps],
        ignored=ignored,
    )


# --- The model call ----------------------------------------------------------------


@lru_cache(maxsize=1)
def _planner_client() -> OpenAI:
    # The generation endpoint and its timeout and retries, as `rag._generation_client`.
    settings = get_rag_settings()
    _, base_url, api_key = settings.generation_endpoint()
    return OpenAI(
        api_key=api_key, base_url=base_url, timeout=settings.llm_timeout_seconds, max_retries=2
    )


def choose_sources(question: str) -> dict[str, Any]:
    """The plan for `question`, JSON-mode. Falls back to the knowledge base, never raises."""
    try:
        model, _, _ = get_rag_settings().generation_endpoint()
        response = _planner_client().chat.completions.create(
            model=model,
            messages=build_planner_messages(question),
            tools=tool_definitions(),
            tool_choice="required",
            temperature=PLANNER_TEMPERATURE,
        )
    except (OpenAIError, KnowledgeBaseError) as error:
        plan = fallback_plan([f"the planner model is unavailable ({type(error).__name__})"])
        return plan.model_dump(mode="json")

    calls = response.choices[0].message.tool_calls if response.choices else None
    return plan_from_calls(question, calls or []).model_dump(mode="json")
