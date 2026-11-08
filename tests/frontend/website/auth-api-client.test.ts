/**
 * `uis/website/lib/auth-api-client.ts`: how the website reaches the in-repo
 * identity API.
 *
 * Kept apart from `lib/api-client.ts`, which talks to the external candidate
 * records API: the session token must only ever travel to the identity API.
 */
import {
  ApiError,
  buildAuthApiUrl,
  extractApiErrorMessage,
  extractApiFieldErrors,
  NetworkError,
  parseAuthResponseJson,
  readErrorPayload,
  readJsonBody,
  requestAuthApi,
  requestAuthenticatedApi,
  sendRequest,
  UnauthorizedError,
  UnreadableResponseError,
} from "@/lib/auth-api-client";
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

/** A fresh copy of the client, loaded under the given environment. */
function clientLoadedWith(env: { url?: string; nodeEnv: string }): typeof import("@/lib/auth-api-client") {
  const saved = { url: process.env.NEXT_PUBLIC_AUTH_API_URL, nodeEnv: process.env.NODE_ENV };
  const writable = process.env as Record<string, string | undefined>;
  if (env.url === undefined) {
    delete writable.NEXT_PUBLIC_AUTH_API_URL;
  } else {
    writable.NEXT_PUBLIC_AUTH_API_URL = env.url;
  }
  writable.NODE_ENV = env.nodeEnv;

  try {
    let client!: typeof import("@/lib/auth-api-client");
    jest.isolateModules(() => {
      client = require("@/lib/auth-api-client");
    });
    return client;
  } finally {
    writable.NEXT_PUBLIC_AUTH_API_URL = saved.url;
    writable.NODE_ENV = saved.nodeEnv;
  }
}

describe("buildAuthApiUrl", () => {
  it("joins the configured origin and the path", () => {
    expect(buildAuthApiUrl("/auth/login")).toBe(`${API}/auth/login`);
    expect(buildAuthApiUrl("auth/login")).toBe(`${API}/auth/login`);
  });

  it("falls back to the local API in development", () => {
    expect(clientLoadedWith({ nodeEnv: "development" }).getAuthApiBaseUrl()).toBe("http://localhost:8000");
  });

  it("refuses to guess in a production build, where the local API is not there", () => {
    expect(() => clientLoadedWith({ nodeEnv: "production" }).buildAuthApiUrl("/auth/login")).toThrow(
      "NEXT_PUBLIC_AUTH_API_URL is not configured",
    );
  });

  it("ignores trailing slashes on the configured origin", () => {
    expect(clientLoadedWith({ url: "http://api.test//", nodeEnv: "production" }).getAuthApiBaseUrl()).toBe(API);
  });

  it("supports the same-origin backend proxy path", () => {
    expect(clientLoadedWith({ url: "/backend", nodeEnv: "production" }).buildAuthApiUrl("/auth/login")).toBe(
      "/backend/auth/login",
    );
  });
});

describe("sendRequest", () => {
  it("returns the answer", async () => {
    scriptFetch(jsonResponse(200, {}));

    await expect(sendRequest(`${API}/auth/me`, {})).resolves.toMatchObject({ status: 200 });
  });

  it("reports an unreachable API as a network failure, not a bare TypeError", async () => {
    scriptFetch(new TypeError("Failed to fetch"));

    await expect(sendRequest(`${API}/auth/me`, {})).rejects.toBeInstanceOf(NetworkError);
  });
});

describe("readJsonBody and readErrorPayload", () => {
  it("read a JSON body", async () => {
    await expect(readJsonBody(jsonResponse(200, { id: "u1" }))).resolves.toEqual({ id: "u1" });
    await expect(readErrorPayload(jsonResponse(400, { detail: "no" }))).resolves.toEqual({ detail: "no" });
  });

  it("report a broken success body, and read a broken error body as nothing", async () => {
    await expect(readJsonBody(textResponse(200, "<html>"))).rejects.toBeInstanceOf(UnreadableResponseError);
    await expect(readErrorPayload(textResponse(502, "<html>Bad Gateway</html>"))).resolves.toBeNull();
  });
});

