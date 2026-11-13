"""What the agent may remember, and what must never enter its memory (MEM-092).

The source is `memory-briefing.md` (Milestone 8, Part 1). Every rule below is that
briefing's, in its own words: the generation prompt (`memory_prompt()`) and the
deterministic validator (`check_draft()`) are both built from these constants,
and `tests/pipelines/test_agent_memory_policy.py` checks them against the
briefing file, so the two cannot drift apart.

The model decides *whether* a message holds something worth remembering (its
self-evaluation). This module decides whether what it proposes *may* be stored:
an allowed category, a complete scope to consolidate it under, and none of the
forbidden content. It is pure: no model, no database.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- What IS worth remembering ------------------------------------------------------

Category = Literal["carrier_rule", "recurring_incident", "client_report_preference"]
Country = Literal["US", "ES"]


@dataclass(frozen=True)
class MemorableKind:
    """One category the briefing allows, with how long an entry lives and how many may."""

    category: Category
    # The briefing's bullet, verbatim (without the leading "- ").
    briefing_text: str
    # Plain wording for the prompt.
    summary: str
    # Default lifetime from approval, and the longest a proposal may ask for.
    ttl_days: int
    max_ttl_days: int
    # Most active entries of this category; past it the least recently used is evicted.
    cap: int


MEMORABLE: Final[tuple[MemorableKind, ...]] = (
    MemorableKind(
        category="carrier_rule",
        briefing_text=(
            "**Corrected carrier assignment rules**: if a carrier changed its coverage for a "
            "certain zone, or stopped operating a certain route, that's worth remembering."
        ),
        summary=(
            "a corrected carrier assignment rule: a carrier changed its coverage for a zone, "
            "or stopped operating a route"
        ),
        # Coverage changes, but not weekly: re-confirmed twice a year.
        ttl_days=180,
        max_ttl_days=180,
        # 8 carriers across two countries (DHL in both), plus room for local carriers.
        cap=24,
    ),
    MemorableKind(
        category="recurring_incident",
        briefing_text=(
            '**Context from known, recurring incidents**: "the delays on the '
            "Zaragoza-to-Catalonia route this week are due to a carrier strike, not a "
            "TrackFlow problem\" — useful so the same alert isn't re-escalated."
        ),
        summary=(
            "context from a known, recurring incident: the cause of repeated delays or "
            "alerts on a route, area or carrier, so the same alert isn't re-escalated"
        ),
        # "This week": a strike or a port closure ends. Two weeks by default, a month at most.
        ttl_days=14,
        max_ttl_days=30,
        cap=20,
    ),
    MemorableKind(
        category="client_report_preference",
        briefing_text=(
            "**Recurring B2B clients' preferences** for their monthly report (format, "
            "highlighted metrics)."
        ),
        summary=(
            "a recurring B2B client's preference for their monthly report: its format or "
            "the metrics to highlight"
        ),
        # A brand's reporting habits change slowly: a year.
        ttl_days=365,
        max_ttl_days=365,
        cap=50,
    ),
)
KIND_OF: Final[dict[str, MemorableKind]] = {kind.category: kind for kind in MEMORABLE}

# A pending proposal the user never answered is dropped after this long.
PENDING_TTL: Final = timedelta(hours=24)

# --- What must NEVER enter memory ---------------------------------------------------

ForbiddenReason = Literal[
    "b2c_location",
    "b2b_location",
    "internal_warehouse_route",
    "single_package_incident",
    "commercial_negotiation",
]


@dataclass(frozen=True)
class ForbiddenRule:
    """One of the briefing's prohibitions, and the validator labels that enforce it."""

    # The briefing's bullet or constraint, verbatim.
    briefing_text: str
    reasons: tuple[ForbiddenReason, ...]


