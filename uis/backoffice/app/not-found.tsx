import Link from "next/link";
import { StateMessage } from "@/components/common/StateMessage";

/** Any address that matches no page. */
export default function NotFound() {
  return (
    <main className="mx-auto flex w-full max-w-xl flex-col px-6 py-24">
      <StateMessage tone="info">
        <p className="font-semibold">We could not find that page.</p>
        <p className="mt-1">The link may be old or mistyped. If you followed a link inside TrackFlow, let TrackFlow Tech know.</p>
        <Link
          href="/"
          className="mt-4 inline-flex text-sm font-semibold text-[color:var(--brand-primary)] hover:underline"
        >
          Go to the dashboard
        </Link>
      </StateMessage>
    </main>
  );
}
