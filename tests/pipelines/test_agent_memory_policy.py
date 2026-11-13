"""The memory policy (MEM-092): the briefing's rules, as code that cannot drift from it.

`memory-briefing.md` says what IS worth remembering, what must NEVER enter
memory, and that the validation must cover sensitive location data for both
B2B and B2C clients. These tests read that file and hold `policy.py` to it,
then exercise the deterministic validator on each rule.
"""

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from services.agent.memory import policy
from services.agent.memory.policy import MemoryDraft, check_draft, forbidden_reasons, redact

BRIEFING = Path(__file__).resolve().parents[2] / "memory-briefing.md"
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def section(title: str) -> str:
    text = BRIEFING.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, f"the briefing has no '{title}' section"
    return match.group(1)


def bullets(title: str) -> list[str]:
    return [line[2:] for line in section(title).splitlines() if line.startswith("- ")]


def numbered(title: str, heading: str) -> list[str]:
    block = section(title).split(heading, 1)[1].split("**", 1)[0]
    return [m.group(1) for m in re.finditer(r'^\d\. "(.+?)"', block, re.M)]


# --- The briefing, verbatim ----------------------------------------------------------


def test_the_memorable_categories_are_the_briefings_own():
    assert [kind.briefing_text for kind in policy.MEMORABLE] == bullets("What IS worth remembering")


def test_the_forbidden_list_is_the_briefings_own():
    assert [rule.briefing_text for rule in policy.FORBIDDEN[:3]] == bullets(
        "What must NEVER enter memory"
    )


def test_location_data_is_covered_for_both_client_types():
    constraint = policy.FORBIDDEN[3]
    assert constraint.briefing_text in " ".join(section("Company constraints").split())
    assert set(constraint.reasons) == {"b2c_location", "b2b_location"}


def test_every_forbidden_rule_is_enforced_by_a_validator_label():
    labels = {reason for rule in policy.FORBIDDEN for reason in rule.reasons}
    assert labels == set(policy.ForbiddenReason.__args__)  # type: ignore[attr-defined]
    assert labels <= set(policy.REFUSAL_REASONS)


def test_the_prompt_shows_the_model_exactly_what_the_validator_enforces():
    prompt = policy.memory_prompt()
    for kind in policy.MEMORABLE:
        assert f"{kind.category}: {kind.summary}" in prompt
    for rule in policy.FORBIDDEN[:3]:
        assert rule.briefing_text in prompt
    assert "B2C" in prompt and "B2B" in prompt
    # The briefing's examples are eval cases, not few-shot answers in the prompt.
    for example in numbered('Examples for your "Self-Evaluation" checklist', "**Should generate"):
        assert example not in prompt


def test_the_briefing_has_three_examples_each_way():
    title = 'Examples for your "Self-Evaluation" checklist'
    assert len(numbered(title, "**Should generate a memory proposal:**")) == 3
    assert len(numbered(title, "**Should NOT generate a proposal:**")) == 3


# --- What may be remembered ----------------------------------------------------------


def draft(**overrides) -> MemoryDraft:
    values = {
        "category": "carrier_rule",
        "content": "SEUR no longer covers rural Zaragoza; the local carrier is used there since "
        "October 2026.",
        "reason": "A carrier changed its coverage for a zone.",
        "carrier": "SEUR",
        "country": "ES",
        **overrides,
    }
    return MemoryDraft.model_validate(values)


BRIEFING_PROPOSALS = [
    draft(),
    draft(
        category="recurring_incident",
        content="Delays reported in Los Angeles incidents this week are due to the port "
        "strike, not a TrackFlow problem.",
        reason="Recurring incident context; the same alert should not be re-escalated.",
        carrier=None,
        country="US",
        route_or_area="Los Angeles port",
    ),
    draft(
        category="client_report_preference",
        content="The cosmetics client wants their monthly report with the returns breakdown "
        "first, before shipment volume.",
        reason="A recurring B2B client's report preference.",
        carrier=None,
        country=None,
        client="the cosmetics client",
    ),
]


@pytest.mark.parametrize("memorable", BRIEFING_PROPOSALS, ids=lambda d: d.category)
def test_the_briefings_memorable_examples_pass_the_validator(memorable):
    verdict = check_draft(memorable)
    assert verdict.allowed, verdict.reasons
    assert verdict.topic_key


@pytest.mark.parametrize(
    "text",
    [
        "The delays on the Zaragoza-to-Catalonia route this week are due to a carrier strike.",
        "MRW stopped operating the Zaragoza to Huesca route.",
        "UPS has the best rate for heavy packages (over 5 kg).",
        "Hold the parcels at the dock until the carrier arrives.",
        "The cosmetics client wants the returns breakdown first, before shipment volume.",
        "Use the local carrier for rural Aragón deliveries.",
    ],
)
def test_routes_zones_and_plain_facts_are_not_flagged(text):
    assert forbidden_reasons(text) == []


