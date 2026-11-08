# Centralized Incident Manager

Backend for the incident manager described in `contexts/centralized-incident.md`:
one record per operational failure, reportable from anywhere in the company,
queryable and aggregatable from the same API.

This document covers stages 1–4 — data model, historical seed, backend, and
the screens in `uis/backoffice`.

## Where things live

| Path | What it holds |
|---|---|
| `packages/shared/trackflow_shared/incidents.py` | Allowed values and the lifecycle |
| `packages/shared/trackflow_shared/incidents_csv.py` | Helpdesk-CSV rules and the CSV → model mapping |
| `services/incidents/models.py` | Pydantic request/response contracts |
| `services/incidents/service.py` | Persistence and the transition rule |
| `services/incidents/router.py` | HTTP routes, no business rules |
| `services/core/http_errors.py` | Exception → response translation for the whole app |
| `scripts/seed_incidents.py` | Loads the historical CSV |
| `tests/` | 101 tests over the API, the seed, the shared rules and the error contract |
| `uis/backoffice/components/incidents/` | The form, the list, the status control, the summary panel |
| `uis/backoffice/lib/friendly-error.ts` | The one place that decides what a user may read about a failure |
| `uis/backoffice/lib/incident.ts` | Labels, the lifecycle mirror, validation, normalisers |

The vocabulary sits in `packages/shared/` rather than in `services/incidents/`
because the seed script has to produce exactly the values the API accepts. A
second copy of the category list would drift the first time one of them gained
an entry.

## Data model

Stored in TinyDB, table `incidents`, alongside the existing `suppliers`,
`users`, `profiles` and `password_resets` tables.

| Field | Type | Notes |
|---|---|---|
| `id` | string | UUID4, generated on insert. Never accepted from a client |
| `title` | string | Required, 1–120 characters, whitespace-stripped |
| `description` | string | Required, 1–4000 characters, whitespace-stripped |
| `category` | enum | One of the nine values below |
| `status` | enum | `open` · `in_progress` · `resolved` · `discarded`. Defaults to `open` |
| `origin` | enum | `customer` · `branch` · `internal` |
| `branch` | enum | Required for every origin. `central` when no specific facility |
| `created_at` | datetime | UTC, set on insert |
| `updated_at` | datetime | UTC, equal to `created_at` on insert, moved by a status change |

`category`: `lost_parcel`, `delivery_failure`, `inventory_discrepancy`,
`carrier_issue`, `returns_issue`, `warehouse_incident`, `system_failure`,
`client_complaint`, `other`.

`branch`: `central`, `la_warehouse`, `la_office`, `zaragoza_warehouse`,
`zaragoza_office`. Display labels for both languages are in
`BRANCH_LABELS`, so the form can show "Zaragoza — Almacén" while storing
`zaragoza_warehouse`.

### Integrity constraints

- `title` and `description` are `min_length=1` **after** whitespace stripping,
  so a field holding three spaces is a 400 rather than an incident with an
  empty title.
- `category`, `status`, `origin` and `branch` are enums, so a value outside the
  list cannot be stored by any route.
- `id`, `created_at` and `updated_at` are absent from `IncidentCreate`. A client
  that sends them has them ignored, not honoured.
- `SLA_CRITICAL_CATEGORIES` names `lost_parcel` and `carrier_issue` once, for
  the SLA filter the CEO and carrier ops need.

### One internal field

`IncidentInDB` carries `source_incident_id`: the `incident_id` of the helpdesk
record a seeded incident came from. It is how the seed stays idempotent. It is
absent from `IncidentResponse`, so it is not part of the API contract, and it is
`None` on anything created through the form.

## Lifecycle

```
open ──→ in_progress ──→ resolved   (final)
 │            │
 └────────────┴─────────→ discarded (final)
```

`ALLOWED_TRANSITIONS` is the single source for this. A refused move returns 400
with the reason in plain language, and it does not touch `updated_at`. Moving an
incident to the status it already holds is refused for the same reason: it would
change the modification timestamp without changing the incident.

## Endpoints

