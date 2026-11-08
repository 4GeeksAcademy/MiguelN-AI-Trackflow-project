# Password Recovery and Change (AUTH-03)

Two mechanisms, for two different situations:

- **Reset** — the user cannot sign in, so the proof of identity is a link mailed
  to the address on the account.
- **Change** — the user is signed in, so the proof is their current password on
  top of the session they already hold.

This document covers the API. The three screens that sit on it -
`/forgot-password`, `/reset-password`, `/account/change-password` - exist in
**both** Next.js apps and are documented in
[AUTHENTICATION-FRONTEND.md](./AUTHENTICATION-FRONTEND.md).

## Endpoints

| Method | Path | Auth | Body | Success | Failure |
| --- | --- | --- | --- | --- | --- |
| `POST` | `/auth/forgot-password` | public | `{email}` | `200` always | `422` only if `email` is not an address |
| `POST` | `/auth/reset-password` | public | `{token, new_password}` | `200` | `400` invalid/expired/used token, `422` password too short |
| `POST` | `/auth/change-password` | token | `{current_password, new_password}` | `200` | `400` wrong current password, `401` no/bad token, `422` password too short |

All three answer with `{"message": "..."}`.

### `POST /auth/forgot-password`

Returns `200` with the same body whether or not the address has an account:

```json
{ "message": "If an account exists for that address, a password reset link is on its way." }
```

That is the point of the route. A `404`, a different message, or a visibly
quicker reply for an unknown address would turn it into a way to test which
addresses are registered. For the same reason the email is queued as a FastAPI
background task and goes out *after* the response, so the two cases cannot be
told apart by timing either.

A deactivated account is treated exactly like a missing one: same response, no
token, no email.

### `POST /auth/reset-password`

Consumes the token from the emailed link and sets the new password. Every way a
token can fail returns the same `400`:

```json
{ "detail": "This password reset link is invalid or has expired." }
```

Which way it actually failed — forged signature, wrong token type, expired,
unknown `jti`, already spent, deleted account — is written to the log, not to
the response. Distinguishing them would tell a caller whether a token they did
not issue was ever real.

On success the token is spent **and every other outstanding reset token for that
user is spent with it**, so a second link requested in the meantime stops working.

### `POST /auth/change-password`

Requires `Authorization: Bearer <token>` *and* the current password. A valid
session alone is not enough: a browser left open on a shared machine should not
be enough to take the account over.

A wrong current password is `400`, not `401` — the session is fine, the payload
is not. Succeeding here also spends any outstanding reset links for that user.

### When outstanding links die

A reset link is a live credential sitting in the mailbox it was sent to, so
every outstanding link for an account is spent when:

- a password is set, by any route: reset, change, or `PUT /users/{id}` - a
  pending link would otherwise undo it;
- the account's email changes through `PUT /users/{id}` - the old mailbox is
  no longer trusted, often the very reason for the change;
- the account is deactivated - reactivating it must not revive links requested
  before.

An edit that changes none of these (re-saving the same address, a role change)
leaves a link its owner may be about to use alone.

## How the reset token works

The token is a JWT signed with `JWT_SECRET_KEY`, the same secret as access
tokens, carrying:

```json
{ "sub": "<user uuid>", "jti": "<uuid>", "typ": "password_reset", "iat": ..., "exp": ... }
```

Signature and `exp` cover authenticity and expiry, so a forged or stale token is
refused without a storage lookup.

**`typ` keeps the two kinds of token apart.** `decode_access_token` accepts only
`typ: access` and `decode_password_reset_token` only `typ: password_reset`, so a
reset link cannot be presented as a bearer credential and a session cannot reset
a password. Tokens minted before this claim existed are treated as access
tokens, so nobody was signed out by the change.

**`jti` is what makes a token single-use**, which is the one property a
signature cannot carry. Each issued token registers a row in the TinyDB table
`password_resets`:

| Field | Meaning |
| --- | --- |
| `jti` | The token's id. Matched against the `jti` claim. |
| `user_id` | Who it was issued for. Must match `sub`. |
| `created_at` / `expires_at` | Issued and expiry timestamps. |
| `used_at` | `null` until spent, then the moment it was. |

The row holds the `jti` **and never the token**, so a leaked database file does
not hand anyone a working reset link. Rows past their expiry are purged whenever
a new token is issued.

A reset therefore fails closed at five separate points: bad signature, wrong
`typ`, expired `exp`, missing or already-stamped registry row, and a `user_id`
that does not match the token's `sub`.

## Email delivery

