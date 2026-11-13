"""The inventory tool: `query_inventory`, read-only by design.

One tool, three reads (`operation`): `list_products`, `get_product`,
`list_orders`, each one GET to the inventory's real routes. There is no
inventory write tool, and the absence is not the protection: a modification
attempt - a write `operation` such as `record_inbound`, a write-only field
such as `quantity`, or a write tool name such as `create_product` - is
refused explicitly with `inventory_read_only` by `ReadOnlyInventoryGuard`
before anything runs (see `permissions`). `operation` is a documented string
rather than an enum precisely so that such an attempt reaches that guard and
is answered with the reason, not with a generic schema error.
"""

import re
import unicodedata
from typing import Annotated, Final
from uuid import UUID

from fastmcp import FastMCP
from pydantic import Field

from mcps.trackflow_tools.contracts import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    PRODUCT_NAME_MAX_LENGTH,
    PRODUCT_SKU_MAX_LENGTH,
    InventoryQueryResult,
    Product,
    ProductAvailability,
    StockMovement,
    Warehouse,
)
from mcps.trackflow_tools.errors import ErrorCode, ToolFailure
from mcps.trackflow_tools.permissions import (
    INVENTORY_READ_OPERATIONS,
    INVENTORY_WRITE_OPERATIONS,
    QUERY_INVENTORY,
    inventory_write_attempt,
)
from mcps.trackflow_tools.tools import describe, parse, read_only_annotations, tool_meta
from mcps.trackflow_tools.upstream import ReadOnlyInventoryApi

SERVICE: Final = "inventory"

# Which optional arguments each read takes.
_ARGUMENTS_OF: Final[dict[str, frozenset[str]]] = {
    "list_products": frozenset({"sku", "name", "warehouse", "limit"}),
    "get_product": frozenset({"product_id"}),
    "list_orders": frozenset({"warehouse", "product_id", "limit"}),
}

_WORD: Final = re.compile(r"[a-z0-9]+")
# Words that say nothing about which product is meant (English and Spanish).
_STOPWORDS: Final = frozenset(
    {
        "a", "an", "and", "any", "do", "does", "for", "have", "how", "in", "is", "many",
        "much", "of", "our", "some", "stock", "the", "there", "unit", "units", "we",
        "de", "del", "el", "en", "hay", "la", "las", "los", "un", "una", "unidades",
    }
)  # fmt: skip


