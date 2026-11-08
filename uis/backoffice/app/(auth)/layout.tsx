import type { ReactNode } from "react";
import { BackofficeNavShell } from "@/components/common/BackofficeNavShell";

/**
 * The public screens. They carry the same header as the rest of the backoffice,
 * minus the account controls, since there is no session to act on yet.
 */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <BackofficeNavShell />
      {children}
    </>
  );
}