Mounted at `/api/incidents`. No token is required at this stage — the CONTEXT
has anyone in the company reporting an incident, and no route exposes
commercially sensitive data the way `/suppliers` does.

| Method | Path | Returns |
|---|---|---|
| `POST` | `/api/incidents` | 201 with the created incident; 400 on any invalid field |
| `GET` | `/api/incidents` | 200 with a list, newest first. Filters: `status`, `origin`, `branch`, `category` |
| `GET` | `/api/incidents/summary` | 200 with totals by status, category, origin and branch |
| `GET` | `/api/incidents/{id}` | 200 with the incident; 404 if unknown |
| `PATCH` | `/api/incidents/{id}/status` | 200 with the incident; 400 on an invalid transition; 404 if unknown |

Filters combine with AND, and an unknown filter *value* is a 400 rather than a
silently empty list.

`/summary` is declared before `/{id}` in the router. An incident id is an
arbitrary string, so the parameterised route would otherwise match the literal
path and look for an incident called "summary".

Read endpoints do not fail on an empty database: `GET /api/incidents` returns
`[]`, and `/summary` returns `total: 0` with every bucket present at zero — all
four statuses, nine categories, three origins and five branches — so a
dashboard can render a fixed set of tiles.

## Error handling

### Scope of the 400 remapping

The 400-with-a-named-field remapping applies **only under `/api/incidents`**.
Exception handlers in Starlette are global, so `make_validation_error_handler`
takes the prefix and hands everything else to FastAPI's own handler untouched.

This is not a detail. `resetPassword` in `uis/*/services/auth-service.ts` reads
**any** 400 as a spent reset token and replaces the form with "request a new
link". Remapping validation errors app-wide would mean a new password that is
merely too short — previously a 422, mapped back onto the password input — now
tells the user their reset link has expired and sends them to restart a recovery
that was working. `changePassword` has the same shape, reading a 400 as "your
current password is wrong".

The prefix is read off the router (`incidents_router.prefix`) rather than
written out again, so moving the router moves the behaviour with it.
`tests/test_error_contract.py` pins both halves.

### Response shape

Every error response has the same shape, whatever its status:

```json
{ "detail": { "field": "status", "message": "An incident that is 'open' can only move to 'in_progress' or 'discarded', not 'resolved'." } }
```

A request that fails to parse adds an `errors` array with every bad field, so a
form can mark all of them at once while `detail` holds the first:

```json
{
  "detail": { "field": "title", "message": "This field is required." },
  "errors": [
    { "field": "title", "message": "This field is required." },
    { "field": "category", "message": "Must be one of: 'lost_parcel', 'delivery_failure', … or 'other'." }
  ]
}
```

Three things this arrangement is for:

- **400, not 422.** FastAPI answers a malformed payload with 422 and a list of
  pydantic dictionaries. `validation_error_handler` remaps it to 400 and
  rewrites each pydantic error type as a sentence — `missing` becomes "This
  field is required.", `enum` becomes "Must be one of: …". The `body` / `query`
  prefix is stripped from the field path, so the field is `title`, not
  `body.title`.
- **No stack traces.** `unhandled_exception_handler` is registered for bare
  `Exception`. Any unhandled failure is a 500 whose body names nothing about the
  exception — not its type, module or line. The traceback goes to the
  `trackflow.errors` logger.
- **One shape.** Domain errors (`ValidationFailed`, `IncidentNotFound`) are
  raised by the service layer and translated in `services/core/http_errors.py`,
  so the service layer stays HTTP-agnostic.

## Historical seed

```bash
npm run seed:incidents                 # data/raw/incidents-trackflow.csv
uv run seed-incidents path/to/other.csv
```

The export is the CSV from the incident-analyser project. Every row was reported
by a client company or an end consumer, so each incident is seeded with
`origin: "customer"` and `branch: "central"` — a customer complaint corresponds
to no facility.

### Transformations

