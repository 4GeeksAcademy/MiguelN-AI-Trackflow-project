# Centralized Inventory

## Database Configuration

The API uses two independent stores:

- Identity, profiles, and password reset records stay in the existing TinyDB file.
- Inventory uses Supabase PostgreSQL through SQLModel. Existing suppliers and
  incidents are unchanged.

`services/inventory/database.py` reuses the cached TinyDB handle from
`services/core/db.py`. Its PostgreSQL engine is lazy and cached; `get_db` yields a new
SQLModel session for each request and closes it afterward. Inventory routes use
`Depends(get_db)` independently of the existing TinyDB authentication dependency.
There is no global SQL session.

### Local Setup

Set `DATABASE_URL` in the repository-root `.env`, preserving the existing settings.
The file is git-ignored. Do not put the URL in frontend configuration or tracked files.
Rotate a database password that has been shared in chat before using it.

Use the connection details from Supabase's Connect dialog. A placeholder illustrates
the format; it is not a working credential:

```dotenv
DATABASE_URL="postgresql+psycopg2://postgres.PROJECT_REF:ENCODED_PASSWORD@POOLER_HOST:6543/postgres?sslmode=require"
```

The plain `postgresql://` scheme is also accepted and normalized to the explicit
psycopg2 driver. URL-encode reserved characters in the password exactly once.
Exclamation marks do not need encoding. Quote the whole value in `.env`; shell
quoting and URL encoding are different concerns.

The supplied Supabase endpoint uses transaction pooling on port 6543. The engine
uses psycopg2, READ COMMITTED isolation, SSL, pre-ping, up to five persistent and
five overflow connections, and ten-second connection/pool timeouts. Do not rely
on session-level PostgreSQL state surviving across transactions.

The existing SQLModel and psycopg2 dependencies are installed with `uv sync`.
Run `npm run api` after configuring the URL. Startup initializes the shared TinyDB
handle and probes PostgreSQL with `SELECT 1` before accepting requests. Missing,
invalid, or unavailable PostgreSQL configuration stops startup with a sanitized
message; the connection URL is not included. The SQL engine is disposed on shutdown
or failed startup. Startup also calls `SQLModel.metadata.create_all` for the three
inventory tables and enables PostgreSQL row-level security in the same transaction.
The connection role must own these tables or have the appropriate privileges.
No public RLS policies are created: Supabase anon/authenticated REST clients must
not bypass the FastAPI identity gate. FastAPI uses the PostgreSQL owner connection.
Do not add public write policies or allow uncoordinated movement writes.

Schema initialization is idempotent and never seeds rows. `create_all` creates
missing tables; it does not migrate an existing table when a model changes.

## Models and Contracts

The company source is `contexts/context.md`: warehouses are **Monterrey** and
**Zaragoza**, not the locations in the older TypeScript exercise. That source has
no detailed inventory schema; the following entity names and minimum fields were
approved during planning.

- `Product`: UUID `id`, `name`, `sku`, and `warehouse`. `(sku, warehouse)` is unique.
- `InboundOrder` and `OutboundOrder`: UUID `id`, `product_id` referencing
  `products.id`, positive integer `quantity`, UTC `created_at`, and `user_uuid`.
- `user_uuid` comes from the authenticated TinyDB account. It is not a PostgreSQL
  foreign key, and there are no SQL identity or profile tables.

ORM tables live in `services/inventory/models.py`. Standalone Pydantic HTTP
contracts live in `services/inventory/schemas.py`; they are not SQLModel classes.
Product requests accept only `name`, `sku`, and `warehouse`. Movement requests
accept only `product_id` and `quantity`. Extra fields are rejected, including
caller-supplied stock, record IDs, user UUIDs, timestamps, and warehouse overrides on orders.
Quantities must be strict integers from 1 to 2,147,483,647, matching PostgreSQL's
integer column. Response timestamps are serialized in UTC.

