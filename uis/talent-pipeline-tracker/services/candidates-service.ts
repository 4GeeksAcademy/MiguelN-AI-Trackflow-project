import { parseResponseJson, requestApi } from "@/lib/api-client";
import {
  buildCandidatePayload,
  normalizeCandidatePayload,
  normalizeCandidatesPayload,
} from "@/lib/candidate";
import type {
  CandidateFormValues,
  CandidateListResponse,
  CandidateRecord,
  CandidateStatusStageUpdate,
} from "@/types/candidate";

export async function fetchCandidates(): Promise<CandidateListResponse> {
  const response = await requestApi("/records");
  const payload = await parseResponseJson(response);

  return normalizeCandidatesPayload(payload);
}

export async function fetchCandidateById(id: string): Promise<CandidateRecord> {
  const response = await requestApi(`/records/${encodeURIComponent(id)}`);
  const payload = await parseResponseJson(response);

  return normalizeCandidatePayload(payload);
}

export async function createCandidate(values: CandidateFormValues): Promise<CandidateRecord | null> {
  const response = await requestApi("/records", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(buildCandidatePayload(values)),
  });

  const payload = await parseResponseJson(response);
  if (!payload) {
    return null;
  }

  return normalizeCandidatePayload(payload);
}

export async function updateCandidateStatusStage(
  id: string,
  payload: CandidateStatusStageUpdate,
): Promise<CandidateRecord | null> {
  const response = await requestApi(`/records/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  const responsePayload = await parseResponseJson(response);
  if (!responsePayload) {
    return null;
  }

  return normalizeCandidatePayload(responsePayload);
}

export async function replaceCandidate(id: string, candidate: CandidateRecord): Promise<CandidateRecord | null> {
  const response = await requestApi(`/records/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(candidate),
  });

  const responsePayload = await parseResponseJson(response);
  if (!responsePayload) {
    return null;
  }

  return normalizeCandidatePayload(responsePayload);
}
