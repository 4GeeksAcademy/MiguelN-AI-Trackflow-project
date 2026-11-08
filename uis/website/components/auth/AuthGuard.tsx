"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useSession } from "@/components/auth/AuthProvider";
import { StateMessage } from "@/components/common/StateMessage";
import { LOGIN_PATH } from "@/lib/auth-storage";

/**
 * Renders protected content only once the session is confirmed. While the
 * token is being checked, or while the redirect to `/login` is in flight, it
 * shows a neutral placeholder so no protected data is ever painted.
 *
 * When the check itself failed - the API was down, not the token - it says so
 * and offers a retry, rather than pretending the user was signed out.
 */
export function AuthGuard({ children }: { children: ReactNode }) {
  const { status, error, retry } = useSession();

  if (status === "authenticated") {
    return <>{children}</>;
  }

  if (status === "error") {
    return (
      <main className="mx-auto flex w-full max-w-xl flex-col px-6 py-24">
        <StateMessage tone="error">
          <p className="font-semibold">We could not check your session.</p>
          <p className="mt-1">{error}</p>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={retry}
              className="inline-flex min-h-10 items-center rounded-xl border border-red-300 bg-white px-4 py-2 text-sm font-semibold text-red-700 transition hover:bg-red-100"
            >
              Try again
            </button>
            <Link href={LOGIN_PATH} className="text-sm font-semibold text-[color:var(--flow-blue)] hover:underline">
              Go to sign in
            </Link>
          </div>
        </StateMessage>
      </main>
    );
  }

  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col items-center justify-center px-6 py-24">
      <div
        className="h-8 w-8 animate-spin rounded-full border-2 border-[color:var(--border-soft)] border-t-[color:var(--flow-blue)]"
        aria-hidden="true"
      />
      <p role="status" className="mt-4 text-sm text-[color:var(--text-muted)]">
        {status === "loading" ? "Checking your session..." : "Redirecting to sign in..."}
      </p>
    </main>
  );
}