Each Product row represents one SKU in one warehouse. The same SKU in the other
warehouse has its own Product UUID. An order therefore belongs to that warehouse
through its product, and cannot draw stock from another partition.

## API

Every route below requires the existing TinyDB-backed bearer authentication:

| Method | Path | Response |
| --- | --- | --- |
| GET | `/inventory/products` | Products with computed `current_stock` |
| POST | `/inventory/products` | Created product with zero stock, 201 |
| GET | `/inventory/products/{id}` | One product with computed stock |
| POST | `/inventory/products/inbound` | Created inbound order, 201 |
| POST | `/inventory/products/outbound` | Created outbound order, 201 |
| GET | `/inventory/orders` | Both order directions with product data and `user_uuid` |

Product listing supports `warehouse` and exact `sku` filters. Order listing supports
`warehouse` and `product_id` filters, returns `direction: inbound | outbound`, and
is ordered newest first with deterministic tie-breakers. Product data embedded in
orders contains `id`, `name`, `sku`, and `warehouse`, never user credentials.

Validation errors stay 422. Missing products return 404, duplicate SKUs within a
warehouse return 409, and insufficient stock returns 400 with a descriptive
`detail: {field, message}`. The existing incident-only 422-to-400 behavior is unchanged.

## Stock Rules

`current_stock` is always computed from `SUM(inbound.quantity) - SUM(outbound.quantity)`
for that warehouse-scoped Product UUID; it is not a column or an accepted request
field. Product creation inserts no movement and starts at zero. List queries
aggregate inbound and outbound separately before joining, avoiding multiplication
when both tables contain several movements for one product.

Every movement write begins a transaction and locks the Product row with
`SELECT FOR UPDATE`. An outbound checks the balance while holding that lock and
rejects a quantity above the available stock before insertion. Exact depletion is
allowed. The transaction commits before releasing the lock, or rolls back on
failure. Inbound uses the same locking protocol. READ COMMITTED isolation lets a
waiting writer see the committed movement history from the previous writer.

The guarantee depends on all stock writes following this service path. Direct
owner-level SQL edits bypass it; database administration and grants must be restricted.

### Verification

```sh
uv run pytest tests/test_inventory_database.py tests/test_inventory_models.py tests/test_inventory_schemas.py tests/test_inventory_api.py -q
npm run test:api
```

Tests pin a non-live PostgreSQL URL and prohibit psycopg2 connections. API tests
mock the startup probe and schema initialization. SQLite tests check portable
constraints, calculations, and HTTP behavior, but do not prove PostgreSQL row locking.

`tests/test_inventory_postgres.py` is opt-in: set `INVENTORY_TEST_DATABASE_URL` in the
process environment explicitly, using an approved database role permitted to create
and drop disposable schemas. It never falls back to the local `DATABASE_URL`.

```sh
uv run pytest tests/test_inventory_postgres.py -q
```

These tests create uniquely named `inventory_test_*` schemas, use schema translation
instead of session-level search paths, and drop only their own schema afterward.
They cover real foreign-key/quantity/uniqueness constraints, UUID provenance through
the API, and two competing withdrawals. The concurrency test verifies that one
writer is blocked on the Product row before the first writer commits, then proves
the second is refused and only one outbound is persisted. A fourth PostgreSQL test
verifies seed reconciliation and an unchanged second run in a disposable schema.

On 2026-10-04, the configured Supabase connection and hosted tables/RLS were verified,
and all four opt-in PostgreSQL tests passed. Test writes occurred only in disposable
schemas and an isolated TinyDB file, not the hosted operational inventory tables.
The offline suite passed with 434 tests and 11 skips (7 legacy Flask tests and the
4 opt-in PostgreSQL tests), and authentication coverage was 89.52%, above its 70%
gate. Formatting, E/F/I lint, and editor diagnostics passed. The running API startup,
all six route registrations, unauthenticated read refusals, and Swagger were verified.

