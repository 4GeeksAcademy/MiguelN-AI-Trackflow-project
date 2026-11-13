# TrackFlow MCP Server: company tools behind OAuth

The ticket is the tech lead's RFP "MCP Server for company tools" (`lead-instr.png`, checklist in `stages-todo.png`).

The knowledge agent used to call the Incidents Manager from inside its graph. Any other consumer (another agent, another team, a partner) would have had to re-implement those calls. This server exposes them once, through the Model Context Protocol, to **any authorized MCP client**:

- manage Incidents Manager tickets: create, update status, check status;
- query inventory data. **Never edit.**

The agent itself is now one of those clients ([`docs/agent/agent-graph.md`](../agent/agent-graph.md), Part 3).

- Code: [`mcps/trackflow_tools/`](../../mcps/trackflow_tools/)
- Tool reference, generated from `tools/list`: [`TOOLS.md`](TOOLS.md)
- MCP Playground runbook: [`PLAYGROUND.md`](PLAYGROUND.md)

## Architecture

```
 MCP clients                          TrackFlow MCP server (:8100)                 Existing services (:8000)
 ───────────                          ────────────────────────────                 ─────────────────────────
 knowledge agent ──┐   Streamable     CORS (browser clients only)
 (langchain-mcp-   │   HTTP + Bearer  Host/Origin guard (DNS rebinding)
  adapters)        ├─────────────────▶ MCP Auth: JWT signature, exp, iss, aud     ┌▶ Incidents Manager
 MCP Playground ───┤   token          AuthContextBridge                          │  /api/incidents
 other teams ──────┘                  FastMCP: audit → errors → read-only guard   │  (no auth)
                                               → scope guard → tool ──── HTTP ───┤
        ▲                                                                         └▶ Inventory
        │ token (client credentials / authorization code)                           /inventory/... (GET only,
        │                                                                             service account)
 Auth0 (OIDC) ◀── /.well-known/oauth-protected-resource/mcp names it ──
```

The server **relies on** the Incidents Manager and the inventory; it does not replace them. Every tool call is one or two requests to their real routes. Their rules stay theirs: the incident lifecycle is enforced by `PATCH /api/incidents/{id}/status`, and stock is computed by the inventory. The server never imports `services/`: it talks to the services over HTTP, like any client (a test enforces it). It shares only `trackflow_shared` with them, the package that already holds the incident vocabulary.

## The four design decisions

### 1. Transport: Streamable HTTP, stateless

stdio fits one local client that starts the server as a subprocess and hands it credentials through the environment. This server has several **remote** clients: the agent inside the API, the MCP Playground, other teams. So it runs as its own network service on Streamable HTTP.

What that implies for authentication:
- The MCP authorization spec is defined for HTTP transports: OAuth bearer tokens, Protected Resource Metadata (RFC 9728) and `WWW-Authenticate` discovery. An HTTP endpoint without auth would expose company tools to anyone who can reach it, so **the endpoint cannot be built without MCP Auth in front of it** (`app.build_app` has no unauthenticated path).
- Endpoints that browsers reach need CORS (`MCP_CORS_ORIGINS`), placed outermost so preflights are answered without a token. They also need a Host/Origin guard against DNS rebinding: hosts are derived from `MCP_RESOURCE_URL`, plus `MCP_ALLOWED_HOSTS`.
- `stateless_http=True`: every request carries its own token and is validated on its own. No session pins a client to one process, so the server can scale horizontally, and one request's auth context can never be read by another. A concurrency test caught exactly that bug during development: a shared state dict leaked auth context across requests. It is fixed in `AuthContextBridge`.

### 2. Permissions: least privilege per tool, and an inventory that is read-only by design

| Scope | Grants | Tools |
|---|---|---|
| `incidents:read` | Read incidents: one by id or helpdesk reference, or a search | `get_incident`, `search_incidents` |
| `incidents:create` | Register new incidents | `create_incident` |
| `incidents:update` | Move an incident along its lifecycle | `update_incident_status` |
| `inventory:read` | Read products, stock and movement history | `query_inventory` |

