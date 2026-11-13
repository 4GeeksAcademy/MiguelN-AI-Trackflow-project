"""TrackFlow company tools: the Incidents Manager and the inventory, over MCP.

An OAuth-protected MCP server (Streamable HTTP, resource-server mode through
MCP Auth) in front of the services TrackFlow already runs:

- incidents: create, update status (through the lifecycle endpoint
  `PATCH /api/incidents/{id}/status`), read one, search;
- inventory: query only - every modification attempt is explicitly refused.

Modules:

- `config`: settings (`MCP_*`).
- `permissions`: scopes, the per-tool policy and the read-only inventory rule.
- `errors`: the error codes a client can receive, and the CLI exit codes.
- `contracts`: the tools' output models, mirroring the APIs' response models.
- `upstream`: the HTTP clients for the Incidents Manager and the inventory.
- `tools/`: the tool functions.
- `middleware`: audit log, error shaping, read-only guard, scope guard.
- `auth`: MCP Auth wiring (Protected Resource Metadata, bearer JWT validation).
- `server` / `app`: the FastMCP server and the Starlette app around it.
- `discovery`: the tool reference rendered from `tools/list`.

Run it with `npm run mcp`; see `docs/mcp/mcp-server.md`.
"""