FORBIDDEN: Final[tuple[ForbiddenRule, ...]] = (
    ForbiddenRule(
        briefing_text=(
            "Exact end-customer (B2C) addresses or internal warehouse routes — this is "
            "sensitive physical-security information, as the company's own guardrails README "
            "already flags."
        ),
        reasons=("b2c_location", "internal_warehouse_route"),
    ),
    ForbiddenRule(
        briefing_text=(
            "Data from a single, non-repeating package incident (an isolated customer "
            "complaint isn't memorable)."
        ),
        reasons=("single_package_incident",),
    ),
    ForbiddenRule(
        briefing_text=(
            "Information from active commercial contract negotiations (that's handled by the "
            "commercial team's CRM, not the support agent's memory)."
        ),
        reasons=("commercial_negotiation",),
    ),
    # Company constraints: "Your 'what must never be remembered' validation must
    # explicitly cover sensitive location data for both client types, not just one."
    ForbiddenRule(
        briefing_text=(
            "TrackFlow distinguishes between B2B clients (brands) and B2C clients (end "
            'recipients). Your "what must never be remembered" validation must explicitly '
            "cover sensitive location data for both client types, not just one."
        ),
        reasons=("b2c_location", "b2b_location"),
    ),
)

# Why a draft was refused, in words a user can read.
REFUSAL_REASONS: Final[dict[str, str]] = {
    "b2c_location": "it contains an end customer's exact location",
    "b2b_location": "it contains a client brand's exact location",
    "internal_warehouse_route": "it describes an internal warehouse route or access detail",
    "single_package_incident": "it is about a single package, order or ticket",
    "commercial_negotiation": "it comes from an active commercial negotiation",
    "incomplete_scope": "it does not say which carrier, country or client it is about",
    "empty_content": "it is empty",
}

# --- The model's proposal ----------------------------------------------------------

MAX_CONTENT_CHARS: Final = 280


class MemoryDraft(BaseModel):
    """What the model proposes to remember: one fact, with what it is about."""

    model_config = ConfigDict(extra="ignore")

    category: Category = Field(description="Which kind of memory this is.")
    content: str = Field(
        max_length=MAX_CONTENT_CHARS,
        description=(
            "The fact, as one self-contained sentence a colleague could act on: what changed, "
            "for which carrier, route, area or client, and since when. As the user reported "
            "it; never an address, a ticket, order or tracking number, or a person's name."
        ),
    )
    reason: str = Field(
        max_length=200, description="Why it is worth remembering next time, in one sentence."
    )
    carrier: str | None = Field(
        default=None, max_length=40, description="The carrier it is about, if any (e.g. SEUR)."
    )
    country: Country | None = Field(
        default=None,
        description="US for Los Angeles operations, ES for Zaragoza / Spain.",
    )
    route_or_area: str | None = Field(
        default=None,
        max_length=80,
        description="The route, zone or area, if any (e.g. Zaragoza-to-Catalonia).",
    )
    client: str | None = Field(
        default=None,
        max_length=80,
        description="The B2B client (brand) it is about, for a report preference.",
    )
    valid_until: date | None = Field(
        default=None,
        description=(
            "Only for something temporary (e.g. a strike this week): the last day it holds."
        ),
    )


# --- Normalisation and consolidation keys -------------------------------------------

# The carriers in TrackFlow's knowledge base (docs/company-knowledge-base/
# trackflow-carrier-coverage.en.md): UPS, FedEx, DHL in the US; MRW, SEUR, DHL and
# two local last-mile carriers in Spain. Aliases map to one spelling, so "Seur"
# and "SEUR" consolidate under one key.
CARRIER_ALIASES: Final[dict[str, str]] = {
    "ups": "UPS",
    "fedex": "FedEx",
    "fed ex": "FedEx",
    "dhl": "DHL",
    "mrw": "MRW",
    "seur": "SEUR",
    "local carrier": "Local carrier",
    "local carriers": "Local carrier",
    "local last-mile carrier": "Local carrier",
    "local last-mile carriers": "Local carrier",
}

COUNTRY_NAMES: Final[dict[str, str]] = {"US": "United States", "ES": "Spain"}


