# Testing the MCP server from the MCP Playground (GitHub Codespaces)

The MCP Playground runs in the browser on the internet, so it cannot reach `localhost`. The server runs in a GitHub Codespace, and its port is forwarded with **public** visibility. The Playground then connects to the forwarded HTTPS URL, with an Auth0 access token.

## 1. Start the codespace

1. On GitHub, open the repository on branch `feature/mcp-company-tools`, then **Code → Codespaces → Create codespace**. [`.devcontainer/devcontainer.json`](../../.devcontainer/devcontainer.json) installs Python 3.14, uv and Node, runs `uv sync`, and forwards ports 8000 (API) and 8100 (MCP server).
2. Give the codespace its secrets (**Settings → Codespaces → Secrets**, or a `.env` written in the codespace; never commit them):
   - API: `JWT_SECRET_KEY`, `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`;
   - MCP server: `MCP_AUTH_ISSUER`, `MCP_AUTH_AUDIENCE`, `MCP_INVENTORY_EMAIL`, `MCP_INVENTORY_PASSWORD`, `MCP_TESTER_CLIENT_ID`, `MCP_TESTER_CLIENT_SECRET`;
   - the agent's client, for the `insufficient_scope` check: `AGENT_OAUTH_CLIENT_ID`, `AGENT_OAUTH_CLIENT_SECRET`.
3. Seed the data the flows use, in the codespace terminal:
   ```bash
   npm run seed:incidents                                   # the helpdesk incidents (TinyDB)
   uv run seed-user --email mcp-inventory@trackflow.com --role user   # the inventory service account
   ```

## 2. Run the API and the MCP server

```bash
npm run api            # terminal 1, :8000
```

Make port 8100 public, then point the server's resource URL at the forwarded address:

```bash
gh codespace ports visibility 8100:public -c "$CODESPACE_NAME"
export MCP_RESOURCE_URL="https://${CODESPACE_NAME}-8100.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}/mcp"
export MCP_HOST=0.0.0.0
# For a browser client, allow the Playground's origin (copy it from its address bar):
export MCP_CORS_ORIGINS="https://<the-playground-origin>"
npm run mcp            # terminal 2, :8100
```

Check from any machine:

```bash
curl -s "$MCP_RESOURCE_URL" -X POST -d '{}' -i | head -5      # 401 + WWW-Authenticate … resource_metadata=…
curl -s "https://${CODESPACE_NAME}-8100.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}/.well-known/oauth-protected-resource/mcp"
```

## 3. Connect the Playground

1. Get a token for the tester client (all four scopes): `npm run mcp:token -- --client tester`.
2. In the Playground, choose **Streamable HTTP**, paste the forwarded `…/mcp` URL, and add the header `Authorization: Bearer <token>`. If the Playground runs the OAuth flow itself, the metadata above leads it to Auth0; this needs Dynamic Client Registration enabled in the tenant ([`mcp-server.md`](mcp-server.md), "Auth0 setup").
3. Connect, then list the tools. Five appear, each with its description, input schema and output schema ([`TOOLS.md`](TOOLS.md)).

## 4. One complete flow per tool

| # | Tool | Arguments | Expected |
|---|---|---|---|
| 1 | `create_incident` | `{"title": "Playground test", "description": "Created from the MCP Playground.", "category": "other", "origin": "internal", "branch": "central"}` | structured `{id, title, status: "open", branch}`; keep the `id` |
| 2 | `get_incident` | `{"incident_id": "<id from 1>"}` | the incident, `status: "open"`, `reference: null` |
| 3 | `update_incident_status` | `{"incident_id": "<id>", "status": "in_progress"}` | the incident, `status: "in_progress"` (sent as `PATCH /api/incidents/<id>/status`) |
| 4 | `update_incident_status` | `{"incident_id": "<id>", "status": "open"}` | error `invalid_status_transition`, naming the allowed moves |
| 5 | `get_incident` | `{"reference": "12"}` | the seeded incident `TRF-000012` |
| 6 | `search_incidents` | `{"status": "open", "category": "lost_parcel", "limit": 5}` | `{incidents: [...≤5], total}` |
| 7 | `query_inventory` | `{"operation": "list_products", "name": "laptop"}` | the products with `current_stock` |
| 8 | `query_inventory` | `{"operation": "get_product", "product_id": "<id from 7>"}` | `{product: {id, current_stock}}` |
| 9 | `query_inventory` | `{"operation": "list_orders"}` | the movements, without `user_uuid` |

## 5. The write attempt on the inventory (must fail, explainably)

| # | Call | Expected |
|---|---|---|
| 10 | `query_inventory` with `{"operation": "record_inbound", "product_id": "<id>", "quantity": 5}` | `isError: true`, `{"error": "inventory_read_only", "message": "The inventory is read-only on this server: operation 'record_inbound' would call POST /inventory/products/inbound.", "tool": "query_inventory"}` |
| 11 | `query_inventory` with `{"operation": "list_products", "quantity": 3}` | `inventory_read_only`: "field(s) quantity only apply to stock movements" |
| 12 | `create_product` (a tool that does not exist), if the Playground allows a raw call | `inventory_read_only`, not `unknown_tool` |

In every case nothing reaches the inventory: the audit log shows `"upstream": []`, and `/inventory/orders` is unchanged afterwards. Why it is refused rather than missing: [`mcp-server.md`](mcp-server.md), decision 2.

## 6. Authorization failures

| # | Call | Expected |
|---|---|---|
| 13 | Reconnect with the agent's token (`npm run mcp:token -- --client agent`), then call `create_incident` | `insufficient_scope`, `required_scopes: ["incidents:create"]`, `granted_scopes: ["incidents:read", "inventory:read"]` |
| 14 | Connect with no token, or a modified one | HTTP 401 `missing_auth_header` / `invalid_token`; the tools cannot even be listed |

## 7. Evidence

`logs/mcp_tool_calls.jsonl` in the codespace has one line per call above: tool, client, outcome, error code and upstream requests. Copy the relevant lines into the results below.

## Results

**Local rehearsal, 2026-11-12.** This was not the Playground: the same 14 rows were run through a real MCP client against `npm run mcp`, with a local OIDC stand-in for Auth0 ([`mcp-server.md`](mcp-server.md), "Verified live"). It confirms the expected column above.

| # | Observed | OK |
|---|---|---|
| 1 | `{id, title: "Playground test", status: "open", branch: "central"}` | ✓ |
| 2–3 | `open`, then `in_progress` | ✓ |
| 4 | `invalid_status_transition`: "An incident that is 'in_progress' can only move to 'resolved' or 'discarded', not 'open'." | ✓ |
| 5–6 | `TRF-000012`; the open lost-parcel incidents with `total` | ✓ |
| 7–9 | Dell Laptop 15 inch, 8 units; its availability; the movements without `user_uuid` | ✓ |
| 10–12 | `inventory_read_only` ×3; stock still 8 afterwards | ✓ |
| 13 | `insufficient_scope` (`incidents:create`) | ✓ |
| 14 | HTTP 401 before `tools/list` | ✓ |

**MCP Playground from Codespaces, with the real Auth0 tenant:** pending. Fill in the date, the Playground used, and the observed result of each row.
