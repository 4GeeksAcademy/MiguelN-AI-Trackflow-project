"use client";

import { useSyncExternalStore } from "react";

/** The query string only changes through a navigation, which remounts the page. */
const subscribe = () => () => {};

/**
 * Reads `window.location.search` safely across server rendering.
 *
 * Returns `null` on the server and for the first hydrating render, then the
 * real query string.
 *
 * `useSearchParams` would be the obvious tool, but it forces every page that
 * calls it behind a Suspense boundary at build time - the reason `LoginPage`
 * has always read `?next=` off `window` instead. Reading it in an effect is the
 * other obvious move, and React 19 rejects that as setting state during an
 * effect. `useSyncExternalStore` is the supported way to read a browser-only
 * value: it renders the server snapshot, then swaps in the client one right
 * after, so the hydrated markup still matches.
 */
export function useLocationSearch(): string | null {
  return useSyncExternalStore(
    subscribe,
    () => window.location.search,
    () => null,
  );
}
