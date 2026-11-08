"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession } from "@/components/auth/AuthProvider";

const PROFILE_PATH = "/account/profile";

/**
 * The account strip shown above every session-protected view. It sits below
 * the public site header, which stays free of any session logic.
 */
export function SessionBar() {
  const pathname = usePathname();
  const { user, signOut } = useSession();

  return (
    <div className="border-b border-[color:var(--border-soft)] bg-[color:var(--surface)]">
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-2 sm:px-6">
        <p className="text-xs text-[color:var(--text-muted)]">
          Signed in{user ? " as " : ""}
          {user ? <span className="font-semibold text-[color:var(--text-strong)]">{user.email}</span> : null}
        </p>

        <div className="flex items-center gap-2">
          <Link
            href={PROFILE_PATH}
            aria-current={pathname === PROFILE_PATH ? "page" : undefined}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              pathname === PROFILE_PATH
                ? "bg-[color:var(--flow-blue)]/10 text-[color:var(--flow-blue)]"
                : "text-[color:var(--text-muted)] hover:text-[color:var(--flow-blue)]"
            }`}
          >
            Profile
          </Link>
          <button
            type="button"
            onClick={signOut}
            className="rounded-lg border border-[color:var(--border-soft)] px-3 py-1.5 text-xs font-semibold text-[color:var(--text-muted)] transition hover:border-[color:var(--flow-blue)] hover:text-[color:var(--flow-blue)]"
          >
            Log out
          </button>
        </div>
      </div>
    </div>
  );
}
