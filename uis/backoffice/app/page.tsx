import { BackofficeConsoleBeacon } from "@/components/BackofficeConsoleBeacon";

const RETURNS_SHARE_MIN_PERCENT = 18;
const RETURNS_SHARE_MAX_PERCENT = 25;

export default function BackofficeHomePage() {
  const returnsMidpointPercent = Number(
    ((RETURNS_SHARE_MIN_PERCENT + RETURNS_SHARE_MAX_PERCENT) / 2).toFixed(1),
  );
  const returnsSpreadPercent = RETURNS_SHARE_MAX_PERCENT - RETURNS_SHARE_MIN_PERCENT;

  const contextMessage =
    "From CONTEXT.md: Reverse Logistics returns represent 18-25% of total volume depending on client and country.";

  console.info(
    `[Backoffice] Reverse Logistics baseline loaded in server runtime -> range: ${RETURNS_SHARE_MIN_PERCENT}-${RETURNS_SHARE_MAX_PERCENT}%, midpoint: ${returnsMidpointPercent}%.`,
  );

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-5xl flex-col justify-center px-6 py-12 md:px-10">
      <BackofficeConsoleBeacon
        message={`[Backoffice] Client console -> returns range ${RETURNS_SHARE_MIN_PERCENT}-${RETURNS_SHARE_MAX_PERCENT}% (midpoint ${returnsMidpointPercent}%).`}
      />

      <section className="rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)]/90 p-8 shadow-[0_24px_90px_rgba(37,99,235,0.12)] backdrop-blur md:p-10">
        <p className="font-mono text-xs uppercase tracking-[0.22em] text-[color:var(--brand-primary)]">
          TrackFlow Internal Backoffice
        </p>
        <h1 className="mt-4 text-3xl font-semibold leading-tight text-[color:var(--foreground)] md:text-5xl">
          Welcome to the operations control surface.
        </h1>
        <p className="mt-4 max-w-3xl text-base text-slate-600 md:text-lg">
          This private interface starts with one operational truth extracted from company context so every team can anchor decisions in the same baseline.
        </p>

        <div className="mt-8 grid gap-4 md:grid-cols-3">
          <article className="rounded-2xl border border-[color:var(--border-soft)] bg-blue-50/80 p-5">
            <p className="text-xs uppercase tracking-[0.18em] text-blue-700">Returns Range</p>
            <p className="mt-2 text-3xl font-semibold text-blue-900">{RETURNS_SHARE_MIN_PERCENT}% - {RETURNS_SHARE_MAX_PERCENT}%</p>
          </article>
          <article className="rounded-2xl border border-[color:var(--border-soft)] bg-orange-50/80 p-5">
            <p className="text-xs uppercase tracking-[0.18em] text-orange-700">Midpoint</p>
            <p className="mt-2 text-3xl font-semibold text-orange-900">{returnsMidpointPercent}%</p>
          </article>
          <article className="rounded-2xl border border-[color:var(--border-soft)] bg-slate-50 p-5">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-700">Variability</p>
            <p className="mt-2 text-3xl font-semibold text-slate-900">{returnsSpreadPercent} pts</p>
          </article>
        </div>

        <div className="mt-8 rounded-2xl border border-dashed border-[color:var(--border-soft)] bg-white p-5">
          <p className="font-mono text-xs uppercase tracking-[0.18em] text-slate-500">Context Source</p>
          <p className="mt-2 text-sm leading-relaxed text-slate-700">{contextMessage}</p>
        </div>
      </section>
    </main>
  );
}
