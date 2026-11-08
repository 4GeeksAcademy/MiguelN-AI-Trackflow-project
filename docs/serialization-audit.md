# Backend Serialization Audit

Date: 2026-10-12

This audit follows the CTO's three phases: Audit, Implementation, Verification. It traces the in-repository callers, not database columns. A response guard/normalizer may read a field to validate a payload even when the UI does not display it; those cases are called out. Where no in-repository caller exists, existing safe fields are preserved until external consumers are confirmed.

## Classification

- **Already shaped**: an explicit response model has a consumer-matched projection.
- **Partially shaped**: an explicit response model exists, but its fields, shape, or relationship depth do not match the known consumer cleanly.
- **Empty response**: the route intentionally returns no body (204).
- No successful business endpoint was found returning a raw ORM object or untyped dictionary. Twenty-eight business endpoints declare a `response_model`; the two DELETE routes are bodyless 204s. Error-handler JSON bodies are separate error contracts, not successful endpoint serialization.

## Endpoint Audit

| Endpoint | Caller and consumer fields | Current response | Internal data and target |
|---|---|---|---|
| `POST /auth/login` | `uis/website` and `uis/backoffice` login flows; read `access_token`. | `TokenResponse`: token, token type, expiry. **Already shaped.** | Password/hash must never be returned. Keep OAuth-compatible explicit token response. |
| `POST /auth/token` | Swagger OAuth Authorize flow; consumes token response. | `TokenResponse`. **Already shaped.** | Same target as login; do not expose submitted credentials. |
| `GET /auth/me` | Both apps' `AuthProvider`; displays email/role and profile name, phone, address. Profile ID is used to determine whether a profile exists. | `AuthenticatedUserResponse` with nested `ProfilePublicResponse`. **Already shaped.** | Returns top-level `email`, `role` and nested profile `id`, `name`, `phone`, `address`; omits unused account `id`/`is_active` and profile `user_id`. |
| `POST /auth/forgot-password` | Both recovery screens; ignore body and show a local generic confirmation. | `MessageResponse`. **Already shaped.** | Message must remain identical for registered and unknown addresses. Never return reset token or account-existence data. |
| `POST /auth/reset-password` | Both reset screens; use HTTP success to navigate; ignore body. | `MessageResponse`. **Already shaped.** | Never return reset token or password. Keep generic confirmation. |
| `POST /auth/change-password` | Both change-password forms; use HTTP success; ignore body. | `MessageResponse`. **Already shaped.** | Never return current/new password or hash. Keep explicit confirmation. |
| `POST /users` | Both registration flows; discard response, then sign in. | `UserResponse` (`id`, email, active state, role, created time). **Partially shaped.** | Hash is already excluded. Known callers use no returned fields; target a minimal acknowledgement only after external contract confirmation. |
| `GET /users` | No in-repo HTTP caller. | `list[UserResponse]`. **Partially shaped.** | Excludes hash, but exposes account IDs, emails, roles, active flags, creation times. Preserve safe fields pending API-owner confirmation; review authorization and least privilege. |
| `GET /users/{user_id}` | No in-repo HTTP caller. | `UserResponse`. **Partially shaped.** | Same unused account fields; preserve until external consumers are confirmed. |
| `PUT /users/{user_id}` | No in-repo HTTP caller. | `UserResponse`. **Partially shaped.** | Request-only `current_password` and stored hash are excluded. Preserve remaining safe fields pending external confirmation. |
| `DELETE /users/{user_id}` | No in-repo HTTP caller; no success body is needed. | Empty `204` with `response_model=None`. **Empty response.** | Keep bodyless; explicit no-content contract. |
| `GET /profiles/me` | No direct in-repo caller; both apps obtain profile through `/auth/me`. | `ProfileResponse` with `id`, `user_id`, name, phone, address. **Partially shaped.** | Includes internal `user_id` FK. Preserve the current safe fields until external consumers are confirmed; then decide whether to remove the FK. |
| `PUT /profiles/me` | Both profile screens; response reads profile ID, name, phone, address. | `ProfilePublicResponse`. **Already shaped.** | Returns `id`, `name`, `phone`, `address`; omits `user_id`. `ProfileUpdate` remains a separate input schema. |
| `POST /suppliers` | Backoffice supplier creation; displays returned name and inserts the record into the list. | `SupplierListItem`. **Already shaped.** | Returns only directory fields; contact email, notes, and update time are not needed in this confirmation/list insertion. |
| `GET /suppliers` | Backoffice list reads ID, name, service zone, country, categories, rate, currency, status. Detail view also uses contact email, notes, updated time. | `list[SupplierListItem]`. **Already shaped.** | List schema now omits detail-only contact/notes/timestamp. |
| `GET /suppliers/{supplier_id}` | Backoffice detail view reads all supplier fields, including ID and updated time. | `SupplierResponse`. **Already shaped.** | Keep the explicit detail projection. |
| `PATCH /suppliers/{supplier_id}/rate` | Backoffice rate editor; full returned supplier replaces row/detail state. | `SupplierResponse`. **Partially shaped.** | Consider returning only changed rate fields and merging client-side; retain full response if replacement semantics are an intentional contract. |
| `PATCH /suppliers/{supplier_id}/status` | Backoffice status toggle; full returned supplier replaces row/detail state. | `SupplierResponse`. **Partially shaped.** | Consider a status-focused result and client merge; decide alongside rate mutation contract. |
| `DELETE /suppliers/{supplier_id}` | No in-repo caller; no success body is needed. | Empty `204` with `response_model=None`. **Empty response.** | Keep bodyless; preserve operation and explicit no-content contract. |
| `POST /api/incidents` | Backoffice incident form; confirmation displays ID, title, status, branch. | `IncidentCreatedResponse`. **Already shaped.** | Returns only confirmation fields; description, category, origin, timestamps, and internal seed key are omitted. |
| `GET /api/incidents` | Backoffice list/status control reads ID, title, description, category, status, origin, branch, created/updated times. | `list[IncidentResponse]`. **Already shaped.** | Those fields support the list. API accepts category filter, but this UI does not send it. Keep source seed key excluded. |
| `GET /api/incidents/summary` | Backoffice summary panel reads total and every status/category/origin/branch bucket. | `IncidentSummary`. **Already shaped.** | Keep aggregate schema; all buckets have consumers. |
| `GET /api/incidents/{incident_id}` | No in-repo HTTP caller. | `IncidentResponse`. **Already shaped.** | `source_incident_id` remains excluded. Preserve safe fields pending external consumer confirmation. |
| `PATCH /api/incidents/{incident_id}/status` | Backoffice status control reads returned incident to replace the row, then refreshes summary. | `IncidentResponse`. **Already shaped.** | Keep list-sized response; request schema is status-only and separate. |
| `GET /inventory/products` | Backoffice product list and inbound/outbound forms read ID, name, SKU, warehouse, current stock. | `list[ProductResponse]`. **Already shaped.** | Keep the used projection; omit database/audit internals. |
| `POST /inventory/products` | Facade function exists; no screen calls it. | `ProductResponse`. **Already shaped.** | Preserve safe fields pending external consumer confirmation. |
| `GET /inventory/products/{id}` | Backoffice outbound form uses `current_stock`; its response guard checks ID and stock. | `ProductAvailability`. **Already shaped.** | Returns only ID and current stock, which the form consumes. |
| `POST /inventory/products/inbound` | Inbound form displays quantity and product name/warehouse. | `InboundOrderConfirmation`. **Already shaped.** | Flat confirmation returns quantity, product name, warehouse, direction; excludes order/user IDs, timestamp, and nested product data. |
| `POST /inventory/products/outbound` | Outbound form has the same response use as inbound. | `OutboundOrderConfirmation`. **Already shaped.** | Same flat confirmation projection. |
| `GET /inventory/orders` | Backoffice history displays order ID, product name/SKU/warehouse, quantity, direction, created time, and `user_uuid`; guard checks `product_id` against product identity. | `OrderHistoryItem`, a flat projection. **Already shaped.** | Keeps displayed fields, actor UUID, and client-checked `product_id`; removes nested product object duplication. |

