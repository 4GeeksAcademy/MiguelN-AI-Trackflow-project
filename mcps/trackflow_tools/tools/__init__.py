"""The MCP tools, one module per service.

Each tool is registered from its `permissions.POLICIES` entry, so the scope it
requires is stated once and appears both in its description and in its
`_meta.required_scopes` - what a client discovers is what the server enforces.
"""

from collections.abc import Callable
from typing import Any, TypeVar

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ValidationError

from mcps.trackflow_tools.errors import ERROR_DESCRIPTIONS, ErrorCode, ToolFailure
from mcps.trackflow_tools.permissions import POLICIES, SCOPE_DESCRIPTIONS

ModelT = TypeVar("ModelT", bound=BaseModel)

# Every tool can answer these, on top of its own.
COMMON_ERRORS = (ErrorCode.INSUFFICIENT_SCOPE, ErrorCode.INVALID_ARGUMENTS)


def describe(tool: str, summary: str, *, use_when: str, errors: tuple[ErrorCode, ...]) -> str:
    """A tool description an agent can act on without any other context."""
    policy = POLICIES[tool]
    scopes = ", ".join(
        f"`{scope.value}` ({SCOPE_DESCRIPTIONS[scope]})" for scope in policy.required_scopes
    )
    error_lines = "\n".join(
        f"- `{code.value}`: {ERROR_DESCRIPTIONS[code]}" for code in (*errors, *COMMON_ERRORS)
    )
    return (
        f"{summary}\n\n"
        f"Use it when: {use_when}\n\n"
        f"Access: {policy.access.value} on {policy.domain.value}. Requires scope {scopes}.\n\n"
        "Errors come back as a tool error whose text is JSON "
        '`{"error": "<code>", "message": "..."}`:\n'
        f"{error_lines}"
    )


def tool_meta(tool: str) -> dict[str, Any]:
    policy = POLICIES[tool]
    return {
        "required_scopes": policy.scope_names,
        "domain": policy.domain.value,
        "access": policy.access.value,
    }


def read_only_annotations(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


def parse(model: type[ModelT], data: Any, service: str) -> ModelT:
    """The service's answer as `model`; an answer that does not fit is a service failure."""
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise ToolFailure(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            f"The {service} sent an answer this server cannot read.",
            service=service,
            failure="invalid_response",
        ) from error


ToolFunction = Callable[..., Any]
