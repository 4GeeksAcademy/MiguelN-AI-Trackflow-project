"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const NAV_LINKS = [
  { href: "/", label: "Dashboard" },
  // Leadership's destination: the weekly KPIs, read from the reporting pipeline.
  { href: "/reporting", label: "Weekly report" },
  { href: "/incidents", label: "Incidents" },
  // Its own entry rather than a button buried in the list: the people the
  // incident manager is for are reporting from a warehouse terminal, and the
  // form is the destination they arrive wanting.
  { href: "/incidents/new", label: "Report incident" },
  { href: "/suppliers", label: "Suppliers" },
  { href: "/backoffice/inventory/products", label: "Products" },
  { href: "/backoffice/inventory/orders", label: "Orders" },
  { href: "/backoffice/inventory/orders/inbound", label: "Inbound order" },
  { href: "/backoffice/inventory/orders/outbound", label: "Outbound order" },
  { href: "/account/profile", label: "Profile" },
] as const;

function matchesLink(pathname: string, href: string): boolean {
  if (href === "/") {
    return pathname === "/";
  }

  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * The href of the one link to mark as current, or null.
 *
 * The longest match wins, because `/incidents` is a prefix of
 * `/incidents/new` and both are in the bar: a plain prefix test would mark two
 * links `aria-current="page"` at once, which tells a screen reader the page is
 * in two places.
 */
function activeHref(pathname: string): string | null {
  return NAV_LINKS.map((link) => link.href)
    .filter((href) => matchesLink(pathname, href))
    .reduce<string | null>(
      (best, href) => (best === null || href.length > best.length ? href : best),
      null,
    );
}

type BackofficeNavShellProps = {
  /** Session controls, when there is a session. Absent on the sign-in screens. */
  actions?: ReactNode;
};

/**
 * The header bar itself: wordmark and destinations, with no session of its own.
 *
 * Rendered on the sign-in and registration screens as-is, and by
 * `BackofficeNav` with the account controls filled in. On the auth screens the
 * destinations still point at protected routes; following one simply returns
 * the visitor to `/login`.
 */
export function BackofficeNavShell({ actions }: BackofficeNavShellProps) {
  const pathname = usePathname();
  const currentHref = activeHref(pathname);

  return (
    <header className="sticky top-0 z-30 border-b border-[color:var(--border-soft)] bg-[color:var(--surface)]/85 backdrop-blur">
      <nav
        aria-label="Backoffice"
        className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-3 px-6 py-3 md:px-10"
      >
        <Link href="/" className="flex flex-col">
          <span className="font-mono text-[0.65rem] uppercase tracking-[0.22em] text-[color:var(--brand-primary)]">
            TrackFlow
          </span>
          <span className="text-base font-semibold leading-tight text-[color:var(--foreground)]">Backoffice</span>
        </Link>

        <div className="flex flex-wrap items-center gap-3">
          <ul className="flex flex-wrap items-center gap-1">
            {NAV_LINKS.map((link) => {
              const isActive = link.href === currentHref;

              return (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    aria-current={isActive ? "page" : undefined}
                    className={`block rounded-xl px-3 py-2 text-sm font-semibold transition ${
                      isActive
                        ? "bg-[color:var(--brand-primary)]/10 text-[color:var(--brand-primary)]"
                        : "text-slate-600 hover:bg-[color:var(--brand-primary)]/5 hover:text-[color:var(--brand-primary)]"
                    }`}
                  >
                    {link.label}
                  </Link>
                </li>
              );
            })}
          </ul>

          {actions ? (
            <div className="flex items-center gap-2 border-l border-[color:var(--border-soft)] pl-3">{actions}</div>
          ) : null}
        </div>
      </nav>
    </header>
  );
}
