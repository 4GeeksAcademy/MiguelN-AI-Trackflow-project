"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { AuthField } from "@/components/auth/AuthField";
import { useSession } from "@/components/auth/AuthProvider";
import { StateMessage } from "@/components/common/StateMessage";
import { profileFormValuesFrom, toFieldErrors, validateProfileForm } from "@/lib/auth";
import { updateMyProfile } from "@/services/auth-service";
import type { FieldErrors, ProfileField, ProfileFormValues } from "@/types/auth";

const PROFILE_FIELDS: readonly ProfileField[] = ["name", "phone", "address"];

/** Read-only account facts come from `GET /auth/me`; edits go to `PUT /profiles/me`. */
export default function ProfilePage() {
  const { user, applyProfile } = useSession();
  const [values, setValues] = useState<ProfileFormValues>(profileFormValuesFrom(user?.profile ?? null));
  const [errors, setErrors] = useState<FieldErrors<ProfileField>>({});
  const [isSaving, setIsSaving] = useState(false);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // `AuthGuard` mounts this page only once the session resolved, so the initial
  // state above is already built from the loaded profile.
  const profileId = user?.profile?.id ?? null;

  function updateValues(patch: Partial<ProfileFormValues>): void {
    setErrors({});
    setSuccessMessage(null);
    setValues((currentValues) => ({ ...currentValues, ...patch }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const validationErrors = validateProfileForm(values);
    if (validationErrors) {
      setErrors(validationErrors);
      setSuccessMessage(null);
      return;
    }

    setIsSaving(true);
    setErrors({});
    setSuccessMessage(null);

    try {
      applyProfile(await updateMyProfile(values));
      setSuccessMessage("Your profile has been updated.");
    } catch (updateError) {
      setErrors(toFieldErrors(updateError, PROFILE_FIELDS));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col px-6 py-10 md:px-10 md:py-14">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.22em] text-[color:var(--flow-accent)]">Account</p>
        <h1 className="font-brand-display mt-3 text-3xl font-bold text-[color:var(--text-strong)] md:text-4xl">
          Your profile
        </h1>
        <p className="mt-2 text-sm text-[color:var(--text-muted)]">
          Your email and role identify the account and cannot be changed here. Name and contact details are stored on
          your profile.
        </p>
      </header>

      <section className="mt-8 rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)]/90 p-6 shadow-[0_24px_90px_rgba(37,99,235,0.1)] backdrop-blur md:p-8">
        <h2 className="text-xs font-semibold uppercase tracking-[0.18em] text-[color:var(--text-muted)]">Account</h2>
        <dl className="mt-4 grid gap-4 sm:grid-cols-2">
          <div>
            <dt className="text-xs uppercase tracking-[0.14em] text-[color:var(--text-muted)]">Email</dt>
            <dd className="mt-1 text-sm text-[color:var(--text-strong)]">{user?.email ?? "-"}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-[0.14em] text-[color:var(--text-muted)]">Role</dt>
            <dd className="mt-1 text-sm capitalize text-[color:var(--text-strong)]">{user?.role ?? "-"}</dd>
          </div>
        </dl>

        <p className="mt-5 text-sm">
          <Link
            href="/account/change-password"
            className="font-semibold text-[color:var(--flow-blue)] hover:underline"
          >
            Change password
          </Link>
        </p>
      </section>

      <section className="mt-6 rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)]/90 p-6 shadow-[0_24px_90px_rgba(37,99,235,0.1)] backdrop-blur md:p-8">
        <h2 className="text-xs font-semibold uppercase tracking-[0.18em] text-[color:var(--text-muted)]">Profile</h2>

        {profileId ? (
          <form className="mt-4 grid gap-4" onSubmit={handleSubmit} noValidate>
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

            <p className="text-xs text-[color:var(--text-muted)]">Clearing a field removes it from your profile.</p>

            {errors.form ? <StateMessage tone="error">{errors.form}</StateMessage> : null}
            {successMessage ? <StateMessage tone="success">{successMessage}</StateMessage> : null}

            <div className="flex justify-end">
              <button
                type="submit"
                disabled={isSaving}
                className="rounded-xl bg-[color:var(--flow-blue)] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[color:var(--flow-soft-blue)] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isSaving ? "Saving..." : "Save changes"}
              </button>
            </div>
          </form>
        ) : (
          <StateMessage tone="info" className="mt-4">
            No profile is linked to this account, so there is nothing to edit yet. Contact TrackFlow Tech
            and they can link one for you.
          </StateMessage>
        )}
      </section>
    </main>
  );
}
