import Image from "next/image";
import Link from "next/link";

type OpsMetric = {
  label: string;
  value: string;
  tone: "blue" | "accent";
};

type ServiceCard = {
  title: string;
  description: string;
};

type BenefitCard = {
  eyebrow: string;
  title: string;
  description: string;
  toneClassName: string;
};

const opsMetrics: OpsMetric[] = [
  { label: "Countries in operation", value: "2", tone: "blue" },
  { label: "People across teams", value: "130", tone: "blue" },
  { label: "Annual revenue baseline", value: "EUR 9M", tone: "accent" },
];

const services: ServiceCard[] = [
  {
    title: "Unified Inventory API",
    description:
      "Real-time stock visibility for any SKU across both warehouses and automated low-stock alerts.",
  },
  {
    title: "Carrier Intelligence",
    description:
      "Smart carrier selection and unified tracking based on destination, weight, urgency, and performance.",
  },
  {
    title: "Reverse Logistics Automation",
    description:
      "Rule-based return approvals, automated collections, and AI-assisted product condition classification.",
  },
  {
    title: "Bilingual CX Automation",
    description:
      "Spanish and English first-line support for tracking and returns, backed by ticketing and sentiment insights.",
  },
];

const benefits: BenefitCard[] = [
  {
    eyebrow: "Speed",
    title: "Real-time decision making",
    description:
      "Leadership and operations teams move from weekly manual reports to live KPI visibility.",
    toneClassName: "border-[color:var(--flow-blue)]/30 bg-[color:var(--flow-blue)]/5",
  },
  {
    eyebrow: "Reliability",
    title: "Fewer manual bottlenecks",
    description:
      "Automation in returns, tracking, and customer support reduces repetitive tasks and errors.",
    toneClassName: "border-[color:var(--flow-accent)]/40 bg-orange-50",
  },
  {
    eyebrow: "Scalability",
    title: "Scalable two-country operations",
    description:
      "A shared data pipeline and observability layer enable consistent execution in Mexico and Spain.",
    toneClassName: "border-[color:var(--flow-soft-blue)]/60 bg-blue-50",
  },
];

function SectionFrame({ id, children }: { id?: string; children: React.ReactNode }) {
  return (
    <section id={id} className="mx-auto w-full max-w-[90rem] px-6 pb-20 md:px-10">
      {children}
    </section>
  );
}

function HeroSection() {
  return (
    <section id="home" className="mx-auto grid w-full max-w-[90rem] gap-8 px-6 pb-16 pt-8 md:grid-cols-12 md:px-10">
      <div className="md:col-span-7">
        <p className="mb-4 inline-flex items-center gap-2 rounded-full border border-[color:var(--flow-blue)]/25 bg-[color:var(--flow-blue)]/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.18em] text-[color:var(--flow-blue)]">
          TrackFlow Tech mandate
        </p>
        <h1 className="font-brand-display text-4xl font-bold leading-tight text-[color:var(--text-strong)] sm:text-5xl md:text-6xl">
          Building the operating system
          <span className="block text-[color:var(--flow-blue)]">for logistics in Mexico and Spain</span>
        </h1>
        <p className="mt-6 max-w-2xl text-base leading-relaxed text-[color:var(--text-muted)] sm:text-lg">
          Founded in 2009, TrackFlow runs warehousing and last-mile delivery from Monterrey and Zaragoza. Today,
          TrackFlow Tech is unifying fragmented systems so operations can scale with real-time visibility,
          automation, and smarter decisions.
        </p>

        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Link
            href="#contact"
            className="rounded-full bg-slate-900 px-6 py-3 text-sm font-semibold text-white transition hover:-translate-y-0.5 hover:bg-slate-800"
          >
            See transformation goals
          </Link>
          <Link
            href="/candidates"
            className="rounded-full border border-[color:var(--flow-soft-blue)]/60 bg-white px-6 py-3 text-sm font-semibold text-slate-700 transition hover:-translate-y-0.5 hover:border-[color:var(--flow-blue)] hover:text-[color:var(--flow-blue)]"
          >
            Visit candidate tracker
          </Link>
        </div>

        <div className="mt-10 grid gap-3 sm:grid-cols-3">
          {opsMetrics.map((metric) => (
            <article
              key={metric.label}
              className="rounded-2xl border border-slate-200 bg-white/85 p-4 shadow-sm"
            >
              <p
                className={`text-2xl font-bold ${
                  metric.tone === "accent" ? "text-[color:var(--flow-accent)]" : "text-[color:var(--flow-blue)]"
                }`}
              >
                {metric.value}
              </p>
              <p className="text-sm text-slate-600">{metric.label}</p>
            </article>
          ))}
        </div>
      </div>

      <aside className="md:col-span-5" aria-label="Operations overview">
        <div className="relative overflow-hidden rounded-3xl border border-[color:var(--flow-blue)]/20 bg-gradient-to-br from-[color:var(--flow-blue)] via-blue-700 to-blue-900 p-6 text-white shadow-[0_20px_60px_-28px_rgba(37,99,235,0.35)] sm:p-8">
          <div className="absolute -right-12 -top-12 h-40 w-40 rounded-full bg-white/10 blur-xl" />
          <div className="absolute -bottom-16 -left-10 h-40 w-40 rounded-full bg-[color:var(--flow-accent)]/25 blur-2xl" />

          <h2 className="font-brand-display text-2xl font-bold">Where complexity lives</h2>
          <p className="mt-3 text-sm leading-relaxed text-blue-100">
            Two warehouses, eight carriers, and high returns volume currently depend on manual processes.
            Modernization focuses on unification, observability, and automation.
          </p>

          <dl className="mt-6 space-y-3">
            <div className="flex items-center justify-between rounded-xl border border-white/25 bg-white/10 px-4 py-3 text-sm">
              <dt>Warehouses</dt>
              <dd className="font-semibold">Monterrey + Zaragoza</dd>
            </div>
            <div className="flex items-center justify-between rounded-xl border border-white/25 bg-white/10 px-4 py-3 text-sm">
              <dt>Carrier network</dt>
              <dd className="font-semibold">8 integrated partners</dd>
            </div>
            <div className="flex items-center justify-between rounded-xl border border-white/25 bg-white/10 px-4 py-3 text-sm">
              <dt>Returns volume</dt>
              <dd className="font-semibold">18%-25% of orders</dd>
            </div>
          </dl>
        </div>
      </aside>
    </section>
  );
}

