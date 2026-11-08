/**
 * Token storage and the redirect contract around it.
 *
 * The API is stateless JWT with no auth cookie, so the token can only live in
 * `localStorage` and every read has to tolerate a browser that denies access to
 * it (private mode, blocked site data) as well as running on the server.
 */

/** Shared with `uis/website`: same product, same API, same token. */
export const TOKEN_STORAGE_KEY = "trackflow.access_token";

export const LOGIN_PATH = "/login";

/** The view a successful login lands on when no `next` path was requested. */
export const DEFAULT_AUTHENTICATED_PATH = "/";

/**
 * Fired when a protected call is rejected. `AuthProvider` listens for it and
 * routes to the login page, so no call site has to handle expiry itself.
 */
export const UNAUTHORIZED_EVENT = "trackflow:unauthorized";

export const FORGOT_PASSWORD_PATH = "/forgot-password";

/**
 * How `/reset-password` tells `/login` that it just succeeded. A query flag
 * rather than stored state, so the message belongs to the redirect that set it
 * and cannot resurface on a later visit.
 */
export const RESET_DONE_PARAM = "reset";
export const RESET_DONE_VALUE = "success";
export const LOGIN_AFTER_RESET_PATH = `${LOGIN_PATH}?${RESET_DONE_PARAM}=${RESET_DONE_VALUE}`;

/** Reads that flag out of a `window.location.search` string. */
export function hasResetSuccessFlag(search: string): boolean {
  return new URLSearchParams(search).get(RESET_DONE_PARAM) === RESET_DONE_VALUE;
}

/**
 * How `/register` tells `/login` that the account exists even though the
 * automatic sign-in after it failed, so the user is not left wondering whether
 * to register again.
 */
export const REGISTERED_PARAM = "registered";
export const REGISTERED_VALUE = "1";

export function buildLoginAfterRegisterUrl(nextPath: string): string {
  return `${LOGIN_PATH}?${REGISTERED_PARAM}=${REGISTERED_VALUE}&next=${encodeURIComponent(nextPath)}`;
}

/** Reads that flag out of a `window.location.search` string. */
export function hasRegisteredFlag(search: string): boolean {
  return new URLSearchParams(search).get(REGISTERED_PARAM) === REGISTERED_VALUE;
}

/** Reads the reset token out of a `window.location.search` string. */
export function readResetToken(search: string): string | null {
  const token = new URLSearchParams(search).get("token");

  return token && token.trim() ? token : null;
}

/**
 * The token for this page load when `localStorage` refused it. Client-side
 * navigation keeps this module alive, so a browser that blocks storage still
 * gets a working session until the next full reload.
 */
let memoryToken: string | null = null;

export function readToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const token = window.localStorage.getItem(TOKEN_STORAGE_KEY);
    if (token && token.trim()) {
      return token;
    }
  } catch {
    // Storage is blocked; the in-memory copy below is all there is.
  }

  return memoryToken;
}

export function storeToken(token: string): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    // Held only when storage failed, so signing out in another tab (which
    // clears storage) still ends this tab's session too.
    memoryToken = null;
  } catch {
    // A browser that refuses storage still gets a working session for this
    // page load through `memoryToken`; a full reload sends it back to login.
    memoryToken = token;
  }
}

export function clearToken(): void {
  memoryToken = null;

  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // Nothing to do: the token was already unreachable.
  }
}

/** Clears the token and asks the session layer to redirect. */
export function handleUnauthorized(): void {
  clearToken();

  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
}

/** Any origin will do: all that matters is whether a path stays on it. */
const RESOLUTION_BASE = "http://trackflow.invalid";

/**
 * A backslash or a control character: what a browser rewrites while parsing a
 * URL. `\` is read as `/`, and tabs and newlines are dropped, so `/\evil.com`
 * and `/<tab>/evil.com` both become `//evil.com` - another site. No path in
 * this app contains any of them.
 */
function hasRewrittenCharacter(value: string): boolean {
  return [...value].some((character) => {
    const code = character.charCodeAt(0);
    return character === "\\" || code < 0x20 || code === 0x7f;
  });
}

/**
 * Only same-origin absolute paths survive, so a crafted `?next=` cannot bounce
 * a freshly authenticated user to another site.
 *
 * Checking the first characters is not enough: the browser, not this function,
 * decides where a string leads. So the value is resolved the way the router
 * will resolve it, and kept only if it stays on the same origin. The sign-in
 * page itself is refused however it is spelled, since landing there after
 * signing in would loop.
 */
export function sanitizeNextPath(value: string | null | undefined): string | null {
  if (!value || !value.startsWith("/") || hasRewrittenCharacter(value)) {
    return null;
  }

  let resolved: URL;
  try {
    resolved = new URL(value, RESOLUTION_BASE);
  } catch {
    return null;
  }

  if (resolved.origin !== RESOLUTION_BASE) {
    return null;
  }

  return resolved.pathname.replace(/\/+$/, "") === LOGIN_PATH ? null : value;
}

export function buildLoginUrl(nextPath?: string | null): string {
  const safeNextPath = sanitizeNextPath(nextPath);

  return safeNextPath ? `${LOGIN_PATH}?next=${encodeURIComponent(safeNextPath)}` : LOGIN_PATH;
}
