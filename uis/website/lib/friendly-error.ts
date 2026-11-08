/**
 * One place that decides what a user is allowed to read about a failure.
 *
 * The rule this file enforces: a message is shown only when it came back in a
 * shape the API documents **and** the status is one the API writes plain
 * language for. Everything else - a 5xx, an unrecognised body, an HTML error
 * page from a proxy, a network failure, a thrown non-Error - is answered with
 * text written here.
 *
 * Without that rule `ApiError`'s fallback ("Request failed with status 500")
 * and the browser's own network errors leak straight into the UI.
 *
 * Mirrors `uis/backoffice/lib/friendly-error.ts`, minus the incident routes
 * this app does not call.
 */

import {
  ApiError,
  NetworkError,
  UnauthorizedError,
  UnreadableResponseError,
  extractApiFieldErrors,
  type ApiFieldError,
} from "@/lib/auth-api-client";

export type FriendlyError = {
  /** Safe to render as-is. Never empty. */
  message: string;
  /** Keyed by the API's field name, so a form can look up its own inputs. */
  fieldErrors: Record<string, string>;
};

/** The wording for failures the API cannot describe itself, per family of screens. */
export type ErrorCopy = {
  offline: string;
  server: string;
  /** For a 404 whose body could not be read. The call site's fallback is used when absent. */
  notFound?: string;
  /**
   * Whether the API's own error text may be shown. True for the in-repo
   * identity API, whose messages are written for a reader; false for the
   * external records API, whose wording this project does not control.
   */
  trustApiText: boolean;
};

/** Sign-in, registration and account screens, served by the in-repo identity API. */
export const GENERAL_ERROR_COPY: ErrorCopy = {
  offline: "Could not reach TrackFlow. Check your connection and try again.",
  server:
    "TrackFlow is having trouble right now. Please try again in a moment, and contact TrackFlow Tech if it keeps happening.",
  trustApiText: true,
};

/** The hiring tracker, served by the external candidate records API. */
export const RECORDS_ERROR_COPY: ErrorCopy = {
  offline: "Could not reach the candidate records service. Check your connection and try again.",
  server:
    "The candidate records service is having trouble right now. Please try again in a moment, and contact TrackFlow Tech if it keeps happening.",
  notFound: "This candidate could not be found. It may have been removed.",
  trustApiText: false,
};

const SESSION_MESSAGE = "Your session has expired. Please sign in again.";

const UNEXPECTED_MESSAGE =
  "Something went wrong on our side. Please try again, and contact TrackFlow Tech if the problem continues.";

/**
 * Statuses whose body the identity API authors for a reader: 400/401/403/404/409
 * carry a sentence written in the routers, 422 carries per-field messages. A
 * 5xx is deliberately absent - its body is generic by design, and trusting it
 * would also trust whatever a proxy or gateway put there instead.
 */
const READABLE_STATUSES = new Set([400, 401, 403, 404, 409, 422]);

/** Longer than any message the API writes; a runaway string is not a sentence. */
const MAX_TRUSTED_MESSAGE_LENGTH = 300;

/** Shapes that betray a message was never meant for a reader. A last line of defence. */
const LEAKED_INTERNALS = [
  /traceback/i,
  /\bat [\w$.]+ \(/,
  /^\w*(Error|Exception):/,
  /\.(py|ts|tsx|js):\d+/,
  /<\/?[a-z]+[\s>]/i,
];

function isReadable(message: string): boolean {
  return (
    message.length > 0 &&
    message.length <= MAX_TRUSTED_MESSAGE_LENGTH &&
    !LEAKED_INTERNALS.some((pattern) => pattern.test(message))
  );
}

/**
 * The identity API's error bodies, flattened to `{ field, message }`:
 * FastAPI's `{ detail: [{ loc, msg }] }` for validation, `{ detail: "sentence" }`
 * for a refusal. A problem with no field is a form-level message.
 */
function readProblems(payload: unknown): ApiFieldError[] {
  const validation = extractApiFieldErrors(payload);
  if (validation.length > 0) {
    return validation;
  }

  const detail =
    typeof payload === "object" && payload !== null ? (payload as { detail?: unknown }).detail : undefined;

  return typeof detail === "string" && detail.trim() ? [{ field: "", message: detail.trim() }] : [];
}

/**
 * Reduce any thrown value to something showable.
 *
 * `fallback` is the form-level message when the API named nothing more useful.
 * `copy` supplies the wording for the failures the API cannot describe:
 * unreachable, broken, or gone.
 */
export function describeError(error: unknown, fallback: string, copy: ErrorCopy): FriendlyError {
  if (error instanceof UnauthorizedError) {
    return { message: SESSION_MESSAGE, fieldErrors: {} };
  }

  if (error instanceof NetworkError) {
    return { message: copy.offline, fieldErrors: {} };
  }

  if (error instanceof UnreadableResponseError) {
    console.error(error);
    return { message: copy.server, fieldErrors: {} };
  }

  if (!(error instanceof ApiError)) {
    // A bug, a missing environment variable, or a throw from outside the API
    // clients. Nothing in it is written for a reader, but whoever investigates
    // needs the original, so it goes to the console instead of the screen.
    console.error(error);
    return { message: UNEXPECTED_MESSAGE, fieldErrors: {} };
  }

  if (error.status >= 500) {
    return { message: copy.server, fieldErrors: {} };
  }

  const problems =
    copy.trustApiText && READABLE_STATUSES.has(error.status)
      ? readProblems(error.payload).filter((problem) => isReadable(problem.message))
      : [];

  if (problems.length === 0) {
    return {
      message: error.status === 404 ? (copy.notFound ?? fallback) : fallback,
      fieldErrors: {},
    };
  }

  const fieldErrors: Record<string, string> = {};
  for (const problem of problems) {
    // First message per field wins: re-reporting the same input twice tells
    // the user nothing.
    if (problem.field && !(problem.field in fieldErrors)) {
      fieldErrors[problem.field] = problem.message;
    }
  }

  const unattached = problems.find((problem) => !problem.field);

  return {
    message: unattached?.message ?? fallback,
    fieldErrors,
  };
}
