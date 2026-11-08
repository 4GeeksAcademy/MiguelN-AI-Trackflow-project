/**
 * Incident display labels, the lifecycle the list UI reads, and the payload
 * and validation helpers the form uses.
 *
 * The lifecycle is duplicated from `trackflow_shared.incidents` on purpose: the
 * list has to know which statuses to offer *before* asking the API, and the API
 * stays the authority that refuses an invalid one. The comment on
 * `ALLOWED_TRANSITIONS` below is the reminder to change both together.
 */

import {
  DESCRIPTION_MAX_LENGTH,
  INCIDENT_BRANCHES,
  INCIDENT_CATEGORIES,
  INCIDENT_ORIGINS,
  INCIDENT_STATUSES,
  TITLE_MAX_LENGTH,
  type Incident,
  type IncidentBranch,
  type IncidentCategory,
  type IncidentCreated,
  type IncidentCreatePayload,
  type IncidentFieldErrors,
  type IncidentFilters,
  type IncidentFormValues,
  type IncidentOrigin,
  type IncidentStatus,
  type IncidentSummary,
} from "@/types/incident";

/** Exactly the display names in the CONTEXT's branch table. */
const BRANCH_LABELS: Record<IncidentBranch, string> = {
  central: "Central",
  la_warehouse: "Los Angeles — Warehouse",
  la_office: "Los Angeles — Office",
  zaragoza_warehouse: "Zaragoza — Warehouse",
  zaragoza_office: "Zaragoza — Office",
};

const STATUS_LABELS: Record<IncidentStatus, string> = {
  open: "Open",
  in_progress: "In progress",
  resolved: "Resolved",
  discarded: "Discarded",
};

const ORIGIN_LABELS: Record<IncidentOrigin, string> = {
  customer: "Customer",
  branch: "Branch",
  internal: "Internal",
};

const CATEGORY_LABELS: Record<IncidentCategory, string> = {
  lost_parcel: "Lost parcel",
  delivery_failure: "Delivery failure",
  inventory_discrepancy: "Inventory discrepancy",
  carrier_issue: "Carrier issue",
  returns_issue: "Returns issue",
  warehouse_incident: "Warehouse incident",
  system_failure: "System failure",
  client_complaint: "Client complaint",
  other: "Other",
};

/** What each origin means, shown under the origin field so the choice is informed. */
const ORIGIN_HINTS: Record<IncidentOrigin, string> = {
  customer: "Reported by a client company or an end consumer.",
  branch: "Detected by warehouse or office staff at a TrackFlow facility.",
  internal: "Detected internally by technology, leadership or operations.",
};

export function formatStatusLabel(status: IncidentStatus): string {
  return STATUS_LABELS[status];
}

export function formatOriginLabel(origin: IncidentOrigin): string {
  return ORIGIN_LABELS[origin];
}

export function formatBranchLabel(branch: IncidentBranch): string {
  return BRANCH_LABELS[branch];
}

export function formatCategoryLabel(category: IncidentCategory): string {
  return CATEGORY_LABELS[category];
}

export function describeOrigin(origin: IncidentOrigin): string {
  return ORIGIN_HINTS[origin];
}

export function formatTimestamp(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
}

/* -----------------------------------------------------------------------------
   Lifecycle
   -------------------------------------------------------------------------- */

/**
 * Mirrors `ALLOWED_TRANSITIONS` in `packages/shared/trackflow_shared/incidents.py`.
 * Change both together. The UI uses it only to decide what to offer; the API
 * re-checks every transition and returns 400 with the reason if it disagrees.
 */
const ALLOWED_TRANSITIONS: Record<IncidentStatus, readonly IncidentStatus[]> = {
  open: ["in_progress", "discarded"],
  in_progress: ["resolved", "discarded"],
  resolved: [],
  discarded: [],
};

export function allowedTransitionsFrom(status: IncidentStatus): readonly IncidentStatus[] {
  return ALLOWED_TRANSITIONS[status];
}

export function isFinalStatus(status: IncidentStatus): boolean {
  return ALLOWED_TRANSITIONS[status].length === 0;
}

/* -----------------------------------------------------------------------------
   Type guards
   -------------------------------------------------------------------------- */

export function isIncidentStatus(value: string): value is IncidentStatus {
  return (INCIDENT_STATUSES as readonly string[]).includes(value);
}

export function isIncidentOrigin(value: string): value is IncidentOrigin {
  return (INCIDENT_ORIGINS as readonly string[]).includes(value);
}

export function isIncidentBranch(value: string): value is IncidentBranch {
  return (INCIDENT_BRANCHES as readonly string[]).includes(value);
}

export function isIncidentCategory(value: string): value is IncidentCategory {
  return (INCIDENT_CATEGORIES as readonly string[]).includes(value);
}

/* -----------------------------------------------------------------------------
   Form
   -------------------------------------------------------------------------- */

export function emptyIncidentFormValues(): IncidentFormValues {
  return {
    title: "",
    description: "",
    category: "",
    status: "open",
    origin: "",
    branch: "",
  };
}

/**
 * Client-side validation, keyed by field so each message can render next to its
 * own input.
 *
 * It checks the same things the API does and in the same terms, so a report
 * typed on a warehouse terminal with a slow connection fails immediately
 * instead of after a round trip. The API remains the authority: this returning
 * nothing does not mean the submit will succeed.
 */
