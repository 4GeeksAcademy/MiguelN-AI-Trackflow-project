import type { ReactNode } from "react";
import { AuthGuard } from "@/components/auth/AuthGuard";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { SessionBar } from "@/components/auth/SessionBar";

/**
 * Every route in this group requires a session. The corporate page at `/` is
 * deliberately outside it and stays fully public: no token read, no redirect.
 *
 * The check is client-side because the token lives in `localStorage`, which
 * Next.js middleware runs too early — and on the wrong side of the network —
 * to read.
 */
export default function ProtectedLayout({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <AuthGuard>
        <SessionBar />
        {children}
      </AuthGuard>
    </AuthProvider>
  );
}