## Framework-Generated Routes

| Endpoint | Consumer and output | Serialization assessment |
|---|---|---|
| `GET /openapi.json` | API clients and `/docs`; generated OpenAPI document. | Framework-owned schema output; verify documented response models match actual contracts. |
| `GET /docs` | Swagger UI; uses `/auth/token` for OAuth Authorize. | Framework-owned HTML; no domain response serializer needed. |
| `GET /docs/oauth2-redirect` | Swagger OAuth callback. | Framework-owned HTML/redirect response. |
| `GET /redoc` | ReDoc API explorer. | Framework-owned HTML; no domain response serializer needed. |

## Phase 2 Implementation Status

- **Implemented:** `/auth/me` returns only email, role, and the public profile projection; `PUT /profiles/me` also omits profile `user_id`. `GET /profiles/me` retains its established fields pending external-consumer confirmation.
- **Implemented:** supplier create/list use `SupplierListItem`; supplier detail and rate/status mutations retain the full response because those consumers need detail fields.
- **Implemented:** incident creation returns only its confirmation fields (`id`, `title`, `status`, `branch`); list/detail/status responses retain the full incident projection.
- **Implemented:** inventory availability is `id` plus stock; movement writes return flat confirmations; order history is flat and keeps displayed `user_uuid` plus the `product_id` used by client validation.
- **Implemented:** user and supplier deletion explicitly declare a bodyless `204` response.
- **Deferred pending API-owner confirmation:** minimize user administration responses, `/profiles/me`'s foreign key, and routes without in-repo callers. Keep existing safe fields until external contracts are known.
- **Deferred:** supplier rate/status mutation response narrowing; the detail view consumes the same operations and currently replaces its supplier state with the full result.
- No authorization or database behavior changed as part of response shaping.