| CSV | Model | |
|---|---|---|
| `incident_id` | — | Duplicate control only. Not a model field |
| `description` | `title` | The export has no title column, so the description is shortened to ≤ 80 characters on a word boundary |
| `description` | `description` | Kept whole |
| `date` | `created_at` | `YYYY-MM-DD` read as midnight UTC, preserving the original date |
| `category` | `category` | See below |
| `status` | `status` | `OPEN`→`open`, `CLOSED`→`resolved`, `DISCARDED`→`discarded` |
| — | `origin` | Always `customer` |
| — | `branch` | Always `central` |

`country`, `customer_type`, `tracking_number`, `carrier`, `customer_email` and
`satisfaction_score` are validated but not stored: the incident model has no
field for them.

The analyser's five categories are narrower than the manager's nine. Each target
is the category whose own definition in the CONTEXT names the CSV case outright:

| CSV | Model | Why |
|---|---|---|
| `LOST_PARCEL` | `lost_parcel` | Direct |
| `WRONG_ADDRESS` | `delivery_failure` | Defined as "failed attempt, **incorrect address**" |
| `RETURN_REQUEST` | `returns_issue` | Direct |
| `DELAYED_DELIVERY` | `carrier_issue` | Defined as "**delay**, damage, SLA breach" |
| `DAMAGE` | `carrier_issue` | Same definition: "delay, **damage**, SLA breach" |

Two CSV categories therefore land on `carrier_issue`. The seed prints its own
category breakdown so the totals stay cross-checkable against the analyser.

Nothing maps to `in_progress`: the legacy helpdesk had no "being worked on"
state, so a seeded incident starts in whatever state the helpdesk last recorded.

### Validation

The rules are not reimplemented in the script. They live in
`trackflow_shared.incidents_csv.validate_csv_row` — the same set the analyser
applied — so the seed rejects exactly the rows the analyser counted as invalid:
country, carrier-for-country, tracking number length, category, description
length, email, `CLOSED` without a satisfaction score, and score range. One rule
is added, `invalid_date`, because the seed cannot derive a `created_at` from an
unparseable date.

An invalid row is never inserted. All of them are listed at the end of the run,
grouped by rule and then individually by CSV line number and `incident_id`.
Every row is checked against every rule rather than stopping at the first
failure, because the report counts how many rows fall into each.

`customer_email` holds real customer addresses. No issue message, console line
or return value in this module ever repeats one; a row that failed *on* the email
field is reported by line number and incident id only. There is a test asserting
this for both the validator and the console report.

### Idempotency

Each seeded incident stores the `incident_id` of its source row, and a row whose
id is already present is skipped. Running the seed twice inserts nothing the
second time, and a re-run after new rows are appended inserts only those.

The CONTEXT offers `title + created_at` as a fallback key. This script uses it
only for a row with no `incident_id`, because in the supplied export that pair
repeats across **7 of the 100 rows** — keying every row on it would silently
drop real incidents as duplicates. An incident created through the API has no
source id at all, so it can never collide with a CSV row.

### Verified output

Against `data/raw/incidents-trackflow.csv`, matching the analyser project's
published figures exactly:

```
TOTAL ROWS IN FILE ......................... 100
Inserted ................................... 95
Rejected (invalid) ......................... 5

INSERTED BY CATEGORY          INSERTED BY STATUS
carrier_issue ........ 45     open ........... 29
delivery_failure ..... 19     resolved ....... 52
lost_parcel .......... 14     discarded ...... 14
returns_issue ........ 17

INVALID RECORDS BY RULE
Carrier missing or not valid for country ... 1
Missing or short tracking number ........... 1
Missing or invalid category ................ 1
Missing or invalid customer email .......... 1
Closed incident with no score .............. 1
```

`GET /api/incidents/summary` after seeding: `total: 95`, `by_status` 29 / 0 / 52
/ 14, `by_origin` 95 customer, `by_branch` 95 central. The status and category
totals agree with the analyser's valid-record counts, with
`DELAYED_DELIVERY` (38) + `DAMAGE` (7) appearing as `carrier_issue` (45).

## Tests

```bash
npm run test:api        # or: uv run pytest tests/ -q
```

