/**
 * `services/auth-service.ts`: every call the sign-in, registration, recovery
 * and account screens make, run against a scripted API.
 *
 * Each call's contract is what the screen does next: whether a session is
 * stored or cleared, and which kind of error it throws. The screens branch on
 * those kinds - a reset-link 400 replaces the form, a 422 keeps it - so the
 * error types are pinned as carefully as the happy paths.
 */
import { clearToken, readToken, storeToken, UNAUTHORIZED_EVENT } from "@/lib/auth-storage";
import {
  changePassword,
  fetchCurrentUser,
  IncorrectCurrentPasswordError,
  InvalidResetTokenError,
  login,
  logout,
  PostRegistrationLoginError,
  register,
  requestPasswordReset,
  resetPassword,
  updateMyProfile,
} from "@/services/auth-service";
import { ApiError, NetworkError, UnauthorizedError } from "@identity-api-client";

import { type FakeBrowser, installBrowser, removeBrowser } from "./support/browser";
import { API, jsonResponse, scriptFetch } from "./support/http";

const SESSION = "header.payload.signature";

const registration = {
  email: " Ana@TrackFlow.com ",
  password: "correct-horse-42",
  confirmPassword: "correct-horse-42",
  name: "Ana Ruiz",
  phone: "",
  address: "",
};

let browser: FakeBrowser;

beforeEach(() => {
  browser = installBrowser();
});

afterEach(() => {
  clearToken();
  removeBrowser();
});

describe("login", () => {
  it("sends the normalised email, stores the session and returns it", async () => {
    const requests = scriptFetch(jsonResponse(200, { access_token: SESSION, token_type: "bearer", expires_in: 3600 }));

    await expect(login({ email: "  ANA@TrackFlow.com ", password: " spaces count " })).resolves.toBe(SESSION);

    expect(requests[0]).toMatchObject({
      url: `${API}/auth/login`,
      method: "POST",
      body: { email: "ana@trackflow.com", password: " spaces count " },
    });
    expect(readToken()).toBe(SESSION);
  });

  it("stores nothing when the credentials are refused", async () => {
    scriptFetch(jsonResponse(401, { detail: "Incorrect email or password" }));

    await expect(login({ email: "ana@trackflow.com", password: "wrong" })).rejects.toMatchObject({ status: 401 });
    expect(readToken()).toBeNull();
  });

  it("stores nothing when the API answers without a token", async () => {
    scriptFetch(jsonResponse(200, { token_type: "bearer" }));

    await expect(login({ email: "ana@trackflow.com", password: "pw" })).rejects.toThrow(
      "The API did not return an access token",
    );
    expect(readToken()).toBeNull();
  });
});

describe("register", () => {
  it("creates the account, then signs in with the same credentials", async () => {
    const requests = scriptFetch(
      jsonResponse(201, { id: "u1", email: "ana@trackflow.com" }),
      jsonResponse(200, { access_token: SESSION }),
    );

    await expect(register(registration)).resolves.toBe(SESSION);

    expect(requests.map((request) => request.url)).toEqual([`${API}/users`, `${API}/auth/login`]);
    expect(requests[0].body).toEqual({ email: "ana@trackflow.com", password: "correct-horse-42", name: "Ana Ruiz" });
    expect(readToken()).toBe(SESSION);
  });

  it("reports a taken email as a registration failure, without trying to sign in", async () => {
    const requests = scriptFetch(jsonResponse(409, { detail: "An account with this email address already exists." }));

    const failure = register(registration);

    await expect(failure).rejects.toBeInstanceOf(ApiError);
    await expect(failure).rejects.not.toBeInstanceOf(PostRegistrationLoginError);
    expect(requests).toHaveLength(1);
  });

  it("says the account exists when only the sign-in after it failed", async () => {
    scriptFetch(jsonResponse(201, { id: "u1" }), jsonResponse(500, { detail: "boom" }));

    await expect(register(registration)).rejects.toBeInstanceOf(PostRegistrationLoginError);
    expect(readToken()).toBeNull();
  });
});

describe("fetchCurrentUser", () => {
  it("asks with the stored session and reads the account", async () => {
    storeToken(SESSION);
    const requests = scriptFetch(
      jsonResponse(200, { email: "ana@trackflow.com", role: "user", profile: null }),
    );

    await expect(fetchCurrentUser()).resolves.toEqual({
      email: "ana@trackflow.com",
      role: "user",
      profile: null,
    });
    expect(requests[0].headers.Authorization).toBe(`Bearer ${SESSION}`);
  });

  it("ends the session and asks for a sign-in when the API refuses it", async () => {
    storeToken(SESSION);
    scriptFetch(jsonResponse(401, { detail: "Invalid or expired token" }));

    await expect(fetchCurrentUser()).rejects.toBeInstanceOf(UnauthorizedError);
    expect(readToken()).toBeNull();
    expect(browser.events).toEqual([UNAUTHORIZED_EVENT]);
  });

  it("does not call the API at all without a session", async () => {
    const requests = scriptFetch();

    await expect(fetchCurrentUser()).rejects.toBeInstanceOf(UnauthorizedError);
    expect(requests).toHaveLength(0);
  });
});

