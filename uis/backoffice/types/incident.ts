/**
 * The incident contract, mirroring `services/incidents/models.py`.
 *
 * The value lists are the ones in `contexts/centralized-incident.md`. They are
 * declared as `readonly` arrays rather than inferred from the union so the
 * dropdowns have a defined order: a `Record` gives no ordering guarantee worth
 * relying on, and the branch list in particular reads as Central first, then
 * Los Angeles, then Zaragoza.
 */

export type IncidentStatus = "open" | "in_progress" | "resolved" | "discarded";

export type IncidentOrigin = "customer" | "branch" | "internal";

export type IncidentBranch =
  | "central"
  | "la_warehouse"
  | "la_office"
  | "zaragoza_warehouse"
  | "zaragoza_office";

export type IncidentCategory =
  | "lost_parcel"
  | "delivery_failure"
  | "inventory_discrepancy"
  | "carrier_issue"
  | "returns_issue"
  | "warehouse_incident"
  | "system_failure"
  | "client_complaint"
  | "other";

export const INCIDENT_STATUSES: readonly IncidentStatus[] = [
  "open",
  "in_progress",
  "resolved",
  "discarded",
];

export const INCIDENT_ORIGINS: readonly IncidentOrigin[] = ["customer", "branch", "internal"];

export const INCIDENT_BRANCHES: readonly IncidentBranch[] = [
  "central",
  "la_warehouse",
  "la_office",
  "zaragoza_warehouse",
  "zaragoza_office",
];

export const INCIDENT_CATEGORIES: readonly IncidentCategory[] = [
  "lost_parcel",
  "delivery_failure",
  "inventory_discrepancy",
  "carrier_issue",
  "returns_issue",
  "warehouse_incident",
  "system_failure",
  "client_complaint",
  "other",
];

/** Kept in step with `TITLE_MAX_LENGTH` / `DESCRIPTION_MAX_LENGTH` in the API. */
export const TITLE_MAX_LENGTH = 120;
export const DESCRIPTION_MAX_LENGTH = 4000;

export type Incident = {
  id: string;
  title: string;
  description: string;
  category: IncidentCategory;
  status: IncidentStatus;
  origin: IncidentOrigin;
  branch: IncidentBranch;
  created_at: string;
  updated_at: string;
};

export type IncidentCreated = Pick<Incident, "id" | "title" | "status" | "branch">;

export type IncidentCreatePayload = {
  title: string;
  description: string;
  category: IncidentCategory;
  status: IncidentStatus;
  origin: IncidentOrigin;
  branch: IncidentBranch;
};

/**
 * Form state. The three enum fields start empty so the operative has to choose
 * one: defaulting `category` would quietly file every rushed report under the
 * same heading. `status` is the exception - an incident is `open` unless the
 * reporter says otherwise, which is the API's default too.
 */
export type IncidentFormValues = {
  title: string;
  description: string;
  category: IncidentCategory | "";
  status: IncidentStatus;
  origin: IncidentOrigin | "";
  branch: IncidentBranch | "";
};

/** The fields an error can be attached to. Matches the API's `field` values. */
export type IncidentFormField = keyof IncidentFormValues;

export type IncidentFieldErrors = Partial<Record<IncidentFormField, string>>;

export type IncidentFilters = {
  status: IncidentStatus | "";
  origin: IncidentOrigin | "";
  branch: IncidentBranch | "";
};

export type IncidentSummary = {
  total: number;
  by_status: Record<IncidentStatus, number>;
  by_category: Record<IncidentCategory, number>;
  by_origin: Record<IncidentOrigin, number>;
  by_branch: Record<IncidentBranch, number>;
};
