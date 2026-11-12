"""Stock lookup: reads the inventory manager's live stock over HTTP.

Read-only by construction: the only request this module makes is
`http.get_json` (GET `/inventory/products`); signing in lives in `auth.py`.

The products route filters by exact SKU and by warehouse but not by name, so:

- a product given as a SKU ('SHOE-BLK-42') -> `GET /inventory/products?sku=...`;
- a product given by name ('black running shoes') -> `GET /inventory/products`
  and the real rows are matched on their name here: every meaningful word of
  the request must be a word of the product's name or SKU (case-insensitive,
  plural 's' ignored).

Either way the warehouse, when named, is passed through as a filter. Any
failure - not configured, sign-in refused, timeout, unreachable, error status,
unreadable body - becomes an `unavailable` result; nothing is made up.
"""

import re
import unicodedata
from functools import lru_cache
from typing import Final

import httpx
from pydantic import TypeAdapter, ValidationError

from services.agent.config import get_agent_settings
from services.agent.tools.auth import InventoryCredentials
from services.agent.tools.contracts import (
    MAX_PRODUCTS,
    ProductStock,
    StockLookupInput,
    StockLookupResult,
)
from services.agent.tools.http import (
    ServiceFailure,
    Stopwatch,
    client_for_call,
    endpoint_label,
    get_json,
)

PRODUCTS_PATH: Final = "/inventory/products"
_PRODUCT_LIST: Final = TypeAdapter(list[ProductStock])

# 'SHOE-BLK-42': upper-case letters and digits in dash-separated groups.
_SKU_SHAPE: Final = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)+")
_WORD: Final = re.compile(r"[a-z0-9]+")
# Words that say nothing about which product is meant (English and Spanish).
_STOPWORDS: Final = frozenset(
    {
        "a", "an", "and", "any", "do", "does", "for", "have", "how", "in", "is", "many",
        "much", "of", "our", "some", "stock", "the", "there", "unit", "units", "we",
        "de", "del", "el", "en", "hay", "la", "las", "los", "un", "una", "unidades",
    }
)  # fmt: skip


@lru_cache(maxsize=4)
def _credentials(base_url: str, email: str, password: str) -> InventoryCredentials:
    # One cached token per account and service, shared by every run.
    return InventoryCredentials(base_url, email, password)


def reset_credentials() -> None:
    """Forget every cached token (tests, and when the agent graph is closed)."""
    _credentials.cache_clear()


def sku_in(product: str) -> str | None:
    """The SKU when the request is one ('shoe-blk-42' -> 'SHOE-BLK-42'), else None."""
    candidate = product.strip().upper()
    return candidate if _SKU_SHAPE.fullmatch(candidate) else None


def _words(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return {
        word[:-1] if len(word) > 3 and word.endswith("s") else word
        for word in _WORD.findall(folded)
        if word not in _STOPWORDS
    }


def matches_name(product: str, row: ProductStock) -> bool:
    """True when every meaningful word of `product` is a word of the row's name or SKU."""
    wanted = _words(product)
    return bool(wanted) and wanted <= _words(f"{row.name} {row.sku}")


def lookup_stock(
    request: StockLookupInput, *, client: httpx.Client | None = None
) -> StockLookupResult:
    """Look the product's stock up in the inventory manager. Never raises for a service failure."""
    settings = get_agent_settings()
    stopwatch = Stopwatch()
    sku = sku_in(request.product)
    params: dict[str, str] = {}
    if request.warehouse is not None:
        params["warehouse"] = request.warehouse.value
    if sku is not None:
        params["sku"] = sku
    endpoint = endpoint_label(PRODUCTS_PATH, params)

    def result(**fields: object) -> StockLookupResult:
        return StockLookupResult.model_validate(
            {
                "request": request,
                "endpoint": endpoint,
                "duration_ms": stopwatch.elapsed_ms(),
                **fields,
            }
        )

    account = settings.inventory_credentials()
    if account is None:
        return result(outcome="unavailable", failure="not_configured")
    credentials = _credentials(settings.inventory_api_url, *account)
    url = f"{settings.inventory_api_url}{PRODUCTS_PATH}"

    try:
        with client_for_call(client) as http:
            reply = get_json(http, url, params=params, headers=_bearer(credentials.token(http)))
            if reply.status == 401:
                # The cached token was refused (expired, or the API's secret
                # changed): sign in once more, then give up.
                credentials.invalidate()
                reply = get_json(http, url, params=params, headers=_bearer(credentials.token(http)))
        if reply.status != 200:
            raise ServiceFailure(f"http_{reply.status}", reply.status)
        rows = _PRODUCT_LIST.validate_python(reply.body)
    except ServiceFailure as failure:
        return result(
            outcome="unavailable", failure=failure.failure, http_status=failure.http_status
        )
    except ValidationError:
        return result(outcome="unavailable", failure="invalid_response", http_status=200)

    found = rows if sku is not None else [row for row in rows if matches_name(request.product, row)]
    if not found:
        return result(outcome="not_found", http_status=200)
    found.sort(key=lambda row: (row.name, row.warehouse.value))
    return result(outcome="ok", products=found[:MAX_PRODUCTS], total=len(found), http_status=200)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