## Phase 3 Verification

- Compare the registered app methods/paths against every business route above and account for the generated docs routes separately.
- Exercise API responses and assert profile `user_id`, password hashes, reset credentials, incident `source_incident_id`, and unintended database fields are absent.
- Verify each frontend projection keeps every field rendered or required by a client guard; update guards when intentionally changing payload shapes.
- Run focused backend and frontend tests/typechecks for each changed contract, then the complete backend suite (`uv run pytest tests/ --cov -q`).
- Inspect `/docs` and `/openapi.json`; mark an audit row complete only after the runtime payload matches its declared schema and consumer.

### Verification Run — 2026-10-12

- Every registered business method/path (30 total) is represented in this audit; the four generated docs routes are listed separately. OpenAPI assertions pin the changed response schemas.
- Backend: `uv run pytest tests/ --cov -q` passed, 438 passed and 11 skipped; total coverage 89.60%, above the 70% gate.
- Backoffice: all 216 Jest tests passed and `npm run typecheck` passed.
- Website: all 170 Jest tests passed and `npm run typecheck` passed.
- API regression tests assert auth account-ID/active-state and profile-FK exclusion in known projections, supplier create/list/detail shapes, incident creation shape, inventory availability/movement/history shapes, and bodyless DELETE schemas.
- OpenAPI was checked programmatically; the Swagger/ReDoc pages were not manually exercised in a browser during this run. The 11 skipped tests include opt-in checks and were not enabled here.
- `git diff --check` passed. Ruff still reports nine existing `B008`/`UP017` style findings in query defaults and timestamp usages/assertions; the new test file passes Ruff formatting, and no broad reformat was applied.

## Source Map

- Route registration: `services/main.py`.
- Auth: `services/auth/router.py`, `services/auth/models.py`; consumers in both apps' `services/auth-service.ts` and auth providers.
- Profiles: `services/profiles/router.py`, `services/profiles/models.py`; consumers in both apps' `components/account/ProfilePage.tsx` and auth normalizers.
- Users: `services/users/router.py`, `services/users/models.py`.
- Suppliers: `services/suppliers/router.py`, `services/suppliers/models.py`, and `uis/backoffice/services/suppliers-service.ts` with supplier components.
- Incidents: `services/incidents/router.py`, `services/incidents/models.py`, and `uis/backoffice/services/incidents-service.ts` with incident components.
- Inventory: `services/inventory/routers/inventory.py`, `services/inventory/schemas.py`, and `uis/backoffice/lib/inventory.ts` with inventory components.
- Contract tests: `tests/auth/`, `tests/test_incidents_api.py`, `tests/test_inventory_api.py`, `tests/test_inventory_schemas.py`, and `tests/test_error_contract.py`.