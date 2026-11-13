"""Who may do what: scopes, the per-tool policy, and the read-only inventory rule.

Least privilege, by construction:

- Each tool requires exactly the scopes of the operation it performs. Reading
  a ticket needs `incidents:read`; creating one, `incidents:create`; moving it
  along its lifecycle, `incidents:update`. A token is checked per call.
- The inventory is read-only **by design**, not by omission:
  - the only inventory scope that exists is `inventory:read`; a token that
    carries something like `inventory:write` gains nothing;
  - `validate_policies` refuses to start the server if an inventory tool is
    declared as anything but read, or asks for any other scope;
  - `ReadOnlyInventoryGuard` (middleware) explicitly rejects a modification
    attempt - a write operation, a write-only field, or a write tool name - with
    `inventory_read_only`, before argument validation, so the refusal says why;
  - the inventory HTTP client can only send GET (`upstream.ReadOnlyInventoryApi`).
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Scope(StrEnum):
    INCIDENTS_READ = "incidents:read"
    INCIDENTS_CREATE = "incidents:create"
    INCIDENTS_UPDATE = "incidents:update"
    INVENTORY_READ = "inventory:read"


SCOPE_DESCRIPTIONS: Final[dict[Scope, str]] = {
    Scope.INCIDENTS_READ: "Read incidents: one by id or helpdesk reference, or a search.",
    Scope.INCIDENTS_CREATE: "Register new incidents.",
    Scope.INCIDENTS_UPDATE: "Move an incident along its lifecycle (status changes).",
    Scope.INVENTORY_READ: "Read products, stock and movement history. There is no write scope.",
}

ALL_SCOPES: Final = tuple(scope.value for scope in Scope)


class Domain(StrEnum):
    INCIDENTS = "incidents"
    INVENTORY = "inventory"


class Access(StrEnum):
    READ = "read"
    WRITE = "write"


# Domains whose data no tool may change, whatever scopes a token carries.
READ_ONLY_DOMAINS: Final = frozenset({Domain.INVENTORY})
# The one scope a read-only domain's tools may require.
READ_SCOPE_OF: Final[dict[Domain, Scope]] = {Domain.INVENTORY: Scope.INVENTORY_READ}


@dataclass(frozen=True)
class ToolPolicy:
    tool: str
    domain: Domain
    access: Access
    required_scopes: tuple[Scope, ...]

    @property
    def scope_names(self) -> list[str]:
        return [scope.value for scope in self.required_scopes]


CREATE_INCIDENT: Final = "create_incident"
UPDATE_INCIDENT_STATUS: Final = "update_incident_status"
GET_INCIDENT: Final = "get_incident"
SEARCH_INCIDENTS: Final = "search_incidents"
QUERY_INVENTORY: Final = "query_inventory"

POLICIES: Final[Mapping[str, ToolPolicy]] = {
    policy.tool: policy
    for policy in (
        ToolPolicy(CREATE_INCIDENT, Domain.INCIDENTS, Access.WRITE, (Scope.INCIDENTS_CREATE,)),
        ToolPolicy(
            UPDATE_INCIDENT_STATUS, Domain.INCIDENTS, Access.WRITE, (Scope.INCIDENTS_UPDATE,)
        ),
        ToolPolicy(GET_INCIDENT, Domain.INCIDENTS, Access.READ, (Scope.INCIDENTS_READ,)),
        ToolPolicy(SEARCH_INCIDENTS, Domain.INCIDENTS, Access.READ, (Scope.INCIDENTS_READ,)),
        ToolPolicy(QUERY_INVENTORY, Domain.INVENTORY, Access.READ, (Scope.INVENTORY_READ,)),
    )
}

# --- The read-only inventory ------------------------------------------------------

# `query_inventory` operations. Reads run; the writes are the inventory API's own
# write routes, named so a refusal can say exactly what was refused.
INVENTORY_READ_OPERATIONS: Final = ("list_products", "get_product", "list_orders")
INVENTORY_WRITE_OPERATIONS: Final[Mapping[str, str]] = {
    "create_product": "POST /inventory/products",
    "record_inbound": "POST /inventory/products/inbound",
    "record_outbound": "POST /inventory/products/outbound",
}
# Fields that only make sense when changing stock.
INVENTORY_WRITE_FIELDS: Final = frozenset({"quantity"})

_WRITE_VERBS: Final = frozenset(
    {"create", "update", "delete", "remove", "record", "adjust", "set", "add", "edit",
     "patch", "put", "post", "insert", "upsert", "modify", "change", "write", "receive",
     "ship", "restock", "decrement", "increment"}
)  # fmt: skip
_INVENTORY_NOUNS: Final = frozenset(
    {"inventory", "product", "products", "stock", "inbound", "outbound", "order", "orders",
     "warehouse", "sku", "movement", "movements"}
)  # fmt: skip


def _name_words(name: str) -> set[str]:
    return {word for word in re.split(r"[^a-z0-9]+", name.lower()) if word}


def is_inventory_write_tool_name(name: str) -> bool:
    """True for a tool name that asks to change inventory data ('create_product',
    'recordInboundOrder', 'update-stock'), so the call is refused as read-only
    rather than reported as merely unknown."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    words = _name_words(spaced)
    return bool(words & _WRITE_VERBS) and bool(words & _INVENTORY_NOUNS)


