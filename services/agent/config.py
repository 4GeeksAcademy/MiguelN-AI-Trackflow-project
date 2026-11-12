"""Agent settings: where checkpoints and run traces are stored.

Only what the agent itself needs, so the CLIs run without the API's settings
(JWT secret, Supabase). The model and Qdrant settings stay in `RagSettings`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR: Final = Path(__file__).resolve().parents[2]

# Git-ignored. One SQLite file holds LangGraph's checkpoints and the run traces.
DEFAULT_STORE_PATH: Final = ROOT_DIR / "data" / "agent" / "agent_runs.sqlite"


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    agent_store_path: Path = DEFAULT_STORE_PATH

    @field_validator("agent_store_path", mode="before")
    @classmethod
    def blank_is_default(cls, value: Any) -> Any:
        # Compose passes `${VAR:-}` through as an empty string.
        return DEFAULT_STORE_PATH if isinstance(value, str) and not value.strip() else value

    @field_validator("agent_store_path", mode="after")
    @classmethod
    def relative_to_the_repository(cls, value: Path) -> Path:
        # A relative path means the same file whichever directory a CLI runs from.
        return value if value.is_absolute() else ROOT_DIR / value


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
