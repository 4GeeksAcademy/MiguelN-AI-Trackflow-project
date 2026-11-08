import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.hash import bcrypt

from services.core.config import get_settings

logger = logging.getLogger(__name__)

BCRYPT_ROUNDS = 12
# bcrypt silently ignores anything past 72 bytes, so payloads are capped there.
MAX_PASSWORD_BYTES = 72
# Compared against when no user matches, so login costs the same either way.
DUMMY_HASH = "$2b$12$FkY3JlZ2j8qq561VHZctmuvXqwG8KiobjCTdK.wmAQwzhXRCzZC1e"

# Every token carries a `typ` claim naming what it is allowed to do, so a reset
# link can never be replayed as a session and a session can never reset a
# password. Tokens minted before this claim existed are access tokens.
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_PASSWORD_RESET = "password_reset"


def fits_bcrypt(plain_password: str) -> bool:
    """Whether bcrypt can take this password at all: no NUL, at most 72 bytes.

    Bytes, not characters: "ñ" is two bytes in UTF-8, so a password can be 72
    characters long and still not fit. bcrypt 5 raises on either problem
    instead of truncating.
    """
    return (
        "\x00" not in plain_password and len(plain_password.encode("utf-8")) <= MAX_PASSWORD_BYTES
    )


def hash_password(plain_password: str) -> str:
    return bcrypt.using(rounds=BCRYPT_ROUNDS).hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not fits_bcrypt(plain_password):
        # `hash_password` could never have produced a hash from this password,
        # so it cannot match. Handing it to bcrypt would raise, and the handler
        # below would then blame the stored hash - a corruption warning any
        # anonymous caller could trigger. The check costs the same whether or
        # not the account exists.
        return False
    try:
        return bcrypt.verify(plain_password, hashed_password)
    except (ValueError, TypeError):
        # The caller still sees a failed check, but a stored hash that cannot
        # be parsed is data corruption, not a wrong password, and must be visible.
        logger.warning("A stored password hash could not be read; treating the check as failed")
        return False


def verify_dummy_password(plain_password: str) -> None:
    """Burn one bcrypt verification so a missing account is not faster to probe."""
    verify_password(plain_password, DUMMY_HASH)


def _encode(claims: dict[str, Any], lifetime: timedelta) -> tuple[str, datetime]:
    """Sign `claims` with `iat`/`exp` attached. Returns the token and its expiry."""
    settings = get_settings()
    # Whole seconds, so the returned expiry is exactly the `exp` in the token.
    issued_at = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = issued_at + lifetime
    payload = {
        **claims,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_at


def _decode(token: str, expected_type: str) -> dict[str, Any] | None:
    """Return the claims, or None when the token is invalid, expired, or misused.

    "Misused" means a token of the wrong `typ`: presenting a password reset
    link as a bearer credential, or vice versa, fails here rather than deeper in.
    """
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            # python-jose only checks `exp` when it is present, so a token
            # without one would never expire. Every token issued here carries
            # it; one that does not was minted by a regression, and must be
            # refused loudly instead of opening a session that never ends.
            options={"require_exp": True},
        )
    except JWTError:
        return None

    if claims.get("typ", TOKEN_TYPE_ACCESS) != expected_type:
        return None
    return claims


def create_access_token(user_id: str, role: str) -> str:
    settings = get_settings()
    token, _ = _encode(
        {"sub": user_id, "role": role, "typ": TOKEN_TYPE_ACCESS},
        timedelta(minutes=settings.access_token_expire_minutes),
    )
    return token


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Return the token claims, or None when the token is invalid or expired."""
    return _decode(token, TOKEN_TYPE_ACCESS)


def create_password_reset_token(user_id: str, token_id: str) -> tuple[str, datetime]:
    """Sign a short-lived reset token. `token_id` is the `jti` the registry tracks.

    The signature and `exp` make the token self-describing, so an altered or
    stale one is rejected without a lookup. Single use is the one property a
    signature cannot carry, which is what the `jti` is for.
    """
    settings = get_settings()
    return _encode(
        {"sub": user_id, "jti": token_id, "typ": TOKEN_TYPE_PASSWORD_RESET},
        timedelta(minutes=settings.password_reset_token_expire_minutes),
    )


def decode_password_reset_token(token: str) -> dict[str, Any] | None:
    return _decode(token, TOKEN_TYPE_PASSWORD_RESET)


def access_token_expires_in_seconds() -> int:
    return get_settings().access_token_expire_minutes * 60


def password_reset_expires_in_minutes() -> int:
    return get_settings().password_reset_token_expire_minutes