def inventory_write_attempt(arguments: Mapping[str, object]) -> str | None:
    """Why a `query_inventory` call is a modification attempt, or None for a read."""
    operation = arguments.get("operation")
    if isinstance(operation, str):
        normalized = operation.strip().lower()
        if normalized in INVENTORY_WRITE_OPERATIONS:
            return f"operation '{normalized}' would call {INVENTORY_WRITE_OPERATIONS[normalized]}"
        if normalized not in INVENTORY_READ_OPERATIONS and is_inventory_write_tool_name(normalized):
            return f"operation '{normalized}' would modify inventory data"
    write_fields = sorted(INVENTORY_WRITE_FIELDS.intersection(arguments))
    if write_fields:
        return f"field(s) {', '.join(write_fields)} only apply to stock movements"
    return None


def missing_scopes(policy: ToolPolicy, granted: Iterable[str]) -> list[str]:
    """The required scopes the token does not carry (all of them are required)."""
    granted_set = set(granted)
    return [scope.value for scope in policy.required_scopes if scope.value not in granted_set]


class PolicyViolation(RuntimeError):
    """The registered tools break the permission design; the server must not start."""


def validate_policies(
    registered_tools: Iterable[str], policies: Mapping[str, ToolPolicy] = POLICIES
) -> None:
    """Refuse a tool without a policy, a policy without a tool, a tool without
    scopes, and any write access - or foreign scope - in a read-only domain."""
    registered = set(registered_tools)
    problems: list[str] = []
    for tool in sorted(registered - set(policies)):
        problems.append(f"tool '{tool}' has no permission policy")
    for tool in sorted(set(policies) - registered):
        problems.append(f"policy '{tool}' has no registered tool")
    for policy in policies.values():
        if not policy.required_scopes:
            problems.append(f"tool '{policy.tool}' requires no scope")
        if policy.domain in READ_ONLY_DOMAINS:
            if policy.access is not Access.READ:
                problems.append(
                    f"tool '{policy.tool}' would write to the read-only {policy.domain} domain"
                )
            if set(policy.required_scopes) != {READ_SCOPE_OF[policy.domain]}:
                problems.append(
                    f"tool '{policy.tool}' must require exactly "
                    f"{READ_SCOPE_OF[policy.domain].value}"
                )
        if is_inventory_write_tool_name(policy.tool):
            problems.append(f"tool '{policy.tool}' is named as an inventory write")
    if problems:
        raise PolicyViolation("; ".join(problems))
