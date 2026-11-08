/**
 * `uis/backoffice/lib/api-client.ts`: how the backoffice reaches the API.
 *
 * Its auth decisions: the session token goes only on authenticated calls, a
 * 401 ends the session everywhere at once, and every failure arrives as an
 * error the screens can tell apart - never a raw `TypeError` or an unreadable
 * body.
 */
import {
  ApiError,
  buildApiUrl,
  extractApiErrorMessage,
  extractApiFieldErrors,
  extractApiProblems,
  NetworkError,
  parseResponseJson,
  requestApi,
  requestAuthenticatedApi,
  UnauthorizedError,
  UnreadableResponseError,
} from "@/lib/api-client";
import { clearToken, readToken, storeToken, UNAUTHORIZED_EVENT } from "@/lib/auth-storage";

import { type FakeBrowser, installBrowser, removeBrowser } from "../shared/support/browser";
import { API, jsonResponse, scriptFetch, textResponse } from "../shared/support/http";

const SESSION = "header.payload.signature";

let browser: FakeBrowser;

beforeEach(() => {
  browser = installBrowser();
});

afterEach(() => {
  clearToken();
  removeBrowser();
});

/** A fresh copy of the client, loaded with `NEXT_PUBLIC_API_URL` set to `value`. */
function clientConfiguredWith(value: string | undefined): typeof import("@/lib/api-client") {
  const saved = process.env.NEXT_PUBLIC_API_URL;
  if (value === undefined) {
    delete process.env.NEXT_PUBLIC_API_URL;
  } else {
    process.env.NEXT_PUBLIC_API_URL = value;
  }

  try {
    let client!: typeof import("@/lib/api-client");
    jest.isolateModules(() => {
      client = require("@/lib/api-client");
    });
    return client;
  } finally {
    process.env.NEXT_PUBLIC_API_URL = saved;
  }
}

describe("buildApiUrl", () => {
  it("joins the configured origin and the path, with or without its leading slash", () => {
    expect(buildApiUrl("/auth/me")).toBe(`${API}/auth/me`);
    expect(buildApiUrl("auth/me")).toBe(`${API}/auth/me`);
  });

  it("ignores trailing slashes on the configured origin", () => {
    expect(clientConfiguredWith("http://api.test///").buildApiUrl("/users")).toBe(`${API}/users`);
  });

  it("supports the same-origin backend proxy path", () => {
    expect(clientConfiguredWith("/backend").buildApiUrl("/users")).toBe("/backend/users");
  });

  it("refuses to build a URL when no API is configured", () => {
    expect(() => clientConfiguredWith(undefined).buildApiUrl("/auth/login")).toThrow(
      "NEXT_PUBLIC_API_URL is not configured",
    );
  });
});

describe("getApiBaseUrl", () => {
  it("reports the configured origin without trailing slashes", () => {
    expect(clientConfiguredWith("  http://api.test/ ").getApiBaseUrl()).toBe(API);
  });

  it("reports an empty origin, rather than a guess, when none is configured", () => {
    expect(clientConfiguredWith(undefined).getApiBaseUrl()).toBe("");
  });
});

describe("requestApi", () => {
  it("returns the answer, and never sends the session token", async () => {
    storeToken(SESSION);
    const requests = scriptFetch(jsonResponse(201, { id: "u1" }));

    const response = await requestApi("/users", { method: "POST" });

    expect(response.status).toBe(201);
    expect(requests[0].headers.Authorization).toBeUndefined();
  });

  it("turns a refusal into an ApiError carrying the API's body", async () => {
    scriptFetch(jsonResponse(409, { detail: "An account with this email address already exists." }));

    const failure = requestApi("/users", { method: "POST" });

    await expect(failure).rejects.toBeInstanceOf(ApiError);
    await expect(failure).rejects.toMatchObject({
      status: 409,
      message: "An account with this email address already exists.",
    });
  });

  it("reports an unreachable API as a network failure, not a bare TypeError", async () => {
    scriptFetch(new TypeError("Failed to fetch"));

    await expect(requestApi("/auth/login")).rejects.toBeInstanceOf(NetworkError);
  });

  it("survives an error page that is not JSON", async () => {
    scriptFetch(textResponse(502, "<html>Bad Gateway</html>"));

    await expect(requestApi("/auth/login")).rejects.toMatchObject({
      status: 502,
      payload: null,
      message: "Request failed with status 502",
    });
  });
});

