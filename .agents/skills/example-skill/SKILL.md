# SKILL: create-service-endpoint

## Purpose
Create exactly one new internal API endpoint under `/services/<service-name>/` following this monorepo's conventions, and do not modify or create files outside that service folder.

## Required Inputs
- Service name: `returns`
- Endpoint path: `/services/returns/approve`
- HTTP method: `POST`
- Error cases:
	- Unknown `orderId` -> `404`
	- Invalid `warehouse` -> `400`
- Auth required: `yes` (internal only)

## Missing Input Rule
If any required input is missing, stop and ask the developer. Do not guess.

## Implementation Rules
1. Create one endpoint file under `/services/returns/` and nowhere else.
2. Model every request body field with explicit TypeScript types. Do not use `any`.
3. Validate request body before any business logic.
4. Return `400` for invalid input (including invalid warehouse).
5. Return `404` when `orderId` is unknown.
6. Return `500` only for genuine server failures, never for client input errors.
7. Enforce internal-only authentication for this endpoint.
8. Do not create or modify files outside `/services/returns/`.

## Expected Output
- One new endpoint implementation for `POST /services/returns/approve`.
- Input validation and typed request body.
- Error handling that maps exactly to:
	- `400` invalid input/warehouse
	- `404` unknown orderId
	- `500` server failure only
- No repo changes outside `/services/returns/`.

## Verification Checklist
- Endpoint file exists under `/services/returns/`.
- No added or changed files outside `/services/returns/`.
- Request body has a concrete TypeScript type with all fields represented.
- Validation runs before endpoint logic uses request data.
- `400`, `404`, and `500` behavior matches the required mapping.
- Endpoint requires internal authentication.
