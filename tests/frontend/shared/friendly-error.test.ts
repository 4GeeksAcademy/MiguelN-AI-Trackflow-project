/**
 * `lib/friendly-error.ts`: the one place that decides what a user may read
 * about a failure on the sign-in, registration, recovery and account screens.
 *
 * The rule: the API's own words reach the screen only for a 4xx it documents,
 * and only if they read like a sentence. Everything else - a 5xx, a network
 * failure, a bug - gets text written in the frontend.
 */
import { describeError, GENERAL_ERROR_COPY } from "@/lib/friendly-error";
import { ApiError, NetworkError, UnauthorizedError, UnreadableResponseError } from "@identity-api-client";

const FALLBACK = "Please check the highlighted fields and try again.";

function describe422(detail: unknown) {
  return describeError(new ApiError(422, { detail }), FALLBACK, GENERAL_ERROR_COPY);
}

let consoleError: jest.SpyInstance;

beforeEach(() => {
  consoleError = jest.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  consoleError.mockRestore();
});

describe("describeError", () => {
  it("passes a refusal the API wrote for a reader straight to the form", () => {
    const error = new ApiError(409, { detail: "An account with this email address already exists." });

    expect(describeError(error, FALLBACK, GENERAL_ERROR_COPY)).toEqual({
      message: "An account with this email address already exists.",
      fieldErrors: {},
    });
  });

  it("maps a validation failure onto the fields it names, first message per field", () => {
    const described = describe422([
      { loc: ["body", "email"], msg: "Enter a valid email address." },
      { loc: ["body", "password"], msg: "Must be at least 8 characters long." },
      { loc: ["body", "password"], msg: "A second complaint about the same input." },
    ]);

    expect(described).toEqual({
      message: FALLBACK,
      fieldErrors: { email: "Enter a valid email address.", password: "Must be at least 8 characters long." },
    });
  });

  it.each([500, 502, 503])("never shows the body of a %i", (status) => {
    const error = new ApiError(status, { detail: "database connection refused at 10.0.0.5" });

    expect(describeError(error, FALLBACK, GENERAL_ERROR_COPY).message).toBe(GENERAL_ERROR_COPY.server);
  });

  it("describes an unreachable API as a connection problem", () => {
    const error = new NetworkError(new TypeError("Failed to fetch"));

    expect(describeError(error, FALLBACK, GENERAL_ERROR_COPY).message).toBe(GENERAL_ERROR_COPY.offline);
  });

  it("sends an expired session back to sign in", () => {
    expect(describeError(new UnauthorizedError(), FALLBACK, GENERAL_ERROR_COPY).message).toBe(
      "Your session has expired. Please sign in again.",
    );
  });

  it.each([
    ["a traceback", "Traceback (most recent call last): File app.py"],
    ["an exception name", "KeyError: 'email'"],
    ["a stack frame", "failed at handler (server.js:10)"],
    ["a file and line", "error in services/auth/router.py:82"],
    ["HTML", "<html><body>502 Bad Gateway</body></html>"],
    ["a runaway string", "x".repeat(301)],
  ])("replaces a message carrying %s with the form's own words", (_label, detail) => {
    const error = new ApiError(400, { detail });

    expect(describeError(error, FALLBACK, GENERAL_ERROR_COPY)).toEqual({ message: FALLBACK, fieldErrors: {} });
  });

  it("hides anything thrown that is not an API error, and keeps it for whoever debugs", () => {
    const bug = new TypeError("Cannot read properties of undefined");

    expect(describeError(bug, FALLBACK, GENERAL_ERROR_COPY).message).toMatch(/^Something went wrong on our side/);
    expect(consoleError).toHaveBeenCalledWith(bug);
  });

  it("reports a success whose body could not be read as a server problem", () => {
    const error = new UnreadableResponseError(200, new SyntaxError("Unexpected token <"));

    expect(describeError(error, FALLBACK, GENERAL_ERROR_COPY).message).toBe(GENERAL_ERROR_COPY.server);
  });
});