New inventory code and touched integration modules pass mypy. The two previously
documented `Settings()` constructor warnings (`jwt_secret_key`, `database_url`)
remain outside that check per the developer's decision not to enable Pydantic's plugin.

## Backoffice Interface

The internal Next.js application at `uis/backoffice` provides four views:

| Route | Purpose |
| --- | --- |
| `/backoffice/inventory/products` | Name, SKU, Warehouse, Current stock, and per-product order links |
| `/backoffice/inventory/orders/inbound` | Register a received delivery |
| `/backoffice/inventory/orders/outbound` | Register consumption or an exit after displaying available stock |
| `/backoffice/inventory/orders` | Read-only history with product, quantity, direction, creation date, and User UUID |

These are literal application routes, not a global Next.js base path. Existing
`/login`, supplier, incident, and account routes remain unchanged. Every inventory
page inherits the existing protected layout, `AuthProvider`, and `AuthGuard`.
Anonymous visitors return to their original path and query after sign-in;
missing or rejected tokens redirect to `/login?next=...`.

`uis/backoffice/lib/inventory.ts` centralizes all six inventory API calls through
the existing `requestAuthenticatedApi`. The bearer token comes from the existing
`trackflow.access_token` storage. Components never call `fetch` directly. The
movement forms use the live **`/inventory/products/inbound`** and
**`/inventory/products/outbound`** endpoints, not `/inventory/orders/inbound`
or `/inventory/orders/outbound`. No backend aliases were added.

The interface uses the approved fields above and the Monterrey/Zaragoza vocabulary
from `contexts/context.md`. Products are selected by name, with SKU and Warehouse
to distinguish identical names. Each product row links to either form using
`?product_id=<UUID>`. Successful submissions clear the form and show confirmation;
failed submissions preserve values. Quantities must be whole numbers from 1 to
2,147,483,647. Only `product_id` and `quantity` are sent; the API assigns identity
and timestamps. Pending submissions disable controls and block duplicate writes.

Stock indicators include a written label as well as color. Their display-only
thresholds, documented beside `stockLevel`, are **0: Out of stock**, **1-9: Low
stock**, **10 or more: Healthy stock**. These are not per-product reorder rules.
Outbound selection fetches fresh stock from `GET /inventory/products/{id}` and
keeps Quantity disabled while availability is unknown. Late responses for prior
selections are ignored. Above-stock quantities produce an inline warning and
disable submission; exact depletion is allowed. A server 400 stays beside Quantity
while stock refreshes, since another operative may have consumed stock meanwhile.

Readable API-authored 4xx and 5xx messages appear on screen, including nested
`detail.message`, string `detail`, and FastAPI 422 field errors. HTML, tracebacks,
unreadable bodies, and connection failures get readable inventory-specific text.
The shared error policy for incidents and identity is unchanged. Malformed success
bodies are errors, never empty inventories. Movement POSTs are not automatically
retried: check history after an ambiguous server/network failure before repeating
a write. History has no edit/delete actions, uses embedded product data without
extra lookups, and displays creation dates in the reader's timezone. Mobile rows
show all fields without requiring horizontal scrolling.

### Run and Verify

Set the backoffice's `NEXT_PUBLIC_API_URL` to the FastAPI origin, never to Supabase
or a database connection string. Run `npm run api` from the repository root, and
`npm --prefix uis/backoffice run dev` to serve the UI on port 3001. Start at
`http://localhost:3001/backoffice/inventory/products` and sign in with an existing
account.

```sh
npm --prefix uis/backoffice run lint -- --fix
npm --prefix uis/backoffice run typecheck
npm --prefix uis/backoffice test -- --runInBand
npm --prefix uis/backoffice run build
```

The focused API/helper tests are `tests/frontend/backoffice/inventory.test.ts`.
The browser acceptance runner is `tests/frontend/backoffice/inventory.browser.cjs`.
It intercepts all identity/inventory requests and performs no live stock writes.
Playwright can be installed outside the repository to avoid application dependency
changes:

