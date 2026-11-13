"""MCP server settings, read from the environment and the repository's `.env`.

Every variable starts with `MCP_`. Two are required, because a server that
does not know who issues its tokens, or which resource it is, cannot validate
anything: `MCP_RESOURCE_URL` and `MCP_AUTH_ISSUER`. Without them the server
does not start (`errors.ExitCode.CONFIG_INVALID`).
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR: Final = Path(__file__).resolve().parents[2]

DEFAULT_SERVICE_URL: Final = "http://localhost:8000"
DEFAULT_PORT: Final = 8100
DEFAULT_UPSTREAM_TIMEOUT_SECONDS: Final = 4.0
MAX_UPSTREAM_TIMEOUT_SECONDS: Final = 10.0
# Git-ignored (`logs/`). One JSON line per tool invocation.
DEFAULT_AUDIT_LOG_PATH: Final = ROOT_DIR / "logs" / "mcp_tool_calls.jsonl"

_DEFAULTS: Final[dict[str, Any]] = {
    "host": "127.0.0.1",
    "port": DEFAULT_PORT,
    "auth_server_type": "oidc",
    "auth_audience": None,
    "incidents_api_url": DEFAULT_SERVICE_URL,
    "inventory_api_url": DEFAULT_SERVICE_URL,
    "inventory_email": None,
    "inventory_password": None,
    "upstream_timeout_seconds": DEFAULT_UPSTREAM_TIMEOUT_SECONDS,
    "cors_origins": "",
    "allowed_hosts": "",
    "audit_log_path": DEFAULT_AUDIT_LOG_PATH,
    "tester_client_id": None,
    "tester_client_secret": None,
}


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class McpServerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)

    # The MCP endpoint's public URL, e.g. `https://<codespace>-8100.app.github.dev/mcp`.
    # It is the protected resource's identifier in its metadata, and its path is
    # where the endpoint is served.
    resource_url: str
    # The authorization server that issues the access tokens (Auth0: the tenant
    # URL with its trailing slash, `https://<tenant>.auth0.com/`).
    auth_issuer: str
    auth_server_type: Literal["oidc", "oauth"] = "oidc"
    # The `aud` a token must carry. Auth0 puts the API identifier there; unset,
    # the resource URL is expected (RFC 8707 resource indicators).
    auth_audience: str | None = None

    # The services this server exposes. It calls them over HTTP, never by import.
    incidents_api_url: str = DEFAULT_SERVICE_URL
    inventory_api_url: str = DEFAULT_SERVICE_URL
    # The inventory routes require a TrackFlow login. The server signs in as this
    # service account; MCP clients never see these credentials, and their own
    # OAuth tokens are never forwarded to the services.
    inventory_email: str | None = None
    inventory_password: SecretStr | None = None
    upstream_timeout_seconds: float = Field(
        default=DEFAULT_UPSTREAM_TIMEOUT_SECONDS, gt=0, le=MAX_UPSTREAM_TIMEOUT_SECONDS
    )

    # Browser clients allowed to call the endpoint (comma-separated origins).
    cors_origins: str = ""
    # Extra Host headers to accept besides the resource URL's host and localhost
    # (comma-separated; e.g. `mcp` for the Compose network).
    allowed_hosts: str = ""
    # Where the audit log is appended. A blank value keeps the default; the
    # logger `trackflow.mcp.audit` receives every record either way.
    audit_log_path: Path = DEFAULT_AUDIT_LOG_PATH

    # Only for `python -m mcps.trackflow_tools token --client tester`: an OAuth
    # client with every scope, to try the write tools from the Playground.
    tester_client_id: str | None = None
    tester_client_secret: SecretStr | None = None

    @field_validator(*_DEFAULTS, mode="before")
    @classmethod
    def blank_is_default(cls, value: Any, info: ValidationInfo) -> Any:
        # Compose passes `${VAR:-}` through as an empty string.
        if isinstance(value, str) and not value.strip():
            return _DEFAULTS.get(info.field_name or "", value)
        return value

    @field_validator("resource_url", mode="after")
    @classmethod
    def resource_with_a_path(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("must be the endpoint's absolute http(s) URL")
        if not parts.path or parts.path == "/":
            raise ValueError("must include the endpoint's path, e.g. https://host/mcp")
        if parts.query or parts.fragment:
            raise ValueError("must not carry a query or a fragment")
        return value

    @field_validator("auth_issuer", mode="after")
    @classmethod
    def issuer_url(cls, value: str) -> str:
        # Kept exactly as the provider writes it: the issuer is compared with the
        # token's `iss` character for character (Auth0's ends with a slash).
        value = value.strip()
        if not value.startswith(("https://", "http://")):
            raise ValueError("must be the authorization server's issuer URL")
        return value

    @field_validator("incidents_api_url", "inventory_api_url", mode="after")
    @classmethod
    def http_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("must be an http:// or https:// base URL")
        return value

    @field_validator("audit_log_path", mode="after")
    @classmethod
    def relative_to_the_repository(cls, value: Path) -> Path:
        return value if value.is_absolute() else ROOT_DIR / value

    @property
    def endpoint_path(self) -> str:
        """`/mcp`: the path the endpoint is served at, taken from the resource URL."""
        return urlsplit(self.resource_url).path

    @property
    def expected_audience(self) -> str:
        return self.auth_audience or self.resource_url

    @property
    def cors_origin_list(self) -> list[str]:
        return _split(self.cors_origins)

    @property
    def allowed_host_list(self) -> list[str]:
        """Host headers the endpoint answers: the resource URL's host plus the extras."""
        resource_host = urlsplit(self.resource_url).hostname or ""
        return [host for host in [resource_host, *_split(self.allowed_hosts)] if host]

    def inventory_credentials(self) -> tuple[str, str] | None:
        if not self.inventory_email or self.inventory_password is None:
            return None
        return self.inventory_email, self.inventory_password.get_secret_value()


@lru_cache(maxsize=1)
def get_mcp_settings() -> McpServerSettings:
    return McpServerSettings()  # type: ignore[call-arg]  # required fields come from the environment
