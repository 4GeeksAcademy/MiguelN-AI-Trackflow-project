"""The reply classifier (MEM-092): an explicit label from the model, then rules that
never assume approval from silence, ambiguity or a change of topic."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from openai import APITimeoutError

from services.agent.memory import classifier
from services.agent.memory.classifier import ReplyClassification, decide, in_message
from tests.pipelines.rag_support import TEST_GENERATION_MODEL, offline_rag  # noqa: F401

PROPOSAL = {
    "proposal_id": "p-1",
    "content": "SEUR no longer covers rural Zaragoza; the local carrier is used there.",
    "replaces_content": None,
}


def label(decision: Any, confidence: float = 0.95, span: str = "", **extra: Any):
    return ReplyClassification(
        decision=decision, confidence=confidence, decision_span=span, **extra
    )


# --- The rules -----------------------------------------------------------------------


def test_a_clear_yes_approves():
    decision, rest = decide("Yes, remember it.", label("approve", span="Yes, remember it"))

    assert (decision.outcome, decision.label, decision.confidence) == ("approve", "approve", 0.95)
    assert decision.reason == 'the user approved it ("Yes, remember it")'
    assert rest == ""


def test_a_clear_no_rejects():
    decision, rest = decide("No, don't save that.", label("reject", span="No, don't save that"))

    assert decision.outcome == "reject"
    assert rest == ""


def test_an_answer_and_another_question_resolves_and_continues():
    message = "Yes, and what's the standard return window?"

    decision, rest = decide(
        message, label("approve", span="Yes", other_request="what's the standard return window?")
    )

    assert decision.outcome == "approve"
    assert rest == "what's the standard return window?"


def test_a_change_of_topic_discards_and_the_whole_message_goes_on():
    message = "What's the SLA for Black Friday?"

    decision, rest = decide(message, label("unrelated", confidence=0.9))

    assert decision.outcome == "discard"
    assert "change of topic" in decision.reason
    assert rest == message


def test_a_hesitant_reply_is_unclear_and_discarded_without_going_on():
    decision, rest = decide(
        "Hmm, I'm not sure, maybe later.", label("unclear", 0.9, span="maybe later")
    )

    assert decision.outcome == "discard"
    assert decision.label == "unclear"
    assert rest == "", "a hesitation asks nothing else"


@pytest.mark.parametrize("confidence", [0.0, 0.5, 0.79])
def test_low_confidence_is_never_approval(confidence):
    decision, _ = decide("hmm, maybe", label("approve", confidence, span="maybe"))

    assert decision.outcome == "discard"
    assert f"{confidence:.2f} < 0.8" in decision.reason


def test_a_decision_span_the_user_never_wrote_is_discarded():
    decision, _ = decide("ok then", label("approve", span="yes please save it"))

    assert decision.outcome == "discard"
    assert "not in the message" in decision.reason


def test_an_edit_needs_the_corrected_text():
    without, _ = decide("yes but change it", label("edit", span="change it"))
    edited, _ = decide(
        "yes but say since October",
        label("edit", span="yes but say since October", edited_content="... since October."),
    )

    assert without.outcome == "discard"
    assert (edited.outcome, edited.edited_content) == ("edit", "... since October.")


def test_another_request_the_user_never_wrote_keeps_the_whole_message():
    message = "Sure. Also, which carrier covers Huesca?"

    _, rest = decide(message, label("approve", span="Sure", other_request="Huesca carrier list"))

    assert rest == message


def test_no_classification_discards():
    decision, rest = decide("yes", None, failure="model unavailable: APITimeoutError")

    assert (decision.outcome, decision.label) == ("discard", "unavailable")
    assert rest == "yes"


def test_spans_are_matched_ignoring_case_spacing_quotes_and_edge_punctuation():
    assert in_message("YES,   please — remember it!", "yes, please")
    assert in_message("Don’t save that", "Don't save that")
    assert not in_message("yes", "")
    assert not in_message("yes", "no")


# --- The model call ------------------------------------------------------------------


class FakeClassifierModel:
    def __init__(self, reply: Any = None, *, error: Exception | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self._reply = reply
        self._error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request: Any) -> Any:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        calls = [
            SimpleNamespace(function=SimpleNamespace(name="classify_reply", arguments=self._reply))
        ]
        message = SimpleNamespace(content=None, tool_calls=calls)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def model(monkeypatch, offline_rag):  # noqa: F811
    def install(reply: Any = None, *, error: Exception | None = None) -> FakeClassifierModel:
        fake = FakeClassifierModel(reply, error=error)
        monkeypatch.setattr(classifier, "_classifier_client", lambda: fake)
        return fake

    return install


def test_the_classifier_is_one_forced_structured_call(model):
    fake = model(json.dumps({"decision": "approve", "confidence": 0.93, "decision_span": "yes"}))

    reply = classifier.classify_reply("yes", PROPOSAL)

    (request,) = fake.requests
    assert request["model"] == TEST_GENERATION_MODEL
    assert request["tool_choice"] == "required"
    assert request["temperature"] == 0
    assert [tool["function"]["name"] for tool in request["tools"]] == ["classify_reply"]
    assert request["messages"][0] == {"role": "system", "content": classifier.CLASSIFIER_PROMPT}
    assert PROPOSAL["content"] in request["messages"][1]["content"]
    assert reply == {
        "message": "yes",
        "classification": {
            "decision": "approve",
            "confidence": 0.93,
            "decision_span": "yes",
            "edited_content": None,
            "other_request": None,
        },
        "decision": {
            "outcome": "approve",
            "reason": 'the user approved it ("yes")',
            "label": "approve",
            "confidence": 0.93,
            "edited_content": None,
        },
        "remaining_question": "",
    }


def test_an_unreachable_model_discards_and_never_raises(model):
    model(error=APITimeoutError(request=None))  # type: ignore[arg-type]

    reply = classifier.classify_reply("yes", PROPOSAL)

    assert reply["decision"]["outcome"] == "discard"
    assert reply["classification"] is None
    assert reply["remaining_question"] == "yes"


@pytest.mark.parametrize("arguments", ["not json", json.dumps({"decision": "maybe"})])
def test_a_malformed_classification_discards(model, arguments):
    model(arguments)

    reply = classifier.classify_reply("yes", PROPOSAL)

    assert reply["decision"]["outcome"] == "discard"
    assert "malformed classification" in reply["decision"]["reason"]


def test_an_update_proposal_shows_the_classifier_what_it_replaces(model):
    fake = model(json.dumps({"decision": "reject", "confidence": 0.9, "decision_span": "no"}))

    classifier.classify_reply("no", {**PROPOSAL, "replaces_content": "SEUR covers rural Aragón."})

    assert "it would replace" in fake.requests[0]["messages"][1]["content"]
