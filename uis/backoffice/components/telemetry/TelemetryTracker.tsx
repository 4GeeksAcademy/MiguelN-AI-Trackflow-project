"use client";

import { usePathname } from "next/navigation";
import { useReportWebVitals } from "next/web-vitals";
import { useEffect, useRef } from "react";
import { trackUiError } from "@/lib/telemetry-errors";
import { routeTemplate, sectionForPath, viewportClass, type Section } from "@/lib/telemetry-routes";
import { track } from "@/services/telemetry-service";

const VITALS = new Set(["LCP", "INP", "CLS", "FCP", "TTFB"]);

type ActiveSection = { section: Section; startedAt: number; interacted: boolean };

function firstEntryType(): "initial_load" | "reload" | "back_forward" {
  const entry = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;

  return entry?.type === "reload" ? "reload" : entry?.type === "back_forward" ? "back_forward" : "initial_load";
}

/**
 * Mounted once in the root layout. Reports section views and dwell time,
 * Core Web Vitals and uncaught browser errors; everything else is tracked where
 * the action happens.
 */
export function TelemetryTracker() {
  const pathname = usePathname();
  const active = useRef<ActiveSection | null>(null);
  const previous = useRef<Section | null>(null);
  const isFirstView = useRef(true);

  useEffect(() => {
    const section = sectionForPath(pathname);
    if (!section) {
      return;
    }

    track("section_viewed", {
      section,
      route_template: routeTemplate(pathname),
      referrer_section: previous.current,
      entry_type: isFirstView.current ? firstEntryType() : "navigation",
      viewport: viewportClass(window.innerWidth),
    });
    isFirstView.current = false;
    active.current = { section, startedAt: performance.now(), interacted: false };

    return () => {
      const current = active.current;
      if (!current) {
        return;
      }

      track("section_left", {
        section: current.section,
        dwell_ms: Math.min(86_400_000, Math.round(performance.now() - current.startedAt)),
        interacted: current.interacted,
      });
      previous.current = current.section;
      active.current = null;
    };
  }, [pathname]);

  useEffect(() => {
    const markInteracted = () => {
      if (active.current) {
        active.current.interacted = true;
      }
    };
    const onError = () => trackUiError("uncaught_exception");
    const onRejection = () => trackUiError("unhandled_rejection");

    document.addEventListener("click", markInteracted, true);
    document.addEventListener("input", markInteracted, true);
    document.addEventListener("submit", markInteracted, true);
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);

    return () => {
      document.removeEventListener("click", markInteracted, true);
      document.removeEventListener("input", markInteracted, true);
      document.removeEventListener("submit", markInteracted, true);
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);

  useReportWebVitals((metric) => {
    const section = sectionForPath(window.location.pathname);
    if (!section || !VITALS.has(metric.name)) {
      return;
    }

    const connection = (navigator as Navigator & { connection?: { effectiveType?: string } }).connection;
    track("page_load_measured", {
      section,
      metric: metric.name,
      value: metric.value,
      rating: metric.rating === "needs-improvement" ? "needs_improvement" : metric.rating,
      connection: connection?.effectiveType ?? null,
    });
  });

  return null;
}
