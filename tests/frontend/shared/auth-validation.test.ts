/**
 * `lib/auth.ts`, the form half: what the sign-in, registration, recovery and
 * profile forms refuse before any request is sent.
 *
 * These rules mirror the API's (`services/users/models.py`), so a form never
 * sends what the API would refuse for a reason the form could have caught.
 */
import {
  emptyChangePasswordFormValues,
  emptyForgotPasswordFormValues,
  emptyLoginFormValues,
  emptyRegisterFormValues,
  emptyResetPasswordFormValues,
  MAX_ADDRESS_LENGTH,
  MAX_NAME_LENGTH,
  MAX_PHONE_LENGTH,
  MIN_PASSWORD_LENGTH,
  profileFormValuesFrom,
  validateChangePasswordForm,
  validateForgotPasswordForm,
  validateLoginForm,
  validateProfileForm,
  validateRegisterForm,
  validateResetPasswordForm,
} from "@/lib/auth";

const PASSWORD = "correct-horse-42";

const validRegistration = {
  email: "ana@trackflow.com",
  password: PASSWORD,
  confirmPassword: PASSWORD,
  name: "",
  phone: "",
  address: "",
};

describe("validateLoginForm", () => {
  it("accepts a complete form", () => {
    expect(validateLoginForm({ email: "ana@trackflow.com", password: PASSWORD })).toBeNull();
  });

  it("requires both fields", () => {
    expect(validateLoginForm({ email: "", password: "" })).toEqual({
      email: "Email is required.",
      password: "Password is required.",
    });
  });

  it.each(["   ", "ana", "ana@", "ana@trackflow", "ana @trackflow.com"])("refuses the email %j", (email) => {
    expect(validateLoginForm({ email, password: PASSWORD })?.email).toBeDefined();
  });
});

describe("validateRegisterForm", () => {
  it("accepts a complete form with the optional fields left blank", () => {
    expect(validateRegisterForm(validRegistration)).toBeNull();
  });

  it("refuses a password one character short of the minimum and accepts the minimum", () => {
    const short = "p".repeat(MIN_PASSWORD_LENGTH - 1);
    const minimum = "p".repeat(MIN_PASSWORD_LENGTH);

    expect(validateRegisterForm({ ...validRegistration, password: short, confirmPassword: short })?.password).toBe(
      "Password must be at least 8 characters.",
    );
    expect(validateRegisterForm({ ...validRegistration, password: minimum, confirmPassword: minimum })).toBeNull();
  });

  it("requires a matching confirmation", () => {
    expect(validateRegisterForm({ ...validRegistration, confirmPassword: "" })?.confirmPassword).toBe(
      "Confirm your password.",
    );
    expect(validateRegisterForm({ ...validRegistration, confirmPassword: "different-42" })?.confirmPassword).toBe(
      "Passwords do not match.",
    );
  });

  it.each([
    ["name", MAX_NAME_LENGTH],
    ["phone", MAX_PHONE_LENGTH],
    ["address", MAX_ADDRESS_LENGTH],
  ] as const)("caps %s at the API's %i characters", (field, limit) => {
    expect(validateRegisterForm({ ...validRegistration, [field]: "x".repeat(limit) })).toBeNull();
    expect(validateRegisterForm({ ...validRegistration, [field]: "x".repeat(limit + 1) })?.[field]).toBeDefined();
  });

  it("counts a 4-emoji password as 8 characters, though the API counts 4 (pinned)", () => {
    // JavaScript measures UTF-16 units; the API measures characters. The API's
    // 422 lands on the password field, so the form still explains it.
    const emoji = "🔐🔐🔐🔐";

    expect(validateRegisterForm({ ...validRegistration, password: emoji, confirmPassword: emoji })).toBeNull();
  });
});

