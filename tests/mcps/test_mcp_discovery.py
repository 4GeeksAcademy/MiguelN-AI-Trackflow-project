"""Discovery is the --help: an agent with no other context can tell what each tool does."""

import asyncio
import json
from pathlib import Path

import pytest

from mcps.trackflow_tools.__main__ import main
from mcps.trackflow_tools.config import McpServerSettings
from mcps.trackflow_tools.discovery import list_tools, render_markdown
from mcps.trackflow_tools.errors import ErrorCode, ExitCode
from mcps.trackflow_tools.permissions import (
    POLICIES,
    Access,
    Domain,
    Scope,
    ToolPolicy,
    validate_policies,
)
from mcps.trackflow_tools.server import INSTRUCTIONS

ROOT = Path(__file__).resolve().parents[2]
TOOLS_DOC = ROOT / "docs" / "mcp" / "TOOLS.md"


@pytest.fixture(scope="module")
def tools():
    return asyncio.run(list_tools())


def test_every_tool_is_listed_with_its_policy(tools):
    assert [tool.name for tool in tools] == list(POLICIES)


def test_every_tool_explains_itself(tools):
    for tool in tools:
        policy = POLICIES[tool.name]
        description = tool.description or ""
        assert "Use it when:" in description, tool.name
        for scope in policy.scope_names:
            assert f"`{scope}`" in description, tool.name
        assert "insufficient_scope" in description, tool.name
        assert tool.annotations is not None and tool.annotations.title, tool.name
        assert tool.annotations.readOnlyHint is (policy.access.value == "read"), tool.name
        assert tool.meta["required_scopes"] == policy.scope_names
        assert tool.meta["access"] == policy.access.value
        assert tool.outputSchema and tool.outputSchema.get("properties"), tool.name


def test_every_input_field_is_described(tools):
    for tool in tools:
        assert tool.inputSchema.get("additionalProperties") is False, tool.name
        for name, schema in tool.inputSchema["properties"].items():
            assert schema.get("description"), f"{tool.name}.{name}"


def test_the_inventory_tool_says_it_is_read_only(tools):
    inventory = next(tool for tool in tools if tool.name == "query_inventory")
    assert "READ-ONLY" in inventory.description
    assert "inventory_read_only" in inventory.description
    assert inventory.annotations.destructiveHint is False


def test_the_lifecycle_is_spelled_out_where_status_changes(tools):
    update = next(tool for tool in tools if tool.name == "update_incident_status")
    assert "PATCH /api/incidents/{incident_id}/status" in update.description
    assert "open -> in_progress | discarded" in update.description
    assert "resolved: final" in update.description


def test_the_server_instructions_list_every_scope_and_error_code():
    for policy in POLICIES.values():
        for scope in policy.scope_names:
            assert scope in INSTRUCTIONS
    for code in ErrorCode:
        assert f"`{code.value}`" in INSTRUCTIONS


def test_the_committed_reference_matches_the_server(tools):
    assert TOOLS_DOC.read_text(encoding="utf-8") == render_markdown(tools), (
        "docs/mcp/TOOLS.md is stale: run `npm run mcp:tools -- --write docs/mcp/TOOLS.md`"
    )


def test_the_cli_prints_the_discovery(capsys):
    assert main(["tools", "--json"]) == ExitCode.OK
    listed = json.loads(capsys.readouterr().out)
    assert [tool["name"] for tool in listed] == list(POLICIES)
    assert all(tool["inputSchema"] and tool["outputSchema"] for tool in listed)


def test_the_cli_exits_2_without_settings(monkeypatch, capsys):
    monkeypatch.delenv("MCP_RESOURCE_URL")
    # Ignore the developer's `.env`, which may hold a resource URL.
    monkeypatch.setattr(
        "mcps.trackflow_tools.__main__._settings",
        lambda: McpServerSettings(_env_file=None),  # type: ignore[call-arg]
    )
    assert main(["serve"]) == ExitCode.CONFIG_INVALID
    assert "MCP_RESOURCE_URL" in capsys.readouterr().err


def test_the_cli_exits_3_when_the_authorization_server_is_unreachable(monkeypatch, capsys):
    from mcpauth.exceptions import MCPAuthConfigException

    def unreachable(*args, **kwargs):
        raise MCPAuthConfigException("fetch_server_config_error", "connection refused")

    monkeypatch.setattr("mcps.trackflow_tools.auth.fetch_server_config", unreachable)
    assert main(["serve"]) == ExitCode.AUTH_SERVER_UNAVAILABLE
    assert "authorization server" in capsys.readouterr().err


def test_the_cli_exits_5_when_the_tools_break_the_permission_design(monkeypatch, capsys):
    broken = {
        **POLICIES,
        "query_inventory": ToolPolicy(
            "query_inventory", Domain.INVENTORY, Access.WRITE, (Scope.INVENTORY_READ,)
        ),
    }
    monkeypatch.setattr(
        "mcps.trackflow_tools.server.validate_policies",
        lambda names: validate_policies(names, broken),
    )
    assert main(["tools"]) == ExitCode.POLICY_VIOLATION
    assert "read-only inventory domain" in capsys.readouterr().err
