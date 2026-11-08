import { IncidentForm } from "@/components/incidents/IncidentForm";
import Link from "next/link";

export default function NewIncidentRoutePage() {
  return (
    <main className="mx-auto w-full max-w-4xl px-6 py-10 md:px-10">
      <div className="space-y-6">
        <section className="rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)]/90 p-6 shadow-[0_24px_90px_rgba(37,99,235,0.12)] backdrop-blur md:p-8">
          <p className="font-mono text-xs uppercase tracking-[0.22em] text-[color:var(--brand-primary)]">
            Incident Manager
          </p>
          <h1 className="mt-3 text-3xl font-semibold leading-tight text-[color:var(--foreground)] md:text-4xl">
            Report an incident.
          </h1>
          <p className="mt-3 max-w-2xl text-sm text-slate-600 md:text-base">
            Anything that went wrong in a warehouse, with a carrier, or with a customer order. Once
            it is here it is logged, categorised and traceable - no more WhatsApp messages that
            nobody can find later.
          </p>
          <p className="mt-4 text-sm">
            <Link
              href="/incidents"
              className="font-semibold text-[color:var(--brand-primary)] underline-offset-4 hover:underline"
            >
              See incidents already registered
            </Link>
          </p>
        </section>

        <section className="rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-6 shadow-[0_16px_38px_-28px_rgba(37,99,235,0.35)] md:p-8">
          <h2 className="text-2xl font-semibold text-[color:var(--foreground)]">Incident details</h2>
          <p className="mt-1 mb-6 text-sm text-slate-600">
            Fields marked <span className="text-[color:var(--brand-primary)]">*</span> are required.
          </p>
          <IncidentForm />
        </section>
      </div>
    </main>
  );
}
