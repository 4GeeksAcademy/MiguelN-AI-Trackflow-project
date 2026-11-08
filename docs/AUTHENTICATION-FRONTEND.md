# Authentication in the Next.js apps (AUTH-02, AUTH-03)

The API rejects every protected route without a bearer token
([AUTHENTICATION.md](./AUTHENTICATION.md)). This document covers the client side
of that contract: where the token is kept, how it is attached, and which views
require a session.

## Which views require a session

| App | Route | Protected | Notes |
| --- | --- | --- | --- |
| `uis/website` (:3000) | `/` | No | Public corporate page, Milestone 1 content |
| | `/login`, `/register` | No | Public by definition |
| | `/forgot-password`, `/reset-password` | No | Public by necessity: for people who cannot sign in |
| | `/candidates`, `/candidates/[id]` | Yes | Talent pipeline tracker |
| | `/account/profile`, `/account/change-password` | Yes | |
| `uis/backoffice` (:3001) | `/login`, `/register` | No | Public by definition |
| | `/forgot-password`, `/reset-password` | No | Public by necessity: for people who cannot sign in |
| | `/`, `/suppliers`, `/suppliers/[id]` | Yes | Internal operations console |
| | `/account/profile`, `/account/change-password` | Yes | |

`apps/website` is the static Flask-served marketing site. It has no session
logic and is untouched by this work.

## How protection is wired

Protected routes live in an `app/(protected)/` route group whose layout renders
`AuthProvider` + `AuthGuard`. The group adds no URL segment, so paths are
unchanged. Anything added under that folder is protected by construction.

```text
app/
  layout.tsx              # shell only
  page.tsx                # public corporate page (website only)
  (auth)/
    layout.tsx            # header bar without the account controls
    login/page.tsx        # public
    register/page.tsx     # public
  (protected)/
    layout.tsx            # AuthProvider > AuthGuard > nav > children
    ...
```

`AuthGuard` renders a placeholder until the session resolves, so protected data
is never painted before the check completes.

The two groups state the app's two modes structurally. In `uis/backoffice` the
header is split so both sides share it: `BackofficeNavShell` holds the wordmark
(linking to the dashboard) and the destinations, and `BackofficeNav` wraps it
with the signed-in email and the Log out button. The auth screens render the
shell alone, since there is no session for those controls to act on; their
destinations still point at protected routes and bounce back to `/login`
through the guard.

`uis/website` needs none of this: its auth screens inherit `TrackFlowNav` from
the root layout, which is public and session-free by design.

### Why not Next.js middleware

The token lives in `localStorage`. Middleware runs on the server, before the
browser hands over anything but cookies, so it cannot read the token. There is
no auth cookie by design, so the check has to be a client guard.

## Token lifecycle

| Event | Behaviour |
| --- | --- |
| Login | `POST /auth/login`, store `access_token` under `trackflow.access_token` |
| Registration | `POST /users`, then `POST /auth/login` with the same credentials, then store |
| Protected call | Read the token, send `Authorization: Bearer <token>` |
| `401` on a protected call | Clear the token, dispatch `trackflow:unauthorized`, redirect to `/login` |
| Logout | Clear the token, redirect to `/login` |

`AuthProvider` listens for `trackflow:unauthorized`, so expiry is handled in one
place rather than at every call site. The redirect carries `?next=<path>`, which
is sanitized to same-origin absolute paths before use, and login returns the
user there.

Every `localStorage` access is wrapped in `try`/`catch`: a browser that blocks
site data falls back to sending the user to `/login` rather than throwing.

## Module layout

Both apps carry the same modules, because `localStorage` is origin-scoped and
the two apps are served from different origins.

```text
types/auth.ts                     # AuthenticatedUser, Profile, form values, FieldErrors
hooks/useLocationSearch.ts        # query string without a Suspense boundary
lib/auth-storage.ts               # read/store/clear token, login URL + next sanitizing,
                                  #   reset-link query readers
lib/auth.ts                       # form validation, payload building, normalizers
services/auth-service.ts          # login, register, fetchCurrentUser, updateMyProfile,
                                  #   requestPasswordReset, resetPassword, changePassword, logout
components/auth/AuthProvider.tsx  # session state, 401 handling
components/auth/AuthGuard.tsx     # gate for the protected route group
components/auth/AuthField.tsx     # labeled input with its validation message
components/auth/ForgotPasswordPage.tsx
components/auth/ResetPasswordPage.tsx
components/account/ChangePasswordPage.tsx
```

The HTTP layer differs by app:

- `uis/backoffice` — `lib/api-client.ts` already points at this API through
  `NEXT_PUBLIC_API_URL`, so it gained `requestAuthenticatedApi` alongside the
  existing `requestApi`.
- `uis/website` — `NEXT_PUBLIC_API_URL` points at the **external** candidate
  records API, so identity calls go through a separate `lib/auth-api-client.ts`
  using `NEXT_PUBLIC_AUTH_API_URL`. The records API never receives the
  TrackFlow bearer token, which would hand a credential to a third party.

## Password recovery (AUTH-03)

