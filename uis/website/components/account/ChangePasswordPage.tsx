"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { AuthField } from "@/components/auth/AuthField";
import { StateMessage } from "@/components/common/StateMessage";
import {
  emptyChangePasswordFormValues,
  toFieldErrors,
  validateChangePasswordForm,
} from "@/lib/auth";
import { changePassword, IncorrectCurrentPasswordError } from "@/services/auth-service";
import type { ChangePasswordField, ChangePasswordFormValues, FieldErrors } from "@/types/auth";

const CHANGE_PASSWORD_FIELDS: readonly ChangePasswordField[] = [
  "currentPassword",
  "newPassword",
  "confirmPassword",
];

/** The API speaks snake_case; this form speaks camelCase. */
const CHANGE_FIELD_ALIASES: Readonly<Record<string, ChangePasswordField>> = {
  current_password: "currentPassword",
  new_password: "newPassword",
};

/**
 * Changes the password of the signed-in account.
 *
 * The current password is required on top of the session: holding a token is
 * not enough, so a browser left open on a shared machine cannot be used to take
 * the account over. The confirmation field never reaches the API - it is
 * checked here, before any request is made.
 */
export default function ChangePasswordPage() {
  const [values, setValues] = useState<ChangePasswordFormValues>(emptyChangePasswordFormValues());
  const [errors, setErrors] = useState<FieldErrors<ChangePasswordField>>({});
  const [isSaving, setIsSaving] = useState(false);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  function updateValues(patch: Partial<ChangePasswordFormValues>): void {
    setErrors({});
    setSuccessMessage(null);
    setValues((currentValues) => ({ ...currentValues, ...patch }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const validationErrors = validateChangePasswordForm(values);
    if (validationErrors) {
      setErrors(validationErrors);
      setSuccessMessage(null);
      return;
    }

    setIsSaving(true);
    setErrors({});
    setSuccessMessage(null);

    try {
      await changePassword(values);
      // Cleared on success so the new password is not left sitting in the DOM.
      setValues(emptyChangePasswordFormValues());
      setSuccessMessage("Your password has been updated.");
    } catch (changeError) {
      if (changeError instanceof IncorrectCurrentPasswordError) {
        setErrors({ currentPassword: changeError.message });
      } else {
        setErrors(toFieldErrors(changeError, CHANGE_PASSWORD_FIELDS, CHANGE_FIELD_ALIASES));
      }
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col px-6 py-10 md:px-10 md:py-14">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.22em] text-[color:var(--flow-accent)]">Account</p>
        <h1 className="font-brand-display mt-3 text-3xl font-bold text-[color:var(--text-strong)] md:text-4xl">Change password</h1>
        <p className="mt-2 text-sm text-[color:var(--text-muted)]">
          Confirm your current password, then choose a new one. You stay signed in on this device
          afterwards.
        </p>
      </header>

      <section className="mt-8 rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)]/90 p-6 shadow-[0_24px_90px_rgba(37,99,235,0.1)] backdrop-blur md:p-8">
        <form className="grid gap-4" onSubmit={handleSubmit} noValidate>
          <AuthField
            label="Current password"
            type="password"
            autoComplete="current-password"
            placeholder="Your current password"
            value={values.currentPassword}
            onValueChange={(currentPassword) => updateValues({ currentPassword })}
            error={errors.currentPassword}
          />

          <AuthField
            label="New password"
            type="password"
            autoComplete="new-password"
            placeholder="At least 8 characters"
            value={values.newPassword}
            onValueChange={(newPassword) => updateValues({ newPassword })}
            error={errors.newPassword}
          />

          <AuthField
            label="Confirm new password"
            type="password"
            autoComplete="new-password"
            placeholder="Repeat the new password"
            value={values.confirmPassword}
            onValueChange={(confirmPassword) => updateValues({ confirmPassword })}
            error={errors.confirmPassword}
          />

          <p className="text-xs text-[color:var(--text-muted)]">
            Changing your password also cancels any reset links you have requested.
          </p>

          {errors.form ? <StateMessage tone="error">{errors.form}</StateMessage> : null}
          {successMessage ? <StateMessage tone="success">{successMessage}</StateMessage> : null}

          <div className="flex items-center justify-between gap-4">
            <Link href="/account/profile" className="text-sm font-medium text-[color:var(--flow-blue)] hover:underline">
              Back to profile
            </Link>
            <button
              type="submit"
              disabled={isSaving}
              className="rounded-xl bg-[color:var(--flow-blue)] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[color:var(--flow-soft-blue)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isSaving ? "Saving..." : "Update password"}
            </button>
          </div>
        </form>
      </section>
    </main>
  );
}
