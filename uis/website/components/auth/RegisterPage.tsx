"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { AuthCard } from "@/components/auth/AuthCard";
import { AuthField } from "@/components/auth/AuthField";
import { StateMessage } from "@/components/common/StateMessage";
import { ApiError } from "@/lib/auth-api-client";
import { GENERAL_ERROR_COPY, describeError } from "@/lib/friendly-error";
import {
  emptyRegisterFormValues,
  MIN_PASSWORD_LENGTH,
  toFieldErrors,
  validateRegisterForm,
} from "@/lib/auth";
import { buildLoginAfterRegisterUrl, DEFAULT_AUTHENTICATED_PATH, LOGIN_PATH } from "@/lib/auth-storage";
import { PostRegistrationLoginError, register } from "@/services/auth-service";
import type { FieldErrors, RegisterField, RegisterFormValues } from "@/types/auth";

/** The inputs the API can name in a 422. `confirmPassword` is client-only. */
const REGISTER_FIELDS: readonly RegisterField[] = ["email", "password", "name", "phone", "address"];

export default function RegisterPage() {
  const router = useRouter();
  const [values, setValues] = useState<RegisterFormValues>(emptyRegisterFormValues());
  const [errors, setErrors] = useState<FieldErrors<RegisterField>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  function updateValues(patch: Partial<RegisterFormValues>): void {
    setErrors({});
    setValues((currentValues) => ({ ...currentValues, ...patch }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const validationErrors = validateRegisterForm(values);
    if (validationErrors) {
      setErrors(validationErrors);
      return;
    }

    setIsSubmitting(true);
    setErrors({});
    let isLeaving = false;

    try {
      await register(values);
      isLeaving = true;
      router.replace(DEFAULT_AUTHENTICATED_PATH);
    } catch (registerError) {
      if (registerError instanceof PostRegistrationLoginError) {
        // The account exists; only the automatic sign-in failed. The login page
        // says so, so the user does not try to register a second time.
        console.error(registerError);
        isLeaving = true;
        router.replace(buildLoginAfterRegisterUrl(DEFAULT_AUTHENTICATED_PATH));
        return;
      }

      if (registerError instanceof ApiError && registerError.status === 409) {
        // `POST /users` reports a taken email as a plain-string 409 detail.
        setErrors({
          email: describeError(
            registerError,
            "An account with this email address already exists.",
            GENERAL_ERROR_COPY,
          ).message,
        });
      } else {
        setErrors(toFieldErrors(registerError, REGISTER_FIELDS));
      }
    } finally {
      if (!isLeaving) {
        setIsSubmitting(false);
      }
    }
  }

  return (
    <AuthCard
      title="Create your account"
      subtitle="Registration creates your credentials and your contact profile in one step."
      footer={
        <>
          Already registered?{" "}
          <Link href={LOGIN_PATH} className="font-semibold text-[color:var(--flow-blue)] hover:underline">
            Sign in
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
        />

        <AuthField
          label="Password"
          type="password"
          autoComplete="new-password"
          placeholder="At least 8 characters"
          hint={`Minimum ${MIN_PASSWORD_LENGTH} characters.`}
          value={values.password}
          onValueChange={(password) => updateValues({ password })}
          error={errors.password}
        />

        <AuthField
          label="Confirm password"
          type="password"
          autoComplete="new-password"
          placeholder="Repeat your password"
          value={values.confirmPassword}
          onValueChange={(confirmPassword) => updateValues({ confirmPassword })}
          error={errors.confirmPassword}
        />

        <fieldset className="mt-2 grid gap-4 rounded-2xl border border-dashed border-[color:var(--border-soft)] p-4">
          <legend className="px-2 text-xs font-semibold uppercase tracking-[0.18em] text-[color:var(--text-muted)]">
            Profile (optional)
          </legend>

          <AuthField
            label="Full name"
            autoComplete="name"
            placeholder="Ana Whitfield"
            value={values.name}
            onValueChange={(name) => updateValues({ name })}
            error={errors.name}
          />

          <AuthField
            label="Phone"
            type="tel"
            autoComplete="tel"
            placeholder="+34 600 000 000"
            value={values.phone}
            onValueChange={(phone) => updateValues({ phone })}
            error={errors.phone}
          />

          <AuthField
            label="Address"
            autoComplete="street-address"
            placeholder="Calle Ejemplo 1, Zaragoza"
            value={values.address}
            onValueChange={(address) => updateValues({ address })}
            error={errors.address}
          />
        </fieldset>

        {errors.form ? <StateMessage tone="error">{errors.form}</StateMessage> : null}

        <button
          type="submit"
          disabled={isSubmitting}
          className="mt-2 rounded-xl bg-[color:var(--flow-blue)] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[color:var(--flow-soft-blue)] disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isSubmitting ? "Creating account..." : "Create account"}
        </button>
      </form>
    </AuthCard>
  );
}
