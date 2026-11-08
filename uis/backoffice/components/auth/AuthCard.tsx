import type { ReactNode } from "react";

type AuthCardProps = {
  title: string;
  subtitle: string;
  children: ReactNode;
  footer: ReactNode;
};

/** The shared frame for the unauthenticated screens: sign in and registration. */
export function AuthCard({ title, subtitle, children, footer }: AuthCardProps) {
  return (
    <main className="mx-auto flex w-full max-w-lg flex-col justify-center px-6 py-12 md:py-16">
      <section className="rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)]/90 p-7 shadow-[0_24px_90px_rgba(37,99,235,0.12)] backdrop-blur md:p-9">
        <h1 className="text-2xl font-semibold text-[color:var(--foreground)] md:text-3xl">{title}</h1>
        <p className="mt-2 text-sm text-slate-600">{subtitle}</p>

        {children}
      </section>

      <p className="mt-6 text-center text-sm text-slate-600">{footer}</p>
    </main>
  );
}
