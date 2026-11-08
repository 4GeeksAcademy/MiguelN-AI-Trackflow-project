# Testing

This document covers the test suites for the TrackFlow authentication API (ticket
AUTH-088): how to run them, what each one covers, and the test plan itself. The
plan lists every case, the reason it is included, and the bugs the cases exposed.

AUTH-088 exists because a refactor broke token expiration, no test caught it, and
users were locked out for two hours. The rule since then is that every
authentication endpoint has a happy-path test, an edge-case test and a
failure-mode test before anything else merges.

## Running the tests

### Backend (pytest)

```bash
uv run pytest                  # every backend test
uv run pytest --cov            # the same, plus coverage of the authentication API (fails under 70%)
uv run pytest tests/auth       # only the authentication suite
npm run test:api               # shortcut for `uv run pytest tests/ -q`
```

The suite needs no `.env`. It pins its own settings, so it behaves the same on
every machine and never sends a real email. See [Fixtures](#fixtures-and-what-they-guarantee).

### Frontend (Jest)

```bash
npm --prefix uis/website install      # first time only
npm --prefix uis/backoffice install   # first time only

npm --prefix uis/website test         # jest --coverage for uis/website
npm --prefix uis/backoffice test      # jest --coverage for uis/backoffice
npm run test:ui                       # both apps
```

Each app has its own `jest.config.ts` at its root. Jest loads it through Node's
built-in TypeScript support, so Node 22.18+ or 23.6+ is required (the repo uses
24). Both configs share the test files in `tests/frontend/shared/`, so each app
is tested against its own copy of the duplicated auth modules. If the two copies
drift apart, one app's run fails.

## What each suite covers

| Suite | Location | What it covers |
| --- | --- | --- |
| Authentication API (216 tests) | `tests/auth/` | One module per endpoint (13), plus the token checks, the protected-route gate and password hashing that every endpoint relies on. |
| Error contract (existing) | `tests/test_error_contract.py` | Which routes answer 400 and which 422, what an error body may contain, the static site server and the console email backend. |
| Incidents (existing) | `tests/test_incidents_api.py`, `tests/test_seed_incidents.py` | The incident manager and its CSV seed. Not part of AUTH-088. |
| Frontend auth logic (website 170, backoffice 175 tests) | `tests/frontend/`, run by `uis/*/jest.config.ts` | The auth utility functions in both Next.js apps: form validation, token storage, the post-login redirect guard, the auth service calls and error mapping. |

### Results at the time of writing

| Command | Result |
| --- | --- |
| `uv run pytest` | 331 passed, 7 skipped. The skips are the static-site server tests, which need Flask, and Flask is not in the uv environment. |
| `uv run pytest --cov` | 88.9% of the authentication API: `services/auth` 99–100%, `services/users` and `services/profiles` 96–100% per file. The one uncovered file is `services/users/seed.py`, the command-line tool that bootstraps an admin, which is not an endpoint. |
| `npm --prefix uis/website test` | 170 passed. 98.6% of lines and 100% of functions in the auth modules. |
| `npm --prefix uis/backoffice test` | 175 passed. 98.2% of lines and 100% of functions in the auth modules. |

To check that the suite actually catches regressions, seven were introduced one
at a time and each was caught:
- tokens issued without `exp`;
- the session lifetime read in hours instead of minutes;
- deactivated accounts let through the gate;
- `Depends(get_current_user)` removed from `GET /users`;
- unknown emails skipping the dummy bcrypt check;
- reset links made reusable;
- `sanitizeNextPath` returning its input unchanged.

Token generation, token validation and password hashing all happen on the
server, in `services/core/security.py`, so they are tested by pytest. The
TypeScript code never creates or verifies a token. It stores one, attaches it to
requests, and reacts when the API refuses it, and that is what Jest tests.

## How the tests are written

- **Business logic only.** Each test calls the service or endpoint function
  directly and asserts what it *decided*: who gets in, what is stored, what is
  refused, and whether anything changed. No test sends an HTTP request or
  asserts a JSON shape, a header or anything else about the framework.
- **Invalid input is asserted where the rule lives.** The password policy and
  the email format are rules on the request models, so an invalid payload is
  asserted as the model refusing it (`ValidationError`). Turning that refusal
  into a 422 is framework behaviour. It is pinned once, in
  `tests/test_error_contract.py`, because the UIs depend on it.
- **Refusals are asserted as decisions.** An `HTTPException` carries the
  decision in its status code: 401 means "we don't know who you are", 403 means
  "we know you, and you may not do this", 400 means "the request is wrong". The
  UIs act differently on each one, so the tests pin them.

### Fixtures and what they guarantee

| Fixture | Where | Guarantee |
| --- | --- | --- |
| `pinned_settings` (autouse) | `tests/conftest.py` | Every test reads fixed settings: test secret, HS256, 60-minute sessions, 30-minute reset links, `RESEND_API_KEY` empty, local frontend URL. No `.env` is needed, and a developer's real Resend key is never used. |
| `db_path` | `tests/conftest.py` | Each test gets its own TinyDB file. The cached handle is cleared too, so nothing leaks between tests or into `data/suppliers.json`. |
| `isolated_db`, `fast_bcrypt` (autouse) | `tests/auth/conftest.py` | Every auth test gets that isolated database. bcrypt runs at cost 4 instead of 12, so the suite takes seconds. The test marked `real_bcrypt_cost` opts out to pin the production cost. |
| `make_user`, `access_token_for`, `craft_token`, `issue_reset_token` | `tests/auth/conftest.py` | Build users, real session tokens, hand-crafted (forged or malformed) tokens and real reset links. |
| `time_machine` | `time-machine` package | Moves the clock that python-jose and the services read, so expiry is tested for real instead of by sleeping. |
| `setup-env.js` | `tests/frontend/` | Points both apps at the fake origin `http://api.test`, so no Jest test can reach a real API. |
| `installBrowser`, `scriptFetch` | `tests/frontend/shared/support/` | A minimal `window` whose storage can be blocked, the way private mode does, and a scripted `fetch` that records every request. Without a browser installed, tests see a server render. |

## The test plan

**Legend.** **Happy** is valid input with the expected outcome. **Edge** covers
boundaries, empty fields, duplicates and malformed tokens. **Failure** is when
the system or its data is in a bad or changed state (a user deleted
mid-session, a corrupt row, an email provider that is down) and the endpoint
must still fail safely. ★ marks the three cases that satisfy the ticket's
minimum for that endpoint.

**Order.** Cases are listed in priority order:
1. Security and the token lifecycle, which is the class of the original incident.
2. Contracts the UIs depend on.
3. Edge-case user experience.
4. Odd behaviour that is pinned deliberately, so a change to it is a decision
   rather than an accident.

A case tagged **BUG-n** exposed a defect in the existing code. Each one is
described under [Bugs found and fixed](#bugs-found-and-fixed).

### Access tokens and `POST /auth/token`: `tests/auth/test_token.py`

This comes first because token expiry is the incident that started AUTH-088.

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 ★ | Edge | A real access token is refused once the clock passes `ACCESS_TOKEN_EXPIRE_MINUTES`. | This is the regression that started AUTH-088. |
| 2 ★ | Happy | `/auth/token` with the email as `username` issues a session for that user: `sub` is the user id, `typ` is `access`, and the lifetime matches the setting. | It is the sign-in the `/docs` Authorize button uses. |
| 3 | Edge | An issued token lives exactly the configured lifetime: accepted 1 s before `exp`, refused 1 s after. | Catches off-by-one and seconds-vs-minutes mistakes. |
| 4 | Edge | **BUG-1**: a correctly signed token with no `exp` is refused. | If a refactor stops setting `exp`, tokens must fail loudly instead of becoming permanent. |
| 5 | Edge | Forged tokens are refused: a different secret, a tampered payload (`role: admin`, a swapped `sub`), `alg: none`, a stripped signature, or HS512 signed with the right secret. | Anyone can edit a JWT's payload. Only the signature and the pinned algorithm stop them. |
| 6 | Edge | A reset-link token is not accepted as a session, and an unknown `typ` is refused. A legacy token without `typ` is still accepted. | A reset link must never open a session. Legacy compatibility is a documented promise. |
| 7 | Edge | Garbage tokens are refused: `""`, `"abc"`, `"a.b.c"`, bad base64, a token padded with spaces. | Malformed input must be a clean refusal, not a crash. |
| 8 ★ | Failure | `/auth/token` against a corrupt stored password hash refuses sign-in and does not crash. | Data corruption must not become a 500 or a way in. |
| 9 | Edge | `username` is free text, not validated as an email. Case and space variants still sign in, and `"admin"` or `""` are refused as wrong credentials. | The OAuth2 form field is not validated as an email. |
| 10 | Edge | Wrong password, unknown user and deactivated account each give exactly the `/auth/login` outcome. | Two ways into the same account need the same checks. |
| 11 | Edge | A token's claims are exactly `sub`, `role`, `typ`, `iat` and `exp`, with no email and no hash. | Anyone holding a JWT can read it. |
| 12 | Edge | Startup is refused when `ACCESS_TOKEN_EXPIRE_MINUTES` is 0 or the secret is shorter than 32 characters. | A zero lifetime or a weak secret must stop the service instead of shipping. |

### Protected-route gate, `get_current_user`: `tests/auth/test_current_user.py`

Every protected endpoint goes through this one function, so the cases for a
session in a bad state are tested here once instead of on each route.

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 ★ | Happy | A valid token for an active user resolves to that user, read fresh from the database. | Every protected endpoint starts here. |
| 2 ★ | Failure | User deleted after the token was issued → 401. | A token must not outlive its account. |
| 3 | Failure | User deactivated after the token was issued → 403. | Deactivation takes effect on the next request, not at token expiry. |
| 4 ★ | Edge | `sub` missing, empty or not a string → 401. | A signed token that names no one must not resolve to anyone. |
| 5 | Edge | A stale role claim does not help: an admin demoted after sign-in cannot act as admin. | Permissions come from the database, not from the token. |
| 6 | Edge | Every route except the five public ones (`POST /users`, `/auth/login`, `/auth/token`, `/auth/forgot-password`, `/auth/reset-password`) requires a session. The incident routes are deliberately open. | A deleted `Depends(get_current_user)` silently opens a route. This catches it. |

### `POST /auth/login`: `tests/auth/test_login.py`

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 ★ | Happy | The right email and password give a session token for that user, with the configured lifetime. | This is the front door. |
| 2 ★ | Edge | A wrong password and an unknown email give the identical refusal: 401 with the same message. | Anything different tells an attacker which emails are registered. |
| 3 | Edge | An unknown email still costs one bcrypt verification. | Otherwise response time reveals which emails are registered. |
| 4 ★ | Failure | Deactivated account: with the right password → 403, with a wrong password → 401. | Deactivation is revealed only to someone who knows the password. |
| 5 | Edge | `"  USER@Example.COM "` signs in. | Emails are stored lower-case and trimmed. |
| 6 | Edge | An empty password is refused by the model. A whitespace-only password is just a wrong password (401). | Covers the empty field without crashing. |
| 7 | Edge | Passwords over 72 bytes, over 4 KB, or containing NUL → 401, never 500, and no "stored hash could not be read" warning (**F-6**). | Anonymous input must not be able to raise data-corruption alarms. |
| 8 | Failure | A corrupt stored hash → 401, with a warning logged. | Real corruption must show up in the log, not as a 500. |
| 9 | Edge | A malformed email is refused before any database lookup. | Invalid input never reaches storage. |

### `POST /users` (registration): `tests/auth/test_register.py`

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 ★ | Happy | A valid payload creates a user with role `user`, active, a UUID id and a UTC `created_at`. The stored hash is bcrypt, never the plain text, and the linked profile holds the name, phone and address. | This is the account every other test builds on. |
| 2 | Edge | Privilege escalation is ignored: `role: "admin"`, `is_active: false`, `id`, `hashed_password` or `created_at` in the body. | Nobody may grant themselves admin at sign-up. |
| 3 ★ | Edge | A duplicate email → 409, and the address is not echoed back. The same holds for a different case, surrounding spaces, or `"Name <a@x.com>"`. `a+tag@x.com` is a separate account. | Duplicate users break sign-in. Normalisation must close every variant. |
| 4 | Edge | Empty or missing email or password is refused. Password length: 7 refused, 8 accepted, 72 ASCII accepted, 73 refused. | Pins the empty fields and both password boundaries. |
| 5 | Edge | **BUG-4**: a password of 72 characters or fewer but over 72 bytes (e.g. one containing `ñ`) is refused by validation. | bcrypt 5 raises past 72 bytes. Before the fix this was a 500, and users in Mexico and Spain type accented passwords. |
| 6 | Edge | **BUG-5**: a password containing NUL is refused by validation. | bcrypt refuses NUL. Before the fix this was a 500. |
| 7 ★ | Failure | If the profile insert fails, the user row is rolled back, the error propagates, and the same email can register again afterwards. | A half-created account would block that email for good. |
| 8 | Failure | A password bcrypt cannot hash leaves no user row behind. | No partial writes. |
| 9 | Edge | Profile field limits: name 120 accepted / 121 refused, phone 40/41, address 255/256. Omitted fields are stored as `null`. | Pins the column sizes the UI mirrors. |
| 10 | Edge | An all-spaces password of 8 or more characters is accepted. *Pinned* (see [open questions](#open-questions-pinned-not-changed)). | Documents a password-policy gap on purpose. |
| 11 | Edge | A Unicode local part or an IDN domain is accepted and lower-cased. | Spanish names appear in email addresses. |

### `GET /auth/me`: `tests/auth/test_me.py`

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 ★ | Happy | Returns the caller's id, email, role and active flag, plus the linked profile. | Both apps build their session from this. |
| 2 ★ | Edge | An account with no profile row gets `profile: null`, not an error. | The API always creates one, but a row edited or restored by hand may lack it, and the UI must not break. |
| 3 ★ | Failure | Account deactivated after the token was issued → 403. A reset token used as a session → 401. | A session must reflect the account's current state. |
| 4 | Edge | The password hash is never part of what is returned. | Keeps credentials out of reach. |

### `POST /auth/forgot-password`: `tests/auth/test_forgot_password.py`

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 ★ | Edge | Known, unknown and deactivated addresses get the identical answer. The email is queued only for a known active account, and no reset row is created otherwise. | Anything else lets anyone test which emails are registered. |
| 2 ★ | Happy | A known user gets a reset row (`jti`, `user_id`, expiry, `used_at: null`) and a queued email. The token is typed `password_reset`, names the user, carries the row's `jti` and lasts 30 minutes. | This is the start of recovery. |
| 3 | Edge | The reset row never stores the token itself. With a provider key set, the log contains neither the token nor the link. | A leaked database or log must not hand out working links. |
| 4 | Edge | Case and space variants of the email find the account. | Same normalisation as sign-in. |
| 5 ★ | Failure | When the provider fails (rejects, answers without an id, or can't be reached), the send returns `False`, logs, and never raises. The answer to the caller is unchanged. | The email is sent after the answer, so a failure there must not leak or crash. |
| 6 | Edge | Issuing a link purges expired rows and keeps live ones. | Stops the table growing without deleting working links. |
| 7 | Edge | Several requests leave several live links. *Pinned*: there is no rate limit. | Documents a known gap. |
| 8 | Edge | A malformed email is refused by validation. | Invalid input never reaches storage. |
| 9 | Edge | The reset URL drops a trailing slash from `FRONTEND_BASE_URL` and URL-encodes the token. The email states the expiry ("30 minutes" or "1 hour") and that the link works once. | The link must work and tell the truth. |
| 10 | Edge | Startup is refused when `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` is outside 15–60: 14 and 61 refused, 15 and 60 accepted. | AUTH-03 fixes that window. |

### `POST /auth/reset-password`: `tests/auth/test_reset_password.py`

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 ★ | Happy | A valid link and a valid password: the new password works, the old one stops working, and the link is marked used. | This is the end of recovery. |
| 2 ★ | Edge | Using the same link again → 400 with the generic message, and the password is unchanged. | Reset links are single use. |
| 3 | Edge | A JWT past its expiry → 400. A JWT that is still valid but whose reset row has expired → 400. | Expiry is checked in two places, and both are tested. |
| 4 | Edge | Every malformed link gets the same 400 text: garbage, whitespace, the wrong secret, a tampered token, a session token, a legacy token without `typ`, a missing `jti` or `sub`, a purged `jti`, or a `jti` belonging to another user's row. | The answer must not reveal whether a token was ever real. |
| 5 | Edge | With two links outstanding, using one kills the other. | Once the password is set, every other link must stop working. |
| 6 ★ | Failure | Account deleted or deactivated after the link was sent → 400, and nothing is written. | A link must not revive a closed account. |
| 7 | Edge | An invalid new password (empty, 7 characters, 73 characters) is refused by validation, **and the link stays usable**. | The UI reads a 400 as "link expired". A weak password must not burn the link. |
| 8 | Edge | **BUG-4/BUG-5** passwords are refused by validation and the link stays usable. | Before the fix these were a 500. |
| 9 | Edge | The failure reason is logged, and the answer is the same whatever the reason. | Support can debug from the log without the API giving hints to attackers. |

### `POST /auth/change-password`: `tests/auth/test_change_password.py`

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 ★ | Happy | The right current password and a valid new one: the new password works and the old one stops. The current session keeps working. | Keeping the session after a change is documented behaviour. |
| 2 ★ | Edge | A wrong current password → 400, not 401, and nothing changes. | A 401 would sign the UI out. The session is fine; only the payload is wrong. |
| 3 | Edge | Outstanding reset links are spent by the change. | A pending link would otherwise undo the change. |
| 4 | Edge | Empty current password or a new password of 7 or 73 characters is refused. **BUG-4/BUG-5** passwords are refused. | Same password rule as every other route. |
| 5 | Edge | A current password shorter than today's rule is still accepted as `current_password`. | Older accounts must be able to move to a compliant password. |
| 6 ★ | Failure | Account deleted between the session check and the update → refused (400). | A race with a deletion must not write anything. |
| 7 | Edge | A new password equal to the current one is accepted. *Pinned* (see open questions). | Documents a policy gap on purpose. |

### `GET /users`: `tests/auth/test_list_users.py`

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 ★ | Happy | Lists every user, with no password hash anywhere. | This is the admin view of all accounts. |
| 2 ★ | Edge | Deactivated users are listed with `is_active: false`. An empty table gives `[]`. | Covers the boundary states of the list. |
| 3 ★ | Failure | **F-7**: one unreadable row is skipped and logged, not a 500. | One damaged record must not take the whole list down. |
| 4 | Edge | A plain `user` can list everyone. *Pinned* (see open questions). | Documents today's access policy. |

### `GET /users/{user_id}`: `tests/auth/test_get_user.py`

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 ★ | Happy | An existing id returns that user, without the hash. | |
| 2 ★ | Edge | An unknown or oddly shaped id (`../x`, 1,000 characters, Unicode) → 404, no crash. | Path input is attacker-controlled. |
| 3 ★ | Failure | A user deleted after it was listed → 404. | The record can disappear between two calls. |
| 4 | Edge | A plain user can read any user. *Pinned* (see open questions). | Documents today's access policy. |

### `PUT /users/{user_id}`: `tests/auth/test_update_user.py`

Policy (the fix for **BUG-3**):
- Changing the `email` or `password` of **your own** account, admins included, requires a correct `current_password`.
- An admin editing someone else needs none.
- Any password set through this route spends that user's outstanding reset links.

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 ★ | Edge | A non-admin targeting another user's id → 403. A non-existent id is also 403. | Nobody edits someone else, and ids cannot be probed. |
| 2 | Edge | **BUG-3**: own account, `email` or `password` sent without `current_password` or with a wrong one → 400, and nothing is written. | Without this, a borrowed session could take the account over, bypassing `/auth/change-password`. |
| 3 | Edge | **BUG-3**: the same applies to an admin editing their own account. | Admin sessions are the most valuable to steal. |
| 4 | Happy | Own account with the right `current_password`: the change is applied, and after an email change only the new address signs in. `current_password` is never stored. | The legitimate path still works. |
| 5 | Edge | **BUG-3**: a password set here (own account, or an admin on someone else's) spends that user's outstanding reset links. | Same rule as the two `/auth` password routes. |
| 5a | Edge | **BUG-6**: moving an account to a new email, or deactivating it, spends its outstanding reset links. They also stay dead if the account is reactivated. | A link in a mailbox the account no longer trusts must not be able to take it over. |
| 5b | Edge | An edit that changes none of those (the same address re-saved, a role change) leaves the links alone. | A link the owner is about to use must not die for no reason. |
| 6 | Edge | A non-admin sending `role` or `is_active`, even on their own account and even as `null`, → 403 naming those fields, and nothing is written. | No self-promotion or self-reactivation. |
| 7 ★ | Happy | An admin changes another user's email, role or `is_active` with no password prompt. A deactivated user is then refused at the gate. | This is the admin workflow. |
| 8 | Edge | An email already in use → 409. Your own email in a different case → accepted, not a conflict. | Duplicate users, from the other direction. |
| 9 ★ | Failure | An email already in use plus a new password in one request → 409, and the password is unchanged. | No partial writes. |
| 10 | Edge | `{}` or explicit `null`s → nothing changes, and no password is needed. | An empty edit must be harmless. |
| 11 | Edge | `role: "superadmin"`, a malformed email or a 7-character password → refused by validation. | Only the three real roles exist. |
| 12 | Failure | An admin targeting an unknown id → 404. A target deleted mid-request → 404. | |
| 13 | Edge | An admin can demote or deactivate themselves or the last admin. *Pinned* (see open questions). | Documents a lockout risk on purpose. |

### `DELETE /users/{user_id}`: `tests/auth/test_delete_user.py`

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 ★ | Happy | Deleting your own account removes the user and their profile. | Account closure. |
| 2 ★ | Edge | A non-admin targeting another id, existing or not, → 403, and nothing is removed. | Nobody deletes someone else. |
| 3 ★ | Failure | After deletion, the old token → 401 and an outstanding reset link → 400. Re-registering the same email creates a new id that the old token cannot reach. | A deleted account must stay dead. |
| 4 | Happy | An admin deletes another user. An admin targeting an unknown id → 404. | The admin path. |
| 5 | Edge | Deleting twice → 401 for your own account, 404 for an admin. | The action is idempotent from the caller's side. |
| 6 | Edge | An admin can delete themselves or the last admin, and anyone can delete their own account with the session alone. *Pinned* (see open questions). | Documents the gaps on purpose. |

### `GET /profiles/me`: `tests/auth/test_read_profile.py`

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 ★ | Happy | Returns the caller's profile, linked to the caller. | |
| 2 ★ | Edge | With two users, each sees only their own profile. | Scoped by construction, with no id to tamper with. |
| 3 ★ | Failure | No linked profile → 404 "No profile is linked to this account". | A row edited or restored by hand may lack it. |

### `PUT /profiles/me`: `tests/auth/test_update_profile.py`

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 ★ | Edge | A body carrying another user's `user_id` or `id` is ignored, and only the caller's profile changes. | Blocks editing someone else's profile by changing an id in the body. |
| 2 ★ | Happy | Partial update: the fields sent change and the omitted ones stay. | The UI sends only what it edits. |
| 3 | Edge | An explicit `null` clears a field. `{}` changes nothing. | This is how the UI erases a field. |
| 4 | Edge | name 121, phone 41 or address 256 characters → refused. | Same limits as registration. |
| 5 | Edge | `""` and whitespace are stored as sent. *Pinned* (see open questions). | The UI sends `null` instead. The API does not normalise. |
| 6 ★ | Failure | No linked profile → 404, including when it disappears mid-request. | |

### Password hashing: `tests/auth/test_password_hashing.py`

| # | Kind | Case → expected outcome | Why |
| --- | --- | --- | --- |
| 1 | Happy | Hashes are bcrypt at cost 12 with a fresh salt each time. Verify accepts the right password and rejects a wrong one. | Pins the production cost. |
| 2 | Edge | `DUMMY_HASH` is a valid cost-12 hash, so checking a password against it costs a full bcrypt run. | If it stopped being valid, the timing protection for unknown emails would vanish without any error. |
| 3 | Edge | Two passwords that differ only after byte 72 are not interchangeable. | Catches a bcrypt downgrade that would bring back silent truncation. |
| 4 | Failure | A corrupt stored hash makes the check return `False` and log. It never raises. | |

### Frontend auth logic (Jest): `tests/frontend/`

Every function gets at least one happy-path test and one failure-mode test.
The `shared/` files run against both apps.

| Module | Functions | Happy path | Failure mode |
| --- | --- | --- | --- |
| `lib/auth-storage.ts` | `sanitizeNextPath`, `buildLoginUrl` | Same-site paths, including query and hash, survive. | **BUG-2**: `/\evil.com`, `/<tab>/evil.com`, `/<newline>/evil.com`, `//evil.com`, absolute URLs and login paths are refused. |
| | `readToken`, `storeToken`, `clearToken`, `handleUnauthorized` | Store, read back and clear. A 401 clears the token and fires `trackflow:unauthorized`. | Blocked `localStorage` falls back to memory. A blank token counts as none. With no `window` (server render), they return nothing and do nothing. |
| | `readResetToken`, `hasResetSuccessFlag`, `hasRegisteredFlag`, `buildLoginAfterRegisterUrl` | Read their flag or token from the query string. | Missing or blank values and other flag values give `null` or `false`. A crafted `next` stays encoded. |
| `lib/auth.ts` | The six form validators | Valid forms give `null`. | Empty fields, a whitespace-only email, 7 vs 8 characters, a mismatched confirmation, a missing current password, over-long profile fields. |
| | `normalizeAuthenticatedUser`, `normalizeProfile`, `extractAccessToken` | Well-formed payloads map through. | A non-object throws. An unknown role becomes `user`. A missing or blank token throws and nothing is stored. |
| | `toFieldErrors`, `buildRegistrationPayload`, `buildProfilePayload`, form factories | A 422 maps onto the right inputs (`new_password` → `newPassword`). Payloads are trimmed and lower-cased. | Unknown fields and network or server failures land on the form-level message. Blank optional fields are left out or sent as `null`. |
| `services/auth-service.ts` | `login`, `register`, `fetchCurrentUser`, `updateMyProfile`, `requestPasswordReset`, `resetPassword`, `changePassword`, `logout` | Right request, token stored or cleared as documented. | `resetPassword`: 400 becomes `InvalidResetTokenError`, and a 422 keeps the form. `changePassword`: 400 becomes `IncorrectCurrentPasswordError`, and a 401 signs out. `register`: a 409 is not reported as "created but not signed in". |
| `lib/api-client.ts` (backoffice), `lib/auth-api-client.ts` (website) | Request helpers and error extractors | The bearer token goes only on authenticated calls, and each error shape is read. | No token means no request is sent. A 401 clears the session, and a network failure becomes a `NetworkError`. Broken JSON is reported as unreadable, and junk bodies get a readable fallback. |
| `lib/friendly-error.ts` | `describeError` | API messages for 4xx statuses reach the form. | A 5xx never shows server text. Tracebacks, HTML and runaway strings are replaced. A 401 shows the session message. |
| `lib/api-client.ts` (website, records API) | `requestApi` | Reaches the records API without the TrackFlow session token. | A 401 from that third-party host does not end the TrackFlow session. |

`hooks/useLocationSearch.ts` is a React hook, not a utility function, so it is
out of scope for these unit tests.

## Bugs found and fixed

These were found by reviewing the endpoint logic for inputs that behave
unexpectedly (see [AI-assisted workflow](#ai-assisted-workflow)). Each one has a
test that failed before its fix and passes after.

| ID | Bug | Test that pins it | Fix |
| --- | --- | --- | --- |
| BUG-1 | A correctly signed JWT with no `exp` was accepted forever. If a refactor dropped `exp` from the encoder, every session would silently become permanent, which is the incident class behind this ticket. | `test_token.py` | `_decode` passes `options={"require_exp": True}` to python-jose. |
| BUG-2 | Open redirect after login: `/login?next=/%5Cevil.com`, `?next=/%09/evil.com` and `?next=%2F%0A%2Fevil.com` passed `sanitizeNextPath`, and browsers resolve them to `evil.com`. Both apps were affected. | `tests/frontend/shared/auth-storage.test.ts` | `sanitizeNextPath` refuses `\` and control characters, then resolves the value the way the router will and keeps it only if it stays on the same origin. |
| BUG-3 | `PUT /users/{own id}` changed the password or email with nothing but the session. That bypassed `/auth/change-password`, which asks for the current password, so a borrowed session could take the account over. A password set this way also left outstanding reset links alive. | `test_update_user.py` | Own-account email or password changes require `current_password`, and any password set through this route spends that user's reset links. |
| BUG-4 | A password of 72 characters or fewer but over 72 bytes (e.g. 72 characters including one `ñ`) passed validation, then bcrypt 5 raised and the API answered 500 on register, reset, change and `PUT /users`. The docs promised a 72-byte cap. | `test_register.py`, `test_reset_password.py`, `test_change_password.py`, `test_update_user.py` | The shared `Password` type checks the UTF-8 byte length. |
| BUG-5 | A password containing NUL passed validation, then passlib raised and the API answered 500 on the same four routes. | same | The shared `Password` type refuses NUL. |
| F-6 | Signing in with a password over 72 bytes or containing NUL logged "A stored password hash could not be read", so anyone could raise fake data-corruption warnings. | `test_login.py` | `verify_password` refuses such a password without calling bcrypt. No stored hash can match it, and the check costs the same whether or not the account exists. |
| F-7 | One unreadable row in `users` turned `GET /users` into a 500. | `test_list_users.py` | `list_users` skips and logs rows that no longer parse, the same way the incident list already does. |
| BUG-6 | Outstanding reset links survived an email change and a deactivation. If an admin moved a compromised account to a new address, a link already in the old mailbox could still reset the password. If an admin reactivated an account within 30 minutes, its older links worked again. Found in the second AI review pass, after the plan was written. | `test_update_user.py` | `PUT /users/{id}` compares the account before and after the edit, and spends the links when the password is set, the address actually changes, or the account is deactivated. |

## Open questions (pinned, not changed)

These behaviours are surprising but are policy decisions, not bugs. Each one has
a test that pins today's behaviour, so changing it becomes a deliberate
decision.

- An all-spaces password of 8 or more characters is accepted. There is no
  complexity rule.
- A new password equal to the current one is accepted.
- Any signed-in user can list every user and read any user record. Whether
  these should be admin-only is still open.
- An admin can demote, deactivate or delete themselves or the last admin, which
  can lock everyone out.
- Deleting your own account needs only the session: it has the same
  borrowed-session risk as BUG-3, but no decision has been made.
- `PUT /profiles/me` stores `""` and whitespace as sent. The UI sends `null`.
- `"Mallory <victim@x.com>"` is accepted as an email and stored as
  `victim@x.com`. This is harmless, because the duplicate check sees the same
  address, but it is surprising.
- `/LOGIN` survives the redirect guard. Next.js routes are case-sensitive, so it
  leads to the 404 page, not the login page, and cannot cause a loop.
- A user row damaged by hand still breaks lookups for that one account: signing
  in, a reset request or a token naming it answers with the generic 500. The
  lists skip such a row (F-7). Treating it as "no account" instead would let
  someone register a duplicate over it, so that choice is left open.

## Known gaps unit tests cannot cover

- **Rate limiting.** Nothing limits login, forgot-password or change-password
  attempts. A stolen session could brute-force the current password.
- **Token revocation.** A password change does not end other sessions. Tokens
  stay valid until they expire.
- **Concurrency.** TinyDB has no unique index and no locking, so two
  simultaneous sign-ups with one email, or one reset link submitted twice at
  once, can both succeed.
- **Type checking the frontend tests.** The Jest files live outside both apps'
  `tsconfig.json`, so `npm run typecheck` does not see them. SWC strips their
  types when running them. They were type-checked once against each app's
  modules when written, but nothing enforces it since.

## AI-assisted workflow

- **Finding missed cases.** Before any test was written, the endpoint logic in
  `services/auth`, `services/users`, `services/profiles` and
  `services/core/security.py`, plus the TypeScript auth modules, was given to an
  AI coding agent with the brief to find inputs that behave unexpectedly. Its
  suggestions were reproduced before being accepted:
  - the bcrypt 5 byte limit and NUL handling were probed against the installed
    libraries;
  - the redirect bypass was checked with the WHATWG URL parser that browsers
    use;
  - the `exp`-less token was decoded with the real decoder.

  That review produced BUG-1 to BUG-5, F-6, F-7 and the open questions above.
  A second pass over the finished endpoint logic, asking what the plan had
  missed, found BUG-6: reset links outliving an email change or a deactivation.
- **Generating boilerplate.** The AI drafted the fixtures and the test modules
  from this plan. Every test must be read and understood before it is
  committed. Each module opens with a docstring saying what it protects, and
  each test's name states the decision it pins.
- **Fixing what the tests reveal.** When a test exposed a bug, the bug was fixed
  in the application code and recorded in
  [Bugs found and fixed](#bugs-found-and-fixed). No test was weakened to make it
  pass.
- **Proving the tests work.** Generated tests can pass without checking
  anything, so seven regressions were introduced on purpose and each one was
  caught (see [Results](#results-at-the-time-of-writing)).