function ServicesSection() {
  return (
    <SectionFrame id="services">
      <div className="rounded-3xl border border-[color:var(--flow-soft-blue)]/40 bg-white/85 p-8 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[color:var(--flow-blue)]">
          What we are building
        </p>
        <h2 className="mt-3 font-brand-display text-3xl font-bold text-[color:var(--text-strong)]">
          Core systems prioritized by TrackFlow Tech
        </h2>
        <div className="mt-8 grid gap-4 md:grid-cols-4">
          {services.map((service) => (
            <article key={service.title} className="rounded-2xl border border-slate-200/80 bg-white p-5">
              <h3 className="font-brand-display text-lg font-semibold text-slate-900">{service.title}</h3>
              <p className="mt-2 text-sm text-slate-600">{service.description}</p>
            </article>
          ))}
        </div>
      </div>
    </SectionFrame>
  );
}

function BenefitsSection() {
  return (
    <SectionFrame id="benefits">
      <div className="grid gap-6 md:grid-cols-12">
        <div className="md:col-span-5">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[color:var(--flow-blue)]">
            Why this matters
          </p>
          <h2 className="mt-3 font-brand-display text-3xl font-bold text-[color:var(--text-strong)]">
            Outcomes expected from modernization
          </h2>
          <p className="mt-4 text-[color:var(--text-muted)]">
            TrackFlow already has strong clients and operations. The gap is infrastructure. These outcomes reduce
            manual work, improve reliability, and support growth across both countries.
          </p>
        </div>
        <div className="md:col-span-7">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {benefits.map((benefit, index) => (
              <article
                key={benefit.title}
                className={`rounded-2xl border p-5 transition hover:-translate-y-[3px] hover:shadow-[0_20px_40px_-30px_rgba(15,23,42,0.45)] ${benefit.toneClassName} ${index === 2 ? "md:col-span-2" : ""}`}
              >
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[color:var(--flow-blue)]">
                  {benefit.eyebrow}
                </p>
                <h3 className="mt-2 font-brand-display text-xl font-bold text-slate-900">{benefit.title}</h3>
                <p className="mt-2 text-sm text-slate-600">{benefit.description}</p>
              </article>
            ))}
          </div>
        </div>
      </div>
    </SectionFrame>
  );
}

