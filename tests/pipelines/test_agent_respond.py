"""The agent's response (MEM-092): the answer and the memory self-evaluation in one
structured call, under exactly the knowledge base's rules."""

import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest
from openai import APITimeoutError

from data.pipelines import rag
from data.process.knowledge_base import KnowledgeBaseError
from services.agent import respond
from services.agent.memory import policy
from tests.pipelines.rag_support import TEST_GENERATION_MODEL, offline_rag  # noqa: F401

CHUNK = {
    "source_document": "carrier-coverage",
    "section": "Carrier Coverage › Spain",
    "text": "SEUR: best coverage in rural areas of Aragón.",
    "score": 0.61,
}
MEMORY = {
    "memory_id": "m-1",
    "category": "carrier_rule",
    "scope": "SEUR, ES",
    "content": "SEUR no longer covers rural Zaragoza; the local carrier is used there.",
    "approved_at": "2026-11-12T12:00:00.000+00:00",
}
PROPOSAL = {
    "category": "carrier_rule",
    "content": "SEUR no longer covers rural Zaragoza; the local carrier is used there.",
    "reason": "A carrier changed its coverage for a zone.",
    "carrier": "SEUR",
    "country": "ES",
}


class FakeGenerationModel:
    def __init__(self, message: Any = None, *, error: Exception | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self._message = message
        self._error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request: Any) -> Any:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(choices=[SimpleNamespace(message=self._message)])


def tool_reply(arguments: Any) -> Any:
    text = arguments if isinstance(arguments, str) else json.dumps(arguments)
    call = SimpleNamespace(function=SimpleNamespace(name="respond", arguments=text))
    return SimpleNamespace(content=None, tool_calls=[call])


@pytest.fixture
def model(monkeypatch, offline_rag):  # noqa: F811
    def install(message: Any = None, *, error: Exception | None = None) -> FakeGenerationModel:
        fake = FakeGenerationModel(message, error=error)
        monkeypatch.setattr(rag, "_generation_client", lambda: fake)
        return fake

    return install


def test_the_knowledge_base_prompt_is_unchanged_by_the_split():
    # /knowledge/query must keep the prompt it was evaluated with, byte for byte.
    digest = hashlib.sha256(rag.SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert digest == "979d12d4cfe2859c3ca2309347aeddc4618fc3433322f30925fcd6a3790f4001"


def test_the_agent_answers_by_the_same_rules_with_its_own_identity():
    prompt = respond.system_prompt()

    assert rag.ANSWER_RULES in prompt
    assert rag.BUSINESS_RULES in prompt
    assert policy.memory_prompt() in prompt
    assert rag.IDENTITY not in prompt, "the agent is not the commercial Q&A assistant"
    assert "Valentina Cruz" in respond.AGENT_IDENTITY


def test_one_call_returns_the_answer_and_the_self_evaluation(model):
    fake = model(
        tool_reply(
            {
                "answer": "The documentation lists SEUR for rural Aragón.",
                "memory_check": "propose",
                "memory_check_reason": "The user corrected a carrier's coverage.",
                "memory_proposal": PROPOSAL,
            }
        )
    )

    response = respond.generate_response(
        "Actually SEUR no longer covers that rural area of Zaragoza.",
        [CHUNK],
        memories=[MEMORY],
        user_update=True,
    )

    (request,) = fake.requests
    assert request["model"] == TEST_GENERATION_MODEL
    assert request["tool_choice"] == "required"
    assert request["temperature"] == rag.GENERATION_TEMPERATURE
    assert [tool["function"]["name"] for tool in request["tools"]] == ["respond"]
    system, user = request["messages"]
    assert system == {"role": "system", "content": respond.system_prompt()}
    assert "CONTEXT\n[1] carrier-coverage — Carrier Coverage › Spain" in user["content"]
    assert (
        "TEAM MEMORY\n[M1] team memory — carrier rule (SEUR, ES), approved 2026-11-12"
        in (user["content"])
    )
    assert respond.USER_UPDATE_NOTICE in user["content"]
    assert response.answer == "The documentation lists SEUR for rural Aragón."
    assert response.structured is True
    assert response.candidate() == {
        "memory_check": "propose",
        "reason": "The user corrected a carrier's coverage.",
        "proposal": {
            **PROPOSAL,
            "route_or_area": None,
            "client": None,
            "valid_until": None,
        },
        "structured": True,
    }


def test_without_memories_or_an_update_the_prompt_says_so(model):
    fake = model(tool_reply({"answer": "30 days.", "memory_check": "nothing_to_remember"}))

    respond.generate_response("Return window?", [CHUNK])

    content = fake.requests[0]["messages"][1]["content"]
    assert f"TEAM MEMORY\n{respond.NO_MEMORY}" in content
    assert "USER UPDATE" not in content


def test_a_proposal_without_propose_is_dropped(model):
    model(
        tool_reply(
            {
                "answer": "30 days.",
                "memory_check": "nothing_to_remember",
                "memory_proposal": PROPOSAL,
            }
        )
    )

    response = respond.generate_response("Return window?", [CHUNK])

    assert response.proposal is None
    assert response.memory_check == "nothing_to_remember"


def test_propose_without_a_proposal_is_invalid(model):
    model(tool_reply({"answer": "Noted.", "memory_check": "propose"}))

    response = respond.generate_response("q", [CHUNK])

    assert (response.memory_check, response.proposal) == ("invalid", None)


def test_a_plain_answer_without_the_tool_still_answers_and_proposes_nothing(model):
    model(SimpleNamespace(content="The standard return window is 30 days.", tool_calls=None))

    response = respond.generate_response("Return window?", [CHUNK])

    assert response.answer == "The standard return window is 30 days."
    assert (response.memory_check, response.proposal, response.structured) == (
        "invalid",
        None,
        False,
    )


def test_malformed_arguments_keep_the_answer_and_drop_the_memory_step(model):
    model(tool_reply({"answer": "30 days.", "memory_check": "remember everything"}))

    response = respond.generate_response("Return window?", [CHUNK])

    assert response.answer == "30 days."
    assert response.proposal is None
    assert response.structured is False


@pytest.mark.parametrize(
    "message",
    [
        SimpleNamespace(content="", tool_calls=None),
        tool_reply({"answer": "   ", "memory_check": "nothing_to_remember"}),
        tool_reply("not json at all"),
    ],
    ids=["empty content", "blank answer", "garbage"],
)
def test_no_answer_at_all_is_a_retryable_failure(model, message):
    model(message)

    with pytest.raises(KnowledgeBaseError, match="empty answer"):
        respond.generate_response("q", [CHUNK])


def test_an_unreachable_model_is_a_retryable_failure(model):
    model(error=APITimeoutError(request=None))  # type: ignore[arg-type]

    with pytest.raises(KnowledgeBaseError, match="could not be reached"):
        respond.generate_response("q", [CHUNK])
