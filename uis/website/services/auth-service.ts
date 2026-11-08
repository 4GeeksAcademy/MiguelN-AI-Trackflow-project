import {
  ApiError,
  parseAuthResponseJson,
  requestAuthApi,
  requestAuthenticatedApi,
} from "@/lib/auth-api-client";
import { clearToken, storeToken } from "@/lib/auth-storage";
import { GENERAL_ERROR_COPY, describeError } from "@/lib/friendly-error";
import {
  buildProfilePayload,
  buildRegistrationPayload,
  extractAccessToken,
  normalizeAuthenticatedUser,
  normalizeProfile,
} from "@/lib/auth";
import type {
  AuthenticatedUser,
  ChangePasswordFormValues,
  ForgotPasswordFormValues,
  LoginFormValues,
  Profile,
  ProfileFormValues,
  RegisterFormValues,
} from "@/types/auth";

const JSON_HEADERS = { "Content-Type": "application/json" };

/**
 * Exchanges credentials for a JWT and stores it. Storing here rather than at
 * the call site means no login path can end with an unsaved token.
 */
export async function login(values: LoginFormValues): Promise<string> {
  const response = await requestAuthApi("/auth/login", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ email: values.email.trim().toLowerCase(), password: values.password }),
  });

  const token = extractAccessToken(await parseAuthResponseJson(response));
  storeToken(token);

  return token;
}

/**
 * Raised when `POST /users` succeeded but the follow-up login did not. The
 * account exists, so the form must not report it as a registration failure.
 */
export class PostRegistrationLoginError extends Error {
  constructor(cause: unknown) {
    super("Your account was created, but signing you in failed. Please sign in manually.", { cause });
    this.name = "PostRegistrationLoginError";
  }
}

/**
 * Creates the account, then signs in with the same credentials so the user
 * never has to type them twice. `POST /users` also creates the linked profile
 * from the optional name, phone and address.
 */
export async function register(values: RegisterFormValues): Promise<string> {
  await requestAuthApi("/users", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(buildRegistrationPayload(values)),
  });

  try {
    return await login({ email: values.email, password: values.password });
  } catch (loginError) {
    throw new PostRegistrationLoginError(loginError);
  }
}

export async function fetchCurrentUser(): Promise<AuthenticatedUser> {
  const response = await requestAuthenticatedApi("/auth/me");

  return normalizeAuthenticatedUser(await parseAuthResponseJson(response));
}

export async function updateMyProfile(values: ProfileFormValues): Promise<Profile> {
  const response = await requestAuthenticatedApi("/profiles/me", {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify(buildProfilePayload(values)),
  });

  return normalizeProfile(await parseAuthResponseJson(response));
}

/** Raised when the API refuses a reset token: expired, already used, or never real. */
export class InvalidResetTokenError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidResetTokenError";
  }
}

/** Raised when `POST /auth/change-password` rejects the current password. */
export class IncorrectCurrentPasswordError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "IncorrectCurrentPasswordError";
  }
}

/**
 * Starts password recovery.
 *
 * The API answers `200` for a registered and an unregistered address alike, so
 * there is nothing here to branch on - and deliberately nothing returned that a
 * caller could use to tell the two apart.
 */
export async function requestPasswordReset(values: ForgotPasswordFormValues): Promise<void> {
  await requestAuthApi("/auth/forgot-password", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ email: values.email.trim().toLowerCase() }),
  });
}

/**
 * Finishes password recovery with the token from the emailed link.
 *
 * Any stored session is discarded on success: it was issued against the old
 * password, and the user is on their way to sign in again with the new one.
 */
export async function resetPassword(token: string, newPassword: string): Promise<void> {
  try {
    await requestAuthApi("/auth/reset-password", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ token, new_password: newPassword }),
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 400) {
      throw new InvalidResetTokenError(
        describeError(error, "This password reset link is invalid or has expired.", GENERAL_ERROR_COPY).message,
      );
    }

    throw error;
  }

  clearToken();
}

/**
 * Changes the password of the signed-in account. The confirmation field is a
 * client-side concern and is never sent: the API takes only the two passwords.
 */
export async function changePassword(values: ChangePasswordFormValues): Promise<void> {
  try {
    await requestAuthenticatedApi("/auth/change-password", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({
        current_password: values.currentPassword,
        new_password: values.newPassword,
      }),
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 400) {
      throw new IncorrectCurrentPasswordError(
        describeError(error, "Your current password is incorrect.", GENERAL_ERROR_COPY).message,
      );
    }

    throw error;
  }
}

/** There is no server-side session to end, so logging out is discarding the token. */
export function logout(): void {
  clearToken();
}
