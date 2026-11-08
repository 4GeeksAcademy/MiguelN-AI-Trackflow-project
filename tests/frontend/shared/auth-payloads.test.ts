/**
 * `lib/auth.ts`, the data half: what goes out to the API, how its answers are
 * read, and how its refusals are mapped back onto the form.
 */
import {
  buildProfilePayload,
  buildRegistrationPayload,
  extractAccessToken,
  normalizeAuthenticatedUser,
  normalizeProfile,
  toFieldErrors,
} from "@/lib/auth";
import { ApiError, NetworkError, UnauthorizedError } from "@identity-api-client";

describe("buildRegistrationPayload", () => {
  it("trims and lower-cases the email and keeps the password exactly as typed", () => {
    const payload = buildRegistrationPayload({
      email: "  Ana@TrackFlow.COM ",
      password: " spaces count ",
      confirmPassword: " spaces count ",
      name: "  Ana Ruiz ",
      phone: "",
      address: "",
    });

    expect(payload.email).toBe("ana@trackflow.com");
    expect(payload.password).toBe(" spaces count ");
    expect(payload.name).toBe("Ana Ruiz");
  });

  it("leaves blank optional fields out instead of sending empty strings", () => {
    const payload = buildRegistrationPayload({
      email: "ana@trackflow.com",
      password: "correct-horse-42",
      confirmPassword: "correct-horse-42",
      name: "   ",
      phone: "",
      address: "",
    });

    expect(JSON.parse(JSON.stringify(payload))).toEqual({ email: "ana@trackflow.com", password: "correct-horse-42" });
  });
});

describe("buildProfilePayload", () => {
  it("sends trimmed values", () => {
    expect(buildProfilePayload({ name: " Ana ", phone: "+34 976 000 000", address: " Zaragoza " })).toEqual({
      name: "Ana",
      phone: "+34 976 000 000",
      address: "Zaragoza",
    });
  });

  it("sends a cleared input as an explicit null, which is how the API erases a field", () => {
    expect(buildProfilePayload({ name: "", phone: "   ", address: "" })).toEqual({
      name: null,
      phone: null,
      address: null,
    });
  });
});

describe("normalizeProfile", () => {
  it("reads the public profile projection and ignores the internal user id", () => {
    const profile = { id: "p1", user_id: "u1", name: "Ana", phone: null, address: "Monterrey" };

    expect(normalizeProfile(profile)).toEqual({
      id: "p1",
      name: "Ana",
      phone: null,
      address: "Monterrey",
    });
  });

  it("reads blank or mistyped fields as absent", () => {
    expect(normalizeProfile({ id: 7, name: "   ", phone: 5, address: "" })).toEqual({
      id: "",
      name: null,
      phone: null,
      address: null,
    });
  });

  it.each([null, "profile", 42])("refuses a payload that is not an object: %j", (payload) => {
    expect(() => normalizeProfile(payload)).toThrow("Unexpected profile payload");
  });
});

describe("normalizeAuthenticatedUser", () => {
  it("reads the account and its profile", () => {
    const user = normalizeAuthenticatedUser({
      id: "internal-user-id",
      email: "ana@trackflow.com",
      role: "manager",
      is_active: false,
      profile: { id: "p1", name: "Ana", phone: null, address: null },
    });

    expect(user).toEqual({
      email: "ana@trackflow.com",
      role: "manager",
      profile: { id: "p1", name: "Ana", phone: null, address: null },
    });
  });

  it("never grants a role it does not recognise", () => {
    expect(normalizeAuthenticatedUser({ id: "u1", role: "superadmin" }).role).toBe("user");
  });

  it("reads a missing profile as none", () => {
    expect(normalizeAuthenticatedUser({ id: "u1", profile: null }).profile).toBeNull();
  });

  it("refuses a payload that is not an object", () => {
    expect(() => normalizeAuthenticatedUser("ana")).toThrow("Unexpected account payload");
  });
});

describe("extractAccessToken", () => {
  it("returns the token the API issued", () => {
    expect(extractAccessToken({ access_token: "header.payload.signature", token_type: "bearer" })).toBe(
      "header.payload.signature",
    );
  });

  it.each([{}, { access_token: "   " }, { access_token: 42 }, null, "header.payload.signature"])(
    "refuses an answer without a usable token: %j",
    (payload) => {
      expect(() => extractAccessToken(payload)).toThrow("The API did not return an access token");
    },
  );
});

describe("toFieldErrors", () => {
  const FIELDS = ["newPassword", "confirmPassword"] as const;
  const ALIASES = { new_password: "newPassword" } as const;

  it("places each validation message on the input it is about, translating snake_case", () => {
    const error = new ApiError(422, {
      detail: [{ type: "string_too_short", loc: ["body", "new_password"], msg: "Must be at least 8 characters long." }],
    });

    expect(toFieldErrors(error, FIELDS, ALIASES)).toEqual({ newPassword: "Must be at least 8 characters long." });
  });

  it("puts a message about a field the form does not have on the form itself", () => {
    const error = new ApiError(422, { detail: [{ loc: ["body", "token"], msg: "This field is required." }] });

    expect(toFieldErrors(error, FIELDS, ALIASES)).toEqual({ form: "This field is required." });
  });

  it("puts a refusal with no field on the form", () => {
    const error = new ApiError(409, { detail: "An account with this email address already exists." });

    expect(toFieldErrors(error, FIELDS)).toEqual({ form: "An account with this email address already exists." });
  });

  it.each([
    ["a network failure", new NetworkError(new TypeError("Failed to fetch")), "Could not reach TrackFlow"],
    ["a server error", new ApiError(500, { detail: "Traceback (most recent call last)" }), "TrackFlow is having trouble"],
    ["an expired session", new UnauthorizedError(), "Your session has expired"],
  ])("describes %s in words written for the reader", (_label, error, start) => {
    const errors = toFieldErrors(error, FIELDS);

    expect(errors.form?.startsWith(start)).toBe(true);
    expect(Object.keys(errors)).toEqual(["form"]);
  });
});
