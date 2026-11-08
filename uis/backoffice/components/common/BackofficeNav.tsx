"use client";

import { useSession } from "@/components/auth/AuthProvider";
import { BackofficeNavShell } from "@/components/common/BackofficeNavShell";

/** The header for signed-in views: the shared bar plus the account controls. */
export function BackofficeNav() {
  const { user, signOut } = useSession();

  return (
    <BackofficeNavShell
      actions={
        <>
          {user ? (
            <span className="hidden max-w-[16rem] truncate font-mono text-xs text-slate-500 sm:block" title={user.email}>
              {user.email}
            </span>
          ) : null}
          <button
            type="button"
            onClick={signOut}
            className="rounded-xl border border-[color:var(--border-soft)] px-3 py-2 text-sm font-semibold text-slate-600 transition hover:border-[color:var(--brand-primary)] hover:text-[color:var(--brand-primary)]"
          >
            Log out
          </button>
        </>
      }
    />
  );
}
