# Authentication and Route Protection (AUTH-01)

Stateless JWT authentication for the TrackFlow API. No sessions, no auth cookies:
the bearer token is the only credential the API accepts.

## Where things live

```text
services/
  main.py                  # app composition root: mounts every domain router
  core/
    config.py              # Settings (pydantic-settings), reads .env at startup
    db.py                  # shared TinyDB handle, one table per domain
    email.py               # transactional email transport (AUTH-03)
    errors.py              # domain errors the routers translate into HTTP
    security.py            # bcrypt hashing + JWT encode/decode
  auth/
    dependencies.py        # get_current_user, is_admin
    emails.py              # the password reset message (AUTH-03)
    models.py              # LoginRequest, TokenResponse, AuthenticatedUserResponse
    router.py              # /auth/login, /auth/token, /auth/me, the password routes
    service.py             # password reset tokens and password change (AUTH-03)
  users/
    models.py              # Role enum, UserCreate/Update/InDB/Response
    service.py             # create/get/get_by_email/list/update/delete
    router.py              # /users
    seed.py                # CLI to bootstrap an admin
  profiles/
    models.py service.py router.py   # /profiles
  suppliers/
    router.py              # existing routes, now token-protected
```

## Storage

`User` and `Profile` live in **TinyDB only**, in the tables `users` and `profiles`
of the same file the suppliers directory uses (`data/suppliers.json`, override with
`DB_PATH`). They are never mirrored into Supabase/SQLModel.

The `id` is a **UUID4 string**, not a TinyDB `doc_id` integer, because PostgreSQL
tables (inventory and the rest) reference it as `user_uuid`. Those tables store that
id and nothing else about the user.

`User` holds credentials only - `id`, `email`, `hashed_password`, `is_active`,
`role`, `created_at`. Display name and contact data live on `Profile`, linked
one-to-one through `user_id`.

## Endpoints

| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| `POST` | `/users` | public | Register. Hashes the password, creates the linked profile in the same operation. Always role `user`. |
| `GET` | `/users` | token | List all users. |
| `GET` | `/users/{id}` | token | Single user. |
| `PUT` | `/users/{id}` | token | Self or admin. `role` and `is_active` are admin-only. Changing your own `email` or `password` (admins included) also takes `current_password`. |
| `DELETE` | `/users/{id}` | token | Self or admin. Removes the linked profile too. |
| `GET` | `/profiles/me` | token | The caller's profile. |
| `PUT` | `/profiles/me` | token | Update `name`, `phone`, `address`. Owner only by construction. |
| `POST` | `/auth/login` | public | JSON `{email, password}` -> JWT. |
| `POST` | `/auth/token` | public | Same thing, OAuth2 form-encoded. This is what the `/docs` **Authorize** button posts to. |
| `GET` | `/auth/me` | token | Email, role and the linked profile. |
| `POST` | `/auth/forgot-password` | public | `{email}` -> emails a reset link. Always `200`. See [PASSWORD-RECOVERY.md](./PASSWORD-RECOVERY.md). |
| `POST` | `/auth/reset-password` | public | `{token, new_password}`. `400` for an invalid, expired or already-used token. |
| `POST` | `/auth/change-password` | token | `{current_password, new_password}`. `400` if the current password is wrong. |
| all 6 | `/suppliers*` | token | Rates and contact emails are commercially sensitive. |

`POST /users`, `POST /auth/login`, `POST /auth/token`, `POST /auth/forgot-password`
and `POST /auth/reset-password` are the only public routes. The last two have to
be: they exist for people who cannot sign in.

## Status codes

- `401` - no token, malformed token, bad signature, expired token, or the user
  behind the token no longer exists.
- `403` - the token is valid but the caller may not do this: acting on someone
  else's account, changing `role`/`is_active` without being an admin, or a
  deactivated account.
- `400` - the request is well-formed and the caller is who they say they are,
  but the content is wrong: an invalid, expired or already-spent password reset
  token, or a missing or wrong current password on `/auth/change-password` or
  on `PUT /users/{id}`.
- `409` - email already registered.
- `422` - payload validation, including a `role` outside `admin|manager|user`.

## Configuration

Copy `.env.example` to `.env` (git-ignored) and set:

| Variable | Meaning |
| --- | --- |
| `JWT_SECRET_KEY` | Signing secret, minimum 32 chars. Never hardcoded, never committed. |
| `JWT_ALGORITHM` | `HS256`. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime. |
| `DB_PATH` | Optional TinyDB file override. |
| `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` | Reset link lifetime, 15-60. |
| `RESEND_API_KEY` | Resend key for the reset email. Empty logs the mail instead of sending it. |
| `EMAIL_FROM` | Sender address for outgoing mail. |
| `FRONTEND_BASE_URL` | Base of the reset link in the email. |

Settings are validated by `pydantic-settings`, so a missing or too-short secret
fails at startup rather than at the first login.

## Passwords

`libpass` with the bcrypt scheme, cost 12 (`from passlib.hash import bcrypt` -
the import path is unchanged from the unmaintained `passlib`). Passwords are
8 characters to 72 **bytes** of UTF-8 - bytes, not characters, since an
accented letter such as `ñ` takes two - and may not contain NUL. bcrypt 5
refuses both rather than truncating, so the shared `Password` type rejects them
with a `422` instead of letting the hash fail as a `500`. Passwords are never
stored or compared in plain text. A login against an unknown email still runs
one bcrypt verification so a missing account is not faster to probe.

## Token types

Every JWT carries a `typ` claim naming what it may do: `access` for a session,
`password_reset` for a reset link. Each decoder accepts only its own, so a reset
link cannot be replayed as a bearer credential and a session cannot reset a
password. Tokens issued before this claim existed are read as access tokens, so
adding it signed nobody out.

Every token must also carry `exp`. python-jose only checks an expiry that is
present, so the decoder requires one: a correctly signed token without it is
refused. A change that stopped setting `exp` therefore fails loudly instead of
issuing sessions that never end - the class of regression behind AUTH-088.

## Running it

```bash
npm run api                              # uvicorn services.main:app --reload --port 8000
uv run seed                              # suppliers directory
uv run seed-user --email you@trackflow.com --role admin
```

`POST /users` deliberately cannot create an admin, so the first admin is created
with `seed-user`. Run it again on an existing email to change that user's role.

## Manual check in /docs

1. `POST /users` - register with an email, a password and optional `name`/`phone`/`address`.
2. **Authorize** (top right) - enter the email in the `username` field and the password.
   Swagger posts to `/auth/token` and stores the token.
3. Call `GET /auth/me`, `GET /profiles/me` or any `/suppliers` route - they work.
4. **Logout** in the Authorize dialog and retry - `401`.
5. Paste a mangled token into Authorize and retry - `401`.

## Frontend

Both Next.js apps now sign in against this API, store the token and send it on
every protected call. See [AUTHENTICATION-FRONTEND.md](./AUTHENTICATION-FRONTEND.md)
for the client-side contract (AUTH-02).

## Password recovery

Forgotten-password reset and signed-in password change are documented separately
in [PASSWORD-RECOVERY.md](./PASSWORD-RECOVERY.md) (AUTH-03): the single-use token
design, the Resend integration, and the environment variables it needs.
