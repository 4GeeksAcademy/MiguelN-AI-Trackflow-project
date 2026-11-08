"use client";

import { StateMessage } from "@/components/common/StateMessage";
import type { CandidateFormValues } from "@/types/candidate";
import type { FormEvent } from "react";

type CandidateCreateModalProps = {
  candidateFormValues: CandidateFormValues;
  isCreatingCandidate: boolean;
  createCandidateError: string | null;
  createCandidateSuccess: string | null;
  onClose: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onFieldChange: (field: keyof CandidateFormValues, value: string) => void;
  onReset: () => void;
};

export function CandidateCreateModal({
  candidateFormValues,
  isCreatingCandidate,
  createCandidateError,
  createCandidateSuccess,
  onClose,
  onSubmit,
  onFieldChange,
  onReset,
}: CandidateCreateModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[color:var(--flow-blue)]/35 px-4 py-6">
      <div
        className="w-full max-w-2xl rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-6 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="font-brand-display text-xl font-semibold text-[color:var(--text-strong)]">
              Register new candidate
            </h2>
            <p className="mt-1 text-sm text-[color:var(--text-muted)]">
              Create a candidate record with the required profile and pipeline fields.
            </p>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-[color:var(--border-soft)] px-3 py-2 text-sm text-[color:var(--text-muted)] transition hover:bg-[color:var(--flow-blue)]/5"
          >
            Close
          </button>
        </div>

        <form className="mt-5 grid gap-4 sm:grid-cols-2" onSubmit={onSubmit}>
          <label className="block text-sm text-[color:var(--text-muted)] sm:col-span-2">
            <span className="mb-1 block font-medium">Full name</span>
            <input
              value={candidateFormValues.fullName}
              onChange={(event) => onFieldChange("fullName", event.target.value)}
              className="w-full rounded-xl border border-[color:var(--border-soft)] px-3 py-2 text-[color:var(--text-strong)] outline-none transition focus:border-[color:var(--flow-blue)]"
              placeholder="Candidate full name"
            />
          </label>

          <label className="block text-sm text-[color:var(--text-muted)]">
            <span className="mb-1 block font-medium">Email</span>
            <input
              type="email"
              value={candidateFormValues.email}
              onChange={(event) => onFieldChange("email", event.target.value)}
              className="w-full rounded-xl border border-[color:var(--border-soft)] px-3 py-2 text-[color:var(--text-strong)] outline-none transition focus:border-[color:var(--flow-blue)]"
              placeholder="candidate@example.com"
            />
          </label>

          <label className="block text-sm text-[color:var(--text-muted)]">
            <span className="mb-1 block font-medium">Position</span>
            <input
              value={candidateFormValues.position}
              onChange={(event) => onFieldChange("position", event.target.value)}
              className="w-full rounded-xl border border-[color:var(--border-soft)] px-3 py-2 text-[color:var(--text-strong)] outline-none transition focus:border-[color:var(--flow-blue)]"
              placeholder="Applied position"
            />
          </label>

          <label className="block text-sm text-[color:var(--text-muted)]">
            <span className="mb-1 block font-medium">Status</span>
            <input
              value={candidateFormValues.status}
              onChange={(event) => onFieldChange("status", event.target.value)}
              className="w-full rounded-xl border border-[color:var(--border-soft)] px-3 py-2 text-[color:var(--text-strong)] outline-none transition focus:border-[color:var(--flow-blue)]"
              placeholder="Current status"
            />
          </label>

          <label className="block text-sm text-[color:var(--text-muted)]">
            <span className="mb-1 block font-medium">Stage</span>
            <input
              value={candidateFormValues.stage}
              onChange={(event) => onFieldChange("stage", event.target.value)}
              className="w-full rounded-xl border border-[color:var(--border-soft)] px-3 py-2 text-[color:var(--text-strong)] outline-none transition focus:border-[color:var(--flow-blue)]"
              placeholder="Pipeline stage"
            />
          </label>

          <div className="flex items-center gap-3 sm:col-span-2">
            <button
              type="submit"
              disabled={isCreatingCandidate}
              className="rounded-xl bg-[color:var(--flow-blue)] px-4 py-2 text-sm font-semibold text-white transition hover:bg-[color:var(--flow-soft-blue)] disabled:cursor-not-allowed disabled:bg-[color:var(--flow-soft-blue)]/70"
            >
              {isCreatingCandidate ? "Creating..." : "Create candidate"}
            </button>

            <button
              type="button"
              onClick={onReset}
              className="rounded-xl border border-[color:var(--border-soft)] px-4 py-2 text-sm font-medium text-[color:var(--text-muted)] transition hover:bg-[color:var(--flow-blue)]/5"
            >
              Reset
            </button>
          </div>
        </form>

        {createCandidateError ? (
          <StateMessage tone="error" className="mt-4">
            {createCandidateError}
          </StateMessage>
        ) : null}

        {createCandidateSuccess ? (
          <StateMessage tone="success" className="mt-4">
            {createCandidateSuccess}
          </StateMessage>
        ) : null}
      </div>
    </div>
  );
}