# --- What must never be remembered ---------------------------------------------------


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        # B2C: an end recipient's exact location.
        ("Deliver to Calle Alfonso I, 15, 3ºB in Zaragoza", "b2c_location"),
        ("The recipient lives at 1234 Sunset Blvd, Los Angeles", "b2c_location"),
        ("Recipient postcode 50001 must always go with MRW", "b2c_location"),
        ("Leave it at apartment 4B, the customer works nights", "b2c_location"),
        ("Drop point 41.6488, -0.8891 for this recipient", "b2c_location"),
        # B2B: a client brand's exact location.
        ("Glow Cosmetics' warehouse at 455 Industrial Way opens at 9", "b2b_location"),
        ("Use the brand's pickup point at Polígono Plaza, nave 12", "b2b_location"),
        # Internal warehouse routes and access details.
        ("Pickers should take aisle 4 then rack B to reach returns", "internal_warehouse_route"),
        ("Stage outbound pallets at dock 3 before noon", "internal_warehouse_route"),
        ("The gate code for the Zaragoza dock is 4471", "internal_warehouse_route"),
        # A single, non-repeating package incident.
        ("Where's the package with tracking XJ4471?", "single_package_incident"),
        ("Ticket TRF-000482 was resolved by reshipping", "single_package_incident"),
        ("Order #88231 arrived damaged", "single_package_incident"),
        ("A customer complained that her parcel arrived wet", "single_package_incident"),
        ("UPS 1Z999AA10123456784 was delivered late", "single_package_incident"),
        # Active commercial negotiations.
        ("We are negotiating a 10% discount with the cosmetics client", "commercial_negotiation"),
        ("Contract renewal terms for Fashion Co are still open", "commercial_negotiation"),
    ],
)
def test_forbidden_content_is_refused_with_its_reason(text, reason):
    assert reason in forbidden_reasons(text)
    verdict = check_draft(draft(content=text))
    assert not verdict.allowed
    assert reason in verdict.reasons


def test_a_scope_field_cannot_smuggle_in_an_address():
    verdict = check_draft(
        draft(
            category="client_report_preference",
            content="The client wants volume first.",
            carrier=None,
            client="Glow, 455 Industrial Way warehouse",
        )
    )
    assert not verdict.allowed
    assert "b2b_location" in verdict.reasons


def test_a_draft_without_its_scope_cannot_be_consolidated():
    assert check_draft(draft(country=None)).reasons == ("incomplete_scope",)
    assert check_draft(draft(carrier=None)).reasons == ("incomplete_scope",)
    no_client = draft(category="client_report_preference", carrier=None, client=None)
    assert check_draft(no_client).reasons == ("incomplete_scope",)
    assert check_draft(draft(content="   ")).reasons == ("empty_content",)


def test_the_explanation_is_readable():
    verdict = check_draft(draft(content="Deliver to Calle Alfonso I, 15, Zaragoza"))
    assert verdict.explain() == "it contains an end customer's exact location"


# --- Consolidation keys and lifetimes ------------------------------------------------


def test_carrier_rules_consolidate_by_carrier_and_country():
    assert policy.topic_key(draft(carrier="Seur")) == "carrier_rule|seur|ES"
    assert policy.topic_key(draft(carrier="SEUR ")) == "carrier_rule|seur|ES"
    assert policy.topic_key(draft(carrier="DHL", country="US")) != policy.topic_key(
        draft(carrier="DHL", country="ES")
    )
    assert policy.topic_key(draft(carrier="local last-mile carriers")) == (
        "carrier_rule|local-carrier|ES"
    )


def test_incidents_consolidate_by_carrier_or_route_and_clients_by_name():
    incident = draft(
        category="recurring_incident", carrier=None, route_or_area="Zaragoza → Catalonia"
    )
    assert policy.topic_key(incident) == "recurring_incident|zaragoza-catalonia|ES"
    client = draft(category="client_report_preference", carrier=None, client="Glow Cosmetics")
    assert policy.topic_key(client) == "client_report_preference|glow-cosmetics"


def test_each_category_has_its_lifetime():
    assert policy.valid_until(draft(), now=NOW) == NOW + timedelta(days=180)
    incident = draft(category="recurring_incident")
    assert policy.valid_until(incident, now=NOW) == NOW + timedelta(days=14)
    week = incident.model_copy(update={"valid_until": date(2026, 11, 15)})
    assert policy.valid_until(week, now=NOW) == datetime(2026, 11, 15, 23, 59, 59, tzinfo=UTC)
    too_far = incident.model_copy(update={"valid_until": date(2027, 3, 1)})
    assert policy.valid_until(too_far, now=NOW) == NOW + timedelta(days=14)
    past = incident.model_copy(update={"valid_until": date(2026, 1, 1)})
    assert policy.valid_until(past, now=NOW) == NOW + timedelta(days=14)


# --- Redaction for the audit log -----------------------------------------------------


def test_redaction_masks_locations_routes_and_identifiers():
    assert "Sunset" not in redact("The recipient lives at 1234 Sunset Blvd, Los Angeles")
    assert "4471" not in redact("The gate code for the Zaragoza dock is 4471")
    assert "TRF-000482" not in redact("Ticket TRF-000482 was resolved")
    assert "aisle 4" not in redact("Take aisle 4 to returns")


def test_redaction_leaves_clean_text_alone():
    text = "Actually SEUR no longer covers that rural area of Zaragoza, since last month."
    assert redact(text) == text