96 tests. Each one runs against its own TinyDB file in a temporary directory;
`conftest.py` redirects `DB_PATH` **and** clears the `lru_cache` on the TinyDB
handle, without which one test's incidents leak into the next and into
`data/suppliers.json`.

Covered: every required field and enum rejected with 400 and the right field
name; all four legal transitions and all ten illegal ones, including that a
refused transition leaves `updated_at` untouched; filters individually and
combined; empty-database reads; summary totals against the list; `/summary` not
being captured by `/{id}`; malformed JSON as 400 rather than 422; a forced
unhandled exception as 500 with no traceback, exception name or file path in the
body; every CSV rule; each entry of the status and category maps; idempotency
including the description+date collision case; and that no console output or
issue message repeats a customer email.


## Frontend (`uis/backoffice`)

The incident manager is internal, so it lives in the backoffice console next to
the supplier directory rather than in the public-facing `uis/website`. Both
screens are in the `(protected)` route group and are reachable from the top nav:
**Incidents** (`/incidents`) and **Report incident** (`/incidents/new`).

The API routes themselves carry no token requirement, so
`services/incidents-service.ts` calls `requestApi`, not
`requestAuthenticatedApi`. Sending a bearer token the API does not read, through
a helper whose whole job is handling a 401 that cannot happen, would describe a
contract that does not exist. That file is the only one that changes if the
incident routes are ever put behind auth.

### Registration form

`components/incidents/IncidentForm.tsx`.

- Every model field is on the form. `id`, `created_at` and `updated_at` are
  system-generated, so they are stated in a line of text rather than faked as
  disabled inputs.
- **`branch` is always visible and always required**, for every origin, with all
  five values under the CONTEXT's display names — including `central`, shown as
  "Central".
- **When `origin` is `branch`**, the branch field takes an accent ring and a
  tinted background, plus the line "You are reporting from a specific location -
  pick the facility this came from." The reminder is wired into the field's
  `aria-describedby`, so the emphasis is never carried by colour alone. Choosing
  any other origin removes the emphasis and leaves the field exactly as
  required.
- **While submitting**, the button shows a spinner, reads "Registering...", is
  `disabled` and carries `aria-busy`. The submit handler also returns early if a
  request is already open, so a double tap or an Enter key cannot fire a second
  one — `disabled` alone does not cover that.
- **On failure**, the user sees text written in the frontend or a message the
  API authored for a reader, never raw server output (see below). An error the
  API attaches to a field renders next to that input and sets `aria-invalid`;
  anything unattached becomes the form-level message. The form keeps what was
  typed.
- **On success**, the fields clear and a confirmation names the incident, its
  status, its branch and the generated reference, with a link to the list.

Fields are sized for the warehouse floor: 48px minimum touch targets,
`text-base` values, and selects everywhere except the two text fields the model
actually requires.

### Incident list

`components/incidents/IncidentsListPage.tsx`.

- Filters by `status`, `origin` and `branch`, held in the query string so a
  filtered view can be linked or reloaded. The API does the filtering; an
  unrecognised value in the URL is ignored rather than sent.
- A loading line while fetching, and a failure keeps whatever was last loaded,
  explains itself and offers **Try again**. The page never blanks: the header,
  the filters and the summary panel are all still there.
- An empty result always says why. No filters and no data reads "No incidents
  have been registered yet" with a link to the form; an empty filtered result
  names the filters that produced it and offers to clear them.
- **Status is editable from the row.** Only the transitions the lifecycle allows
  are offered, and a final status shows "Final state" with no control. The
  change is applied optimistically so a warehouse terminal responds to the tap
  rather than to the round trip.
- **A failed update puts the row back.** The previous status is captured before
  anything is touched, and on failure it is reapplied and the reason shown next
  to the control — including the API's own explanation of why a transition was
  refused, which arrives attached to the `status` field.
- A confirmed change refreshes the summary panel. The row itself stays put even
  if its new status no longer matches an active filter: dropping the row the
  reader just acted on, out from under the pointer, is worse than showing it
  until the next load.

### Summary panel

`components/incidents/IncidentSummaryPanel.tsx`, on the list page with its own
request and its own states, so neither one can take the other down.

