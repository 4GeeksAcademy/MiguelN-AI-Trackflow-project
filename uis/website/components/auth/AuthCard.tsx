import type { ReactNode } from "react";

type AuthCardProps = {
  title: string;
  subtitle: string;
  children: ReactNode;
  footer: ReactNode;
};

/**
 * The shared frame for the sign-in and registration screens. The site header
 * is already rendered by the root layout, so this only frames the form.
 */
export function AuthCard({ title, subtitle, children, footer }: AuthCardProps) {
  return (
    <main className="mx-auto flex w-full max-w-lg flex-col justify-center px-4 py-12 sm:px-6 sm:py-16">
      <section className="rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-7 shadow-[0_24px_90px_rgba(37,99,235,0.12)] sm:p-9">
        <p className="text-xs font-semibold uppercase tracking-[0.22em] text-[color:var(--flow-accent)]">
          Team access
        </p>
        <h1 className="font-brand-display mt-3 text-2xl font-bold text-[color:var(--text-strong)] sm:text-3xl">
          {title}
        </h1>
        <p className="mt-2 text-sm text-[color:var(--text-muted)]">{subtitle}</p>

        {children}
      </section>

      <p className="mt-6 text-center text-sm text-[color:var(--text-muted)]">{footer}</p>
    </main>
  );
}
