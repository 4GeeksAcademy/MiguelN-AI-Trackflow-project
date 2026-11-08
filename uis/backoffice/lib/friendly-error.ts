/**
 * One place that decides what a user is allowed to read about a failure.
 *
 * The rule this file enforces: a message is shown only when it came back in a
 * shape the API documents **and** the status is one the API writes plain
 * language for. Everything else - a 5xx, an unrecognised body, an HTML error
 * page from a proxy, a network failure, a thrown non-Error - is answered with
 * text written here.
 *
 * Without that rule the fallback in `ApiError` ("Request failed with status
 * 500") and the browser's own network errors leak straight into the UI, which
 * is exactly what a form must never show.
 */

import {
  ApiError,
  NetworkError,
  UnauthorizedError,
  UnreadableResponseError,
  extractApiFieldErrors,
  extractApiProblems,
  type ApiFieldError,
} from "@/lib/api-client";

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
};

export const INCIDENT_ERROR_COPY: ErrorCopy = {
  offline: "Could not reach the incident service. Check your connection and try again.",
  // Deliberately makes no promise about whether a write landed: the API's own
  // 500 cannot know which side of the write it failed on.
  server:
    "The incident service is having trouble right now. Please try again in a moment, and contact TrackFlow Tech if it keeps happening.",
  notFound: "That incident no longer exists. Refresh the list to see the latest.",
};

/** Suppliers, sign-in and account screens: everything that is not the incident manager. */
export const GENERAL_ERROR_COPY: ErrorCopy = {
  offline: "Could not reach TrackFlow. Check your connection and try again.",
  server:
    "TrackFlow is having trouble right now. Please try again in a moment, and contact TrackFlow Tech if it keeps happening.",
};

const SESSION_MESSAGE = "Your session has expired. Please sign in again.";

const UNEXPECTED_MESSAGE =
  "Something went wrong on our side. Please try again, and contact TrackFlow Tech if the problem continues.";

/**
 * Statuses whose body the API authors for a reader.
 *
 * 400 covers both halves of `services/core/http_errors.py` - a field that
 * failed to validate and a lifecycle transition that was refused - as well as
 * the auth routes' refusals. 401/403/404/409 carry a sentence written in the
 * routers, and 422 carries per-field messages. A 5xx is deliberately absent:
 * the body is generic by design, and trusting it would also trust whatever a
 * proxy or gateway put there instead.
 */
const READABLE_STATUSES = new Set([400, 401, 403, 404, 409, 422]);

/** Longer than any message the API writes; a runaway string is not a sentence. */
const MAX_TRUSTED_MESSAGE_LENGTH = 300;

/**
 * Shapes that betray a message was never meant for a reader.
 *
 * A last line of defence rather than a routine check: the API is not supposed
 * to be able to produce any of these, so if one appears the frontend's own text
 * is the right answer.
 */
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
 * Every error body this API produces, flattened to `{ field, message }`.
 *
 * The incident routes answer `{ detail: { field, message } }`, the other
 * routes' validation failures are FastAPI's `{ detail: [{ loc, msg }] }`, and
 * their refusals are `{ detail: "sentence" }`. A problem with no field is a
 * form-level message.
 */
function readProblems(payload: unknown): ApiFieldError[] {
  const problems = extractApiProblems(payload);
  if (problems.length > 0) {
    return problems;
  }

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
 * `fallback` is the form-level message for a validation failure, where the real
 * information is next to the inputs and a repeat of the first field error at
 * the top of the form would only be noise. `copy` supplies the wording for the
 * failures the API cannot describe: unreachable, broken, or gone.
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
    // client. Nothing in it is written for a reader, but whoever investigates
    // needs the original, so it goes to the console instead of the screen.
    console.error(error);
    return { message: UNEXPECTED_MESSAGE, fieldErrors: {} };
  }

  if (error.status >= 500) {
    return { message: copy.server, fieldErrors: {} };
  }

  if (!READABLE_STATUSES.has(error.status)) {
    return { message: fallback, fieldErrors: {} };
  }

  const problems = readProblems(error.payload).filter((problem) => isReadable(problem.message));

  if (problems.length === 0) {
    // A readable status whose body we could not read. The status is still
    // known, so the more specific message is safe to use for a 404.
    return {
      message: error.status === 404 ? (copy.notFound ?? fallback) : fallback,
      fieldErrors: {},
    };
  }

  const fieldErrors: Record<string, string> = {};
  for (const problem of problems) {
    // First message per field wins: the API lists them in the order it found
    // them, and re-reporting the same input twice tells the user nothing.
    if (problem.field && !(problem.field in fieldErrors)) {
      fieldErrors[problem.field] = problem.message;
    }
  }

  // A problem with no field name - a refused transition, a 404, a wrong
  // password - has nowhere to sit next to an input, so it becomes the
  // form-level message. When every problem did name a field, the fallback
  // introduces them.
  const unattached = problems.find((problem) => !problem.field);

  return {
    message: unattached?.message ?? fallback,
    fieldErrors,
  };
}