[Resend](https://resend.com) carries the message. Two layers, so neither knows
about the other:

- `services/core/email.py` — transport only. Picks the backend and puts the
  message on the wire.
- `services/auth/emails.py` — the reset message itself: subject, HTML, plain
  text, and the link.

**The backend is chosen by configuration, not by the call site:**

| `RESEND_API_KEY` | Backend | Behaviour |
| --- | --- | --- |
| set | Resend | Sends over the Resend HTTPS API. |
| unset or empty | console | Logs the whole message, reset link included. |

The console backend is what makes the flow walkable in development without an
email account: request a reset, read the link out of the API log, paste it in.
It logs at `WARNING` so it is visible with uvicorn's default log level.

Delivery failures never reach the caller. `send_password_reset_email` catches
them, logs them and returns `False`, because it runs after the response has
already gone out.

The message is one fluid column capped at 600px with inline styles, 16px body
text and a 48px-tall button, so it reads on a phone without zooming; it ships a
plain-text alternative and embeds no images. Verified in a real browser at
390px and 800px with no horizontal overflow.

### Setting up Resend

1. Create an account at [resend.com](https://resend.com) and generate a key
   under **API keys**.
2. Put it in `.env` as `RESEND_API_KEY`. Never in the code, never in a commit —
   `.env` is git-ignored and `.env.example` carries the variable names only.
3. Leave `EMAIL_FROM` as `TrackFlow <onboarding@resend.dev>` to start. That is
   Resend's shared sender and needs no DNS setup, but **in test mode it only
   delivers to the address that owns the Resend account**. To mail anyone else,
   verify a domain with Resend and use an address on it.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `RESEND_API_KEY` | _(empty)_ | Resend API key. Empty selects the console backend. |
| `EMAIL_FROM` | `TrackFlow <onboarding@resend.dev>` | Sender address for outgoing mail. |
| `FRONTEND_BASE_URL` | `http://localhost:3000` | Base of the reset link. The API appends `/reset-password?token=<jwt>`. |
| `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` | `30` | Link lifetime. Must be 15-60; outside that the API refuses to start. |

All four are documented in `.env.example`. The 15-60 bound is validated by
`pydantic-settings`, so a bad value fails at startup rather than at the first
reset request.

### One link, two apps

`/reset-password` exists in `uis/website` (:3000) and `uis/backoffice` (:3001),
but `FRONTEND_BASE_URL` is a single value, so every emailed link points at one
of them - :3000 by default. A backoffice user who requests a reset finishes it
on the website and then signs in wherever they were going; the token is issued
by this API and neither app is special to it.

If the backoffice should own the link instead, point `FRONTEND_BASE_URL` at
`http://localhost:3001`. Splitting it per app would mean telling the API which
app a request came from, which is a bigger change than this ticket called for.

## Walking the flow locally

With `RESEND_API_KEY` empty:

```bash
npm run api

curl -X POST localhost:8000/auth/forgot-password \
  -H 'Content-Type: application/json' -d '{"email":"you@example.com"}'
```

The API log prints the full message. Copy the `token=` value out of the link:

```bash
curl -X POST localhost:8000/auth/reset-password \
  -H 'Content-Type: application/json' \
  -d '{"token":"<paste>","new_password":"a-new-password"}'
```

Running it a second time with the same token returns `400`.

For the authenticated route:

```bash
TOKEN=$(curl -s -X POST localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"a-new-password"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

curl -X POST localhost:8000/auth/change-password \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"current_password":"a-new-password","new_password":"another-password"}'
```

## Where the code lives

```text
services/
  core/
    config.py     # the four settings above, validated at startup
    email.py      # transport: Resend or console, chosen by configuration
    errors.py     # InvalidResetToken, IncorrectPassword
    security.py   # typed JWTs: create/decode for access and reset tokens
  auth/
    emails.py     # the reset message: subject, HTML, text, link
    models.py     # the three request bodies, MessageResponse, PasswordResetInDB
    router.py     # the three routes
    service.py    # issue, consume and invalidate reset tokens; change password
```

## Not covered

- **Revocation.** Access tokens are JWTs that stay valid until they expire, so a
  session already issued on another device survives both a reset and a change.
  The reset flow clears the *local* token, which is not the same thing.
- **Rate limiting.** `POST /auth/forgot-password` can be called repeatedly for
  the same address. Each call is cheap and every previous link dies as soon as
  one is used, but nothing stops an attacker filling someone's inbox. A per-IP
  and per-address limit belongs in front of this route.
- **Notifying the user that their password changed.** A "your password was
  changed" email is the usual way someone finds out an account was taken over.
- **Reusing an old password.** Neither route checks the new password against the
  current one or against history.
