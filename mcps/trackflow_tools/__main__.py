"""CLI: `python -m mcps.trackflow_tools serve | tools | token`.

    serve [--host H] [--port P]   run the MCP server (Streamable HTTP, OAuth via MCP Auth)
    tools [--json] [--write PATH] print the tool reference (the discovery, as --help)
    token --client agent|tester   get an access token by client credentials (for testing
                                  from the MCP Playground or curl); prints only the token

Exit codes (`errors.ExitCode`): 0 ok, 1 unexpected, 2 settings missing or
invalid, 3 authorization server metadata unreachable or invalid, 4 token
request refused, 5 the tools break the permission design.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx
from mcpauth.exceptions import MCPAuthAuthServerException, MCPAuthConfigException
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from mcps.trackflow_tools.config import ROOT_DIR, McpServerSettings, get_mcp_settings
from mcps.trackflow_tools.errors import ExitCode
from mcps.trackflow_tools.permissions import PolicyViolation


class _AgentClient(BaseSettings):
    """The agent's OAuth client, read like the agent reads it (no import of `services`)."""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_OAUTH_", env_file=ROOT_DIR / ".env", extra="ignore"
    )
    client_id: str | None = None
    client_secret: SecretStr | None = None
    scopes: str = "incidents:read inventory:read"


def _fail(code: ExitCode, message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return int(code)


def _settings() -> McpServerSettings:
    return get_mcp_settings()


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from mcps.trackflow_tools.app import build_app

    settings = _settings()
    app = build_app(settings)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    print(
        f"MCP server on http://{args.host or settings.host}:{args.port or settings.port}"
        f"{settings.endpoint_path} (resource {settings.resource_url})",
        file=sys.stderr,
    )
    uvicorn.run(app, host=args.host or settings.host, port=args.port or settings.port)
    return int(ExitCode.OK)


def _tools(args: argparse.Namespace) -> int:
    from mcps.trackflow_tools.discovery import as_json, list_tools, render_markdown

    tools = asyncio.run(list_tools())
    text = json.dumps(as_json(tools), indent=2) + "\n" if args.json else render_markdown(tools)
    if args.write:
        Path(args.write).write_text(text, encoding="utf-8")
        print(f"wrote {args.write}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return int(ExitCode.OK)


def _token(args: argparse.Namespace) -> int:
    from mcps.trackflow_tools.auth import fetch_authorization_server

    settings = _settings()
    if args.client == "agent":
        agent = _AgentClient()
        client_id, secret, scope = agent.client_id, agent.client_secret, agent.scopes
    else:
        client_id, secret, scope = settings.tester_client_id, settings.tester_client_secret, None
    if not client_id or secret is None:
        variables = (
            "AGENT_OAUTH_CLIENT_ID / AGENT_OAUTH_CLIENT_SECRET"
            if args.client == "agent"
            else "MCP_TESTER_CLIENT_ID / MCP_TESTER_CLIENT_SECRET"
        )
        return _fail(ExitCode.CONFIG_INVALID, f"set {variables}")
    server = fetch_authorization_server(settings)
    form: dict[str, str] = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": secret.get_secret_value(),
        # Auth0 names the API by `audience`; RFC 8707 servers by `resource`.
        "audience": settings.expected_audience,
    }
    requested = args.scope or scope
    if requested:
        form["scope"] = str(requested)
    try:
        response = httpx.post(server.metadata.token_endpoint, data=form, timeout=10)
    except httpx.HTTPError as error:
        return _fail(ExitCode.TOKEN_REQUEST_FAILED, f"token endpoint unreachable ({error})")
    if response.status_code != 200:
        return _fail(
            ExitCode.TOKEN_REQUEST_FAILED,
            f"token endpoint answered {response.status_code}: {response.text[:300]}",
        )
    print(response.json()["access_token"])
    return int(ExitCode.OK)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mcps.trackflow_tools")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the MCP server")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    tools = commands.add_parser("tools", help="print the tool reference (discovery)")
    tools.add_argument("--json", action="store_true", help="the raw tools/list entries")
    tools.add_argument("--write", metavar="PATH", help="write to a file instead of stdout")
    token = commands.add_parser("token", help="get an access token by client credentials")
    token.add_argument("--client", choices=["agent", "tester"], required=True)
    token.add_argument("--scope", help="space-separated scopes to ask for")
    args = parser.parse_args(argv)
    handler = {"serve": _serve, "tools": _tools, "token": _token}[args.command]
    try:
        return handler(args)
    except ValidationError as error:
        problems = "; ".join(
            f"MCP_{'_'.join(str(p) for p in item['loc']).upper()}: {item['msg']}"
            for item in error.errors()
        )
        return _fail(ExitCode.CONFIG_INVALID, f"invalid settings - {problems}")
    except (MCPAuthAuthServerException, MCPAuthConfigException) as error:
        return _fail(
            ExitCode.AUTH_SERVER_UNAVAILABLE,
            f"authorization server metadata unavailable or invalid ({error})",
        )
    except PolicyViolation as error:
        return _fail(ExitCode.POLICY_VIOLATION, f"permission design violated - {error}")


if __name__ == "__main__":
    sys.exit(main())
