import type { ReactNode } from "react";
import { AuthGuard } from "@/components/auth/AuthGuard";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { BackofficeNav } from "@/components/common/BackofficeNav";

/**
 * Every route in this group requires a session. The check is client-side on
 * purpose: the token lives in `localStorage`, which Next.js middleware runs too
 * early — and on the wrong side of the network — to read.
 */
export default function ProtectedLayout({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <AuthGuard>
        <BackofficeNav />
        {children}
      </AuthGuard>
    </AuthProvider>
  );
}
