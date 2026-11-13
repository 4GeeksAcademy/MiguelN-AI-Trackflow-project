"""Agent settings: where checkpoints, run traces and the agent's memory are stored,
and how the agent reaches the TrackFlow MCP server for live data.

Only what the agent itself needs, so the CLIs run without the API's settings
(JWT secret, Supabase). The model and Qdrant settings stay in `RagSettings`.

The agent no longer knows where the Incidents Manager or the inventory live:
it reads both through the MCP server (`mcps/trackflow_tools`), as an OAuth
client with its own client-credentials grant and only the read scopes.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR: Final = Path(__file__).resolve().parents[2]

# Git-ignored. One SQLite file holds LangGraph's checkpoints and the run traces.
DEFAULT_STORE_PATH: Final = ROOT_DIR / "data" / "agent" / "agent_runs.sqlite"
# Git-ignored. The agent's memory (MEM-092): conversations, memory proposals, the
# append-only decision log and the approved memories. Its own file, apart from
# the run traces and far from the knowledge base (Qdrant `trackflow_knowledge`),
# which the agent only ever reads.
DEFAULT_MEMORY_PATH: Final = ROOT_DIR / "data" / "agent" / "agent_memory.sqlite"

# The MCP server's endpoint (`npm run mcp`).
DEFAULT_MCP_URL: Final = "http://localhost:8100/mcp"
# Least privilege: the agent only reads. It cannot create or update incidents,
# and nothing can write to the inventory.
DEFAULT_OAUTH_SCOPES: Final = "incidents:read inventory:read"

# The whole budget of one live lookup through MCP (token, connection, tool call,
# and the service behind it). A lookup that runs out makes it `unavailable`;
# the run goes on.
DEFAULT_TOOL_TIMEOUT_SECONDS: Final = 5.0
MAX_TOOL_TIMEOUT_SECONDS: Final = 10.0

_DEFAULTS: Final[dict[str, Any]] = {
    "agent_store_path": DEFAULT_STORE_PATH,
    "agent_memory_path": DEFAULT_MEMORY_PATH,
    "agent_mcp_url": DEFAULT_MCP_URL,
    "agent_tool_timeout_seconds": DEFAULT_TOOL_TIMEOUT_SECONDS,
    "agent_oauth_token_url": None,
    "agent_oauth_client_id": None,
    "agent_oauth_client_secret": None,
    "agent_oauth_audience": None,
    "agent_oauth_scopes": DEFAULT_OAUTH_SCOPES,
}


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    agent_store_path: Path = DEFAULT_STORE_PATH
    agent_memory_path: Path = DEFAULT_MEMORY_PATH
    # The MCP server's Streamable HTTP endpoint.
    agent_mcp_url: str = DEFAULT_MCP_URL
    agent_tool_timeout_seconds: float = Field(
        default=DEFAULT_TOOL_TIMEOUT_SECONDS, gt=0, le=MAX_TOOL_TIMEOUT_SECONDS
    )
    # The agent's OAuth client (Auth0 machine-to-machine application). Unset,
    # every live lookup answers `unavailable / not_configured` without a request.
    agent_oauth_token_url: str | None = None
    agent_oauth_client_id: str | None = None
    agent_oauth_client_secret: SecretStr | None = None
    # The API the token is for: Auth0's `audience` (the MCP server's API identifier).
    agent_oauth_audience: str | None = None
    agent_oauth_scopes: str = DEFAULT_OAUTH_SCOPES

    @field_validator(*_DEFAULTS, mode="before")
    @classmethod
    def blank_is_default(cls, value: Any, info: ValidationInfo) -> Any:
        # Compose passes `${VAR:-}` through as an empty string.
        if isinstance(value, str) and not value.strip():
            return _DEFAULTS.get(info.field_name or "", value)
        return value

    @field_validator("agent_store_path", "agent_memory_path", mode="after")
    @classmethod
    def relative_to_the_repository(cls, value: Path) -> Path:
        # A relative path means the same file whichever directory a CLI runs from.
        return value if value.is_absolute() else ROOT_DIR / value

    @field_validator("agent_mcp_url", "agent_oauth_token_url", mode="after")
    @classmethod
    def http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("must be an http:// or https:// URL")
        return value

    def oauth_client(self) -> tuple[str, str, str] | None:
        """(token URL, client id, client secret), or None when any is unset."""
        if (
            not self.agent_oauth_token_url
            or not self.agent_oauth_client_id
            or self.agent_oauth_client_secret is None
        ):
            return None
        return (
            self.agent_oauth_token_url,
            self.agent_oauth_client_id,
            self.agent_oauth_client_secret.get_secret_value(),
        )


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