```sh
npm install --prefix /tmp/trackflow-inventory-browser --no-package-lock --no-save playwright
/tmp/trackflow-inventory-browser/node_modules/.bin/playwright install chromium
NODE_PATH=/tmp/trackflow-inventory-browser/node_modules \
  node tests/frontend/backoffice/inventory.browser.cjs
```

The browser runner defaults to `http://localhost:3001`; override with
`BACKOFFICE_URL` if necessary. Screenshots default to
`/tmp/trackflow-inventory-screenshots` (`INVENTORY_SCREENSHOTS` overrides this).
On 2026-10-06, 38 focused tests, all 213 backoffice Jest tests, and 84 browser
checks passed. Lint, typecheck, and production build passed. Browser coverage
includes redirects on every route, missing/expired/rejected sessions, bearer
headers, both movement workflows, duplicate submission, success resets,
400/422/500 errors, stock refresh and reversed response completion, empty data,
malformed/HTML/offline failures, and desktop/mobile layouts. Browser requests use
test responses; live end-to-end movement writes remain an isolated-environment
verification step, not a reason to change operational stock.

## Stage 6: Context-Based Demo Seed

The inventory source is `contexts/coding-fundamentals.md`, **Sample Products**.
Root Markdown files contain no additional inventory dataset;
`Supplier-directory-context.md` contains supplier seeds, which do not belong in
inventory. The incident context's CSV seed is also a separate domain.

The developer approved mapping the sample's **Los Angeles** products to **Monterrey**,
while keeping Zaragoza unchanged. Names and SKUs are copied exactly. Only fields
supported by the approved inventory contract are imported; TypeScript-only category,
dimension, cost, and stock-threshold fields do not expand the SQL schema.

The sample shipment is **Pending**, so it is not imported as a completed historical
movement. The developer separately approved a synthetic demo laptop outbound of
one unit and a nine-unit opening receipt, preserving the original eight-unit balance.
All seed movement timestamps are generated at seed time, not presented as historical
completion dates from the source.

| SKU | Product | Warehouse | Inbound | Outbound | Initial Net Stock |
| --- | --- | --- | --- | --- | --- |
| SHOE-BLK-42 | Black Running Shoes - Size 42 | Monterrey | 45 | 0 | 45 |
| LAPTOP-DELL-15 | Dell Laptop 15 inch | Zaragoza | 9 | 1 | 8 |
| PERFUME-COCO-50 | Coco Perfume 50ml | Monterrey | 120 | 0 | 120 |

Run explicitly with the UUID of an existing active TinyDB account:

```sh
npm run seed:inventory -- --user-uuid YOUR_EXISTING_USER_UUID
```

The equivalent command is `uv run python -m scripts.seed_inventory --user-uuid YOUR_EXISTING_USER_UUID`.
The UUID is required; the seed never chooses an arbitrary user or creates a SQL
identity. Every movement is credited to that account.

The whole seed is one transaction. It reuses the inventory service's `record_order`
locking and outbound checks, inserts inbound before outbound, and uses stable UUIDs
for its demo rows. An identical second run inserts nothing and preserves timestamps.
Conflicting existing products or altered seed movements cause refusal and rollback,
not silent replacement. Use the same actor on subsequent runs. Real operational
movements remain untouched, so rerunning the seed never refills consumed stock.
Seeding is not automatic during API startup.

On 2026-10-04, the approved seed was loaded into Supabase: **3 products, 3 inbound,
1 outbound**, with computed balances **45/8/120**. A repeated run inserted zero rows
and skipped all seven seed records. The authenticated API verified these balances
and the selected TinyDB UUID. Ten focused seed tests cover reconciliation,
idempotency, active actors, conflicts, atomic rollback, and preservation of later usage.