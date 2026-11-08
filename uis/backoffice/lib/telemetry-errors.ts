import { errorFingerprint, routeTemplate } from "@/lib/telemetry-routes";
import { track } from "@/services/telemetry-service";

type UiErrorClass = "network" | "unreadable_response" | "render_crash" | "uncaught_exception" | "unhandled_rejection";

/** Records an error the user can see. Only the class and route are sent, never the message or stack. */
export function trackUiError(errorClass: UiErrorClass): void {
  const route = routeTemplate(typeof window === "undefined" ? "/" : window.location.pathname);

  track("ui_error_displayed", {
    error_class: errorClass,
    route_template: route,
    http_status: null,
    fingerprint: errorFingerprint(errorClass, route),
  });
}
