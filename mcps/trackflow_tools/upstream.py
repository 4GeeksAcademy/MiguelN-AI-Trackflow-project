"""HTTP clients for the services this server exposes: the Incidents Manager and the inventory.

The MCP server relies on those services; it does not replace them. Every tool
call becomes one or two requests to the service's real routes, and the
service's answer - including its refusals - is what the client gets back:

- the lifecycle rules stay in the Incidents Manager: a status change goes
  through `PATCH /api/incidents/{id}/status`, and a forbidden move comes back
  as `invalid_status_transition` with the service's own explanation;
- stock is computed by the inventory, never here.

Each request has a numeric timeout and no retries or redirects. Every failure
becomes a `ToolFailure` with a code (`errors.ErrorCode`); nothing is made up.

**The inventory client can only GET.** `ReadOnlyInventoryApi._send` refuses
any other method with `ReadOnlyViolation` before a request is built. The one
POST this module makes for the inventory is the service account's sign-in
(`POST /auth/login`), which changes no data and goes through its own client.

The MCP client's OAuth token is never forwarded to the services: the
incident routes need no credentials, and the inventory routes get the
server's own service-account token.
"""

import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, ClassVar, Final
from urllib.parse import quote

import httpx
from jose import JWTError, jwt

from mcps.trackflow_tools.config import McpServerSettings
from mcps.trackflow_tools.errors import ErrorCode, ToolFailure

INCIDENTS_PATH: Final = "/api/incidents"
PRODUCTS_PATH: Final = "/inventory/products"
ORDERS_PATH: Final = "/inventory/orders"
LOGIN_PATH: Final = "/auth/login"

# Sign in again this long before the service account token's `exp`.
EXPIRY_MARGIN_SECONDS: Final = 60
FALLBACK_LIFETIME_SECONDS: Final = 300

# The requests made for the tool call in progress, for the audit log.
_upstream_calls: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "mcp_upstream_calls", default=None
)


@contextmanager
def recording_upstream_calls() -> Iterator[list[dict[str, Any]]]:
    """Collect the upstream requests made inside the block (method, path, status)."""
    calls: list[dict[str, Any]] = []
    token = _upstream_calls.set(calls)
    try:
        yield calls
    finally:
        _upstream_calls.reset(token)


def _record(method: str, path: str, status: int | None, failure: str | None = None) -> None:
    calls = _upstream_calls.get()
    if calls is not None:
        entry: dict[str, Any] = {"method": method, "path": path, "status": status}
        if failure is not None:
            entry["failure"] = failure
        calls.append(entry)


class ReadOnlyViolation(RuntimeError):
    """A non-GET request was about to be sent to the inventory."""


