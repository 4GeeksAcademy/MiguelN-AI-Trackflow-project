"""The FastMCP server: tools, middleware and the permission check, without HTTP.

`build_server` is transport-free so discovery (`npm run mcp:tools`) and tests
can list the tools without settings. It is exposed to clients only through
`app.build_app`, which always puts MCP Auth in front of it.
"""

from fastmcp import FastMCP

from mcps.trackflow_tools.audit import AuditSink
from mcps.trackflow_tools.auth import AuthResolver, request_auth_info
from mcps.trackflow_tools.errors import ERROR_DESCRIPTIONS
from mcps.trackflow_tools.middleware import (
    AuditMiddleware,
    ErrorShapingMiddleware,
    ReadOnlyInventoryGuard,
    ScopeGuard,
)
from mcps.trackflow_tools.permissions import SCOPE_DESCRIPTIONS, validate_policies
from mcps.trackflow_tools.tools import incidents, inventory
from mcps.trackflow_tools.upstream import Upstream

SERVER_NAME = "trackflow-company-tools"
SERVER_VERSION = "1.0.0"

INSTRUCTIONS = (
    "TrackFlow's company tools over MCP, in front of the services TrackFlow already runs.\n\n"
    "- Incidents Manager (support tickets): create_incident, update_incident_status "
    "(through the lifecycle endpoint), get_incident (by id or helpdesk reference such as "
    "'TRF-000482' or '482'), search_incidents.\n"
    "- Inventory, READ-ONLY: query_inventory (products and stock, one product's stock, "
    "movement history). Nothing on this server can modify inventory data; any attempt is "
    "refused with the error `inventory_read_only`.\n\n"
    "Every call needs an OAuth access token; each tool requires its own scope:\n"
    + "\n".join(f"- `{scope.value}`: {text}" for scope, text in SCOPE_DESCRIPTIONS.items())
    + "\n\nA refused or failed call is a tool error whose text is JSON "
    '`{"error": "<code>", "message": "..."}`. Codes:\n'
    + "\n".join(f"- `{code.value}`: {text}" for code, text in ERROR_DESCRIPTIONS.items())
)


def build_server(
    upstream: Upstream, *, sink: AuditSink, auth: AuthResolver = request_auth_info
) -> FastMCP:
    mcp = FastMCP(
        name=SERVER_NAME,
        instructions=INSTRUCTIONS,
        version=SERVER_VERSION,
        mask_error_details=True,
        middleware=[
            AuditMiddleware(sink, auth),
            ErrorShapingMiddleware(),
            ReadOnlyInventoryGuard(),
            ScopeGuard(auth),
        ],
    )
    registered = incidents.register(mcp, upstream.incidents)
    registered += inventory.register(mcp, upstream.inventory)
    # Refuses to build a server whose tools break the permission design.
    validate_policies(registered)
    return mcp
