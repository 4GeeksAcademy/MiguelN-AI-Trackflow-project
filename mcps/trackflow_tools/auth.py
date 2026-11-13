"""OAuth for the MCP endpoint, through MCP Auth (`mcpauth`) in resource-server mode.

FastMCP's own auth layer is not used. MCP Auth provides the three pieces the
MCP authorization spec asks of a protected resource:

1. **Protected Resource Metadata** (RFC 9728) at
   `/.well-known/oauth-protected-resource/<endpoint path>`: the resource
   identifier, the authorization server (Auth0) and `scopes_supported`, so a
   client can discover where to get a token and which scopes exist.
2. **Bearer JWT validation** on the MCP endpoint, before any MCP method runs -
   `tools/list` included: signature against the authorization server's JWKS,
   expiry, issuer and audience. A failure is a 401 whose
   `WWW-Authenticate: Bearer error=..., resource_metadata=...` points the
   client to the metadata above.
3. **Scopes**: the validated token's `AuthInfo.scopes`, which `ScopeGuard`
   checks against each tool's `required_scopes`.

One deliberate change from MCP Auth's built-in `"jwt"` mode: that mode creates
a new `PyJWKClient` - and so downloads the JWKS again, synchronously - on every
request. `make_verifier` composes MCP Auth's own `create_verify_jwt` with one
cached `PyJWKClient`, and MCP Auth's `TokenVerifier` for the issuer check, so
the validation is still MCP Auth's and its errors still become its 401s.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from fastmcp.server.dependencies import get_http_request
from jwt import PyJWKClient
from mcpauth import MCPAuth
from mcpauth.auth.token_verifier import TokenVerifier
from mcpauth.config import AuthServerConfig, AuthServerType
from mcpauth.exceptions import (
    AuthServerExceptionCode,
    MCPAuthAuthServerException,
)
from mcpauth.types import (
    AuthInfo,
    ResourceServerConfig,
    ResourceServerMetadata,
    VerifyAccessTokenFunction,
)
from mcpauth.utils import create_verify_jwt, fetch_server_config
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from mcps.trackflow_tools.config import McpServerSettings
from mcps.trackflow_tools.permissions import ALL_SCOPES

RESOURCE_NAME: Final = "TrackFlow company tools (Incidents Manager + read-only inventory)"
# Where `AuthContextBridge` leaves the validated token's `AuthInfo`.
STATE_KEY: Final = "auth_info"
# Re-read the JWKS at most this often (seconds); a key rotation is picked up after it.
JWKS_CACHE_SECONDS: Final = 3600


def fetch_authorization_server(settings: McpServerSettings) -> AuthServerConfig:
    """The authorization server's metadata (OIDC discovery). Raises MCP Auth's
    exceptions when it cannot be fetched or is invalid; the CLI exits 3."""
    return fetch_server_config(settings.auth_issuer, type=AuthServerType(settings.auth_server_type))


def make_verifier(
    server: AuthServerConfig, jwks: PyJWKClient | None = None
) -> VerifyAccessTokenFunction:
    """MCP Auth's JWT verification with one cached JWKS client."""
    if not server.metadata.jwks_uri:
        raise MCPAuthAuthServerException(AuthServerExceptionCode.MISSING_JWKS_URI)
    client = jwks or PyJWKClient(
        server.metadata.jwks_uri, cache_keys=True, lifespan=JWKS_CACHE_SECONDS
    )
    verify_signature = create_verify_jwt(client)
    issuers = TokenVerifier([server])

    def verify(token: str) -> AuthInfo:
        # Signature and expiry first: a malformed token becomes MCP Auth's
        # `invalid_token` instead of an exception the middleware cannot map.
        info = verify_signature(token)
        issuers.validate_jwt_issuer(info.issuer)
        return info

    return verify


@dataclass(frozen=True)
class AuthSetup:
    mcp_auth: MCPAuth
    bearer: type[BaseHTTPMiddleware]


def build_auth(
    settings: McpServerSettings, server: AuthServerConfig, *, jwks: PyJWKClient | None = None
) -> AuthSetup:
    metadata = ResourceServerMetadata(
        resource=settings.resource_url,
        authorization_servers=[server],
        scopes_supported=list(ALL_SCOPES),
        bearer_methods_supported=["header"],
        resource_name=RESOURCE_NAME,
    )
    mcp_auth = MCPAuth(protected_resources=ResourceServerConfig(metadata=metadata))
    bearer = mcp_auth.bearer_auth_middleware(
        make_verifier(server, jwks),
        resource=settings.resource_url,
        audience=settings.expected_audience,
        show_error_details=False,
    )
    return AuthSetup(mcp_auth=mcp_auth, bearer=bearer)


class AuthContextBridge:
    """ASGI middleware inside MCP Auth's: puts the validated `AuthInfo` on the request.

    MCP Auth keeps it in a context variable set for the request's task. The
    MCP layer runs tools elsewhere, but always hands them the HTTP request that
    carried the call, so the request is where per-request auth must travel.
    """

    def __init__(self, app: ASGIApp, mcp_auth: MCPAuth) -> None:
        self.app = app
        self._mcp_auth = mcp_auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            # A new dict for this request only. `scope["state"]` may be one dict
            # shared by every request (servers are only expected to copy the
            # lifespan state; asgi-lifespan does not), so writing into it would
            # let concurrent requests read each other's token.
            scope["state"] = {**scope.get("state", {}), STATE_KEY: self._mcp_auth.auth_info}
        await self.app(scope, receive, send)


def request_auth_info() -> AuthInfo | None:
    """The `AuthInfo` of the HTTP request behind the current MCP call, or None
    (no HTTP request, or no validated token - and then every scope check fails)."""
    try:
        request = get_http_request()
    except RuntimeError:
        return None
    info = getattr(request.state, STATE_KEY, None)
    return info if isinstance(info, AuthInfo) else None


AuthResolver = Callable[[], AuthInfo | None]
