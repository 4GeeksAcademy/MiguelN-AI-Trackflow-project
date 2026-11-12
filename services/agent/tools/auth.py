"""The stock tool's credential exchange: sign in as the configured service account.

The inventory routes require a bearer token for an existing, active user. The
account's email and password come from configuration
(`AGENT_INVENTORY_EMAIL` / `AGENT_INVENTORY_PASSWORD`), never from code. The
token is cached until shortly before it expires and dropped when the
inventory manager answers 401, so the next call signs in again.

This is the one POST in the tools package, and it creates nothing: it is the
same `POST /auth/login` the UIs make. It is kept out of the lookup modules,
which only ever GET.
"""

import threading
import time
from typing import Final

import httpx
from jose import JWTError, jwt

from services.agent.tools.http import ServiceFailure

LOGIN_PATH: Final = "/auth/login"
# Sign in again this long before the token's `exp`, so it never expires mid-call.
EXPIRY_MARGIN_SECONDS: Final = 60
# Used when a token carries no readable `exp`.
FALLBACK_LIFETIME_SECONDS: Final = 300


class InventoryCredentials:
    """A cached access token for one account on one inventory manager."""

    def __init__(self, base_url: str, email: str, password: str) -> None:
        self._base_url = base_url
        self._email = email
        self._password = password
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def token(self, client: httpx.Client) -> str:
        """A valid access token, signing in if there is none or it is about to expire."""
        with self._lock:
            if self._token is None or time.time() >= self._expires_at:
                self._token, self._expires_at = self._sign_in(client)
            return self._token

    def invalidate(self) -> None:
        """Forget the token: the service refused it."""
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    def _sign_in(self, client: httpx.Client) -> tuple[str, float]:
        try:
            response = client.post(
                f"{self._base_url}{LOGIN_PATH}",
                json={"email": self._email, "password": self._password},
            )
        except httpx.TimeoutException as error:
            raise ServiceFailure("timeout") from error
        except httpx.TransportError as error:
            raise ServiceFailure("unreachable") from error

        if response.status_code in {400, 401, 403, 422}:
            raise ServiceFailure("login_rejected", response.status_code)
        if response.status_code != 200:
            raise ServiceFailure(f"http_{response.status_code}", response.status_code)
        try:
            token = response.json()["access_token"]
        except (ValueError, KeyError, TypeError) as error:
            raise ServiceFailure("invalid_response", 200) from error
        if not isinstance(token, str) or not token:
            raise ServiceFailure("invalid_response", 200)
        return token, _expiry(token) - EXPIRY_MARGIN_SECONDS


def _expiry(token: str) -> float:
    """The token's `exp`, read without verifying it: only the API can verify it."""
    try:
        expires = jwt.get_unverified_claims(token).get("exp")
    except JWTError:
        expires = None
    if isinstance(expires, int | float):
        return float(expires)
    return time.time() + FALLBACK_LIFETIME_SECONDS
