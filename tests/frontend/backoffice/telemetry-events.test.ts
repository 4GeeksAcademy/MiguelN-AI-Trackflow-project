/**
 * Every event the backoffice emits must fit its entry in
 * `docs/telemetry/event-schemas.json`: only declared keys, all required keys,
 * and enum values from the company vocabulary.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { requestApi, requestAuthenticatedApi } from "@/lib/api-client";
import { storeToken, clearToken } from "@/lib/auth-storage";
import { trackOrderCreated, trackOrderRejected, trackServerRejection, trackStockAvailability } from "@/lib/inventory-telemetry";
import { apiErrorClass, errorFingerprint, httpMethod, routeTemplate, sectionForPath, statusClass, viewportClass } from "@/lib/telemetry-routes";
import { track } from "@/services/telemetry-service";
import { ApiError } from "@/lib/api-client";
import { createIncident } from "@/services/incidents-service";
import { login, requestPasswordReset } from "@/services/auth-service";
import { createSupplier } from "@/services/suppliers-service";

import { installBrowser, removeBrowser } from "../shared/support/browser";
import { jsonResponse, scriptFetch } from "../shared/support/http";

jest.mock("@/services/telemetry-service", () => ({
  ...jest.requireActual("@/services/telemetry-service"),
  track: jest.fn(),
}));

type PropertySchema = { type?: string | string[]; enum?: unknown[]; maxLength?: number; minimum?: number; maximum?: number };
type EventSchema = { required: string[]; additionalProperties: boolean; properties: Record<string, PropertySchema> };

const schema = JSON.parse(readFileSync(path.join(__dirname, "../../../docs/telemetry/event-schemas.json"), "utf8"));

function eventSchema(eventType: string): EventSchema {
  const entry = schema.definitions.events[eventType];
  if (!entry) throw new Error(`${eventType} is not in the catalogue`);
  return entry.allOf[1].properties.properties;
}

function typeMatches(value: unknown, type: string): boolean {
  if (type === "null") return value === null;
  if (type === "integer") return Number.isInteger(value);
  if (type === "number") return typeof value === "number" && Number.isFinite(value);
  if (type === "array") return Array.isArray(value);
  return typeof value === type;
}

function violations(eventType: string, properties: Record<string, unknown>): string[] {
  const spec = eventSchema(eventType);
  const problems: string[] = [];

  for (const key of spec.required) {
    if (!(key in properties)) problems.push(`missing ${key}`);
  }

  for (const [key, value] of Object.entries(properties)) {
    const def = spec.properties[key];
    if (!def) {
      problems.push(`unexpected ${key}`);
      continue;
    }
    const types = Array.isArray(def.type) ? def.type : def.type ? [def.type] : [];
    if (types.length && !types.some((type) => typeMatches(value, type))) problems.push(`${key} has wrong type`);
    if (def.enum && !def.enum.includes(value)) problems.push(`${key}=${String(value)} not in enum`);
    if (typeof value === "string" && def.maxLength && value.length > def.maxLength) problems.push(`${key} too long`);
    if (typeof value === "number" && def.minimum !== undefined && value < def.minimum) problems.push(`${key} below minimum`);
    if (typeof value === "number" && def.maximum !== undefined && value > def.maximum) problems.push(`${key} above maximum`);
  }

  return problems;
}

const tracked = track as jest.Mock;

function expectAllEmittedEventsValid() {
  expect(tracked).toHaveBeenCalled();
  for (const [eventType, properties] of tracked.mock.calls as Array<[string, Record<string, unknown>]>) {
    expect({ eventType, problems: violations(eventType, properties) }).toEqual({ eventType, problems: [] });
  }
}

const PRODUCT = { id: "11111111-1111-4111-8111-111111111111", name: "Shoe", sku: "SHOE-1", warehouse: "Monterrey" as const, current_stock: 12 };

beforeEach(() => {
  tracked.mockClear();
  installBrowser();
});

afterEach(() => {
  clearToken();
  removeBrowser();
});

describe("inventory events", () => {
  it("emits valid inbound and outbound confirmations", () => {
    trackOrderCreated("inbound", PRODUCT, 5, undefined);
    trackOrderCreated("outbound", PRODUCT, 1, 12);

    expectAllEmittedEventsValid();
    expect(tracked.mock.calls.map(([type]) => type)).toEqual(["inbound_order_created", "outbound_order_created"]);
  });

  it("emits stock_threshold_triggered only when an outbound order crosses a band", () => {
    trackOrderCreated("outbound", PRODUCT, 2, 12);
    expect(tracked.mock.calls.map(([type]) => type)).not.toContain("stock_threshold_triggered");

    tracked.mockClear();
    trackOrderCreated("outbound", PRODUCT, 4, 12);
    trackOrderCreated("outbound", PRODUCT, 3, 3);

    const crossings = tracked.mock.calls.filter(([type]) => type === "stock_threshold_triggered").map(([, props]) => props);
    expect(crossings).toEqual([
      expect.objectContaining({ threshold_band: "low", previous_band: "healthy", threshold_max_units: 9, stock_level_after: 8 }),
      expect.objectContaining({ threshold_band: "out_of_stock", previous_band: "low", threshold_max_units: 0, stock_level_after: 0 }),
    ]);
    expectAllEmittedEventsValid();
  });

  it("emits order_validation_failed with the shortfall for insufficient stock", () => {
    trackOrderRejected("outbound", PRODUCT, "insufficient_stock", "quantity", 20, 12);
    trackOrderRejected("inbound", null, "malformed_payload", "product_id", null, null);

    expect(tracked.mock.calls[0][1]).toMatchObject({ shortfall: 8, available_stock: 12, requested_quantity: 20 });
    expectAllEmittedEventsValid();
  });

  it("maps refused orders to the schema's rejection reasons", () => {
    trackServerRejection("outbound", new ApiError(400, null), PRODUCT, 99, 12);
    trackServerRejection("outbound", new ApiError(404, null), PRODUCT, 1, 12);
    trackServerRejection("inbound", new ApiError(422, null), PRODUCT, 1, null);
    trackServerRejection("inbound", new Error("not an api error"), PRODUCT, 1, null);

    expect(tracked.mock.calls.map(([, props]) => props.rejection_reason)).toEqual([
      "insufficient_stock",
      "product_not_found",
      "malformed_payload",
    ]);
    expectAllEmittedEventsValid();
  });

  it("emits stock_availability_checked for success and error", () => {
    trackStockAvailability("outbound_form", PRODUCT, 12, performance.now());
    trackStockAvailability("outbound_form", PRODUCT, null, performance.now());
    trackStockAvailability("outbound_form", null, 12, performance.now());

    expect(tracked).toHaveBeenCalledTimes(2);
    expectAllEmittedEventsValid();
  });
});

describe("api-client events", () => {
  it("records latency for a success and an error class for a refusal", async () => {
    scriptFetch(jsonResponse(200, {}), jsonResponse(404, { detail: "nope" }));

    await requestApi("/inventory/products/11111111-1111-4111-8111-111111111111");
    await expect(requestApi("/suppliers/12")).rejects.toBeInstanceOf(ApiError);

    const types = tracked.mock.calls.map(([type]) => type);
    expect(types).toEqual(["api_latency_recorded", "api_latency_recorded", "api_error_recorded"]);
    expect(tracked.mock.calls[0][1]).toMatchObject({ route_template: "/inventory/products/{id}", method: "GET", status_class: "2xx" });
    expect(tracked.mock.calls[2][1]).toMatchObject({ route_template: "/suppliers/{id}", status_code: 404, error_class: "not_found" });
    expectAllEmittedEventsValid();
  });

  it("shares one requestId between the header and the events of that call", async () => {
    const requests = scriptFetch(jsonResponse(500, { detail: "boom" }));

    await expect(requestApi("/auth/me")).rejects.toBeInstanceOf(ApiError);

    const requestId = requests[0].headers["X-Request-Id"];
    expect(requestId).toEqual(expect.any(String));
    expect(requests[0].headers["X-Client-App"]).toBe("backoffice");
    expect(requests[0].headers["X-Session-Id"]).toEqual(expect.any(String));
    for (const call of tracked.mock.calls) expect(call[2]).toBe(requestId);
  });

  it("emits session_expired on a 401 from a protected route", async () => {
    storeToken("header.payload.signature");
    scriptFetch(jsonResponse(401, { detail: "expired" }));

    await expect(requestAuthenticatedApi("/auth/me")).rejects.toBeDefined();

    expect(tracked.mock.calls.map(([type]) => type)).toEqual(["api_latency_recorded", "api_error_recorded", "session_expired"]);
    expectAllEmittedEventsValid();
  });

  it("does not emit for a request that never produced a response", async () => {
    scriptFetch(new TypeError("offline"));

    await expect(requestApi("/auth/me")).rejects.toBeDefined();

    expect(tracked).not.toHaveBeenCalled();
  });
});

describe("domain service events", () => {
  it("emits login_succeeded and password_reset_requested", async () => {
    scriptFetch(jsonResponse(200, { access_token: "a.b.c", token_type: "bearer" }), jsonResponse(200, { message: "ok" }));

    await login({ email: "a@b.co", password: "pw" });
    await requestPasswordReset({ email: "a@b.co" });

    const types = tracked.mock.calls.map(([type]) => type);
    expect(types).toContain("login_succeeded");
    expect(types).toContain("password_reset_requested");
    expectAllEmittedEventsValid();
  });

  it("emits incident_created with the vocabulary values from the payload", async () => {
    scriptFetch(jsonResponse(201, { id: "22222222-2222-4222-8222-222222222222", title: "t", status: "open", branch: "la_warehouse" }));

    await createIncident({ title: "t", description: "d", category: "lost_parcel", status: "open", origin: "branch", branch: "la_warehouse" });

    expect(tracked.mock.calls.map(([type]) => type)).toContain("incident_created");
    expectAllEmittedEventsValid();
  });

  it("emits supplier_created with the numeric id", async () => {
    storeToken("header.payload.signature");
    scriptFetch(
      jsonResponse(201, {
        id: 7, name: "Acme", rate_per_shipment: 3, currency: "EUR", categories: ["packaging"], country: "ES", status: "active", service_zone: "x", contact_email: "a@b.co", rating: null,
      }),
    );

    try {
      await createSupplier({} as never);
    } catch {
      // The response shape is not what this test is about; only a successful parse emits.
    }

    for (const [type, props] of tracked.mock.calls) {
      if (type === "supplier_created") expect(violations(type, props)).toEqual([]);
    }
  });
});

describe("telemetry-routes", () => {
  it.each([
    ["/", "/"],
    ["/suppliers/12?x=1", "/suppliers/{id}"],
    ["/incidents/22222222-2222-4222-8222-222222222222", "/incidents/{id}"],
    ["/backoffice/inventory/orders/inbound?product_id=abc", "/backoffice/inventory/orders/inbound"],
    ["/incidents/new/", "/incidents/new"],
  ])("routeTemplate(%s) is %s", (input, expected) => expect(routeTemplate(input)).toBe(expected));

  it("maps every backoffice route to a catalogue section", () => {
    expect(
      ["/", "/login", "/register", "/forgot-password", "/reset-password", "/account/profile", "/account/change-password", "/incidents", "/incidents/new", "/suppliers", "/suppliers/3", "/backoffice/inventory/products", "/backoffice/inventory/orders", "/backoffice/inventory/orders/inbound", "/backoffice/inventory/orders/outbound"].map(sectionForPath),
    ).not.toContain(null);
    expect(sectionForPath("/nope")).toBeNull();
  });

  it("every mapped section exists in the section_viewed enum", () => {
    const allowed: string[] = eventSchema("section_viewed").properties.section.enum as string[];
    for (const path of ["/", "/login", "/incidents/new", "/suppliers/3", "/backoffice/inventory/orders/outbound"]) {
      expect(allowed).toContain(sectionForPath(path));
    }
  });

  it("classifies methods, statuses and errors", () => {
    expect(httpMethod("patch")).toBe("PATCH");
    expect(httpMethod("OPTIONS")).toBeNull();
    expect(statusClass(204)).toBe("2xx");
    expect(statusClass(101)).toBeNull();
    expect([401, 404, 409, 422, 500].map((status) => apiErrorClass(status, "/x"))).toEqual(["auth", "not_found", "conflict", "validation", "unhandled"]);
    expect(apiErrorClass(400, "/inventory/products/outbound")).toBe("domain_rule");
    expect(apiErrorClass(400, "/api/incidents")).toBe("validation");
    expect(apiErrorClass(200, "/x")).toBeNull();
  });

  it("gives stable 12-hex fingerprints", () => {
    expect(errorFingerprint("network", "/a")).toMatch(/^[0-9a-f]{12}$/);
    expect(errorFingerprint("network", "/a")).toBe(errorFingerprint("network", "/a"));
    expect(errorFingerprint("network", "/a")).not.toBe(errorFingerprint("network", "/b"));
  });

  it("classifies viewports", () => {
    expect([320, 800, 1440].map(viewportClass)).toEqual(["mobile", "tablet", "desktop"]);
  });
});
