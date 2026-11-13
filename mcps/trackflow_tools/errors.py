"""What a client can be told when something is refused or fails, and the CLI exit codes.

Three layers answer, each with its own codes (the full table is in
`docs/mcp/mcp-server.md`):

1. **HTTP, before MCP** - MCP Auth's bearer middleware. 401 with
   `WWW-Authenticate: Bearer error="<code>", resource_metadata="..."` for
   `missing_auth_header`, `invalid_auth_header_format`, `missing_bearer_token`,
   `invalid_token`, `token_verification_failed`, `invalid_issuer` and
   `invalid_audience`. A 500 `server_error` when the authorization server
   cannot be used. No MCP method runs, `tools/list` included.
2. **Tool errors** - a `tools/call` result with `isError: true` whose text is
   JSON: `{"error": "<ErrorCode>", "message": "...", ...details}`. The codes
   are `ErrorCode` below.
3. **Process** - `python -m mcps.trackflow_tools` exits with `ExitCode`.
"""

import json
from enum import IntEnum, StrEnum
from typing import Any, Final

from fastmcp.exceptions import ToolError


class ErrorCode(StrEnum):
    # Authorization: the token is valid but does not grant this tool.
    INSUFFICIENT_SCOPE = "insufficient_scope"
    # The inventory is read-only on this server, whatever the token grants.
    INVENTORY_READ_ONLY = "inventory_read_only"
    # Validation: the arguments do not match the tool's input schema or rules.
    INVALID_ARGUMENTS = "invalid_arguments"
    UNKNOWN_TOOL = "unknown_tool"
    # The services' own answers.
    INCIDENT_NOT_FOUND = "incident_not_found"
    PRODUCT_NOT_FOUND = "product_not_found"
    INVALID_STATUS_TRANSITION = "invalid_status_transition"
    UPSTREAM_REJECTED = "upstream_rejected"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UPSTREAM_AUTH_FAILED = "upstream_auth_failed"
    # Anything else; the details stay in the server log.
    INTERNAL_ERROR = "internal_error"


ERROR_DESCRIPTIONS: Final[dict[ErrorCode, str]] = {
    ErrorCode.INSUFFICIENT_SCOPE: (
        "The access token is valid but lacks a scope this tool requires. "
        "`required_scopes` and `granted_scopes` say which."
    ),
    ErrorCode.INVENTORY_READ_ONLY: (
        "A modification of inventory data was attempted. The inventory is read-only on "
        "this server: no scope and no tool can create products or record stock movements."
    ),
    ErrorCode.INVALID_ARGUMENTS: (
        "The arguments break the tool's input schema or rules; `problems` lists them."
    ),
    ErrorCode.UNKNOWN_TOOL: "No tool with that name exists. Call tools/list.",
    ErrorCode.INCIDENT_NOT_FOUND: "No incident has that id or helpdesk reference.",
    ErrorCode.PRODUCT_NOT_FOUND: "No product has that id.",
    ErrorCode.INVALID_STATUS_TRANSITION: (
        "The incident lifecycle does not allow that status change; the message says "
        "which moves are available."
    ),
    ErrorCode.UPSTREAM_REJECTED: (
        "The Incidents Manager or the inventory refused the request; `field` and the "
        "message are theirs."
    ),
    ErrorCode.UPSTREAM_UNAVAILABLE: (
        "The service behind the tool did not answer in time, could not be reached, or "
        "failed. Retrying later may work."
    ),
    ErrorCode.UPSTREAM_AUTH_FAILED: (
        "This server could not sign in to the inventory with its service account."
    ),
    ErrorCode.INTERNAL_ERROR: "The server failed unexpectedly; the details are in its log.",
}


class ToolFailure(ToolError):
    """A refused or failed tool call, sent to the client as JSON with its code.

    A `ToolError` is the one exception FastMCP passes to the client as written
    even with `mask_error_details`, so the code and message always arrive.
    """

    def __init__(self, code: ErrorCode, message: str, **details: Any) -> None:
        self.code = code
        self.payload: dict[str, Any] = {"error": code.value, "message": message, **details}
        super().__init__(json.dumps(self.payload, default=str))


def parse_failure(text: str) -> dict[str, Any] | None:
    """The JSON payload of a tool error, or None when it is not one of ours."""
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("error"), str):
        return payload
    return None


class ExitCode(IntEnum):
    """`python -m mcps.trackflow_tools` exit statuses."""

    OK = 0
    UNEXPECTED = 1
    # Settings missing or invalid (`MCP_RESOURCE_URL`, `MCP_AUTH_ISSUER`, ...).
    CONFIG_INVALID = 2
    # The authorization server's metadata could not be fetched or is invalid,
    # so no token could be validated: the server refuses to start.
    AUTH_SERVER_UNAVAILABLE = 3
    # `token`: the authorization server refused the client credentials.
    TOKEN_REQUEST_FAILED = 4
    # The registered tools break the permission design (`permissions.PolicyViolation`),
    # e.g. an inventory tool declared as a write: the server refuses to start.
    POLICY_VIOLATION = 5
