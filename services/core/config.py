from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration. Invalid or missing values fail at startup."""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    jwt_secret_key: str = Field(min_length=32)
    app_env: Literal["development", "production"] = "production"
    database_url: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=60, gt=0)

    # Password reset link lifetime. AUTH-03 fixes the window at 15-60 minutes,
    # so a value outside it is a configuration error, not a preference.
    password_reset_token_expire_minutes: int = Field(default=30, ge=15, le=60)

    # Transactional email through Resend. Leaving the key unset selects the
    # console backend, which logs the message instead of sending it, so the
    # reset flow stays exercisable locally without an account.
    resend_api_key: str | None = None
    email_from: str = "TrackFlow <onboarding@resend.dev>"

    # Where the reset link points. The token is appended as `?token=<jwt>`.
    frontend_base_url: str = "http://localhost:3000"

    # Supabase project that stores telemetry events. The secret key bypasses Row
    # Level Security, so it is read here, on the server, and nowhere else.
    supabase_url: str = Field(pattern=r"^https://")
    supabase_secret_key: SecretStr = Field(min_length=1)

    # Redis is both the Celery broker and its result backend (DEV-55). The API
    # enqueues on it and the worker consumes from it, so both must read the same
    # value. Secret because a Redis URL can carry a password.
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            sslmode = url.query.get("sslmode", "require")
            valid = (
                url.drivername in {"postgresql", "postgresql+psycopg2"}
                and bool(url.host and url.username and url.password and url.database)
                and (url.port is None or 0 < url.port <= 65535)
                and (
                    sslmode in {"require", "verify-ca", "verify-full"}
                    or (sslmode == "disable" and info.data.get("app_env") == "development")
                )
            )
        except (ArgumentError, ValueError):
            valid = False
        if not valid:
            raise ValueError(
                "Must be a PostgreSQL connection URL with credentials and SSL enabled "
                "outside development"
            )
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        try:
            valid = parsed.scheme in {"redis", "rediss"} and bool(parsed.hostname)
            valid = valid and (parsed.port is None or 0 < parsed.port <= 65535)
        except ValueError:  # a non-numeric or out-of-range port
            valid = False
        if not valid:
            raise ValueError("Must be a redis:// or rediss:// URL with a host")
        return value

    @field_validator("frontend_base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def email_is_live(self) -> bool:
        """False when no provider key is configured and mail is only logged."""
        return bool(self.resend_api_key)


class ConfigurationError(RuntimeError):
    """The environment does not describe a runnable service.

    The message names each setting and what is wrong with it, and never its
    value: this ends up in logs and on the terminal.
    """


def _describe(error: ValidationError) -> str:
    problems = []
    for item in error.errors(include_input=False, include_url=False):
        name = ".".join(str(part) for part in item["loc"]).upper() or "SETTINGS"
        problems.append(f"{name}: {item['msg']}")
    return "; ".join(problems)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as error:
        # pydantic's own message quotes the rejected input: a too-short
        # JWT_SECRET_KEY verbatim, and for a missing one every other setting,
        # RESEND_API_KEY included. `from None` keeps that message out of the
        # traceback as well, so only names and reasons are ever printed.
        raise ConfigurationError(
            f"Invalid configuration - {_describe(error)}. Compare .env with .env.example."
        ) from None
