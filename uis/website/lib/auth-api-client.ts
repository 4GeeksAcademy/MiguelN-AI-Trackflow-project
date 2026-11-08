import { handleUnauthorized, readToken } from "@/lib/auth-storage";

/**
 * Client for the in-repo TrackFlow identity API (`services/`).
 *
 * Deliberately separate from `lib/api-client.ts`: that one talks to the
 * external candidate records API through `NEXT_PUBLIC_API_URL`, which is a
 * different host with a different contract. Sending our bearer token there
 * would hand a TrackFlow credential to a third party.
 */
const configuredAuthApiUrl = process.env.NEXT_PUBLIC_AUTH_API_URL?.trim().replace(/\/+$/, "") ?? "";

// The localhost default exists for development only. A production build with
// the variable missing must fail loudly as a configuration error, not quietly
// point every sign-in at a machine that is not there.
const authApiBaseUrl =
  configuredAuthApiUrl || (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000");

export function getAuthApiBaseUrl(): string {
  return authApiBaseUrl;
}

export function buildAuthApiUrl(path: string): string {
  if (!authApiBaseUrl) {
    throw new Error("NEXT_PUBLIC_AUTH_API_URL is not configured");
  }

  return `${authApiBaseUrl}${path.startsWith("/") ? path : `/${path}`}`;
}

type ValidationDetail = {
  loc?: unknown;
  msg?: unknown;
};

/** One FastAPI validation entry, reduced to what a form needs to render it. */
export type ApiFieldError = {
  field: string;
  message: string;
};

/**
 * Carries the decoded body alongside the flattened message, so a form can map
 * a 422 back onto its inputs.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(status: number, payload: unknown) {
    super(extractApiErrorMessage(payload, status));
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

/** A 401 from a protected route: the token is missing, expired or invalid. */
export class UnauthorizedError extends ApiError {
  constructor(payload: unknown = null) {
    super(401, payload);
    this.name = "UnauthorizedError";
  }
}

/**
 * The request never produced a response: offline, DNS, CORS, or the API is
 * down. `fetch` reports all of these as a bare `TypeError`, which is also what
 * a coding bug throws, so it is wrapped where the two can still be told apart.
 * Shared with `lib/api-client.ts`, whose records API fails the same way.
 */
export class NetworkError extends Error {
  constructor(cause: unknown) {
    super("The request did not reach the API", { cause });
    this.name = "NetworkError";
  }
}

/** A successful status whose body claimed to be JSON and was not. */
export class UnreadableResponseError extends Error {
  readonly status: number;

  constructor(status: number, cause: unknown) {
    super(`The API answered ${status} with a body that is not valid JSON`, { cause });
    this.name = "UnreadableResponseError";
    this.status = status;
  }
}

/** `fetch`, with a network failure turned into a `NetworkError`. */
export async function sendRequest(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (cause) {
    throw new NetworkError(cause);
  }
}

/** `response.json()` for a body already known to be JSON, with a broken one reported as such. */
export async function readJsonBody(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch (cause) {
    throw new UnreadableResponseError(response.status, cause);
  }
}

/**
 * FastAPI validation errors arrive as `{ loc: ["body", "name"], msg: "..." }`.
 * The first segment names the request part, so only the rest is useful as a field path.
 */
function formatValidationDetail(detail: ValidationDetail): string {
  const location = Array.isArray(detail.loc)
    ? detail.loc.filter((part): part is string | number => typeof part === "string" || typeof part === "number")
    : [];
  const fieldPath = location.slice(1).join(".");
  const message = typeof detail.msg === "string" && detail.msg.trim() ? detail.msg.trim() : "Invalid value";

  return fieldPath ? `${fieldPath}: ${message}` : message;
}

/** Handles both FastAPI error shapes: `{ detail: string }` (401, 409) and `{ detail: [...] }` (422). */
export function extractApiErrorMessage(payload: unknown, status: number): string {
  const detail =
    typeof payload === "object" && payload !== null ? (payload as { detail?: unknown }).detail : undefined;

  if (typeof detail === "string" && detail.trim()) {
    return detail.trim();
  }

  if (Array.isArray(detail)) {
    const messages = detail
      .filter((item): item is ValidationDetail => typeof item === "object" && item !== null)
      .map(formatValidationDetail);

    if (messages.length > 0) {
      return messages.join(" · ");
    }
  }

  return `Request failed with status ${status}`;
}

/** Splits a 422 body into per-field entries. Returns `[]` for any other shape. */
export function extractApiFieldErrors(payload: unknown): ApiFieldError[] {
  const detail =
    typeof payload === "object" && payload !== null ? (payload as { detail?: unknown }).detail : undefined;

  if (!Array.isArray(detail)) {
    return [];
  }

  return detail
    .filter((item): item is ValidationDetail => typeof item === "object" && item !== null)
    .map((item) => {
      const location = Array.isArray(item.loc)
        ? item.loc.filter((part): part is string => typeof part === "string")
        : [];
      const message = typeof item.msg === "string" && item.msg.trim() ? item.msg.trim() : "Invalid value";

      return { field: location.slice(1).join("."), message };
    });
}

export async function readErrorPayload(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function withJsonHeaders(init?: RequestInit, token?: string | null): HeadersInit {
  return {
    Accept: "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(init?.headers ?? {}),
  };
}

/** Unauthenticated call. Use it only for `POST /users` and `POST /auth/login`. */
export async function requestAuthApi(path: string, init?: RequestInit): Promise<Response> {
  const response = await sendRequest(buildAuthApiUrl(path), {
    ...init,
    headers: withJsonHeaders(init),
  });

  if (!response.ok) {
    throw new ApiError(response.status, await readErrorPayload(response));
  }

  return response;
}

/**
 * Call against a protected route. Reads the token, sends it as
 * `Authorization: Bearer <token>`, and on a 401 clears it and hands control to
 * the session layer before rethrowing so the caller stops.
 */
export async function requestAuthenticatedApi(path: string, init?: RequestInit): Promise<Response> {
  const token = readToken();

  if (!token) {
    handleUnauthorized();
    throw new UnauthorizedError({ detail: "Your session has expired. Please sign in again." });
  }

  const response = await sendRequest(buildAuthApiUrl(path), {
    ...init,
    headers: withJsonHeaders(init, token),
  });

  if (response.status === 401) {
    const payload = await readErrorPayload(response);
    handleUnauthorized();
    throw new UnauthorizedError(payload);
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readErrorPayload(response));
  }

  return response;
}

export async function parseAuthResponseJson(response: Response): Promise<unknown | null> {
  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    return null;
  }

  return readJsonBody(response);
}