def normalise_text(text: str) -> str:
    """Case-, accent- and spacing-insensitive form, for comparisons only."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


def canonical_carrier(carrier: str | None) -> str | None:
    if carrier is None or not carrier.strip():
        return None
    key = normalise_text(carrier)
    return CARRIER_ALIASES.get(key, " ".join(carrier.split()))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalise_text(text)).strip("-")


def topic_key(draft: MemoryDraft) -> str | None:
    """The key a memory is consolidated under, or None when the draft lacks its scope.

    The briefing suggests consolidating "by carrier + country rather than by
    individual ticket": one active entry per carrier and country, per route or
    area when no carrier is named, and per client for report preferences.
    """
    if draft.category == "carrier_rule":
        carrier = canonical_carrier(draft.carrier)
        if carrier is None or draft.country is None:
            return None
        return f"carrier_rule|{_slug(carrier)}|{draft.country}"
    if draft.category == "recurring_incident":
        subject = canonical_carrier(draft.carrier) or (draft.route_or_area or "").strip()
        if not subject or draft.country is None:
            return None
        return f"recurring_incident|{_slug(subject)}|{draft.country}"
    if not draft.client or not draft.client.strip():
        return None
    return f"client_report_preference|{_slug(draft.client)}"


def describe_scope(
    category: str,
    carrier: str | None,
    country: str | None,
    route_or_area: str | None,
    client: str | None,
) -> str:
    """A short label of what a memory is about, e.g. "SEUR, ES" or "client: Glow Cosmetics"."""
    if category == "client_report_preference":
        return f"client: {client}" if client else "client"
    subject = canonical_carrier(carrier) or route_or_area or ""
    return ", ".join(part for part in (subject, country) if part)


def valid_until(draft: MemoryDraft, *, now: datetime) -> datetime:
    """When an approved memory expires: the category's default, or the draft's earlier date.

    A date in the past, or later than the category allows, is ignored.
    """
    kind = KIND_OF[draft.category]
    default = now + timedelta(days=kind.ttl_days)
    if draft.valid_until is None:
        return default
    proposed = datetime.combine(draft.valid_until, time(23, 59, 59), tzinfo=UTC)
    if proposed <= now or proposed > now + timedelta(days=kind.max_ttl_days):
        return default
    return proposed


# --- Forbidden content ---------------------------------------------------------------

_STREET_WORDS_EN = (
    r"street|st\.?|avenue|ave\.?|road|rd\.?|boulevard|blvd\.?|lane|ln\.?|drive|dr\.?|way|"
    r"court|ct\.?|place|pl\.?|highway|hwy\.?|parkway|pkwy\.?|terrace|circle"
)
_STREET_WORDS_ES = (
    r"calle|c/|avda\.?|avenida|av\.|paseo|p[º°]|plaza|pza\.?|camino|carretera|ctra\.?|"
    r"ronda|traves[ií]a|glorieta|pasaje|pol[ií]gono|nave"
)

# Exact locations: a street address, a postcode given as one, an apartment or
# floor, or coordinates. A route between two cities ("Zaragoza-to-Catalonia") or
# a zone ("rural Aragón") is not an exact location and passes.
_EXACT_LOCATION: Final = (
    re.compile(rf"\b\d{{1,5}}[a-z]?\s+(?:[\w'.-]+\s+){{0,4}}(?:{_STREET_WORDS_EN})(?=\W|$)", re.I),
    re.compile(
        rf"(?:^|\W)(?:{_STREET_WORDS_ES})\s+[\w .'-]{{1,40}}?,?\s*(?:n[º°o.]?\s*)?\d{{1,4}}\b", re.I
    ),
    re.compile(
        r"\b(?:zip|zip code|postcode|postal code|post code|c\.?p\.?|c[oó]digo postal)"
        r"\s*:?\s*\d{4,5}(?:-\d{4})?\b",
        re.I,
    ),
    re.compile(r"\b\d{5}-\d{4}\b"),
    re.compile(
        r"\b(?:apt\.?|apartment|suite|unit|flat|piso|puerta|portal|escalera|floor|planta)\s*#?\s*\d+[a-z]?\b",
        re.I,
    ),
    re.compile(r"-?\b\d{1,2}\.\d{3,},\s*-?\d{1,3}\.\d{3,}\b"),
)

# Whose location: a client brand's premises (B2B), otherwise an end recipient's (B2C).
_B2B_MARKERS: Final = re.compile(
    r"\b(?:b2b|brand|client'?s? (?:warehouse|site|office|premises|store|shop|facility|plant|"
    r"dock|pickup)|warehouse|premises|facility|factory|plant|headquarters|hq|office|"
    r"pickup point|pick-up point|collection point|loading bay|depot|store|shop|"
    r"nave|almac[eé]n|oficina|tienda|f[aá]brica|sede)\b",
    re.I,
)

_INTERNAL_ROUTE: Final = (
    # A place inside a warehouse, with the identifier that may follow it ("aisle 4",
    # "rack B").
    re.compile(
        r"\b(?:aisle|rack|shelf|shelves|bin location|picking route|pick path|pick route|"
        r"warehouse route|internal route|route inside the warehouse|pasillo|"
        r"estanter[ií]a|ubicaci[oó]n interna|ruta interna|ruta de picking)\b"
        r"(?:\s*#?\s*(?:\d[\w-]{0,5}|[A-Z]\d{0,3})\b)?",
        re.I,
    ),
    # Generic words that only point inside a warehouse with an identifier ("dock 3";
    # "hold it at the dock" passes).
    re.compile(r"\b(?:loading dock|dock|bay|bin|muelle)\s*#?\s*\d[\w-]{0,5}\b", re.I),
    # An access code, with its value when the sentence gives it ("... is 4471").
    re.compile(
        r"\b(?:(?:access|gate|door|alarm|security|entry|keypad)\s+(?:code|pin|password)|"
        r"c[oó]digo de (?:acceso|alarma|puerta))\b(?:[^.\n]{0,40}?\b(?:is|es|:|=)\s*\S+)?",
        re.I,
    ),
)

_SINGLE_PACKAGE: Final = (
    # Ticket references as the Incidents Manager writes them (TRF-000482, TRF 482).
    re.compile(r"\bTRF[\s-]*\d+\b", re.I),
    # A tracking, order, parcel or ticket number.
    re.compile(
        r"\b(?:tracking|track|waybill|awb|order|pedido|parcel|package|paquete|shipment|"
        r"env[ií]o|ticket|incident|incidencia)\s*(?:number|no\.?|num\.?|n[º°]|#|code|id|ref"
        r"(?:erence)?)?\s*[:#]?\s*(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{3,}\b",
        re.I,
    ),
    # UPS (1Z...) and long numeric carrier tracking codes.
    re.compile(r"\b1Z[0-9A-Z]{16}\b", re.I),
    re.compile(r"\b\d{10,22}\b"),
    # A lone complaint.
    re.compile(
        r"\b(?:a|one|single|this|that|an individual)\s+(?:customer|recipient|client|consumer)"
        r"(?:'s)?\s+(?:complained|complaint|parcel|package|order|claim)\b",
        re.I,
    ),
)

_NEGOTIATION: Final = re.compile(
    r"\b(?:negotiat\w*|renegotiat\w*|negociaci[oó]n\w*|negociando|counter-?offer|"
    r"contract (?:negotiation|renewal|terms|talks|proposal|draft|discussion)s?|"
    r"draft contract|(?:discount|rebate|preferential rate|special rate|price increase|"
    r"price reduction|new rate|rate card)s?|descuento\w*|tarifa preferente|tender|rfp|"
    r"under negotiation|being negotiated|deal (?:terms|on the table))\b",
    re.I,
)


def _location_reason(text: str) -> ForbiddenReason | None:
    if not any(pattern.search(text) for pattern in _EXACT_LOCATION):
        return None
    return "b2b_location" if _B2B_MARKERS.search(text) else "b2c_location"


def forbidden_reasons(text: str) -> list[ForbiddenReason]:
    """Every forbidden kind of content found in `text`, in a fixed order."""
    found: list[ForbiddenReason] = []
    location = _location_reason(text)
    if location is not None:
        found.append(location)
    if any(pattern.search(text) for pattern in _INTERNAL_ROUTE):
        found.append("internal_warehouse_route")
    if any(pattern.search(text) for pattern in _SINGLE_PACKAGE):
        found.append("single_package_incident")
    if _NEGOTIATION.search(text):
        found.append("commercial_negotiation")
    return found


@dataclass(frozen=True)
class DraftCheck:
    """The validator's verdict on a draft: allowed, or refused with its reasons."""

    allowed: bool
    reasons: tuple[str, ...] = ()
    topic_key: str | None = None

    def explain(self) -> str:
        return "; ".join(REFUSAL_REASONS.get(reason, reason) for reason in self.reasons)