Each tool requires exactly the scope of what it does (`permissions.POLICIES`), and every call is checked. The agent's OAuth client is granted only `incidents:read inventory:read`.

MCP Auth's `bearer_auth_middleware(required_scopes=...)` applies to a whole endpoint and requires all of the listed scopes, while one `/mcp` endpoint carries every tool. So the endpoint-wide check validates the token (signature, expiry, issuer, audience), and `ScopeGuard` enforces each tool's `required_scopes` from MCP Auth's `AuthInfo.scopes`, with the same "all required" semantics. `tools/list` is not filtered: any valid token can discover every tool and the scope it requires.

**The inventory is read-only by design, not by omission.** Four layers make that hold:

1. **No write scope exists.** The only inventory scope is `inventory:read`. A token that carries `inventory:write` gains nothing (tested).
2. **The server refuses to start** if an inventory tool is declared as anything but `read`, or asks for any scope other than `inventory:read` (`validate_policies`, exit code 5).
3. **Explicit rejection, before validation.** `ReadOnlyInventoryGuard` refuses every modification attempt with `inventory_read_only` and a message saying what would have been written:
   - a write operation on `query_inventory` (`create_product`, `record_inbound`, `record_outbound`, which are the inventory API's own write routes, or any write-shaped verb);
   - a write-only field (`quantity`);
   - a call to an inventory write tool name (`create_product`, `recordInboundOrder`, ...).

   `operation` is a documented string rather than an enum precisely so that such an attempt reaches this guard and gets an explainable refusal instead of a generic schema error.
4. **The inventory HTTP client can only GET** (`ReadOnlyInventoryApi`). Anything else raises `ReadOnlyViolation` before a request is built. If a bug ever got that far, the call still ends as `inventory_read_only` and is logged as a bug.

The inventory service account's sign-in (`POST /auth/login`) is the only POST towards the inventory host, and it changes no data.

### 3. Discovery: an external agent understands the tools without a human

`tools/list` is the documentation. [`TOOLS.md`](TOOLS.md) is generated from it, and a test fails when the two differ.
- **Names and fields are the APIs' own**: `incident_id`, `reference`, `status`, `category`, `origin`, `branch`, `title`, `description`; `sku`, `name`, `warehouse`, `product_id`. Enums come from `trackflow_shared`, and the field limits are the APIs' limits (drift tests).
- **Descriptions** say what the tool does and which route it calls, when to use it, the scope it requires, and every error it can return. The lifecycle (`open -> in_progress | discarded; …`) is written into `update_incident_status`, generated from the shared `ALLOWED_TRANSITIONS`.
- **Every input field is described**, and unknown fields are refused (`additionalProperties: false`).
- **Output schemas**: every tool returns typed structured content mirroring the API response models.
- **Annotations**: `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`, and a title. `_meta` repeats `required_scopes`, `domain` and `access`.
- **Server-wide**: the server's `instructions` list the tools, the scopes and every error code. The Protected Resource Metadata lists `scopes_supported` and the authorization server.
- `npm run mcp:tools` prints all of it, the MCP equivalent of `--help` (`--json` for the raw entries).

### 4. The agent consumes the server, with the same routing

The graph node that called the Incidents Manager is replaced by an MCP client node, and so is the stock node:
- `lookup_ticket` became `lookup_ticket_mcp`, and `lookup_stock` became `lookup_stock_mcp`.
- The new nodes load the server's tools with `langchain-mcp-adapters`, keep only `get_incident`, `search_incidents` and `query_inventory`, and invoke them with the agent's own read-scoped token.
- They return the same result models, so the planner, the routers and the fallbacks did not change.
- The direct implementation (`services/agent/tools/incidents.py`, `inventory.py`, `http.py`, `auth.py`) is **deleted**, and a test guards that the agent has no other path to the services.
- The eval `test_the_routing_is_unchanged_since_the_direct_tools` compares every case's decisions with the Part 2 recording, frozen in `data/eval/agent/routing-baseline.json` before the migration.

Details: [`docs/agent/agent-graph.md`](../agent/agent-graph.md).

## Tools

| Tool | What | Calls | Scope |
|---|---|---|---|
| `create_incident` | Register a new incident (starts `open`) | `POST /api/incidents` | `incidents:create` |
| `update_incident_status` | Move an incident along its lifecycle | `PATCH /api/incidents/{incident_id}/status` | `incidents:update` |
| `get_incident` | One incident by `incident_id` or helpdesk `reference` (`482`, `TRF-482`, `TRF-000482`) | `GET /api/incidents/{id}` or `GET /api/incidents?reference=` | `incidents:read` |
| `search_incidents` | Incidents by `status`, `category`, `origin`, `branch` (AND), up to `limit`, with `total` | `GET /api/incidents` | `incidents:read` |
| `query_inventory` | `list_products` (filters `sku`, `name`, `warehouse`), `get_product` (`product_id`), `list_orders` (`warehouse`, `product_id`). Read-only. | `GET /inventory/products`, `/products/{id}`, `/orders` | `inventory:read` |

Full schemas: [`TOOLS.md`](TOOLS.md).

## Authentication with MCP Auth

[`mcps/trackflow_tools/auth.py`](../../mcps/trackflow_tools/auth.py). MCP Auth (`mcpauth` 0.2.0b1) runs in **resource-server mode**. FastMCP's own auth layer is not used.

1. **Protected Resource Metadata** at `/.well-known/oauth-protected-resource/mcp`: `resource` (= `MCP_RESOURCE_URL`), `authorization_servers` (= the Auth0 tenant, from its OIDC metadata, fetched at startup), `scopes_supported` and `bearer_methods_supported: ["header"]`. It is public, as the spec requires.
2. **Bearer JWT validation** on `/mcp`, before any MCP method runs (`tools/list` included):
   - signature against the tenant's JWKS (RS256);
   - `exp`, with MCP Auth's 60 s leeway;
   - `iss` against the configured issuer;
   - `aud` against `MCP_AUTH_AUDIENCE` (the Auth0 API identifier), so a token minted for another API is refused.
3. **Scopes** from the token's `scope` claim (`AuthInfo.scopes`), checked per tool by `ScopeGuard`.

`mcpauth` 0.2's built-in `"jwt"` mode creates a new `PyJWKClient` for every request, so it would download the JWKS again, synchronously, each time. `make_verifier` composes MCP Auth's own `create_verify_jwt` with **one cached** `PyJWKClient` (re-read hourly, or when a new `kid` appears), plus MCP Auth's `TokenVerifier` for the issuer. The validation is still MCP Auth's, and its failures are still MCP Auth's 401s. A test proves the JWKS is fetched once.

**Token passthrough is not done.** The client's token is never forwarded to the services:
- the incident routes need no credentials ([`docs/INCIDENTS.md`](../INCIDENTS.md));
- the inventory routes get the server's own service-account token (`MCP_INVENTORY_EMAIL` / `MCP_INVENTORY_PASSWORD`), cached until 60 s before it expires, with one re-sign-in on a 401.

## Errors and exit codes

Three layers answer, each with its own codes. None of them is a generic "error".

### HTTP, before MCP (MCP Auth)

| Status | `error` (body and `WWW-Authenticate`) | When |
|---|---|---|
| 401 | `missing_auth_header` | No `Authorization` header |
| 401 | `invalid_auth_header_format` / `missing_bearer_token` | Not `Bearer <token>` |
| 401 | `invalid_token` | Malformed, bad signature, unknown key, or expired |
| 401 | `invalid_issuer` | Issued by another authorization server |
| 401 | `invalid_audience` | Issued for another API |
| 500 | `server_error` | The authorization server's configuration cannot be used |
| 421 | (plain text) | Host header not allowed (DNS-rebinding guard) |
| 403 | (plain text) | Browser Origin not in `MCP_CORS_ORIGINS` |

Every 401 carries `WWW-Authenticate: Bearer error="…", error_description="…", resource_metadata="https://…/.well-known/oauth-protected-resource/mcp"`, so a client knows where to get a token.

### Tool errors (`isError: true`, JSON text)

The text of the error result is `{"error": "<code>", "message": "...", ...details}`:

| Code | Kind | Details |
|---|---|---|
| `insufficient_scope` | authorization | `required_scopes`, `granted_scopes` |
| `inventory_read_only` | authorization (by design) | `tool`; the message names the write that was refused |
| `invalid_arguments` | validation | `problems: [{field, message}]` |
| `unknown_tool` | validation | |
| `incident_not_found`, `product_not_found` | the service's answer | `http_status` |
| `invalid_status_transition` | the service's lifecycle | `field: "status"`; the message lists the allowed moves |
| `upstream_rejected` | the service's validation | `field`, `http_status` |
| `upstream_unavailable` | the service is down, slow or failing | `failure`: `timeout`, `unreachable`, `http_5xx`, `invalid_response` |
| `upstream_auth_failed` | the service account | `failure: not_configured`, or the status |
| `internal_error` | anything else | Details only in the server log (`mask_error_details`) |

### Exit codes (`python -m mcps.trackflow_tools`)

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | Unexpected error |
| 2 | Settings missing or invalid (e.g. no `MCP_RESOURCE_URL` / `MCP_AUTH_ISSUER`). The message names the variable. |
| 3 | The authorization server's metadata could not be fetched or is invalid, so no token could be validated. The server does not start. |
| 4 | `token`: the token endpoint refused the client credentials |
| 5 | The registered tools break the permission design (e.g. an inventory tool declared as a write). The server does not start. |

## Audit log: every invocation is traceable

[`audit.py`](../../mcps/trackflow_tools/audit.py). Records go to the logger `trackflow.mcp.audit` and are appended as JSON lines to `MCP_AUDIT_LOG_PATH` (default `logs/mcp_tool_calls.jsonl`, git-ignored). A refused or failed call is logged like a successful one.

```json
{"event": "tool_call", "ts": "2026-11-12T22:10:04.512+00:00", "request_id": "3",
 "tool": "get_incident", "client_id": "trackflow-agent", "subject": "trackflow-agent@clients",
 "scopes": ["incidents:read", "inventory:read"], "arguments": {"reference": "TRF-000012"},
 "outcome": "ok", "error": null,
 "upstream": [{"method": "GET", "path": "/api/incidents", "status": 200}], "duration_ms": 18.4}
```

- **Which tool**: `tool`. **Which client**: `client_id` (`azp` / `client_id`), `subject` and the granted `scopes`. **What result**: `outcome` and `error`, plus the requests made upstream and the duration.
- Arguments that identify or filter (ids, references, statuses, operations, SKUs) are logged by value. Free text (an incident's title or description, a product name) is logged as its length, e.g. `"<38 chars>"`.
- Tokens are never logged.
- Every 401/403 MCP Auth answers is recorded as `{"event": "auth_rejected", "status": 401, "error": "invalid_token", "path": "/mcp", ...}`.

## Running it

```bash
npm run mcp                        # serve (MCP_HOST:MCP_PORT, default 127.0.0.1:8100)
npm run mcp:tools                  # the tool reference; -- --json, -- --write docs/mcp/TOOLS.md
npm run mcp:token -- --client tester   # an access token for curl / the Playground
```

The API must be running too (`npm run api`): the server calls it. In Compose, the `mcp` service runs the same image as `api`, on `127.0.0.1:8100`. It reaches the API as `http://api:8000`, and the API reaches it as `http://mcp:8100/mcp`.

| Setting | Default | |
|---|---|---|
| `MCP_RESOURCE_URL` | required | The endpoint's public URL; its path is where `/mcp` is served |
| `MCP_AUTH_ISSUER` | required | The Auth0 tenant URL, with its trailing slash |
| `MCP_AUTH_AUDIENCE` | `MCP_RESOURCE_URL` | The Auth0 API identifier the tokens must carry |
| `MCP_AUTH_SERVER_TYPE` | `oidc` | `oauth` for a pure OAuth 2.0 server (RFC 8414 metadata) |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8100` | |
| `MCP_INCIDENTS_API_URL` / `MCP_INVENTORY_API_URL` | `http://localhost:8000` | |
| `MCP_INVENTORY_EMAIL` / `MCP_INVENTORY_PASSWORD` | unset | The inventory service account; unset → `upstream_auth_failed / not_configured` |
| `MCP_UPSTREAM_TIMEOUT_SECONDS` | 4 (≤ 10) | Per request to a service |
| `MCP_CORS_ORIGINS` / `MCP_ALLOWED_HOSTS` | empty | Browser origins (e.g. the Playground) / extra Host headers |
| `MCP_AUDIT_LOG_PATH` | `logs/mcp_tool_calls.jsonl` | |
| `MCP_TESTER_CLIENT_ID` / `_SECRET` | unset | Only for `mcp:token -- --client tester` |

### Auth0 setup (once)

1. **APIs → Create API**:
   - Name: `TrackFlow MCP Server`.
   - Identifier: e.g. `https://mcp.trackflow.dev/mcp`. This is `MCP_AUTH_AUDIENCE` and `AGENT_OAUTH_AUDIENCE`; it is a name and does not have to resolve.
   - Signing algorithm: RS256.
2. In that API, **Permissions**: add `incidents:read`, `incidents:create`, `incidents:update` and `inventory:read`.
3. **Applications → Create Application → Machine to Machine** `trackflow-agent`: authorize it for the API with **`incidents:read` and `inventory:read` only**. Its client id and secret become `AGENT_OAUTH_CLIENT_ID` / `AGENT_OAUTH_CLIENT_SECRET`.
4. A second M2M application, `trackflow-mcp-tester`, with all four permissions, to try the write tools from the Playground (`MCP_TESTER_CLIENT_ID` / `_SECRET`).
5. In `.env`, using the tenant's domain (e.g. `dev-abc123.us.auth0.com`):
   ```
   MCP_RESOURCE_URL=http://localhost:8100/mcp
   MCP_AUTH_ISSUER=https://dev-abc123.us.auth0.com/
   MCP_AUTH_AUDIENCE=https://mcp.trackflow.dev/mcp
   MCP_INVENTORY_EMAIL=mcp-inventory@trackflow.com
   MCP_INVENTORY_PASSWORD=...
   AGENT_MCP_URL=http://localhost:8100/mcp
   AGENT_OAUTH_TOKEN_URL=https://dev-abc123.us.auth0.com/oauth/token
   AGENT_OAUTH_CLIENT_ID=...
   AGENT_OAUTH_CLIENT_SECRET=...
   AGENT_OAUTH_AUDIENCE=https://mcp.trackflow.dev/mcp
   ```
6. Create the inventory service account once: `uv run seed-user --email mcp-inventory@trackflow.com --role user`.
7. Optional, for a client that runs the full OAuth flow itself, such as the Playground's "connect" button: in **Settings → Advanced**, enable Dynamic Client Registration, and the resource-parameter compatibility profile so the `resource` parameter is accepted as the audience. Without it, use a token from `npm run mcp:token`.

Auth0's free tier limits machine-to-machine tokens per month. The agent caches its token until a minute before it expires, so it asks for one roughly per day, not per question.

## Verified live (2026-11-12, local rehearsal)

Run with the real processes, before Auth0 was configured. A local OIDC stand-in on `127.0.0.1:9400` served discovery, the JWKS and Auth0-shaped client-credentials tokens (`azp`, `scope`, `aud`). The API ran on :8010 against a throwaway `postgres:17-alpine` with the seeded inventory (45 / 8 / 120), and `npm run mcp` ran on :8100.

- Startup: the OIDC metadata was fetched over HTTP; `/healthz`, the Protected Resource Metadata, and a 401 with `WWW-Authenticate … resource_metadata=…` for an untokened `tools/list`.
- `mcp:token`: a token for the tester and for the agent; exit 4 on a wrong secret.
- The 14 rows of [`PLAYGROUND.md`](PLAYGROUND.md), through a real MCP client (fastmcp, Streamable HTTP):
  - every tool flow passed;
  - the forbidden move came back as `invalid_status_transition` ("An incident that is 'in_progress' can only move to 'resolved' or 'discarded', not 'open'.");
  - the three inventory write attempts came back as `inventory_read_only`, and stock stayed at 8;
  - the agent's token got `insufficient_scope` on `create_incident`;
  - a forged token was refused with 401 before `tools/list`.
- The agent CLI, with the real 4Geeks models: "What's the status of ticket 12, and do we have stock of the Dell laptop?" was answered "Ticket TRF-000012 is **open**. The Dell Laptop 15 inch is in stock in Zaragoza: **8 units**." through `lookup_ticket_mcp` (67.7 ms) and `lookup_stock_mcp` (48.9 ms). The audit log shows `client_id: trackflow-agent` with the read scopes.
- With the MCP server stopped: `lookup_ticket_mcp → unavailable (mcp_unreachable, 34 ms)`, then the fixed honest answer, with no generation.
- `POST /agent/query` on the API (the graph inside FastAPI's threadpool): "45 units of the black running shoes in Monterrey", `sources: [inventory_manager]`.

Still to do with the real Auth0 tenant and in the Playground: [`PLAYGROUND.md`](PLAYGROUND.md).

## Tests

`tests/mcps/` (128 tests). Everything runs offline: an RSA key stands in for Auth0, and the JWKS and OIDC metadata are served in process. The Incidents Manager is the real FastAPI app. The inventory is a `MockTransport` fake (it needs PostgreSQL), guarded by contract drift tests.

| File | Proves |
|---|---|
| `test_mcp_auth.py` | The metadata document; the 401 matrix with `WWW-Authenticate` (no token, wrong format, malformed, expired, unknown key, wrong audience, wrong issuer); `tools/list` refused without a token; a token with no scope discovers but cannot call; `/healthz`; the host guard; CORS preflight; **per-request scopes under concurrency**; JWKS fetched once; settings validation |
| `test_mcp_permissions.py` | Each tool refuses a token holding every other scope; the agent's read scopes cannot create or update; **nine inventory write attempts → `inventory_read_only`** with nothing sent upstream (operations, `quantity`, write tool names, camelCase); `inventory:write` in a token gains nothing; the GET-only client; the last-layer refusal; the startup policy check |
| `test_mcp_tools.py` | Create → update through `PATCH …/status` (method and path asserted) → get, against the real Incidents Manager; an invalid transition with the service's reason; not found; references in every form; search counts; `invalid_arguments` naming the field; `upstream_rejected`, `upstream_unavailable`; every inventory read; the service account re-signing in after a 401; **field-name drift against the API models**; the server never imports `services/` |
| `test_mcp_discovery.py` | Every tool has a description with "Use it when", its scopes and errors, annotations, `_meta.required_scopes`, an output schema and described inputs; `TOOLS.md` matches `tools/list`; CLI exit codes 2, 3 and 5 |
| `test_mcp_audit_log.py` | One record per call (tool, client, scopes, arguments, outcome, upstream); refusals logged with their code; no free text or token in the log |
| `test_agent_over_mcp.py` | **The agent through the real server on a local port**: tickets, searches, not found, stock by name and SKU, `insufficient_scope` when the agent lacks a scope, an unverifiable token retried once and refused, a down server; and the whole graph answering from MCP with the same routing decisions, or falling back honestly |

## Known limitations

- `mcpauth` 0.2.0b1 is a pre-release, the only one with resource-server mode. The pin (`>=0.2.0b1,<0.3`) and its reason are in `pyproject.toml`. `fastmcp` stays below 4, because every `langchain-mcp-adapters` release needs `mcp<2`.
- One audience per server: tokens are validated against a single Auth0 API identifier.
- The incident routes behind the server are still unauthenticated for direct callers on the API port. The server is the authenticated, scoped entry point; protecting the API itself is a separate decision ([`docs/INCIDENTS.md`](../INCIDENTS.md)).
- Step-up authorization is not offered: a missing scope is a tool error (`insufficient_scope`), not an HTTP 403 asking the client to re-authorize.
- `list_products` name matching is literal, word for word: "portátil" does not find "laptop".
