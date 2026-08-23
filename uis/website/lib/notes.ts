import type { CandidateNote } from "@/types/note";

export function normalizeNotesPayload(payload: unknown): CandidateNote[] {
  const rawNotes =
    Array.isArray(payload)
      ? payload
      : typeof payload === "object" && payload !== null && Array.isArray((payload as { data?: unknown }).data)
        ? ((payload as { data: unknown[] }).data ?? [])
        : [];

  return rawNotes.flatMap((note, index) => {
    if (typeof note !== "object" || note === null) {
      return [];
    }

    const candidateNote = note as Record<string, unknown>;
    const possibleId = candidateNote.id ?? candidateNote._id ?? candidateNote.note_id ?? candidateNote.noteId;
    const body =
      typeof candidateNote.content === "string"
        ? candidateNote.content
        : typeof candidateNote.body === "string"
          ? candidateNote.body
          : typeof candidateNote.note === "string"
            ? candidateNote.note
            : typeof candidateNote.text === "string"
              ? candidateNote.text
              : typeof candidateNote.message === "string"
                ? candidateNote.message
                : "";

    const createdAt =
      typeof candidateNote.createdAt === "string"
        ? candidateNote.createdAt
        : typeof candidateNote.created_at === "string"
          ? candidateNote.created_at
          : typeof candidateNote.timestamp === "string"
            ? candidateNote.timestamp
            : typeof candidateNote.date === "string"
              ? candidateNote.date
              : "";

    const author =
      typeof candidateNote.author === "string"
        ? candidateNote.author
        : typeof candidateNote.createdBy === "string"
          ? candidateNote.createdBy
          : typeof candidateNote.user === "string"
            ? candidateNote.user
            : typeof candidateNote.owner === "string"
              ? candidateNote.owner
              : "";

    return [
      {
        id: possibleId ? String(possibleId) : String(index),
        body: body.trim() || "-",
        createdAt: createdAt.trim(),
        author: author.trim(),
      },
    ];
  });
}

export function normalizeSingleNotePayload(payload: unknown): CandidateNote | null {
  const notes = normalizeNotesPayload(payload);
  return notes.length > 0 ? notes[0] : null;
}
