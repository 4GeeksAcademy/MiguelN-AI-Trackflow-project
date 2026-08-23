import { parseResponseJson, requestApi } from "@/lib/api-client";
import { normalizeNotesPayload, normalizeSingleNotePayload } from "@/lib/notes";
import type { CandidateNote } from "@/types/note";

export async function fetchCandidateNotes(candidateId: string): Promise<CandidateNote[]> {
  const response = await requestApi(`/records/${encodeURIComponent(candidateId)}/notes`);
  const payload = await parseResponseJson(response);

  return normalizeNotesPayload(payload);
}

export async function createCandidateNote(candidateId: string, content: string): Promise<CandidateNote | null> {
  const response = await requestApi(`/records/${encodeURIComponent(candidateId)}/notes`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content }),
  });

  const payload = await parseResponseJson(response);
  if (!payload) {
    return null;
  }

  return normalizeSingleNotePayload(payload);
}

export async function deleteCandidateNote(candidateId: string, noteId: string): Promise<void> {
  await requestApi(`/records/${encodeURIComponent(candidateId)}/notes/${encodeURIComponent(noteId)}`, {
    method: "DELETE",
  });
}