function ContactSection() {
  return (
    <SectionFrame id="contact">
      <div className="rounded-3xl border border-[color:var(--flow-blue)]/20 bg-white/90 p-8 shadow-sm md:p-10">
        <div className="grid gap-8 md:grid-cols-2 md:items-center">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[color:var(--flow-blue)]">
              Company snapshot
            </p>
            <h2 className="mt-3 font-brand-display text-3xl font-bold text-[color:var(--text-strong)]">
              TrackFlow is building the next stage of logistics
            </h2>
            <p className="mt-4 text-[color:var(--text-muted)]">
              From warehouse floors to executive dashboards, the mission is clear: unify systems, automate
              intelligently, and operate with real-time confidence.
            </p>
          </div>
          <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50 p-6 text-sm">
            <p>
              <span className="font-semibold text-slate-900">Founded:</span> 2009
            </p>
            <p>
              <span className="font-semibold text-slate-900">Headquarters:</span> Monterrey, Mexico
            </p>
            <p>
              <span className="font-semibold text-slate-900">Tech Office:</span> Zaragoza, Spain
            </p>
            <p>
              <span className="font-semibold text-slate-900">Current footprint:</span> Warehousing + last-mile in two
              countries
            </p>
          </div>
        </div>

        <div className="mt-8 grid gap-6 md:grid-cols-2">
          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-6 text-sm">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[color:var(--flow-blue)]">
              Direct contact
            </p>
            <h3 className="mt-2 font-brand-display text-xl font-bold text-slate-900">Need to reach TrackFlow?</h3>
            <p className="mt-3 text-slate-600">
              Contact our team directly and we will get back with the right department for your request.
            </p>
            <div className="mt-4 space-y-2">
              <p>
                <span className="font-semibold text-slate-900">Phone:</span> +52 81 5555 0146
              </p>
              <p>
                <span className="font-semibold text-slate-900">Email:</span> hello@trackflowlogistics.com
              </p>
            </div>
          </div>

          <form
            className="rounded-2xl border border-slate-200 bg-slate-50 p-6"
            action="#"
            method="post"
            aria-label="Contact callback form"
          >
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[color:var(--flow-blue)]">
              Request a callback
            </p>
            <h3 className="mt-2 font-brand-display text-xl font-bold text-slate-900">Share your details</h3>

            <div className="mt-4 grid gap-3">
              <label className="grid gap-1 text-sm font-medium text-slate-700" htmlFor="lead-name">
                Name
                <input
                  id="lead-name"
                  name="name"
                  type="text"
                  required
                  className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-[color:var(--flow-blue)] focus:ring-2 focus:ring-[color:var(--flow-blue)]/20"
                  placeholder="Your full name"
                />
              </label>

              <label className="grid gap-1 text-sm font-medium text-slate-700" htmlFor="lead-email">
                Email
                <input
                  id="lead-email"
                  name="email"
                  type="email"
                  required
                  className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-[color:var(--flow-blue)] focus:ring-2 focus:ring-[color:var(--flow-blue)]/20"
                  placeholder="you@company.com"
                />
              </label>

              <label className="grid gap-1 text-sm font-medium text-slate-700" htmlFor="lead-phone">
                Phone number
                <input
                  id="lead-phone"
                  name="phone"
                  type="tel"
                  required
                  className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-[color:var(--flow-blue)] focus:ring-2 focus:ring-[color:var(--flow-blue)]/20"
                  placeholder="+52 81 5555 0146"
                />
              </label>
            </div>

            <button
              type="submit"
              className="mt-4 inline-flex items-center justify-center rounded-full bg-[color:var(--flow-blue)] px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-700"
            >
              Contact me back
            </button>
          </form>
        </div>
      </div>
    </SectionFrame>
  );
}

function CorporateFooter() {
  return (
    <footer className="border-t border-[color:var(--flow-blue)]/15 bg-white/90">
      <div className="mx-auto grid w-full max-w-[90rem] gap-8 px-6 py-10 md:grid-cols-3 md:px-10">
        <div>
          <div className="flex items-center gap-3">
            <Image
              src="/trackflow-logo.png"
              alt="TrackFlow logo symbol"
              width={40}
              height={40}
              className="h-10 w-10 rounded-xl object-cover ring-1 ring-[color:var(--flow-blue)]/20"
            />
            <p className="font-brand-display text-lg font-bold text-[color:var(--text-strong)]">TrackFlow Inc.</p>
          </div>
          <p className="mt-3 text-sm text-slate-600">
            Last-mile delivery and warehouse management, now evolving through TrackFlow Tech across Mexico and Spain.
          </p>
        </div>
        <div>
          <p className="font-brand-display text-base font-bold text-[color:var(--text-strong)]">Quick links</p>
          <ul className="mt-3 space-y-2 text-sm text-slate-600">
            <li>
              <Link href="#services" className="transition hover:text-[color:var(--flow-blue)]">
                Services
              </Link>
            </li>
            <li>
              <Link href="#benefits" className="transition hover:text-[color:var(--flow-blue)]">
                Benefits
              </Link>
            </li>
            <li>
              <Link href="#contact" className="transition hover:text-[color:var(--flow-blue)]">
                Contact
              </Link>
            </li>
          </ul>
        </div>
        <div>
          <p className="font-brand-display text-base font-bold text-[color:var(--text-strong)]">Motto</p>
          <p className="mt-3 rounded-xl border border-[color:var(--flow-accent)]/40 bg-orange-50 px-4 py-3 text-sm font-semibold text-slate-700">
            &quot;Faster routes, smarter deliveries&quot;
          </p>
        </div>
      </div>
      <div className="border-t border-[color:var(--flow-blue)]/10 py-4 text-center text-xs text-slate-500">
        © 2026 TrackFlow Inc. All rights reserved.
      </div>
    </footer>
  );
}

export function CorporatePage() {
  return (
    <main>
      <HeroSection />
      <ServicesSection />
      <BenefitsSection />
      <ContactSection />
      <CorporateFooter />
    </main>
  );
}