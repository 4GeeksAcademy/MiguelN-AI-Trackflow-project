"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { AuthCard } from "@/components/auth/AuthCard";
import { AuthField } from "@/components/auth/AuthField";
import { StateMessage } from "@/components/common/StateMessage";
import {
  emptyResetPasswordFormValues,
  toFieldErrors,
  validateResetPasswordForm,
} from "@/lib/auth";
import {
  FORGOT_PASSWORD_PATH,
  LOGIN_AFTER_RESET_PATH,
  LOGIN_PATH,
  readResetToken,
} from "@/lib/auth-storage";
import { useLocationSearch } from "@/hooks/useLocationSearch";
import { InvalidResetTokenError, resetPassword } from "@/services/auth-service";
import type { FieldErrors, ResetPasswordField, ResetPasswordFormValues } from "@/types/auth";

const RESET_PASSWORD_FIELDS: readonly ResetPasswordField[] = ["newPassword", "confirmPassword"];

/** The API names the field `new_password`; this form calls it `newPassword`. */
const RESET_FIELD_ALIASES: Readonly<Record<string, ResetPasswordField>> = {
  new_password: "newPassword",
};

const MISSING_TOKEN_MESSAGE =
  "This page needs the link from your reset email. Open the email and follow the button there, or request a new link.";

/**
 * Sets a new password from an emailed link.
 *
 * The token comes off the query string through `useLocationSearch`, which reads
 * it without putting this page behind a Suspense boundary. Until that resolves
 * on the client the page says it is checking, rather than flashing the
 * "link is missing" error at someone whose link is fine.
 */
export default function ResetPasswordPage() {
  const router = useRouter();
  const search = useLocationSearch();
  const isReadingToken = search === null;
  const token = search === null ? null : readResetToken(search);
  const [values, setValues] = useState<ResetPasswordFormValues>(emptyResetPasswordFormValues());
  const [errors, setErrors] = useState<FieldErrors<ResetPasswordField>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [rejectionMessage, setRejectionMessage] = useState<string | null>(null);

  function updateValues(patch: Partial<ResetPasswordFormValues>): void {
    setErrors({});
    setValues((currentValues) => ({ ...currentValues, ...patch }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!token) {
      return;
    }

    const validationErrors = validateResetPasswordForm(values);
    if (validationErrors) {
      setErrors(validationErrors);
      return;
    }

    setIsSubmitting(true);
    setErrors({});
    let isLeaving = false;

    try {
      await resetPassword(token, values.newPassword);
      isLeaving = true;
      router.replace(LOGIN_AFTER_RESET_PATH);
    } catch (resetError) {
      if (resetError instanceof InvalidResetTokenError) {
        // The token is spent or expired. Resubmitting cannot help, so the form
        // is replaced with a way to start over rather than left to be retried.
        // The message was already passed through `describeError` in the service.
        setRejectionMessage(resetError.message);
      } else {
        setErrors(toFieldErrors(resetError, RESET_PASSWORD_FIELDS, RESET_FIELD_ALIASES));
      }
    } finally {
      if (!isLeaving) {
        setIsSubmitting(false);
      }
    }
  }

  const blockingMessage = rejectionMessage ?? (!isReadingToken && !token ? MISSING_TOKEN_MESSAGE : null);

  return (
    <AuthCard
      title="Set a new password"
      subtitle="Choose a new password for your TrackFlow account."
      footer={
        <>
          Know your password?{" "}
          <Link href={LOGIN_PATH} className="font-semibold text-[color:var(--flow-blue)] hover:underline">
            Back to sign in
          </Link>
        </>
      }
    >
      {isReadingToken ? (
        <StateMessage tone="info" className="mt-6">
          Checking your reset link...
        </StateMessage>
      ) : blockingMessage ? (
        <div className="mt-6 grid gap-4">
          <StateMessage tone="error">{blockingMessage}</StateMessage>
          <Link
            href={FORGOT_PASSWORD_PATH}
            className="rounded-xl bg-[color:var(--flow-blue)] px-4 py-2.5 text-center text-sm font-semibold text-white transition hover:bg-[color:var(--flow-soft-blue)]"
          >
            Request a new link
          </Link>
        </div>
      ) : (
        <form className="mt-6 grid gap-4" onSubmit={handleSubmit} noValidate>
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

          {errors.form ? <StateMessage tone="error">{errors.form}</StateMessage> : null}

          <button
            type="submit"
            disabled={isSubmitting}
            className="mt-2 rounded-xl bg-[color:var(--flow-blue)] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[color:var(--flow-soft-blue)] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isSubmitting ? "Saving..." : "Set new password"}
          </button>
        </form>
      )}
    </AuthCard>
  );
}