def _problem(response: httpx.Response) -> tuple[str | None, str]:
    """The service's `{detail: {field, message}}`, or a plain fallback."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, dict) and isinstance(detail.get("message"), str):
        field = detail.get("field")
        return (field if isinstance(field, str) else None), detail["message"]
    if isinstance(detail, str):
        return None, detail
    return None, f"The service answered HTTP {response.status_code}."


class _Service:
    """One service reached over HTTP with a numeric timeout."""

    label: ClassVar[str] = "service"

    def __init__(
        self, base_url: str, *, timeout: float, client: httpx.Client | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # An injected client (tests) is reused and never closed here.
        self._client = client

    def _new_client(self) -> httpx.Client:
        return httpx.Client(timeout=httpx.Timeout(self._timeout), follow_redirects=False)

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        http = self._client or self._new_client()
        try:
            response = http.request(
                method, f"{self._base_url}{path}", params=params, json=json, headers=headers
            )
        except httpx.TimeoutException as error:
            _record(method, path, None, "timeout")
            raise ToolFailure(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                f"The {self.label} did not answer within {self._timeout:g} s.",
                service=self.label,
                failure="timeout",
            ) from error
        except httpx.TransportError as error:
            _record(method, path, None, "unreachable")
            raise ToolFailure(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                f"The {self.label} could not be reached.",
                service=self.label,
                failure="unreachable",
            ) from error
        finally:
            if self._client is None:
                http.close()
        _record(method, path, response.status_code)
        return response

    def _body(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as error:
            raise ToolFailure(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                f"The {self.label} sent an unreadable answer.",
                service=self.label,
                failure="invalid_response",
            ) from error

    def _expect(
        self,
        response: httpx.Response,
        expected: int,
        *,
        not_found: ErrorCode | None = None,
        transition_field: str | None = None,
    ) -> Any:
        """The JSON body of an `expected` answer; any other status becomes its error code."""
        status = response.status_code
        if status == expected:
            return self._body(response)
        field, message = _problem(response)
        if status == 404 and not_found is not None:
            raise ToolFailure(not_found, message, http_status=404)
        if status == 400 and transition_field is not None and field == transition_field:
            raise ToolFailure(
                ErrorCode.INVALID_STATUS_TRANSITION, message, field=field, http_status=400
            )
        if status in {400, 404, 409, 422}:
            raise ToolFailure(ErrorCode.UPSTREAM_REJECTED, message, field=field, http_status=status)
        if status in {401, 403}:
            raise ToolFailure(
                ErrorCode.UPSTREAM_AUTH_FAILED,
                f"The {self.label} refused this server's credentials.",
                http_status=status,
            )
        raise ToolFailure(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            f"The {self.label} failed (HTTP {status}).",
            service=self.label,
            failure=f"http_{status}",
            http_status=status,
        )


class IncidentsApi(_Service):
    """The Incidents Manager (`/api/incidents`). Its routes need no credentials."""

    label = "Incidents Manager"

    def get(self, incident_id: str) -> dict[str, Any]:
        response = self._send("GET", f"{INCIDENTS_PATH}/{quote(incident_id, safe='')}")
        body: dict[str, Any] = self._expect(response, 200, not_found=ErrorCode.INCIDENT_NOT_FOUND)
        return body

    def find_by_reference(self, reference: str) -> dict[str, Any]:
        """The incident with this canonical helpdesk reference ('TRF-000482')."""
        response = self._send("GET", INCIDENTS_PATH, params={"reference": reference})
        rows = self._expect(response, 200)
        if not rows:
            raise ToolFailure(
                ErrorCode.INCIDENT_NOT_FOUND,
                f"No incident has the helpdesk reference '{reference}'.",
                reference=reference,
            )
        first: dict[str, Any] = rows[0]
        return first

    def search(self, filters: Mapping[str, str]) -> list[dict[str, Any]]:
        response = self._send("GET", INCIDENTS_PATH, params=dict(filters) or None)
        rows: list[dict[str, Any]] = self._expect(response, 200)
        return rows

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        response = self._send("POST", INCIDENTS_PATH, json=dict(payload))
        body: dict[str, Any] = self._expect(response, 201)
        return body

    def update_status(self, incident_id: str, status: str) -> dict[str, Any]:
        """The lifecycle endpoint; the Incidents Manager decides whether the move is allowed."""
        response = self._send(
            "PATCH",
            f"{INCIDENTS_PATH}/{quote(incident_id, safe='')}/status",
            json={"status": status},
        )
        body: dict[str, Any] = self._expect(
            response, 200, not_found=ErrorCode.INCIDENT_NOT_FOUND, transition_field="status"
        )
        return body


class ServiceAccount:
    """The inventory service account's token, cached until shortly before it expires."""

    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
        *,
        timeout: float,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._timeout = timeout
        self._client = client
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if self._token is None or time.time() >= self._expires_at:
                self._token, self._expires_at = self._sign_in()
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    def _sign_in(self) -> tuple[str, float]:
        http = self._client or httpx.Client(
            timeout=httpx.Timeout(self._timeout), follow_redirects=False
        )
        try:
            response = http.post(
                f"{self._base_url}{LOGIN_PATH}",
                json={"email": self._email, "password": self._password},
            )
        except httpx.HTTPError as error:
            _record("POST", LOGIN_PATH, None, "unreachable")
            raise ToolFailure(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                "The inventory's sign-in did not answer.",
                service="inventory",
                failure="unreachable",
            ) from error
        finally:
            if self._client is None:
                http.close()
        _record("POST", LOGIN_PATH, response.status_code)
        if response.status_code != 200:
            raise ToolFailure(
                ErrorCode.UPSTREAM_AUTH_FAILED,
                "The inventory refused this server's service account.",
                http_status=response.status_code,
            )
        try:
            token = response.json()["access_token"]
        except (ValueError, KeyError, TypeError) as error:
            raise ToolFailure(
                ErrorCode.UPSTREAM_AUTH_FAILED, "The inventory's sign-in answer was unreadable."
            ) from error
        return token, _expiry(token) - EXPIRY_MARGIN_SECONDS


