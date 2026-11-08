/**
 * HTTP access to `/api/incidents`.
 *
 * These calls use `requestApi`, not `requestAuthenticatedApi`: the incident
 * routes carry no token requirement, because at TrackFlow anyone in the company
 * reports incidents. The screens still sit inside the `(protected)` group, so a
 * signed-out visitor never reaches them - but sending a bearer token the API
 * does not read, through a helper whose whole job is to handle the 401 that
 * cannot happen, would describe a contract that does not exist.
 *
 * If the incident routes are ever put behind auth, this is the one file that
 * changes.
 */

import { parseResponseJson, requestApi } from "@/lib/api-client";
import { track } from "@/services/telemetry-service";
import {
  normalizeIncident,
  normalizeIncidentCreated,
  normalizeIncidents,
  normalizeIncidentSummary,
} from "@/lib/incident";
import type {
  Incident,
  IncidentCreatePayload,
  IncidentCreated,
  IncidentFilters,
  IncidentStatus,
  IncidentSummary,
} from "@/types/incident";

const JSON_HEADERS = { "Content-Type": "application/json" };

/** Only set filters are sent, so an empty selection means "no filter" not "match empty". */
function buildIncidentsQuery(filters?: Partial<IncidentFilters>): string {
  const params = new URLSearchParams();

  if (filters?.status) {
    params.set("status", filters.status);
  }
  if (filters?.origin) {
    params.set("origin", filters.origin);
  }
  if (filters?.branch) {
    params.set("branch", filters.branch);
  }

  const query = params.toString();
  return query ? `?${query}` : "";
}

export async function fetchIncidents(filters?: Partial<IncidentFilters>): Promise<Incident[]> {
  const response = await requestApi(`/api/incidents${buildIncidentsQuery(filters)}`);
  const incidents = normalizeIncidents(await parseResponseJson(response));

  const filterKeys = (Object.keys(filters ?? {}) as Array<keyof IncidentFilters>).filter((key) => Boolean(filters?.[key]));
  if (filterKeys.length > 0) {
    track("list_filter_applied", { view: "incidents_list", filter_keys: filterKeys, result_count: incidents.length });
  }

  return incidents;
}

export async function fetchIncidentSummary(): Promise<IncidentSummary> {
  const response = await requestApi("/api/incidents/summary");

  return normalizeIncidentSummary(await parseResponseJson(response));
}

export async function createIncident(payload: IncidentCreatePayload): Promise<IncidentCreated> {
  const response = await requestApi("/api/incidents", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  });

  const created = normalizeIncidentCreated(await parseResponseJson(response));
  track("incident_created", {
    incident_id: created.id,
    category: payload.category,
    origin: payload.origin,
    branch: payload.branch,
  });

  return created;
}

export async function updateIncidentStatus(id: string, status: IncidentStatus): Promise<Incident> {
  const response = await requestApi(`/api/incidents/${encodeURIComponent(id)}/status`, {
    method: "PATCH",
    headers: JSON_HEADERS,
    body: JSON.stringify({ status }),
  });

  return normalizeIncident(await parseResponseJson(response));
}
