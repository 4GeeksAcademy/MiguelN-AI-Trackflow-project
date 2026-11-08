/** Pure mappings from URLs and statuses to the vocabulary in `docs/telemetry/event-schemas.json`. */

export type Section =
  | "dashboard_home"
  | "inventory_products"
  | "inventory_inbound_order"
  | "inventory_outbound_order"
  | "inventory_orders"
  | "suppliers_list"
  | "supplier_detail"
  | "incidents_list"
  | "incident_new"
  | "incident_detail"
  | "account"
  | "account_change_password"
  | "profile"
  | "login"
  | "register"
  | "forgot_password"
  | "reset_password"
  | "public_site";

const UUID_SEGMENT = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const NUMERIC_SEGMENT = /^\d+$/;

const SECTION_BY_TEMPLATE: Record<string, Section> = {
  "/": "dashboard_home",
  "/login": "login",
  "/register": "register",
  "/forgot-password": "forgot_password",
  "/reset-password": "reset_password",
  "/account/profile": "profile",
  "/account/change-password": "account_change_password",
  "/incidents": "incidents_list",
  "/incidents/new": "incident_new",
  "/incidents/{id}": "incident_detail",
  "/suppliers": "suppliers_list",
  "/suppliers/{id}": "supplier_detail",
  "/backoffice/inventory/products": "inventory_products",
  "/backoffice/inventory/orders": "inventory_orders",
  "/backoffice/inventory/orders/inbound": "inventory_inbound_order",
  "/backoffice/inventory/orders/outbound": "inventory_outbound_order",
};

/** `/suppliers/12?x=1` becomes `/suppliers/{id}`: never the raw path or its query. */
export function routeTemplate(path: string): string {
  const pathname = path.split(/[?#]/, 1)[0] || "/";
  const trimmed = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  const template = trimmed
    .split("/")
    .map((segment) => (UUID_SEGMENT.test(segment) || NUMERIC_SEGMENT.test(segment) ? "{id}" : segment))
    .join("/");

  return (template.startsWith("/") ? template : `/${template}`).slice(0, 200);
}

/** `null` for a route that is not part of the section catalogue (for example a 404 page). */
export function sectionForPath(path: string): Section | null {
  return SECTION_BY_TEMPLATE[routeTemplate(path)] ?? null;
}

export function viewportClass(width: number): "mobile" | "tablet" | "desktop" {
  return width < 640 ? "mobile" : width < 1024 ? "tablet" : "desktop";
}

const METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);

export function httpMethod(method: string | undefined): "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | null {
  const upper = (method ?? "GET").toUpperCase();

  return METHODS.has(upper) ? (upper as "GET") : null;
}

export function statusClass(status: number): "2xx" | "3xx" | "4xx" | "5xx" | null {
  if (status < 200 || status > 599) {
    return null;
  }

  return `${Math.floor(status / 100)}xx` as "2xx";
}

export type ApiErrorClass = "validation" | "domain_rule" | "auth" | "not_found" | "conflict" | "unhandled";

export function apiErrorClass(status: number, path: string): ApiErrorClass | null {
  if (status === 401 || status === 403) return "auth";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 422) return "validation";
  if (status === 400) return path.startsWith("/api/incidents") ? "validation" : "domain_rule";
  if (status >= 500 && status <= 599) return "unhandled";

  return status >= 400 ? "domain_rule" : null;
}

export type UiErrorClass = "client_error_4xx" | "server_error_5xx";

export function uiErrorClass(status: number): UiErrorClass | null {
  if (status >= 500 && status <= 599) return "server_error_5xx";
  if (status >= 400 && status <= 499) return "client_error_4xx";

  return null;
}

/** Stable 12-hex hash of class + route, so identical failures group without carrying any message text. */
export function errorFingerprint(errorClass: string, route: string): string {
  const text = `${errorClass}|${route}`;
  let first = 0x811c9dc5;
  let second = 0x1b873593;

  for (let index = 0; index < text.length; index += 1) {
    first = Math.imul(first ^ text.charCodeAt(index), 0x01000193) >>> 0;
    second = Math.imul(second ^ text.charCodeAt(index), 0x85ebca6b) >>> 0;
  }

  return (first.toString(16).padStart(8, "0") + second.toString(16).padStart(8, "0")).slice(0, 12);
}
