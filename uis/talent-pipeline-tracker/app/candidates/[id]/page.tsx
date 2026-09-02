"use client";

import { StateMessage } from "@/components/common/StateMessage";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  applyCandidateFormValues,
  applyStatusAndStage,
  getCandidateFormValues,
  getCandidateFullName,
  getCandidateStage,
  getCandidateStatus,
  getStringField,
  toDisplayText,
  validateCandidateForm,
} from "@/lib/candidate";
import {
  fetchCandidateById,
  replaceCandidate,
  updateCandidateStatusStage,
} from "@/services/candidates-service";
import {
  createCandidateNote,
  deleteCandidateNote,
  fetchCandidateNotes,
} from "@/services/notes-service";
import type { AsyncStatus } from "@/types/async-state";
import type { CandidateFormValues, CandidateRecord } from "@/types/candidate";
import type { CandidateNote } from "@/types/note";
import Link from "next/link";
import { useParams } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

const apiBaseUrl = getApiBaseUrl();

export default function CandidateDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";

  const [candidate, setCandidate] = useState<CandidateRecord | null>(null);
  const [notes, setNotes] = useState<CandidateNote[]>([]);
  const [candidateFormValues, setCandidateFormValues] = useState<CandidateFormValues>({
    fullName: "",
    email: "",
    position: "",
    status: "",
    stage: "",
  });
  const [statusValue, setStatusValue] = useState("");
  const [stageValue, setStageValue] = useState("");
  const [newNote, setNewNote] = useState("");

  const [candidateFetchStatus, setCandidateFetchStatus] = useState<AsyncStatus>("idle");
  const [candidateFetchError, setCandidateFetchError] = useState<string | null>(null);
  const [notesFetchStatus, setNotesFetchStatus] = useState<AsyncStatus>("idle");
  const [notesFetchError, setNotesFetchError] = useState<string | null>(null);

  const [isSubmittingCandidateForm, setIsSubmittingCandidateForm] = useState(false);
  const [isSavingRecord, setIsSavingRecord] = useState(false);
  const [isSavingNote, setIsSavingNote] = useState(false);
  const [deletingNoteId, setDeletingNoteId] = useState<string | null>(null);

  const [candidateFormError, setCandidateFormError] = useState<string | null>(null);
  const [candidateFormSuccess, setCandidateFormSuccess] = useState<string | null>(null);
  const [recordActionError, setRecordActionError] = useState<string | null>(null);
  const [recordActionSuccess, setRecordActionSuccess] = useState<string | null>(null);
  const [noteActionError, setNoteActionError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) {
      return;
    }

    let active = true;

    async function loadCandidateOnMount() {
      setCandidateFetchStatus("loading");
      setCandidateFetchError(null);

      try {
        const loadedCandidate = await fetchCandidateById(id);
        if (!active) {
          return;
        }

        setCandidate(loadedCandidate);
        setCandidateFormValues(getCandidateFormValues(loadedCandidate));
        setStatusValue(getCandidateStatus(loadedCandidate));
        setStageValue(getCandidateStage(loadedCandidate));
        setCandidateFetchStatus("success");
      } catch (loadError) {
        if (!active) {
          return;
        }

        setCandidateFetchError(loadError instanceof Error ? loadError.message : "Unable to load candidate");
        setCandidateFetchStatus("error");
      }
    }

    async function loadNotesOnMount() {
      setNotesFetchStatus("loading");
      setNotesFetchError(null);

      try {
        const loadedNotes = await fetchCandidateNotes(id);
        if (!active) {
          return;
        }

        setNotes(loadedNotes);
        setNotesFetchStatus("success");
      } catch (loadError) {
        if (!active) {
          return;
        }

        setNotesFetchError(loadError instanceof Error ? loadError.message : "Unable to load notes");
        setNotesFetchStatus("error");
      }
    }

    void loadCandidateOnMount();
    void loadNotesOnMount();

    return () => {
      active = false;
    };
  }, [id]);

  async function handleRecordUpdate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!candidate || !id) {
      return;
    }

    const payload = {
      status: statusValue.trim(),
      stage: stageValue.trim(),
    };

    setIsSavingRecord(true);
    setRecordActionError(null);
    setRecordActionSuccess(null);

    try {
      const updatedCandidate = await updateCandidateStatusStage(id, payload);
      const nextCandidate = updatedCandidate ?? applyStatusAndStage(candidate, payload);

      setCandidate(nextCandidate);
      setCandidateFormValues(getCandidateFormValues(nextCandidate));
      setStatusValue(getCandidateStatus(nextCandidate));
      setStageValue(getCandidateStage(nextCandidate));
      setRecordActionSuccess("Status and stage updated.");
    } catch (updateError) {
      setRecordActionError(updateError instanceof Error ? updateError.message : "Unable to update candidate");
    } finally {
      setIsSavingRecord(false);
    }
  }

  async function handleCandidateSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!candidate || !id) {
      return;
    }

    const validationError = validateCandidateForm(candidateFormValues);
    if (validationError) {
      setCandidateFormError(validationError);
      setCandidateFormSuccess(null);
      return;
    }

    setIsSubmittingCandidateForm(true);
    setCandidateFormError(null);
    setCandidateFormSuccess(null);

    try {
      const nextCandidatePayload = applyCandidateFormValues(candidate, candidateFormValues);
      const updatedCandidate = await replaceCandidate(id, nextCandidatePayload);
      const nextCandidate = updatedCandidate ?? nextCandidatePayload;

      setCandidate(nextCandidate);
      setCandidateFormValues(getCandidateFormValues(nextCandidate));
      setStatusValue(getCandidateStatus(nextCandidate));
      setStageValue(getCandidateStage(nextCandidate));
      setCandidateFormSuccess("Candidate details updated successfully.");
    } catch (updateError) {
      setCandidateFormError(updateError instanceof Error ? updateError.message : "Unable to save candidate");
    } finally {
      setIsSubmittingCandidateForm(false);
    }
  }

  async function handleAddNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!id) {
      return;
    }

    const noteContent = newNote.trim();
    if (!noteContent) {
      return;
    }

    setIsSavingNote(true);
    setNoteActionError(null);

    try {
      const createdNote = await createCandidateNote(id, noteContent);

      if (createdNote) {
        setNotes((currentNotes) => [createdNote, ...currentNotes]);
      } else {
        const loadedNotes = await fetchCandidateNotes(id);
        setNotes(loadedNotes);
        setNotesFetchStatus("success");
        setNotesFetchError(null);
      }

      setNewNote("");
    } catch (createError) {
      setNoteActionError(createError instanceof Error ? createError.message : "Unable to add note");
    } finally {
      setIsSavingNote(false);
    }
  }

  async function handleDeleteNote(noteId: string) {
    if (!id) {
      return;
    }

    setDeletingNoteId(noteId);
    setNoteActionError(null);

    try {
      await deleteCandidateNote(id, noteId);
      setNotes((currentNotes) => currentNotes.filter((note) => note.id !== noteId));
    } catch (deleteError) {
      setNoteActionError(deleteError instanceof Error ? deleteError.message : "Unable to delete note");
    } finally {
      setDeletingNoteId(null);
    }
  }

  const candidateName = getCandidateFullName(candidate);
  const candidateEmail = getStringField(candidate, ["email", "emailAddress", "contactEmail"]);
  const candidatePosition = getStringField(candidate, ["position", "appliedPosition", "role", "jobTitle"]);
  const detailEntries = Object.entries(candidate ?? {}).filter(
    ([key]) =>
      ![
        "status",
        "currentStatus",
        "stage",
        "currentStage",
        "pipelineStage",
        "fullName",
        "name",
        "full_name",
        "candidateName",
        "firstName",
        "first_name",
        "lastName",
        "last_name",
        "email",
        "emailAddress",
        "contactEmail",
        "position",
        "appliedPosition",
        "role",
        "jobTitle",
      ].includes(key),
  );

  return (
    <main className="px-4 py-8 sm:px-6 sm:py-10">
      <div className="mx-auto w-full max-w-5xl rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-6 shadow-[0_22px_58px_-40px_rgba(37,99,235,0.45)] sm:p-8">
        <header className="mb-6 border-b border-[color:var(--border-soft)] pb-5">
          <Link
            href="/"
            className="mb-4 inline-flex text-sm font-semibold text-[color:var(--flow-blue)] transition hover:text-[color:var(--flow-soft-blue)]"
          >
            {"<- "}Back to candidates
          </Link>
          <h1 className="font-brand-display text-2xl font-semibold text-[color:var(--text-strong)]">Candidate Detail</h1>
          <p className="mt-2 text-sm text-[color:var(--text-muted)]">Candidate ID: {id || "-"}</p>
        </header>

        {candidateFetchStatus === "loading" ? (
          <StateMessage tone="info">
            Fetching record from {apiBaseUrl ? `${apiBaseUrl}/records/${id}` : "NEXT_PUBLIC_API_URL/records/{id}"}...
          </StateMessage>
        ) : null}

        {candidateFetchStatus === "error" && candidateFetchError ? (
          <StateMessage tone="error">Could not load candidate: {candidateFetchError}</StateMessage>
        ) : null}

        {candidateFetchStatus === "success" && candidate ? (
          <div className="space-y-6">
            <section className="rounded-2xl border border-[color:var(--border-soft)] bg-[color:var(--flow-blue)]/5 p-5">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <h2 className="font-brand-display text-xl font-semibold text-[color:var(--text-strong)]">{candidateName}</h2>
                  <p className="mt-1 text-sm text-[color:var(--text-muted)]">{candidateEmail || "No email available"}</p>
                  <p className="mt-1 text-sm text-[color:var(--text-muted)]">{candidatePosition || "No position available"}</p>
                </div>
              </div>

              <form className="mt-5 grid gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end" onSubmit={handleRecordUpdate}>
                <label className="block text-sm text-[color:var(--text-muted)]">
                  <span className="mb-1 block font-medium">Status</span>
                  <input
                    value={statusValue}
                    onChange={(event) => setStatusValue(event.target.value)}
                    className="w-full rounded-xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] px-3 py-2 text-[color:var(--text-strong)] outline-none transition focus:border-[color:var(--flow-blue)]"
                    placeholder="Update candidate status"
                  />
                </label>

                <label className="block text-sm text-[color:var(--text-muted)]">
                  <span className="mb-1 block font-medium">Stage</span>
                  <input
                    value={stageValue}
                    onChange={(event) => setStageValue(event.target.value)}
                    className="w-full rounded-xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] px-3 py-2 text-[color:var(--text-strong)] outline-none transition focus:border-[color:var(--flow-blue)]"
                    placeholder="Update pipeline stage"
                  />
                </label>

                <button
                  type="submit"
                  disabled={isSavingRecord}
                  className="rounded-xl bg-[color:var(--flow-blue)] px-4 py-2 text-sm font-semibold text-white transition hover:bg-[color:var(--flow-soft-blue)] disabled:cursor-not-allowed disabled:bg-[color:var(--flow-soft-blue)]/70"
                >
                  {isSavingRecord ? "Saving..." : "Save changes"}
                </button>
              </form>

              {recordActionError ? (
                <StateMessage tone="error" className="mt-4">
                  Could not update candidate: {recordActionError}
                </StateMessage>
              ) : null}

              {recordActionSuccess ? (
                <StateMessage tone="success" className="mt-4">
                  {recordActionSuccess}
                </StateMessage>
              ) : null}
            </section>

            <section className="rounded-2xl border border-[color:var(--border-soft)] p-5">
              <div>
                <h2 className="font-brand-display text-lg font-semibold text-[color:var(--text-strong)]">Edit candidate data</h2>
                <p className="mt-1 text-sm text-[color:var(--text-muted)]">Update the main profile fields and save the record with PUT /records/:id.</p>
              </div>

              <form className="mt-4 grid gap-4 sm:grid-cols-2" onSubmit={handleCandidateSave}>
                <label className="block text-sm text-[color:var(--text-muted)] sm:col-span-2">
                  <span className="mb-1 block font-medium">Full name</span>
                  <input
                    value={candidateFormValues.fullName}
                    onChange={(event) => {
                      setCandidateFormSuccess(null);
                      setCandidateFormError(null);
                      setCandidateFormValues((currentValues) => ({ ...currentValues, fullName: event.target.value }));
                    }}
                    className="w-full rounded-xl border border-[color:var(--border-soft)] px-3 py-2 text-[color:var(--text-strong)] outline-none transition focus:border-[color:var(--flow-blue)]"
                    placeholder="Candidate full name"
                  />
                </label>

                <label className="block text-sm text-[color:var(--text-muted)]">
                  <span className="mb-1 block font-medium">Email</span>
                  <input
                    type="email"
                    value={candidateFormValues.email}
                    onChange={(event) => {
                      setCandidateFormSuccess(null);
                      setCandidateFormError(null);
                      setCandidateFormValues((currentValues) => ({ ...currentValues, email: event.target.value }));
                    }}
                    className="w-full rounded-xl border border-[color:var(--border-soft)] px-3 py-2 text-[color:var(--text-strong)] outline-none transition focus:border-[color:var(--flow-blue)]"
                    placeholder="candidate@example.com"
                  />
                </label>

                <label className="block text-sm text-[color:var(--text-muted)]">
                  <span className="mb-1 block font-medium">Position</span>
                  <input
                    value={candidateFormValues.position}
                    onChange={(event) => {
                      setCandidateFormSuccess(null);
                      setCandidateFormError(null);
                      setCandidateFormValues((currentValues) => ({ ...currentValues, position: event.target.value }));
                    }}
                    className="w-full rounded-xl border border-[color:var(--border-soft)] px-3 py-2 text-[color:var(--text-strong)] outline-none transition focus:border-[color:var(--flow-blue)]"
                    placeholder="Applied position"
                  />
                </label>

                <label className="block text-sm text-[color:var(--text-muted)]">
                  <span className="mb-1 block font-medium">Status</span>
                  <input
                    value={candidateFormValues.status}
                    onChange={(event) => {
                      setCandidateFormSuccess(null);
                      setCandidateFormError(null);
                      setCandidateFormValues((currentValues) => ({ ...currentValues, status: event.target.value }));
                    }}
                    className="w-full rounded-xl border border-[color:var(--border-soft)] px-3 py-2 text-[color:var(--text-strong)] outline-none transition focus:border-[color:var(--flow-blue)]"
                    placeholder="Current status"
                  />
                </label>

                <label className="block text-sm text-[color:var(--text-muted)]">
                  <span className="mb-1 block font-medium">Stage</span>
                  <input
                    value={candidateFormValues.stage}
                    onChange={(event) => {
                      setCandidateFormSuccess(null);
                      setCandidateFormError(null);
                      setCandidateFormValues((currentValues) => ({ ...currentValues, stage: event.target.value }));
                    }}
                    className="w-full rounded-xl border border-[color:var(--border-soft)] px-3 py-2 text-[color:var(--text-strong)] outline-none transition focus:border-[color:var(--flow-blue)]"
                    placeholder="Pipeline stage"
                  />
                </label>

                <div className="sm:col-span-2">
                  <button
                    type="submit"
                    disabled={isSubmittingCandidateForm}
                    className="rounded-xl bg-[color:var(--flow-blue)] px-4 py-2 text-sm font-semibold text-white transition hover:bg-[color:var(--flow-soft-blue)] disabled:cursor-not-allowed disabled:bg-[color:var(--flow-soft-blue)]/70"
                  >
                    {isSubmittingCandidateForm ? "Saving candidate..." : "Save candidate data"}
                  </button>
                </div>
              </form>

              {candidateFormError ? (
                <StateMessage tone="error" className="mt-4">
                  {candidateFormError}
                </StateMessage>
              ) : null}

              {candidateFormSuccess ? (
                <StateMessage tone="success" className="mt-4">
                  {candidateFormSuccess}
                </StateMessage>
              ) : null}
            </section>

            <section className="rounded-2xl border border-[color:var(--border-soft)] p-5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="font-brand-display text-lg font-semibold text-[color:var(--text-strong)]">Notes</h2>
                  <p className="text-sm text-[color:var(--text-muted)]">Track conversation history and recruiting context.</p>
                </div>
                {notesFetchStatus === "loading" ? <p className="text-sm text-[color:var(--text-muted)]">Loading notes...</p> : null}
              </div>

              <form className="mt-4 space-y-3" onSubmit={handleAddNote}>
                <label className="block text-sm text-[color:var(--text-muted)]">
                  <span className="mb-1 block font-medium">Add note</span>
                  <textarea
                    value={newNote}
                    onChange={(event) => setNewNote(event.target.value)}
                    className="min-h-28 w-full rounded-xl border border-[color:var(--border-soft)] px-3 py-2 text-[color:var(--text-strong)] outline-none transition focus:border-[color:var(--flow-blue)]"
                    placeholder="Write a note for this candidate"
                  />
                </label>
                <button
                  type="submit"
                  disabled={isSavingNote || !newNote.trim()}
                    className="rounded-xl bg-[color:var(--flow-blue)] px-4 py-2 text-sm font-semibold text-white transition hover:bg-[color:var(--flow-soft-blue)] disabled:cursor-not-allowed disabled:bg-[color:var(--flow-soft-blue)]/70"
                >
                  {isSavingNote ? "Adding..." : "Add note"}
                </button>
              </form>

              {notesFetchStatus === "error" && notesFetchError ? (
                <StateMessage tone="error" className="mt-4">
                  Could not load notes: {notesFetchError}
                </StateMessage>
              ) : null}

              {noteActionError ? (
                <StateMessage tone="error" className="mt-4">
                  Note action failed: {noteActionError}
                </StateMessage>
              ) : null}

              {notesFetchStatus === "success" && notes.length === 0 ? (
                <p className="mt-4 text-sm text-[color:var(--text-muted)]">No notes have been added for this candidate yet.</p>
              ) : null}

              {notesFetchStatus === "success" && notes.length > 0 ? (
                <ul className="mt-4 space-y-3">
                  {notes.map((note) => (
                    <li key={note.id} className="rounded-xl border border-[color:var(--border-soft)] p-4">
                      <div className="flex items-start justify-between gap-4">
                        <div>
                          <p className="whitespace-pre-wrap break-words text-sm text-[color:var(--text-strong)]">{note.body}</p>
                          <p className="mt-2 text-xs text-[color:var(--text-muted)]">
                            {[note.author, note.createdAt].filter(Boolean).join(" • ") || "No metadata available"}
                          </p>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleDeleteNote(note.id)}
                          disabled={deletingNoteId === note.id}
                          className="shrink-0 rounded-lg border border-[color:var(--flow-accent)]/45 px-3 py-2 text-sm font-medium text-[color:var(--flow-accent)] transition hover:bg-[color:var(--flow-accent)]/10 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {deletingNoteId === note.id ? "Deleting..." : "Delete"}
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>

            <section className="space-y-3">
              {detailEntries.map(([key, value]) => (
                <div key={key} className="rounded-xl border border-[color:var(--border-soft)] p-4">
                  <p className="text-xs font-semibold uppercase tracking-wide text-[color:var(--text-muted)]">{key}</p>
                  <p className="mt-1 whitespace-pre-wrap break-words text-sm text-[color:var(--text-strong)]">{toDisplayText(value)}</p>
                </div>
              ))}
            </section>
          </div>
        ) : null}
      </div>
    </main>
  );
}