Three screens, in both apps. The API contract they sit on is documented in
[PASSWORD-RECOVERY.md](./PASSWORD-RECOVERY.md).

| Route | Session | What it does |
| --- | --- | --- |
| `/forgot-password` | none | Email field. Submits to `POST /auth/forgot-password`, then locks. |
| `/reset-password` | none | Reads `?token=`, takes a new password plus confirmation, submits to `POST /auth/reset-password`, redirects to `/login?reset=success`. |
| `/account/change-password` | required | Current password, new password, confirmation. Submits to `POST /auth/change-password`. |

`/login` carries a **Forgot your password?** link under the password field, and
renders a success banner when it is reached with `?reset=success`.
`/account/profile` links to the change-password screen, so it is reachable
without typing the URL.

### Not revealing whether an address is registered

The API answers `POST /auth/forgot-password` with `200` and one fixed message
for a registered and an unregistered address alike. The frontend has to not give
that back:

- One confirmation string, rendered from a constant, for every success. The two
  outcomes are character-identical - there is no branch that could diverge.
- Client-side validation only checks that the field *looks* like an address. It
  never asks the API whether one exists.
- `requestPasswordReset` returns `void`. There is deliberately nothing in the
  return value a caller could inspect.
- A failure re-enables the form, which is safe: the route does not fail for an
  unknown address, so only a malformed payload or an unreachable API gets there.

### Locking the form after submit

Once the request succeeds the email input and the submit button are both
disabled and the button reads "Link sent", so an impatient second click cannot
queue a second email. The handler also returns early when it has already sent,
so a programmatic `form.requestSubmit()` is refused too - the disabled attribute
is the visible half of the guard, not the whole of it. Requesting another link
means reloading the page, which the screen says.

### Reading the token off the URL

`useSearchParams` would be the obvious tool, but it forces any page that calls
it behind a Suspense boundary at build time. Reading in an effect is the other
obvious move, and React 19 rejects setting state from an effect body
(`react-hooks/set-state-in-effect`).

`hooks/useLocationSearch.ts` wraps `useSyncExternalStore`, which is the
supported way to read a browser-only value: it renders the server snapshot
(`null`), then swaps in `window.location.search` immediately after, so hydration
still matches. `/reset-password` shows "Checking your reset link..." while it is
`null`, rather than flashing a "link is missing" error at someone whose link is
fine.

### Which failures are recoverable

`/reset-password` distinguishes two kinds of failure, because they need
different answers:

- **`400` - the token is dead** (expired, already used, or never real). The
  service raises `InvalidResetTokenError`. Resubmitting cannot help, so the form
  is *replaced* by the error and a **Request a new link** button to
  `/forgot-password`, rather than left there to be retried.
- **`422` - the password is wrong for the rules.** Mapped back onto the input
  and the form stays, because a second attempt can succeed.

The same split is why `/account/change-password` raises
`IncorrectCurrentPasswordError` on `400` and puts it on the current-password
field: the session is fine, only that one input is wrong.

### Confirmation fields never reach the API

`confirmPassword` exists only in the browser. Both forms check the match before
any request, because the API has no confirmation field to reject it with.
`validateResetPasswordForm` and `validateChangePasswordForm` share one
`newPasswordErrors` helper, so the two cannot drift apart.

### Field name translation

The API speaks snake_case (`new_password`, `current_password`); these forms
speak camelCase. `toFieldErrors` takes an optional alias map so a `422` lands on
the right input instead of falling through to a form-level message.

### Sessions and password changes

A successful reset clears any stored token before redirecting: it was issued
against the old password, and the user is on their way to sign in again anyway.
A successful *change* keeps the session, because the user is already signed in
and proved it.

Neither is a revocation story. Access tokens are JWTs that stay valid until they
expire, so a token already issued on another device survives both flows. That
gap is unchanged by this work and is still on the backlog.

## Error handling in forms

`ApiError` carries the decoded body alongside the flattened message, so forms
can map failures back onto their inputs:

- `422` — the `detail` array is split into `field: message` pairs and rendered
  under the matching input. Anything unmapped falls back to a form-level message.
- `409` on `POST /users` — a plain-string detail, shown on the email field.
- `401` on `POST /auth/login` — the API reports a wrong email and a wrong
  password identically, so the message belongs to the form, not to an input.
- `400` on `POST /auth/reset-password` — one generic message for every dead
  token, shown in place of the form with a way to request a new link.
- `400` on `POST /auth/change-password` — shown on the current-password field.

## Configuration

```bash
# uis/backoffice/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000

# uis/website/.env.local
NEXT_PUBLIC_API_URL=http://localhost:4000        # external records API
NEXT_PUBLIC_AUTH_API_URL=http://localhost:8000   # this repo's identity API
```

The API's CORS allowlist already covers `http://localhost:3000` and
`http://localhost:3001`.

## Running it

```bash
npm run api            # FastAPI on :8000
npm run dev:website    # :3000
npm run dev:backoffice # :3001
```

Register at `/register` in either app, or bootstrap an admin with
`uv run seed-user`.
