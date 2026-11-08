"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { AuthCard } from "@/components/auth/AuthCard";
import { AuthField } from "@/components/auth/AuthField";
import { StateMessage } from "@/components/common/StateMessage";
import {
  emptyForgotPasswordFormValues,
  toFieldErrors,
  validateForgotPasswordForm,
} from "@/lib/auth";
import { LOGIN_PATH } from "@/lib/auth-storage";
import { requestPasswordReset } from "@/services/auth-service";
import type { FieldErrors, ForgotPasswordField, ForgotPasswordFormValues } from "@/types/auth";

const FORGOT_PASSWORD_FIELDS: readonly ForgotPasswordField[] = ["email"];

/**
 * Shown after every successful submit, whether or not the address has an
 * account. `POST /auth/forgot-password` answers the two cases identically on
 * purpose; wording anything more specific here would hand back the very
 * difference the API withholds.
 */
const CONFIRMATION_MESSAGE =
  "If that address is registered, you'll receive a link shortly. Check your inbox, and your spam folder.";

export default function ForgotPasswordPage() {
  const [values, setValues] = useState<ForgotPasswordFormValues>(emptyForgotPasswordFormValues());
  const [errors, setErrors] = useState<FieldErrors<ForgotPasswordField>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isSent, setIsSent] = useState(false);

  function updateValues(patch: Partial<ForgotPasswordFormValues>): void {
    setErrors({});
    setValues((currentValues) => ({ ...currentValues, ...patch }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (isSent) {
      return;
    }

    const validationErrors = validateForgotPasswordForm(values);
    if (validationErrors) {
      setErrors(validationErrors);
      return;
    }

    setIsSubmitting(true);
    setErrors({});

    try {
      await requestPasswordReset(values);
      // Locked from here on, so an impatient second click cannot queue a
      // second email. A reload is the way to ask for another link.
      setIsSent(true);
    } catch (requestError) {
      // Only a malformed payload or an unreachable API can land here: the route
      // does not fail for an unknown address. Re-enabling the form is therefore
      // safe - it says nothing about whether the address exists.
      setErrors(toFieldErrors(requestError, FORGOT_PASSWORD_FIELDS));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthCard
      title="Forgot your password?"
      subtitle="Enter the email address on your account and we'll send you a link to set a new password."
      footer={
        <>
          Remembered it?{" "}
          <Link href={LOGIN_PATH} className="font-semibold text-[color:var(--flow-blue)] hover:underline">
            Back to sign in
          </Link>
        </>
      }
    >
      <form className="mt-6 grid gap-4" onSubmit={handleSubmit} noValidate>
        <AuthField
          label="Email"
          type="email"
          autoComplete="email"
          placeholder="you@trackflow.com"
          value={values.email}
          onValueChange={(email) => updateValues({ email })}
          error={errors.email}
          disabled={isSent}
        />

        {errors.form ? <StateMessage tone="error">{errors.form}</StateMessage> : null}

        {isSent ? (
          <>
            <StateMessage tone="success">{CONFIRMATION_MESSAGE}</StateMessage>
            <p className="text-xs text-[color:var(--text-muted)]">
              Nothing arrived? Reload this page to request another link.
            </p>
          </>
        ) : null}

        <button
          type="submit"
          disabled={isSubmitting || isSent}
          className="mt-2 rounded-xl bg-[color:var(--flow-blue)] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[color:var(--flow-soft-blue)] disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isSent ? "Link sent" : isSubmitting ? "Sending..." : "Send reset link"}
        </button>
      </form>
    </AuthCard>
  );
}