Form was chosen before colour, per the `dataviz` skill:

- `total` is the one number the view leads with, so it is the hero figure and
  the only one on the page.
- Category has nine classes, past the point where more colours help, so all
  three of category, origin and branch are a **table with bars**: one hue
  (`--brand-primary`, 5.17:1 on the surface), the label and the count always
  written out. Nothing is reachable only by hovering, so there is no tooltip to
  add. Bars are drawn against the largest value in their own group, which the
  panel says in as many words.
- **Status is the exception that earns colour**, because its classes are states
  rather than identities. It uses the reserved status roles — `critical` for
  open, `warning` for in progress, `good` for resolved, muted for discarded —
  each with an icon and a label, so the state never rides on hue alone.

The trio was run through `scripts/validate_palette.js` against this app's white
surface. The first mapping tried (`serious` for open) failed the normal-vision
floor: `warning ↔ serious` measures ΔE 13.6, below the 15 gate, so two adjacent
tiles would have been hard to tell apart even with full colour vision. Moving
open to `critical` clears both gates — worst adjacent CVD ΔE 11.3, normal-vision
ΔE 27.6. `warning` remains 1.83:1 on white, which is a documented property of
the fixed status palette; it appears only as a filled mark beside dark text, and
the relief rule is satisfied by the always-visible labels. The tokens live in
`app/globals.css` with those numbers recorded.

States: a first load says it is loading; a refresh keeps the numbers on screen
and only marks itself busy, so the page does not reflow under the reader; a
failure keeps the last figures, labels them stale and offers **Retry**.

An empty database is not an error here — the API returns every bucket at zero,
and the panel renders the full grid of zeros.

### Verified in a browser

Driven with `playwright-core` against real Chrome, the dev server and the API,
with the seeded 95 incidents. 79 assertions across five runs:

| Run | Covers |
|---|---|
| list + summary | hero 95, all four breakdowns, seeded totals (open 29 / resolved 52 / discarded 14, carrier_issue 45), CONTEXT branch labels, 95 rows, both endpoints called, no console errors |
| interactions | each filter, a filter combination with no results, clear-filters, only-legal transitions offered, an optimistic move confirmed and the summary refreshed, a refused move reverted with the API's reason |
| form | reachable from the menu, every field present, all five branches, the branch emphasis appearing and clearing with `origin`, per-field validation, spinner + disabled + `aria-busy` while in flight, confirmation and cleared fields |
| failures | an API field error bound to its own input, a 500 carrying a stack trace shown as a friendly line with no leak of `Traceback` / `sqlite3` / `service.py` / line numbers, an aborted request, list failure with retry and a non-blank page, summary failure with the list still fully working |
| empty database | zero metrics with every bucket present, an informative empty list, no error state |

### Never the raw server error

`lib/friendly-error.ts` is the only place that decides what a user reads about a
failure. A message is shown **only** when it arrived in the API's documented
`{ field, message }` shape *and* the status is 400 or 404 — the two the API
writes plain language for. Everything else gets text written in the frontend:

| What happened | What the user sees |
|---|---|
| 400 / 404 in the documented shape | the API's own message, on the field it names |
| 5xx | "The incident service is having trouble right now..." |
| fetch never completed, or a non-`ApiError` threw | "Could not reach the incident service..." |
| 401 | "Your session has expired. Please sign in again." |
| an unreadable body on a 400/404 | the caller's fallback, or the not-found line |

Without that rule, `ApiError`'s own fallback ("Request failed with status 500")
and the browser's `TypeError: Failed to fetch` go straight to the screen. There
is also a guard that rejects any message containing a traceback, a stack frame,
a `file:line`, an HTML tag, or more than 300 characters — the API is not
supposed to be able to produce one, and if it ever does the frontend's text wins.

### Running it

```bash
npm run api              # FastAPI on :8000
npm run seed:incidents   # load the historical CSV
npm run dev:backoffice   # backoffice on :3001
```

Then sign in and open **Incidents**. `uis/backoffice/.env.local` points
`NEXT_PUBLIC_API_URL` at `http://localhost:8000`.