describe("requestAuthApi", () => {
  it("returns the answer, and never sends the session token", async () => {
    storeToken(SESSION);
    const requests = scriptFetch(jsonResponse(200, { access_token: "t" }));

    await requestAuthApi("/auth/login", { method: "POST" });

    expect(requests[0].headers.Authorization).toBeUndefined();
  });

  it("turns a refusal into an ApiError carrying the API's body", async () => {
    scriptFetch(jsonResponse(401, { detail: "Incorrect email or password" }));

    const failure = requestAuthApi("/auth/login", { method: "POST" });

    await expect(failure).rejects.toBeInstanceOf(ApiError);
    await expect(failure).rejects.toMatchObject({ status: 401, message: "Incorrect email or password" });
  });
});

describe("requestAuthenticatedApi", () => {
  it("sends the stored session as a bearer token", async () => {
    storeToken(SESSION);
    const requests = scriptFetch(jsonResponse(200, {}));

    await requestAuthenticatedApi("/auth/me");

    expect(requests[0].headers.Authorization).toBe(`Bearer ${SESSION}`);
  });

  it("does not call the API without a session, and asks for a sign-in", async () => {
    const requests = scriptFetch();

    await expect(requestAuthenticatedApi("/auth/me")).rejects.toBeInstanceOf(UnauthorizedError);
    expect(requests).toHaveLength(0);
    expect(browser.events).toEqual([UNAUTHORIZED_EVENT]);
  });

  it("ends the session when the API refuses it, and only then", async () => {
    storeToken(SESSION);
    scriptFetch(jsonResponse(403, { detail: "Inactive user account" }), jsonResponse(401, { detail: "expired" }));

    await expect(requestAuthenticatedApi("/auth/me")).rejects.toMatchObject({ status: 403 });
    expect(readToken()).toBe(SESSION);

    await expect(requestAuthenticatedApi("/auth/me")).rejects.toBeInstanceOf(UnauthorizedError);
    expect(readToken()).toBeNull();
  });
});

describe("parseAuthResponseJson", () => {
  it("reads a JSON body", async () => {
    await expect(parseAuthResponseJson(jsonResponse(200, { id: "u1" }))).resolves.toEqual({ id: "u1" });
  });

  it("reads no content, and content that is not JSON, as nothing", async () => {
    await expect(parseAuthResponseJson(new Response(null, { status: 204 }))).resolves.toBeNull();
    await expect(parseAuthResponseJson(textResponse(200, "<p>hi</p>"))).resolves.toBeNull();
  });

  it("reports a body that claims to be JSON and is not", async () => {
    const broken = new Response("{not json", { status: 200, headers: { "content-type": "application/json" } });

    await expect(parseAuthResponseJson(broken)).rejects.toBeInstanceOf(UnreadableResponseError);
  });
});

describe("extractApiErrorMessage", () => {
  it("reads a sentence and a validation list", () => {
    expect(extractApiErrorMessage({ detail: " Incorrect email or password " }, 401)).toBe("Incorrect email or password");
    expect(extractApiErrorMessage({ detail: [{ loc: ["body", "email"], msg: "Enter a valid email address." }] }, 422)).toBe(
      "email: Enter a valid email address.",
    );
  });

  it.each([null, {}, { detail: "  " }, { detail: [] }])("falls back to the status for %j", (payload) => {
    expect(extractApiErrorMessage(payload, 500)).toBe("Request failed with status 500");
  });
});

describe("extractApiFieldErrors", () => {
  it("splits a validation failure into fields", () => {
    expect(extractApiFieldErrors({ detail: [{ loc: ["body", "new_password"], msg: "Too short." }] })).toEqual([
      { field: "new_password", message: "Too short." },
    ]);
  });

  it("finds no field errors in a sentence", () => {
    expect(extractApiFieldErrors({ detail: "Incorrect email or password" })).toEqual([]);
  });
});
