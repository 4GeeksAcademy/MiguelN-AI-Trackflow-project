import { normalizeSuppliers } from "@/lib/supplier";

it("keeps supplier list responses to fields used by the directory", () => {
  expect(normalizeSuppliers([{
    id: 17,
    name: "Estafeta",
    country: "USA",
    categories: ["carrier_last_mile"],
    rate_per_shipment: 4.5,
    currency: "USD",
    status: "active",
    service_zone: "Northern Mexico",
    contact_email: "dispatch@example.com",
    notes: "Negotiated terms",
    updated_at: "2026-10-12T12:00:00Z",
  }])).toEqual([{
    id: 17,
    name: "Estafeta",
    country: "USA",
    categories: ["carrier_last_mile"],
    rate_per_shipment: 4.5,
    currency: "USD",
    status: "active",
    service_zone: "Northern Mexico",
  }]);
});