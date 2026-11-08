export type UserRole = "admin" | "manager" | "user";

/** Display name and contact data. Lives on `Profile`, never on `User`. */
export type Profile = {
  id: string;
  name: string | null;
  phone: string | null;
  address: string | null;
};

/** The `GET /auth/me` shape: credentials from `User`, contact data from `Profile`. */
export type AuthenticatedUser = {
  email: string;
  role: UserRole;
  profile: Profile | null;
};

export type LoginFormValues = {
  email: string;
  password: string;
};

export type RegisterFormValues = {
  email: string;
  password: string;
  confirmPassword: string;
  name: string;
  phone: string;
  address: string;
};

export type ForgotPasswordFormValues = {
  email: string;
};

export type ResetPasswordFormValues = {
  newPassword: string;
  confirmPassword: string;
};

export type ChangePasswordFormValues = {
  currentPassword: string;
  newPassword: string;
  confirmPassword: string;
};

export type ProfileFormValues = {
  name: string;
  phone: string;
  address: string;
};

/**
 * Field name to message. The `form` key carries anything that belongs to no
 * single input, so a form can always render an error even for an unmapped one.
 */
export type FieldErrors<TField extends string> = Partial<Record<TField | "form", string>>;

export type LoginField = keyof LoginFormValues;
export type RegisterField = keyof RegisterFormValues;
export type ProfileField = keyof ProfileFormValues;
export type ForgotPasswordField = keyof ForgotPasswordFormValues;
export type ResetPasswordField = keyof ResetPasswordFormValues;
export type ChangePasswordField = keyof ChangePasswordFormValues;

/**
 * `error` means the session could not be checked at all - the API was
 * unreachable or failed - which says nothing about whether the token is good.
 */
export type SessionStatus = "loading" | "authenticated" | "unauthenticated" | "error";
