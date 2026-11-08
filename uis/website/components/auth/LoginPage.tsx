"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { AuthCard } from "@/components/auth/AuthCard";
import { AuthField } from "@/components/auth/AuthField";
import { StateMessage } from "@/components/common/StateMessage";
import { emptyLoginFormValues, toFieldErrors, validateLoginForm } from "@/lib/auth";
import {
  DEFAULT_AUTHENTICATED_PATH,
  FORGOT_PASSWORD_PATH,
  hasRegisteredFlag,
  hasResetSuccessFlag,
  sanitizeNextPath,
} from "@/lib/auth-storage";
import { useLocationSearch } from "@/hooks/useLocationSearch";
import { login } from "@/services/auth-service";
import type { FieldErrors, LoginField, LoginFormValues } from "@/types/auth";

const LOGIN_FIELDS: readonly LoginField[] = ["email", "password"];

/**
 * Where to land after signing in. Read from the URL at submit time rather than
 * through `useSearchParams`, which would force this page behind a Suspense
 * boundary at build time.
 */
function resolveNextPath(): string {
  const requested = new URLSearchParams(window.location.search).get("next");

  return sanitizeNextPath(requested) ?? DEFAULT_AUTHENTICATED_PATH;
}

export default function LoginPage() {
  const router = useRouter();
  const [values, setValues] = useState<LoginFormValues>(emptyLoginFormValues());
  const [errors, setErrors] = useState<FieldErrors<LoginField>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Set by the redirect out of `/reset-password`.
  const search = useLocationSearch();
  const showResetSuccess = search !== null && hasResetSuccessFlag(search);
  // Set by `/register` when the account was created but the automatic sign-in failed.
  const showRegistered = search !== null && hasRegisteredFlag(search);

  function updateValues(patch: Partial<LoginFormValues>): void {
    setErrors({});
    setValues((currentValues) => ({ ...currentValues, ...patch }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const validationErrors = validateLoginForm(values);
    if (validationErrors) {
      setErrors(validationErrors);
      return;
    }

    setIsSubmitting(true);
    setErrors({});
    let isLeaving = false;

    try {
      await login(values);
      isLeaving = true;
      router.replace(resolveNextPath());
    } catch (loginError) {
      // The API answers a wrong email and a wrong password identically, so the
      // message belongs to the form rather than to either input.
      setErrors(toFieldErrors(loginError, LOGIN_FIELDS));
    } finally {
      // Stays locked only while the page is navigating away, so a second click
      // cannot sign in twice; every other outcome hands the form back.
      if (!isLeaving) {
        setIsSubmitting(false);
      }
    }
  }

  return (
    <AuthCard
      title="Sign in"
      subtitle="Sign in to reach the talent pipeline. The public site stays open to everyone."
      footer={
        <>
          No account yet?{" "}
          <Link href="/register" className="font-semibold text-[color:var(--flow-blue)] hover:underline">
            Create one
          </Link>
        </>
      }
    >
      <form className="mt-6 grid gap-4" onSubmit={handleSubmit} noValidate>
        {showResetSuccess ? (
          <StateMessage tone="success">
            Your password has been reset. Sign in with your new password.
          </StateMessage>
        ) : null}

        {showRegistered ? (
          <StateMessage tone="success">
            Your account was created, but we could not sign you in automatically. Sign in with the email and
            password you just chose.
          </StateMessage>
        ) : null}

        <AuthField
          label="Email"
          type="email"
          autoComplete="email"
          placeholder="you@trackflow.com"
          value={values.email}
          onValueChange={(email) => updateValues({ email })}
          error={errors.email}
        />

        <AuthField
          label="Password"
          type="password"
          autoComplete="current-password"
          placeholder="Your password"
          value={values.password}
          onValueChange={(password) => updateValues({ password })}
          error={errors.password}
        />

        <div className="-mt-1 flex justify-end">
          <Link
            href={FORGOT_PASSWORD_PATH}
            className="text-xs font-medium text-[color:var(--flow-blue)] hover:underline"
          >
            Forgot your password?
          </Link>
        </div>

        {errors.form ? <StateMessage tone="error">{errors.form}</StateMessage> : null}

        <button
          type="submit"
          disabled={isSubmitting}
          className="mt-2 rounded-xl bg-[color:var(--flow-blue)] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[color:var(--flow-soft-blue)] disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isSubmitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </AuthCard>
  );
}
