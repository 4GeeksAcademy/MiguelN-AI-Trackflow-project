"""Agent settings: where checkpoints and run traces are stored, and how the tools
reach the services they read.

Only what the agent itself needs, so the CLIs run without the API's settings
(JWT secret, Supabase). The model and Qdrant settings stay in `RagSettings`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR: Final = Path(__file__).resolve().parents[2]

# Git-ignored. One SQLite file holds LangGraph's checkpoints and the run traces.
DEFAULT_STORE_PATH: Final = ROOT_DIR / "data" / "agent" / "agent_runs.sqlite"

# The incident and inventory managers are routers of this repository's API, so
# by default the tools call the API they run in.
DEFAULT_SERVICE_URL: Final = "http://localhost:8000"

# Per phase of one HTTP call (connect, write, read, pool). A service that does
# not answer in time makes the lookup return `unavailable`; the run goes on.
DEFAULT_TOOL_TIMEOUT_SECONDS: Final = 4.0
MAX_TOOL_TIMEOUT_SECONDS: Final = 5.0

_DEFAULTS: Final[dict[str, Any]] = {
    "agent_store_path": DEFAULT_STORE_PATH,
    "incidents_api_url": DEFAULT_SERVICE_URL,
    "inventory_api_url": DEFAULT_SERVICE_URL,
    "agent_tool_timeout_seconds": DEFAULT_TOOL_TIMEOUT_SECONDS,
    "agent_inventory_email": None,
    "agent_inventory_password": None,
}


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    agent_store_path: Path = DEFAULT_STORE_PATH
    # Base URLs of the services the tools read; the tools add the route paths.
    incidents_api_url: str = DEFAULT_SERVICE_URL
    inventory_api_url: str = DEFAULT_SERVICE_URL
    agent_tool_timeout_seconds: float = Field(
        default=DEFAULT_TOOL_TIMEOUT_SECONDS, gt=0, le=MAX_TOOL_TIMEOUT_SECONDS
    )
    # The inventory routes need a bearer token: the stock tool signs in as this
    # account. Unset, the stock tool answers `unavailable` without a request.
    agent_inventory_email: str | None = None
    agent_inventory_password: SecretStr | None = None

    @field_validator(*_DEFAULTS, mode="before")
    @classmethod
    def blank_is_default(cls, value: Any, info: ValidationInfo) -> Any:
        # Compose passes `${VAR:-}` through as an empty string.
        if isinstance(value, str) and not value.strip():
            return _DEFAULTS.get(info.field_name or "", value)
        return value

    @field_validator("agent_store_path", mode="after")
    @classmethod
    def relative_to_the_repository(cls, value: Path) -> Path:
        # A relative path means the same file whichever directory a CLI runs from.
        return value if value.is_absolute() else ROOT_DIR / value

    @field_validator("incidents_api_url", "inventory_api_url", mode="after")
    @classmethod
    def http_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("must be an http:// or https:// base URL")
        return value

    def inventory_credentials(self) -> tuple[str, str] | None:
        """(email, password) for the stock tool, or None when either is unset."""
        if not self.agent_inventory_email or self.agent_inventory_password is None:
            return None
        return self.agent_inventory_email, self.agent_inventory_password.get_secret_value()


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