def check_draft(draft: MemoryDraft) -> DraftCheck:
    """May this draft be proposed (and later stored)? Deterministic, never calls a model.

    Every text field is checked, not only `content`: a scope field must not
    smuggle in an address either.
    """
    if not draft.content.strip():
        return DraftCheck(False, ("empty_content",))
    texts = [draft.content, draft.reason, draft.carrier, draft.route_or_area, draft.client]
    reasons: list[str] = []
    for text in texts:
        for reason in forbidden_reasons(text or ""):
            if reason not in reasons:
                reasons.append(reason)
    key = topic_key(draft)
    if key is None:
        reasons.append("incomplete_scope")
    if reasons:
        return DraftCheck(False, tuple(reasons), key)
    return DraftCheck(True, (), key)


def check_text(text: str) -> DraftCheck:
    """The forbidden-content check alone, for a text the user wrote (an edit)."""
    if not text.strip():
        return DraftCheck(False, ("empty_content",))
    reasons = tuple(forbidden_reasons(text))
    return DraftCheck(not reasons, reasons)


def redact(text: str) -> str:
    """`text` with every exact location, internal route and package identifier masked.

    Used before a user's message is written to the decision log, so the audit
    trail never becomes the place where forbidden data is kept.
    """
    redacted = text
    location = _location_reason(text)
    if location is not None:
        for pattern in _EXACT_LOCATION:
            redacted = pattern.sub(f"[REDACTED:{location}]", redacted)
    for pattern in _INTERNAL_ROUTE:
        redacted = pattern.sub("[REDACTED:internal_warehouse_route]", redacted)
    for pattern in _SINGLE_PACKAGE[:4]:
        redacted = pattern.sub("[REDACTED:package_identifier]", redacted)
    if redacted != text:
        # Whatever number is left near a masked location or route (a door number, a
        # code) is masked too: over-masking a log line costs nothing.
        redacted = re.sub(r"\b\d{3,}\b", "[REDACTED:number]", redacted)
    return redacted


