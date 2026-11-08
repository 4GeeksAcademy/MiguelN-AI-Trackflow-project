/**
 * `uis/website/lib/api-client.ts` talks to the external candidate records API,
 * a host this project does not control. Its one authentication rule: a
 * TrackFlow session must never travel there, and nothing that host answers can
 * end the TrackFlow session.
 */
import { requestApi } from "@/lib/api-client";
import { clearToken, readToken, storeToken } from "@/lib/auth-storage";

import { installBrowser, removeBrowser } from "../shared/support/browser";
import { API, jsonResponse, scriptFetch } from "../shared/support/http";

const SESSION = "header.payload.signature";

beforeEach(() => {
  installBrowser();
  storeToken(SESSION);
});

afterEach(() => {
  clearToken();
  removeBrowser();
});

it("never hands the TrackFlow session to the records API", async () => {
  const requests = scriptFetch(jsonResponse(200, []));

  await requestApi("/records");

  expect(requests[0].url).toBe(`${API}/records`);
  expect(requests[0].headers.Authorization).toBeUndefined();
});

it("does not end the TrackFlow session when the records API refuses a request", async () => {
  scriptFetch(jsonResponse(401, { detail: "unauthorized" }));

  await expect(requestApi("/records")).rejects.toMatchObject({ status: 401 });
  expect(readToken()).toBe(SESSION);
});