def _words(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return {
        word[:-1] if len(word) > 3 and word.endswith("s") else word
        for word in _WORD.findall(folded)
        if word not in _STOPWORDS
    }


def matches_name(name: str, product: Product) -> bool:
    """True when every meaningful word of `name` is a word of the product's name or SKU
    (case- and accent-insensitive, plural 's' ignored)."""
    wanted = _words(name)
    return bool(wanted) and wanted <= _words(f"{product.name} {product.sku}")


def register(mcp: FastMCP, inventory: ReadOnlyInventoryApi) -> list[str]:
    """Add the inventory tool to `mcp`; returns its name."""

    def query_inventory(
        operation: Annotated[
            str,
            Field(
                min_length=1,
                max_length=64,
                description=(
                    "The read to perform: `list_products` (products and their stock; filters "
                    "`sku`, `name`, `warehouse`), `get_product` (one product's stock by "
                    "`product_id`) or `list_orders` (inbound/outbound movements; filters "
                    "`warehouse`, `product_id`). Write operations ("
                    + ", ".join(f"`{name}`" for name in INVENTORY_WRITE_OPERATIONS)
                    + ") are refused with `inventory_read_only`."
                ),
                json_schema_extra={"examples": list(INVENTORY_READ_OPERATIONS)},
            ),
        ],
        sku: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=PRODUCT_SKU_MAX_LENGTH,
                description="list_products: exact SKU, e.g. 'SHOE-BLK-42'.",
            ),
        ] = None,
        name: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=PRODUCT_NAME_MAX_LENGTH,
                description=(
                    "list_products: words of the product's name, e.g. 'black running shoes'. "
                    "Every meaningful word must appear in the name or SKU."
                ),
            ),
        ] = None,
        warehouse: Annotated[
            Warehouse | None,
            Field(description="list_products / list_orders: only this warehouse."),
        ] = None,
        product_id: Annotated[
            UUID | None,
            Field(description="get_product (required) / list_orders: the product's id."),
        ] = None,
        limit: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_LIMIT,
                description="list_products / list_orders: the most records to return; "
                "`total` still counts every match.",
            ),
        ] = DEFAULT_LIMIT,
    ) -> InventoryQueryResult:
        given = {
            argument
            for argument, value in (
                ("sku", sku),
                ("name", name),
                ("warehouse", warehouse),
                ("product_id", product_id),
                ("limit", None if limit == DEFAULT_LIMIT else limit),
            )
            if value is not None
        }
        normalized = operation.strip().lower()
        # The guard refuses these before the tool runs; refused here too, so the
        # rule does not depend on middleware order.
        reason = inventory_write_attempt({"operation": normalized})
        if reason is not None:
            raise ToolFailure(
                ErrorCode.INVENTORY_READ_ONLY,
                f"The inventory is read-only on this server: {reason}.",
                operation=normalized,
            )
        if normalized not in _ARGUMENTS_OF:
            raise ToolFailure(
                ErrorCode.INVALID_ARGUMENTS,
                f"Unknown operation '{operation}'. Use one of: "
                + ", ".join(INVENTORY_READ_OPERATIONS)
                + ".",
                problems=[{"field": "operation", "message": "unknown operation"}],
            )
        unused = sorted(given - _ARGUMENTS_OF[normalized])
        if unused:
            raise ToolFailure(
                ErrorCode.INVALID_ARGUMENTS,
                f"`{normalized}` does not take {', '.join(unused)}.",
                problems=[
                    {"field": field, "message": "not used by this operation"} for field in unused
                ],
            )
        warehouse_value = warehouse.value if warehouse is not None else None

        if normalized == "get_product":
            if product_id is None:
                raise ToolFailure(
                    ErrorCode.INVALID_ARGUMENTS,
                    "`get_product` needs `product_id`.",
                    problems=[{"field": "product_id", "message": "required"}],
                )
            product = parse(ProductAvailability, inventory.get_product(str(product_id)), SERVICE)
            return InventoryQueryResult(operation="get_product", product=product, total=1)

        if normalized == "list_orders":
            rows = inventory.list_orders(
                warehouse=warehouse_value,
                product_id=str(product_id) if product_id is not None else None,
            )
            orders = [parse(StockMovement, row, SERVICE) for row in rows]
            return InventoryQueryResult(
                operation="list_orders", orders=orders[:limit], total=len(orders)
            )

        rows = inventory.list_products(warehouse=warehouse_value, sku=sku)
        products = [parse(Product, row, SERVICE) for row in rows]
        if name is not None:
            products = [product for product in products if matches_name(name, product)]
            products.sort(key=lambda product: (product.name, product.warehouse.value))
        return InventoryQueryResult(
            operation="list_products", products=products[:limit], total=len(products)
        )

    mcp.tool(
        query_inventory,
        name=QUERY_INVENTORY,
        description=describe(
            QUERY_INVENTORY,
            "Query inventory data - products and their current stock, one product's stock, "
            "or the inbound/outbound movement history - from the inventory "
            "(`GET /inventory/products`, `GET /inventory/products/{id}`, "
            "`GET /inventory/orders`). READ-ONLY: this server never creates products or "
            "records stock movements; any such attempt is refused with "
            "`inventory_read_only`.",
            use_when="someone asks how much of a product is in stock, in which warehouse, "
            "or what came in or went out.",
            errors=(
                ErrorCode.INVENTORY_READ_ONLY,
                ErrorCode.PRODUCT_NOT_FOUND,
                ErrorCode.UPSTREAM_AUTH_FAILED,
                ErrorCode.UPSTREAM_UNAVAILABLE,
            ),
        ),
        annotations=read_only_annotations("Query inventory (read-only)"),
        meta=tool_meta(QUERY_INVENTORY),
    )
    return [QUERY_INVENTORY]