describe("validateForgotPasswordForm", () => {
  it("accepts a valid address", () => {
    expect(validateForgotPasswordForm({ email: "ana@trackflow.com" })).toBeNull();
  });

  it("refuses an empty or malformed address without asking the API whether it exists", () => {
    expect(validateForgotPasswordForm({ email: "" })).toEqual({ email: "Email is required." });
    expect(validateForgotPasswordForm({ email: "ana@" })).toEqual({ email: "Enter a valid email address." });
  });
});

describe("validateResetPasswordForm", () => {
  it("accepts a new password and its matching confirmation", () => {
    expect(validateResetPasswordForm({ newPassword: PASSWORD, confirmPassword: PASSWORD })).toBeNull();
  });

  it("refuses an empty, short or mismatched password before the link is spent", () => {
    expect(validateResetPasswordForm({ newPassword: "", confirmPassword: "" })).toEqual({
      newPassword: "New password is required.",
      confirmPassword: "Confirm your new password.",
    });
    expect(validateResetPasswordForm({ newPassword: "short7!", confirmPassword: "short7!" })?.newPassword).toBe(
      "Password must be at least 8 characters.",
    );
    expect(validateResetPasswordForm({ newPassword: PASSWORD, confirmPassword: "other-42" })?.confirmPassword).toBe(
      "Passwords do not match.",
    );
  });
});

describe("validateChangePasswordForm", () => {
  it("accepts the current password plus a matching new one", () => {
    expect(
      validateChangePasswordForm({ currentPassword: "old-password", newPassword: PASSWORD, confirmPassword: PASSWORD }),
    ).toBeNull();
  });

  it("requires the current password and applies the same new-password rules as reset", () => {
    expect(validateChangePasswordForm({ currentPassword: "", newPassword: "short7!", confirmPassword: "nope" })).toEqual({
      currentPassword: "Your current password is required.",
      newPassword: "Password must be at least 8 characters.",
      confirmPassword: "Passwords do not match.",
    });
  });
});

describe("validateProfileForm", () => {
  it("accepts blank fields, which clear the profile", () => {
    expect(validateProfileForm({ name: "", phone: "", address: "" })).toBeNull();
  });

  it("refuses a field over the API's limit, measured after trimming", () => {
    expect(validateProfileForm({ name: `  ${"x".repeat(MAX_NAME_LENGTH)}  `, phone: "", address: "" })).toBeNull();
    expect(validateProfileForm({ name: "x".repeat(MAX_NAME_LENGTH + 1), phone: "", address: "" })?.name).toBe(
      "Name must be 120 characters or fewer.",
    );
  });
});

describe("empty form factories", () => {
  it.each([
    ["login", emptyLoginFormValues],
    ["register", emptyRegisterFormValues],
    ["forgot password", emptyForgotPasswordFormValues],
    ["reset password", emptyResetPasswordFormValues],
    ["change password", emptyChangePasswordFormValues],
  ] as const)("start the %s form blank, with a fresh object every time", (_form, factory) => {
    const first = factory() as Record<string, string>;
    expect(Object.values(first).every((value) => value === "")).toBe(true);

    first[Object.keys(first)[0]] = "typed into a previous form";

    expect(Object.values(factory() as Record<string, string>).every((value) => value === "")).toBe(true);
  });
});

describe("profileFormValuesFrom", () => {
  it("fills the form from a stored profile", () => {
    const profile = { id: "p1", user_id: "u1", name: "Ana Ruiz", phone: "+52 81 5555 0000", address: "Monterrey" };

    expect(profileFormValuesFrom(profile)).toEqual({ name: "Ana Ruiz", phone: "+52 81 5555 0000", address: "Monterrey" });
  });

  it("gives blank inputs for a missing profile or missing fields", () => {
    expect(profileFormValuesFrom(null)).toEqual({ name: "", phone: "", address: "" });
    expect(profileFormValuesFrom({ id: "p1", user_id: "u1", name: null, phone: null, address: null })).toEqual({
      name: "",
      phone: "",
      address: "",
    });
  });
});