# --- The criterion, as the model reads it --------------------------------------------


def memory_prompt() -> str:
    """The MEMORY section of the agent's system prompt: the explicit criterion.

    Rendered from the constants above, so the model is shown exactly what the
    validator enforces.
    """
    allowed = "\n".join(f"- {kind.category}: {kind.summary}." for kind in MEMORABLE)
    forbidden = "\n".join(f"- {rule.briefing_text}" for rule in FORBIDDEN[:3])
    return f"""\
Self-evaluation (memory) - after answering, decide whether the user's message holds \
something new or corrected that TrackFlow's team should remember for next time. \
Most messages hold nothing to remember. Set memory_check to:
- "propose" only when the user states, or corrects, one of these:
{allowed}
- "nothing_to_remember" for questions, one-off lookups (a single package, tracking \
number or ticket), conversation closings and thanks, and single-use tasks such as \
translating or rewriting a text;
- "already_remembered" when TEAM MEMORY already holds the same fact unchanged;
- "must_not_be_remembered" when it would contain anything that must never enter \
memory:
{forbidden}
- Sensitive location data of either kind of client: an end recipient's (B2C) exact \
address, or a client brand's (B2B) exact address, site or pickup point.

When you propose, memory_proposal states the fact as the user reported it, in one \
self-contained sentence written in English whatever the language of the conversation \
(one memory is shared by the teams in both countries), and names its scope: carrier \
and country for a carrier rule; carrier or route, and country, for a recurring \
incident; the client for a report preference. Turn relative times ("last month", \
"this week") into the dates they mean, from TODAY; for something temporary, set \
valid_until to its last day. If TEAM MEMORY holds an entry on the same carrier and \
country (or the same client), propose the corrected entry as a whole, merging what \
still holds. Never include an address, a ticket, order or tracking number, or a \
person's name. Never say in your answer that you saved or will remember anything, and \
do not ask whether to: the system asks the user and saves only what they approve.
"""
