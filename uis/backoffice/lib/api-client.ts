import { handleUnauthorized, readToken } from "@/lib/auth-storage";
import { apiErrorClass, httpMethod, routeTemplate, statusClass } from "@/lib/telemetry-routes";
import { CLIENT_APP, getTelemetrySessionId, newTelemetryRequestId, track } from "@/services/telemetry-service";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL?.trim().replace(/\/+$/, "") ?? "";

export function getApiBaseUrl(): string {
  return apiBaseUrl;
}

export function buildApiUrl(path: string): string {
  if (!apiBaseUrl) {
    throw new Error("NEXT_PUBLIC_API_URL is not configured");
  }

  return `${apiBaseUrl}${path.startsWith("/") ? path : `/${path}`}`;
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
 * a 422 back onto its inputs. `message` keeps the shape every existing call
 * site already relies on.
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
 * a coding bug throws, so it is wrapped here where the two can still be told apart.
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

/** `{ detail: { field, message } }` - the shape every `/api/incidents` error uses. */
function readProblemDetail(detail: unknown): ApiFieldError | null {
  if (typeof detail !== "object" || detail === null || Array.isArray(detail)) {
    return null;
  }

  const record = detail as { field?: unknown; message?: unknown };
  if (typeof record.message !== "string" || !record.message.trim()) {
    return null;
  }

  return {
    field: typeof record.field === "string" ? record.field : "",
    message: record.message.trim(),
  };
}

/**
 * Handles every error shape this API surface produces: `{ detail: string }`
 * (FastAPI's 404), `{ detail: [...] }` (FastAPI's 422) and
 * `{ detail: { field, message } }` (the incident routes).
 */
export function extractApiErrorMessage(payload: unknown, status: number): string {
  const detail =
    typeof payload === "object" && payload !== null ? (payload as { detail?: unknown }).detail : undefined;

  if (typeof detail === "string" && detail.trim()) {
    return detail.trim();
  }

  const problem = readProblemDetail(detail);
  if (problem) {
    return problem.message;
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

/**
 * Per-field errors from an `/api/incidents` response, in the order the API
 * listed them.
 *
 * A validation failure carries `errors` with every bad field plus `detail`
 * holding the first; a business-rule refusal or a 404 carries `detail` alone.
 * Reading `errors` first and falling back to `detail` covers both without the
 * caller having to know which it got.
 *
 * Separate from `extractApiFieldErrors`, which reads FastAPI's raw 422 array:
 * the supplier and auth screens depend on that one, and the two payload shapes
 * have nothing in common beyond the word "detail".
 */
export function extractApiProblems(payload: unknown): ApiFieldError[] {
  if (typeof payload !== "object" || payload === null) {
    return [];
  }

  const record = payload as { detail?: unknown; errors?: unknown };

  if (Array.isArray(record.errors)) {
    const problems = record.errors
      .map(readProblemDetail)
      .filter((problem): problem is ApiFieldError => problem !== null);

    if (problems.length > 0) {
      return problems;
    }
  }

  const single = readProblemDetail(record.detail);
  return single ? [single] : [];
}

async function readErrorPayload(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function withJsonHeaders(init: RequestInit | undefined, token: string | null | undefined, requestId: string): HeadersInit {
  return {
    Accept: "application/json",
    "X-Client-App": CLIENT_APP,
    "X-Session-Id": getTelemetrySessionId(),
    "X-Request-Id": requestId,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(init?.headers ?? {}),
  };
}

/** Emits `api_latency_recorded` for every response and `api_error_recorded` for every 4xx/5xx. */
function recordApiCall(path: string, method: string | undefined, status: number, startedAt: number, requestId: string) {
  const route = routeTemplate(path);
  const verb = httpMethod(method);
  const responseClass = statusClass(status);

  if (!verb || !responseClass) {
    return;
  }

  track(
    "api_latency_recorded",
    { route_template: route, method: verb, status_class: responseClass, duration_ms: Math.max(0, performance.now() - startedAt), sample_rate: 1 },
    requestId,
  );

  const errorClass = apiErrorClass(status, route);
  if (errorClass) {
    track("api_error_recorded", { route_template: route, method: verb, status_code: status, error_class: errorClass, exception_type: null }, requestId);
  }
}

async function send(path: string, url: string, init: RequestInit, requestId: string): Promise<Response> {
  const startedAt = performance.now();

  try {
    const response = await fetch(url, init);
    recordApiCall(path, init.method, response.status, startedAt, requestId);
    return response;
  } catch (cause) {
    throw new NetworkError(cause);
  }
}

/** Unauthenticated call. Use it only for `POST /users` and `POST /auth/login`. */
export async function requestApi(path: string, init?: RequestInit): Promise<Response> {
  const requestId = newTelemetryRequestId();
  const response = await send(path, buildApiUrl(path), {
    ...init,
    headers: withJsonHeaders(init, null, requestId),
  }, requestId);

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

  const requestId = newTelemetryRequestId();
  const response = await send(path, buildApiUrl(path), {
    ...init,
    headers: withJsonHeaders(init, token, requestId),
  }, requestId);

  if (response.status === 401) {
    const payload = await readErrorPayload(response);
    track("session_expired", { trigger: "api_401", session_age_seconds: null, route_template: routeTemplate(path) }, requestId);
    handleUnauthorized();
    throw new UnauthorizedError(payload);
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readErrorPayload(response));
  }

  return response;
}

export async function parseResponseJson(response: Response): Promise<unknown | null> {
  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    return null;
  }

  try {
    return await response.json();
  } catch (cause) {
    throw new UnreadableResponseError(response.status, cause);
  }
}