def _expiry(token: str) -> float:
    try:
        expires = jwt.get_unverified_claims(token).get("exp")
    except JWTError:
        expires = None
    if isinstance(expires, int | float):
        return float(expires)
    return time.time() + FALLBACK_LIFETIME_SECONDS


class ReadOnlyInventoryApi(_Service):
    """The inventory (`/inventory/...`), for reading only."""

    label = "inventory"
    ALLOWED_METHOD: ClassVar[str] = "GET"

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float,
        account: ServiceAccount | None,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(base_url, timeout=timeout, client=client)
        self._account = account

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        if method.upper() != self.ALLOWED_METHOD or json is not None:
            raise ReadOnlyViolation(
                f"refused {method.upper()} {path}: the inventory client is read-only"
            )
        return super()._send(method, path, params=params, headers=headers)

    def _get(self, path: str, params: Mapping[str, str] | None = None) -> httpx.Response:
        if self._account is None:
            raise ToolFailure(
                ErrorCode.UPSTREAM_AUTH_FAILED,
                "This server has no inventory service account configured "
                "(MCP_INVENTORY_EMAIL / MCP_INVENTORY_PASSWORD).",
                failure="not_configured",
            )
        response = self._send("GET", path, params=params, headers=self._bearer())
        if response.status_code == 401:
            # The cached token was refused (expired, or the API's secret changed):
            # sign in once more, then give up.
            self._account.invalidate()
            response = self._send("GET", path, params=params, headers=self._bearer())
        return response

    def _bearer(self) -> dict[str, str]:
        assert self._account is not None
        return {"Authorization": f"Bearer {self._account.token()}"}

    def list_products(
        self, *, warehouse: str | None = None, sku: str | None = None
    ) -> list[dict[str, Any]]:
        params = {name: value for name, value in (("warehouse", warehouse), ("sku", sku)) if value}
        rows: list[dict[str, Any]] = self._expect(self._get(PRODUCTS_PATH, params or None), 200)
        return rows

    def get_product(self, product_id: str) -> dict[str, Any]:
        response = self._get(f"{PRODUCTS_PATH}/{quote(product_id, safe='')}")
        body: dict[str, Any] = self._expect(response, 200, not_found=ErrorCode.PRODUCT_NOT_FOUND)
        return body

    def list_orders(
        self, *, warehouse: str | None = None, product_id: str | None = None
    ) -> list[dict[str, Any]]:
        params = {
            name: value
            for name, value in (("warehouse", warehouse), ("product_id", product_id))
            if value
        }
        rows: list[dict[str, Any]] = self._expect(self._get(ORDERS_PATH, params or None), 200)
        return rows


@dataclass(frozen=True)
class Upstream:
    incidents: IncidentsApi
    inventory: ReadOnlyInventoryApi

    @classmethod
    def from_settings(cls, settings: McpServerSettings) -> "Upstream":
        timeout = settings.upstream_timeout_seconds
        account = settings.inventory_credentials()
        return cls(
            incidents=IncidentsApi(settings.incidents_api_url, timeout=timeout),
            inventory=ReadOnlyInventoryApi(
                settings.inventory_api_url,
                timeout=timeout,
                account=(
                    ServiceAccount(settings.inventory_api_url, *account, timeout=timeout)
                    if account
                    else None
                ),
            ),
        )