export function validateIncidentForm(values: IncidentFormValues): IncidentFieldErrors {
  const errors: IncidentFieldErrors = {};

  const title = values.title.trim();
  if (!title) {
    errors.title = "Give the incident a short title.";
  } else if (title.length > TITLE_MAX_LENGTH) {
    errors.title = `Keep the title under ${TITLE_MAX_LENGTH} characters.`;
  }

  const description = values.description.trim();
  if (!description) {
    errors.description = "Describe what happened.";
  } else if (description.length > DESCRIPTION_MAX_LENGTH) {
    errors.description = `Keep the description under ${DESCRIPTION_MAX_LENGTH} characters.`;
  }

  if (!values.category) {
    errors.category = "Choose a category.";
  }

  if (!values.origin) {
    errors.origin = "Choose where this report came from.";
  }

  // Required for every origin, which is why it is never hidden or defaulted.
  // `central` is the answer when the incident belongs to no single facility.
  if (!values.branch) {
    errors.branch = "Choose a branch. Use Central if no single facility owns this.";
  }

  return errors;
}

export function hasFieldErrors(errors: IncidentFieldErrors): boolean {
  return Object.keys(errors).length > 0;
}

/** Assumes `validateIncidentForm` returned no errors. */
export function buildIncidentPayload(values: IncidentFormValues): IncidentCreatePayload {
  if (!values.category || !values.origin || !values.branch) {
    throw new Error("buildIncidentPayload called with an incomplete form");
  }

  return {
    title: values.title.trim(),
    description: values.description.trim(),
    category: values.category,
    status: values.status,
    origin: values.origin,
    branch: values.branch,
  };
}

/* -----------------------------------------------------------------------------
   Normalisation
   -------------------------------------------------------------------------- */

/**
 * Trusts the verified API contract but will not let a malformed payload throw
 * inside a render.
 *
 * An unreadable enum falls back to the most neutral value rather than being
 * dropped: losing the row entirely would hide an incident that does exist,
 * which is worse than showing it as `other`.
 */
export function normalizeIncident(payload: unknown): Incident {
  if (typeof payload !== "object" || payload === null) {
    throw new Error("Unexpected incident payload received from the API");
  }

  const record = payload as Record<string, unknown>;
  const text = (value: unknown, fallback: string): string =>
    typeof value === "string" && value.trim() ? value : fallback;

  const status = typeof record.status === "string" && isIncidentStatus(record.status) ? record.status : "open";
  const origin =
    typeof record.origin === "string" && isIncidentOrigin(record.origin) ? record.origin : "internal";
  const branch =
    typeof record.branch === "string" && isIncidentBranch(record.branch) ? record.branch : "central";
  const category =
    typeof record.category === "string" && isIncidentCategory(record.category) ? record.category : "other";

  return {
    id: text(record.id, ""),
    title: text(record.title, "Untitled incident"),
    description: text(record.description, ""),
    category,
    status,
    origin,
    branch,
    created_at: text(record.created_at, ""),
    updated_at: text(record.updated_at, ""),
  };
}

export function normalizeIncidentCreated(payload: unknown): IncidentCreated {
  if (typeof payload !== "object" || payload === null) {
    throw new Error("Unexpected incident confirmation received from the API");
  }

  const record = payload as Record<string, unknown>;
  if (
    typeof record.id !== "string" || !record.id.trim()
    || typeof record.title !== "string" || !record.title.trim()
    || typeof record.status !== "string" || !isIncidentStatus(record.status)
    || typeof record.branch !== "string" || !isIncidentBranch(record.branch)
  ) {
    throw new Error("Unexpected incident confirmation received from the API");
  }

  return {
    id: record.id,
    title: record.title,
    status: record.status,
    branch: record.branch,
  };
}

export function normalizeIncidents(payload: unknown): Incident[] {
  if (!Array.isArray(payload)) {
    return [];
  }

  return payload.map(normalizeIncident);
}

/**
 * Reads a counts object, filling in every key the API might have omitted.
 *
 * The API sends all of them, including the zeros. Rebuilding the record from
 * the known key list anyway is what lets the panel render a fixed grid of
 * tiles without a `?? 0` at every cell.
 */
function readCounts<Key extends string>(
  value: unknown,
  keys: readonly Key[],
): Record<Key, number> {
  const source = typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};

  return keys.reduce(
    (counts, key) => {
      const raw = source[key];
      counts[key] = typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
      return counts;
    },
    {} as Record<Key, number>,
  );
}

export function normalizeIncidentSummary(payload: unknown): IncidentSummary {
  const record = typeof payload === "object" && payload !== null ? (payload as Record<string, unknown>) : {};

  return {
    total: typeof record.total === "number" && Number.isFinite(record.total) ? record.total : 0,
    by_status: readCounts(record.by_status, INCIDENT_STATUSES),
    by_category: readCounts(record.by_category, INCIDENT_CATEGORIES),
    by_origin: readCounts(record.by_origin, INCIDENT_ORIGINS),
    by_branch: readCounts(record.by_branch, INCIDENT_BRANCHES),
  };
}

/** Reads the filters out of a query string, ignoring anything unrecognised. */
export function readIncidentFilters(params: URLSearchParams): IncidentFilters {
  const status = params.get("status") ?? "";
  const origin = params.get("origin") ?? "";
  const branch = params.get("branch") ?? "";

  return {
    status: isIncidentStatus(status) ? status : "",
    origin: isIncidentOrigin(origin) ? origin : "",
    branch: isIncidentBranch(branch) ? branch : "",
  };
}

export function countActiveFilters(filters: IncidentFilters): number {
  return Object.values(filters).filter((value) => value !== "").length;
}
