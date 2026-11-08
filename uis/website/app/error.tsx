"use client";

import Link from "next/link";
import { useEffect } from "react";
import { StateMessage } from "@/components/common/StateMessage";

type ErrorPageProps = {
  error: Error & { digest?: string };
  unstable_retry: () => void;
};

/**
 * The fallback for anything that throws while a page renders. It never shows
 * the error itself - the original goes to the console for whoever investigates -
 * and always offers a way forward: retry, or back to a page that works.
 */
export default function ErrorPage({ error, unstable_retry }: ErrorPageProps) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="mx-auto flex w-full max-w-xl flex-col px-6 py-24">
      <StateMessage tone="error">
        <p className="font-semibold">Something went wrong while showing this page.</p>
        <p className="mt-1">Please try again. If it keeps happening, contact TrackFlow Tech.</p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => unstable_retry()}
            className="inline-flex min-h-10 items-center rounded-xl border border-red-300 bg-white px-4 py-2 text-sm font-semibold text-red-700 transition hover:bg-red-100"
          >
            Try again
          </button>
          <Link href="/" className="text-sm font-semibold text-[color:var(--flow-blue)] hover:underline">
            Go to the homepage
          </Link>
        </div>
      </StateMessage>
    </main>
  );
}
