"""The HTTP application: Streamable HTTP behind MCP Auth.

Why Streamable HTTP and not stdio: this server is consumed by several remote
clients - the LangGraph agent inside the API, the MCP Playground, any other
team's agent - so it runs as its own network service. stdio would tie it to
one local client process that passes credentials through its environment;
the MCP authorization spec (OAuth bearer tokens, Protected Resource Metadata,
`WWW-Authenticate` discovery) is defined for HTTP transports. The endpoint is
**stateless** (`stateless_http=True`): every request carries its own token and
is validated on its own, no session ties a client to one process, and one
request's auth context cannot leak into another's.

Layout, outermost first:

    CORSMiddleware                       (only with MCP_CORS_ORIGINS; preflights never hit auth)
    /.well-known/oauth-protected-resource/<path>   MCP Auth metadata, public
    /healthz                                       liveness, public, says nothing else
    /<path> (e.g. /mcp)                            the MCP endpoint:
        Host/Origin guard (FastMCP, DNS-rebinding protection; hosts from MCP_RESOURCE_URL)
        AuthRejectionLog                 (audit record for every 401/403)
        MCP Auth bearer middleware       (JWT: signature, expiry, issuer, audience)
        AuthContextBridge                (AuthInfo onto the request for ScopeGuard)
        FastMCP                          (audit -> errors -> read-only guard -> scopes -> tool)

There is no way to build the endpoint without MCP Auth in front of it.
"""

from jwt import PyJWKClient
from mcpauth.config import AuthServerConfig
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Mount, Route

from mcps.trackflow_tools.audit import AuditSink, AuthRejectionLog
from mcps.trackflow_tools.auth import AuthContextBridge, build_auth, fetch_authorization_server
from mcps.trackflow_tools.config import McpServerSettings
from mcps.trackflow_tools.server import build_server
from mcps.trackflow_tools.upstream import Upstream

CORS_ALLOWED_HEADERS = [
    "authorization",
    "content-type",
    "mcp-protocol-version",
    "mcp-session-id",
    "last-event-id",
]
CORS_EXPOSED_HEADERS = ["mcp-session-id", "www-authenticate"]


async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def build_app(
    settings: McpServerSettings,
    *,
    authorization_server: AuthServerConfig | None = None,
    jwks: PyJWKClient | None = None,
    upstream: Upstream | None = None,
    sink: AuditSink | None = None,
) -> Starlette:
    """The ASGI app. `authorization_server`, `jwks`, `upstream` and `sink` are
    injectable for tests; by default they come from the settings."""
    server = authorization_server or fetch_authorization_server(settings)
    auth = build_auth(settings, server, jwks=jwks)
    audit_sink = sink or AuditSink(settings.audit_log_path)
    mcp = build_server(upstream or Upstream.from_settings(settings), sink=audit_sink)

    mcp_app = mcp.http_app(
        path=settings.endpoint_path,
        stateless_http=True,
        middleware=[
            Middleware(AuthRejectionLog, sink_factory=lambda: audit_sink),
            Middleware(auth.bearer),
            Middleware(AuthContextBridge, mcp_auth=auth.mcp_auth),
        ],
        host_origin_protection=True,
        allowed_hosts=settings.allowed_host_list,
        allowed_origins=settings.cors_origin_list,
    )
    routes: list[BaseRoute] = [
        *auth.mcp_auth.resource_metadata_router().routes,
        Route("/healthz", healthz, methods=["GET"]),
        Mount("/", app=mcp_app),
    ]
    middleware = (
        [
            Middleware(
                CORSMiddleware,
                allow_origins=settings.cors_origin_list,
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=CORS_ALLOWED_HEADERS,
                expose_headers=CORS_EXPOSED_HEADERS,
            )
        ]
        if settings.cors_origin_list
        else []
    )
    return Starlette(routes=routes, middleware=middleware, lifespan=mcp_app.lifespan)
