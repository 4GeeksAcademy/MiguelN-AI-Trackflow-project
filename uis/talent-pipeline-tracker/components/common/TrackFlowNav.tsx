import Image from "next/image";
import Link from "next/link";

type TrackFlowNavProps = {
  subtitle?: string;
};

export function TrackFlowNav({ subtitle = "Talent Pipeline Tracker" }: TrackFlowNavProps) {
  return (
    <header className="sticky top-0 z-30 border-b border-[color:var(--border-soft)] bg-[color:var(--surface)/0.9] backdrop-blur-md">
      <nav
        aria-label="Primary"
        className="mx-auto flex w-full max-w-6xl items-center justify-between gap-3 px-4 py-3 sm:px-6"
      >
        <Link href="/" className="flex items-center gap-3" aria-label="TrackFlow candidates home">
          <Image
            src="/trackflow-logo.png"
            alt="TrackFlow company logo"
            width={40}
            height={40}
            className="h-10 w-10 rounded-xl object-cover ring-1 ring-[color:var(--flow-blue)]/25"
            priority
          />
          <div>
            <p className="font-brand-display text-lg font-bold leading-none text-[color:var(--text-strong)]">TrackFlow</p>
            <p className="text-xs text-[color:var(--text-muted)]">{subtitle}</p>
          </div>
        </Link>

        <div className="hidden items-center gap-6 sm:flex">
          <Link
            href="/"
            className="text-sm font-semibold text-[color:var(--text-muted)] transition hover:text-[color:var(--flow-blue)]"
          >
            Candidates
          </Link>
        </div>
      </nav>
    </header>
  );
}
