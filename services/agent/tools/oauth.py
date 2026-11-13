"""The agent's OAuth access token for the MCP server: the client-credentials grant.

The agent is a machine client (an Auth0 machine-to-machine application) with
its own client id and secret from configuration (`AGENT_OAUTH_*`), never from
code. It asks only for the read scopes (`incidents:read inventory:read`): it
answers questions, so it never needs to create, update or write anything.

The token is cached until shortly before it expires, shared by every run, and
dropped when the MCP server answers 401, so the next call gets a fresh one.
Every failure is a `TokenFailure` with a short, safe label; the secret never
appears in it.
"""

import threading
import time
from functools import lru_cache
from typing import Final

import httpx
from jose import JWTError, jwt

from services.agent.config import AgentSettings, get_agent_settings

# Ask for a new token this long before the current one expires.
EXPIRY_MARGIN_SECONDS: Final = 60
# Used when the answer says nothing about the token's lifetime.
FALLBACK_LIFETIME_SECONDS: Final = 300


class TokenFailure(Exception):
    """No access token could be obtained. `failure` is a short, safe label."""

    def __init__(self, failure: str, http_status: int | None = None) -> None:
        super().__init__(failure)
        self.failure = failure
        self.http_status = http_status


def make_http_client(timeout: float) -> httpx.Client:
    """The client for the token endpoint (replaced in tests)."""
    return httpx.Client(timeout=httpx.Timeout(timeout), follow_redirects=False)


class ClientCredentials:
    """A cached client-credentials access token for one client and one API."""

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        audience: str | None,
        scopes: str,
        timeout: float,
        client: httpx.Client | None = None,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._audience = audience
        self._scopes = scopes
        self._timeout = timeout
        self._client = client
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if self._token is None or time.time() >= self._expires_at:
                self._token, self._expires_at = self._request()
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    def _request(self) -> tuple[str, float]:
        form = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if self._audience:
            # Auth0 names the API by `audience`.
            form["audience"] = self._audience
        if self._scopes.strip():
            form["scope"] = self._scopes.strip()
        http = self._client or make_http_client(self._timeout)
        try:
            response = http.post(self._token_url, data=form)
        except httpx.TimeoutException as error:
            raise TokenFailure("oauth_timeout") from error
        except httpx.TransportError as error:
            raise TokenFailure("oauth_unreachable") from error
        finally:
            if self._client is None:
                http.close()

        if response.status_code in {400, 401, 403}:
            raise TokenFailure("oauth_rejected", response.status_code)
        if response.status_code != 200:
            raise TokenFailure(f"oauth_http_{response.status_code}", response.status_code)
        try:
            body = response.json()
            token = body["access_token"]
        except (ValueError, KeyError, TypeError) as error:
            raise TokenFailure("oauth_invalid_response", 200) from error
        if not isinstance(token, str) or not token:
            raise TokenFailure("oauth_invalid_response", 200)
        return token, _expiry(token, body.get("expires_in")) - EXPIRY_MARGIN_SECONDS


def _expiry(token: str, expires_in: object) -> float:
    if isinstance(expires_in, int | float) and expires_in > 0:
        return time.time() + float(expires_in)
    try:
        expires = jwt.get_unverified_claims(token).get("exp")
    except JWTError:
        expires = None
    if isinstance(expires, int | float):
        return float(expires)
    return time.time() + FALLBACK_LIFETIME_SECONDS


@lru_cache(maxsize=4)
def _cached(
    token_url: str,
    client_id: str,
    client_secret: str,
    audience: str | None,
    scopes: str,
    timeout: float,
) -> ClientCredentials:
    return ClientCredentials(
        token_url, client_id, client_secret, audience=audience, scopes=scopes, timeout=timeout
    )


def credentials(settings: AgentSettings | None = None) -> ClientCredentials | None:
    """The agent's cached credentials, or None when the OAuth client is not configured."""
    settings = settings or get_agent_settings()
    client = settings.oauth_client()
    if client is None:
        return None
    return _cached(
        *client,
        settings.agent_oauth_audience,
        settings.agent_oauth_scopes,
        settings.agent_tool_timeout_seconds,
    )


def reset_credentials() -> None:
    """Forget every cached token (tests, and when the agent graph is closed)."""
    _cached.cache_clear()