describe("requestAuthenticatedApi", () => {
  it("sends the stored session as a bearer token", async () => {
    storeToken(SESSION);
    const requests = scriptFetch(jsonResponse(200, {}));

    await requestAuthenticatedApi("/auth/me");

    expect(requests[0]).toMatchObject({ url: `${API}/auth/me`, headers: { Authorization: `Bearer ${SESSION}` } });
  });

  it("does not call the API without a session, and asks for a sign-in", async () => {
    const requests = scriptFetch();

    await expect(requestAuthenticatedApi("/auth/me")).rejects.toBeInstanceOf(UnauthorizedError);
    expect(requests).toHaveLength(0);
    expect(browser.events).toEqual([UNAUTHORIZED_EVENT]);
  });

  it("ends the session when the API refuses it", async () => {
    storeToken(SESSION);
    scriptFetch(jsonResponse(401, { detail: "Invalid or expired token" }));

    await expect(requestAuthenticatedApi("/suppliers")).rejects.toBeInstanceOf(UnauthorizedError);
    expect(readToken()).toBeNull();
    expect(browser.events).toEqual([UNAUTHORIZED_EVENT]);
  });

  it("keeps the session for a refusal that is not about it", async () => {
    storeToken(SESSION);
    scriptFetch(jsonResponse(403, { detail: "You can only act on your own account" }));

    await expect(requestAuthenticatedApi("/users/someone-else")).rejects.toMatchObject({ status: 403 });
    expect(readToken()).toBe(SESSION);
    expect(browser.events).toEqual([]);
  });
});

describe("parseResponseJson", () => {
  it("reads a JSON body", async () => {
    await expect(parseResponseJson(jsonResponse(200, { id: "u1" }))).resolves.toEqual({ id: "u1" });
  });

  it("reads no content, and content that is not JSON, as nothing", async () => {
    await expect(parseResponseJson(new Response(null, { status: 204 }))).resolves.toBeNull();
    await expect(parseResponseJson(textResponse(200, "<p>hi</p>"))).resolves.toBeNull();
  });

  it("reports a body that claims to be JSON and is not", async () => {
    const broken = new Response("{not json", { status: 200, headers: { "content-type": "application/json" } });

    await expect(parseResponseJson(broken)).rejects.toBeInstanceOf(UnreadableResponseError);
  });
});

describe("extractApiErrorMessage", () => {
  it("reads each shape the API answers with", () => {
    expect(extractApiErrorMessage({ detail: "  Incorrect email or password " }, 401)).toBe("Incorrect email or password");
    expect(
      extractApiErrorMessage(
        {
          detail: [
            { loc: ["body", "email"], msg: "Enter a valid email address." },
            { loc: ["body", "password"], msg: "Must be at least 8 characters long." },
          ],
        },
        422,
      ),
    ).toBe("email: Enter a valid email address. · password: Must be at least 8 characters long.");
    expect(extractApiErrorMessage({ detail: { field: "title", message: "This field is required." } }, 400)).toBe(
      "This field is required.",
    );
  });

  it.each([null, "oops", {}, { detail: "" }, { detail: [] }])("falls back to the status for %j", (payload) => {
    expect(extractApiErrorMessage(payload, 418)).toBe("Request failed with status 418");
  });
});

describe("extractApiFieldErrors", () => {
  it("splits a validation failure into fields, dropping the request-part prefix", () => {
    expect(
      extractApiFieldErrors({
        detail: [
          { loc: ["body", "new_password"], msg: "Must be at least 8 characters long." },
          { loc: ["body", "profile", "name"], msg: "   " },
        ],
      }),
    ).toEqual([
      { field: "new_password", message: "Must be at least 8 characters long." },
      { field: "profile.name", message: "Invalid value" },
    ]);
  });

  it.each([null, { detail: "Incorrect email or password" }, { detail: { field: "x", message: "y" } }])(
    "finds no field errors in %j",
    (payload) => {
      expect(extractApiFieldErrors(payload)).toEqual([]);
    },
  );
});

describe("extractApiProblems", () => {
  it("reads every problem, or the single one", () => {
    expect(
      extractApiProblems({
        detail: { field: "title", message: "This field is required." },
        errors: [
          { field: "title", message: "This field is required." },
          { field: "branch", message: "Must be one of: central." },
        ],
      }),
    ).toHaveLength(2);
    expect(extractApiProblems({ detail: { field: "id", message: "No incident exists with id 'x'." } })).toEqual([
      { field: "id", message: "No incident exists with id 'x'." },
    ]);
  });

  it.each([null, { detail: "sentence" }, { errors: [{ message: "" }] }])("finds no problems in %j", (payload) => {
    expect(extractApiProblems(payload)).toEqual([]);
  });
});
