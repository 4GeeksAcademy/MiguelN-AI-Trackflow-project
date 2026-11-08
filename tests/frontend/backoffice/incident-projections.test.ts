import { normalizeIncidentCreated } from "@/lib/incident";

it("accepts the incident creation confirmation projection", () => {
  expect(normalizeIncidentCreated({
    id: "incident-1",
    title: "Parcel missing",
    status: "open",
    branch: "zaragoza_warehouse",
    description: "Not needed by the confirmation UI",
    source_incident_id: "internal-seed-key",
  })).toEqual({
    id: "incident-1",
    title: "Parcel missing",
    status: "open",
    branch: "zaragoza_warehouse",
  });
});

it("rejects an incomplete incident confirmation", () => {
  expect(() => normalizeIncidentCreated({ id: "incident-1", status: "open" })).toThrow(
    "Unexpected incident confirmation",
  );
});