describe("updateMyProfile", () => {
  it("sends the edited fields, with cleared ones as null", async () => {
    storeToken(SESSION);
    const requests = scriptFetch(
      jsonResponse(200, { id: "p1", name: "Ana", phone: null, address: null }),
    );

    await expect(updateMyProfile({ name: " Ana ", phone: "", address: "" })).resolves.toMatchObject({ name: "Ana" });
    expect(requests[0]).toMatchObject({
      url: `${API}/profiles/me`,
      method: "PUT",
      body: { name: "Ana", phone: null, address: null },
    });
  });

  it("passes a validation refusal back to the form", async () => {
    storeToken(SESSION);
    scriptFetch(jsonResponse(422, { detail: [{ loc: ["body", "name"], msg: "Must be at most 120 characters long." }] }));

    await expect(updateMyProfile({ name: "x", phone: "", address: "" })).rejects.toMatchObject({ status: 422 });
    expect(readToken()).toBe(SESSION);
  });
});

describe("requestPasswordReset", () => {
  it("resolves with nothing a caller could use to tell a registered address from an unregistered one", async () => {
    const requests = scriptFetch(jsonResponse(200, { message: "If an account exists..." }));

    await expect(requestPasswordReset({ email: " Ana@TrackFlow.com " })).resolves.toBeUndefined();
    expect(requests[0].body).toEqual({ email: "ana@trackflow.com" });
  });

  it("reports an unreachable API as a network failure", async () => {
    scriptFetch(new TypeError("Failed to fetch"));

    await expect(requestPasswordReset({ email: "ana@trackflow.com" })).rejects.toBeInstanceOf(NetworkError);
  });
});

describe("resetPassword", () => {
  it("sets the password and discards a session issued against the old one", async () => {
    storeToken(SESSION);
    const requests = scriptFetch(jsonResponse(200, { message: "Your password has been reset." }));

    await resetPassword("reset.link.token", "battery-staple-77");

    expect(requests[0].body).toEqual({ token: "reset.link.token", new_password: "battery-staple-77" });
    expect(readToken()).toBeNull();
  });

  it("turns a 400 into a dead-link error, which replaces the form", async () => {
    scriptFetch(jsonResponse(400, { detail: "This password reset link is invalid or has expired." }));

    const failure = resetPassword("spent.link.token", "battery-staple-77");

    await expect(failure).rejects.toBeInstanceOf(InvalidResetTokenError);
    await expect(failure).rejects.toThrow("This password reset link is invalid or has expired.");
  });

  it("keeps a 422 as a field error, so a weak password does not look like a dead link", async () => {
    storeToken(SESSION);
    scriptFetch(jsonResponse(422, { detail: [{ loc: ["body", "new_password"], msg: "Password is too long." }] }));

    const failure = resetPassword("live.link.token", "x".repeat(80));

    await expect(failure).rejects.toBeInstanceOf(ApiError);
    await expect(failure).rejects.not.toBeInstanceOf(InvalidResetTokenError);
    expect(readToken()).toBe(SESSION);
  });
});

describe("changePassword", () => {
  it("sends the two passwords but never the confirmation, and keeps the session", async () => {
    storeToken(SESSION);
    const requests = scriptFetch(jsonResponse(200, { message: "Your password has been updated." }));

    await changePassword({
      currentPassword: "correct-horse-42",
      newPassword: "battery-staple-77",
      confirmPassword: "battery-staple-77",
    });

    expect(requests[0].body).toEqual({ current_password: "correct-horse-42", new_password: "battery-staple-77" });
    expect(requests[0].headers.Authorization).toBe(`Bearer ${SESSION}`);
    expect(readToken()).toBe(SESSION);
  });

  it("turns a 400 into a wrong-current-password error for that field", async () => {
    storeToken(SESSION);
    scriptFetch(jsonResponse(400, { detail: "Current password is incorrect" }));

    const failure = changePassword({ currentPassword: "wrong", newPassword: "battery-staple-77", confirmPassword: "x" });

    await expect(failure).rejects.toBeInstanceOf(IncorrectCurrentPasswordError);
    await expect(failure).rejects.toThrow("Current password is incorrect");
    expect(readToken()).toBe(SESSION);
  });

  it("signs out when the session itself has expired", async () => {
    storeToken(SESSION);
    scriptFetch(jsonResponse(401, { detail: "Invalid or expired token" }));

    await expect(
      changePassword({ currentPassword: "pw", newPassword: "battery-staple-77", confirmPassword: "x" }),
    ).rejects.toBeInstanceOf(UnauthorizedError);
    expect(readToken()).toBeNull();
  });
});

describe("logout", () => {
  it("discards the session", () => {
    storeToken(SESSION);

    logout();

    expect(readToken()).toBeNull();
  });

  it("still ends the session when storage is blocked", () => {
    removeBrowser();
    installBrowser({ storage: "blocked" });
    storeToken(SESSION);

    expect(() => logout()).not.toThrow();
    expect(readToken()).toBeNull();
  });
});
