"use client";

import { StateMessage } from "@/components/common/StateMessage";
import { RECORDS_ERROR_COPY, describeError } from "@/lib/friendly-error";
import {
  candidateId,
  emptyCandidateFormValues,
  getCandidateFullName,
  toDisplayText,
  validateCandidateForm,
} from "@/lib/candidate";
import { createCandidate, fetchCandidates } from "@/services/candidates-service";
import type { AsyncStatus } from "@/types/async-state";
import type { CandidateFormValues, CandidateRecord } from "@/types/candidate";
import dynamic from "next/dynamic";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useCallback, useEffect, useMemo, useState } from "react";

const CandidateCreateModal = dynamic(
  () => import("@/components/candidates/CandidateCreateModal").then((module) => module.CandidateCreateModal),
  {
    loading: () => (
      <div role="status" className="fixed inset-0 z-50 grid place-items-center bg-black/20">
        <span className="rounded-lg bg-white px-4 py-3 text-sm">Loading candidate form...</span>
      </div>
    ),
  },
);

type CandidateListItem = {
  id: string;
  fullName: string;
  email: string;
  position: string;
  status: string;
  stage: string;
};

type CandidateFilterProps = {
  label: string;
  placeholder: string;
  options: string[];
  value: string;
  onChange: (value: string) => void;
};

function CandidateFilter({ label, placeholder, options, value, onChange }: CandidateFilterProps) {
  return (
    <label className="block">
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] px-3 py-2 text-sm text-[color:var(--text-strong)] outline-none ring-[color:var(--flow-blue)] transition focus:ring-2"
      >
        <option value="">{placeholder}</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function CandidatesListPage() {
  return (
    <Suspense
      fallback={
        <main className="px-4 py-8 sm:px-6 sm:py-10">
          <div className="mx-auto w-full max-w-6xl">
            <StateMessage tone="info">Loading candidate pipeline...</StateMessage>
          </div>
        </main>
      }
    >
      <CandidatesListContent />
    </Suspense>
  );
}

function CandidatesListContent() {
  const [candidates, setCandidates] = useState<CandidateRecord[]>([]);
  const [totalCandidates, setTotalCandidates] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageLimit, setPageLimit] = useState(0);
  const [searchTerm, setSearchTerm] = useState("");
  const [listStatus, setListStatus] = useState<AsyncStatus>("loading");
  const [listError, setListError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isCreatingCandidate, setIsCreatingCandidate] = useState(false);
  const [createCandidateError, setCreateCandidateError] = useState<string | null>(null);
  const [createCandidateSuccess, setCreateCandidateSuccess] = useState<string | null>(null);
  const [candidateFormValues, setCandidateFormValues] = useState<CandidateFormValues>(emptyCandidateFormValues());

  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const statusFilter = (searchParams.get("status") ?? "").trim().toLowerCase();
  const stageFilter = (searchParams.get("stage") ?? "").trim().toLowerCase();

  const retry = useCallback(() => setAttempt((current) => current + 1), []);

  /**
   * Reloads the list in place after a create. It reports its own failure on the
   * list, with a retry, and never throws: a failed refresh must not be mistaken
   * for a failed create by the caller.
   */
  async function refreshCandidates(options?: { silent?: boolean }) {
    if (!options?.silent) {
      setListStatus("loading");
    }

    setListError(null);

    try {
      const data = await fetchCandidates();
      setCandidates(data.candidates);
      setTotalCandidates(data.total);
      setCurrentPage(data.page);
      setPageLimit(data.limit);
      setListStatus("success");
    } catch (loadError) {
      setListError(describeError(loadError, "Could not refresh the candidate list.", RECORDS_ERROR_COPY).message);
      setListStatus("error");
    }
  }

  useEffect(() => {
    let active = true;

    async function loadCandidatesOnMount() {
      setListStatus("loading");
      setListError(null);

      try {
        const data = await fetchCandidates();
        if (!active) {
          return;
        }

        setCandidates(data.candidates);
        setTotalCandidates(data.total);
        setCurrentPage(data.page);
        setPageLimit(data.limit);
        setListStatus("success");
      } catch (loadError) {
        if (!active) {
          return;
        }

        setListError(describeError(loadError, "Could not load the candidate list.", RECORDS_ERROR_COPY).message);
        setListStatus("error");
      }
    }

    void loadCandidatesOnMount();

    return () => {
      active = false;
    };
  }, [attempt]);

  const totalLabel = useMemo(() => {
    if (listStatus === "loading") {
      return "Loading candidates...";
    }

    return `${totalCandidates} total candidates`;
  }, [listStatus, totalCandidates]);

  const normalizedCandidates = useMemo<CandidateListItem[]>(() => {
    return candidates.map((candidate, index) => {
      const id = candidateId(candidate, index);
      const fullName = getCandidateFullName(candidate);
      const email = toDisplayText(candidate.email ?? candidate.emailAddress ?? candidate.contactEmail);
      const position = toDisplayText(
        candidate.position ?? candidate.appliedPosition ?? candidate.role ?? candidate.jobTitle,
      );
      const status = toDisplayText(candidate.status ?? candidate.currentStatus);
      const stage = toDisplayText(candidate.stage ?? candidate.currentStage ?? candidate.pipelineStage);

      return {
        id,
        fullName,
        email,
        position,
        status,
        stage,
      };
    });
  }, [candidates]);

  const statusOptions = useMemo(() => {
    const unique = new Set<string>();
    for (const candidate of normalizedCandidates) {
      if (candidate.status !== "-") {
        unique.add(candidate.status);
      }
    }

    return Array.from(unique).sort((a, b) => a.localeCompare(b));
  }, [normalizedCandidates]);

  const stageOptions = useMemo(() => {
    const unique = new Set<string>();
    for (const candidate of normalizedCandidates) {
      if (candidate.stage !== "-") {
        unique.add(candidate.stage);
      }
    }

    return Array.from(unique).sort((a, b) => a.localeCompare(b));
  }, [normalizedCandidates]);

  const filteredCandidates = useMemo(() => {
    const normalizedSearch = searchTerm.trim().toLowerCase();

    return normalizedCandidates.filter((candidate) => {
      const matchesSearch =
        !normalizedSearch ||
        candidate.fullName.toLowerCase().includes(normalizedSearch) ||
        candidate.email.toLowerCase().includes(normalizedSearch);
      const matchesStatus = !statusFilter || candidate.status.toLowerCase() === statusFilter;
      const matchesStage = !stageFilter || candidate.stage.toLowerCase() === stageFilter;

      return matchesSearch && matchesStatus && matchesStage;
    });
  }, [normalizedCandidates, searchTerm, statusFilter, stageFilter]);

  const pageLabel = useMemo(() => {
    if (listStatus === "loading") {
      return "Loading page info...";
    }

    return `Page ${currentPage} • Limit ${pageLimit} • Showing ${filteredCandidates.length}`;
  }, [currentPage, filteredCandidates.length, listStatus, pageLimit]);

  function updateQueryFilter(key: "status" | "stage", value: string): void {
    const params = new URLSearchParams(searchParams.toString());

    if (!value) {
      params.delete(key);
    } else {
      params.set(key, value);
    }

    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }

  async function handleCreateCandidate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const validationError = validateCandidateForm(candidateFormValues);
    if (validationError) {
      setCreateCandidateError(validationError);
      setCreateCandidateSuccess(null);
      return;
    }

    setIsCreatingCandidate(true);
    setCreateCandidateError(null);
    setCreateCandidateSuccess(null);

    let isCreated = false;

    try {
      await createCandidate(candidateFormValues);
      isCreated = true;
    } catch (createError) {
      setCreateCandidateError(
        describeError(
          createError,
          "Could not create the candidate. Check the details and try again.",
          RECORDS_ERROR_COPY,
        ).message,
      );
    } finally {
      setIsCreatingCandidate(false);
    }

    if (!isCreated) {
      return;
    }

    // The record exists from here on. The form is cleared and success is shown
    // before the list reloads, so a failed refresh cannot invite duplicate submission.
    setCandidateFormValues(emptyCandidateFormValues());
    setCreateCandidateSuccess("Candidate created successfully.");
    await refreshCandidates({ silent: true });
  }

  return (
    <main className="px-4 py-8 sm:px-6 sm:py-10">
      <div className="mx-auto w-full max-w-6xl space-y-6">
        <section className="overflow-hidden rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)]/90 p-6 shadow-[0_24px_60px_-36px_rgba(37,99,235,0.45)] sm:p-8">
          <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-[color:var(--border-soft)] bg-[color:var(--flow-blue)]/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.16em] text-[#1f56c8]">
            TrackFlow Tech
          </div>
          <h1 className="font-brand-display text-3xl font-bold leading-tight text-[color:var(--text-strong)] sm:text-4xl">
            Candidate Pipeline Control Center
          </h1>
          <p className="mt-3 max-w-3xl text-sm leading-relaxed text-[color:var(--text-muted)] sm:text-base">
            Built with the same TrackFlow identity used across our operations platform. Recruit faster, keep decisions visible,
            and move talent through each stage with confidence.
          </p>
          <div className="mt-6 grid gap-3 sm:grid-cols-3">
            <article className="rounded-2xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-4">
              <p className="text-xs uppercase tracking-[0.14em] text-[color:var(--text-muted)]">Candidates</p>
              <p className="mt-2 font-brand-display text-2xl font-bold text-[color:var(--flow-blue)]">{totalCandidates}</p>
            </article>
            <article className="rounded-2xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-4">
              <p className="text-xs uppercase tracking-[0.14em] text-[color:var(--text-muted)]">Page</p>
              <p className="mt-2 font-brand-display text-2xl font-bold text-[color:var(--flow-blue)]">{currentPage}</p>
            </article>
            <article className="rounded-2xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-4">
              <p className="text-xs uppercase tracking-[0.14em] text-[color:var(--text-muted)]">Motto</p>
              <p className="mt-2 text-sm font-semibold text-[#b55316]">Faster routes, smarter deliveries</p>
            </article>
          </div>
        </section>

        <div className="rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-6 shadow-[0_16px_38px_-28px_rgba(37,99,235,0.35)] sm:p-8">
          <header className="mb-6 border-b border-[color:var(--border-soft)] pb-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h2 className="font-brand-display text-2xl font-semibold text-[color:var(--text-strong)]">Candidate Pipeline</h2>
                <p className="mt-2 text-sm text-[color:var(--text-muted)]">{totalLabel}</p>
                <p className="mt-1 text-sm text-[color:var(--text-muted)]">{pageLabel}</p>
              </div>

              <button
                type="button"
                onClick={() => {
                  setCreateCandidateError(null);
                  setCreateCandidateSuccess(null);
                  setIsCreateModalOpen(true);
                }}
                className="rounded-xl bg-[color:var(--flow-blue)] px-4 py-2 text-sm font-semibold text-white transition hover:bg-[color:var(--flow-soft-blue)]"
              >
                Register candidate
              </button>
            </div>
          </header>

          <section className="mb-6 grid gap-3 sm:grid-cols-3">
            <input
              type="search"
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="Search by full name or email"
              className="rounded-xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] px-3 py-2 text-sm text-[color:var(--text-strong)] outline-none ring-[color:var(--flow-blue)] transition focus:ring-2"
            />
            <CandidateFilter
              label="Filter candidates by status"
              placeholder="All statuses"
              options={statusOptions}
              value={searchParams.get("status") ?? ""}
              onChange={(value) => updateQueryFilter("status", value)}
            />
            <CandidateFilter
              label="Filter candidates by stage"
              placeholder="All stages"
              options={stageOptions}
              value={searchParams.get("stage") ?? ""}
              onChange={(value) => updateQueryFilter("stage", value)}
            />
          </section>

        {listStatus === "loading" ? (
          <StateMessage tone="info">Loading candidates...</StateMessage>
        ) : null}

        {listStatus === "error" && listError ? (
          <StateMessage tone="error">
            <p>{listError}</p>
            <button
              type="button"
              onClick={retry}
              className="mt-3 inline-flex min-h-10 items-center rounded-xl border border-red-300 bg-white px-4 py-2 text-sm font-semibold text-red-700 transition hover:bg-red-100"
            >
              Try again
            </button>
          </StateMessage>
        ) : null}

        {listStatus === "success" && normalizedCandidates.length === 0 ? (
          <p className="text-[color:var(--text-muted)]">
            No candidates have been registered yet.
          </p>
        ) : null}

        {listStatus === "success" && normalizedCandidates.length > 0 && filteredCandidates.length === 0 ? (
          <p className="text-[color:var(--text-muted)]">No candidates match the current search and filters.</p>
        ) : null}

        {listStatus === "success" && filteredCandidates.length > 0 ? (
          <ul className="space-y-3">
            {filteredCandidates.map((candidate, index) => {
              return (
                <li key={`${candidate.id}-${index}`}>
                  <Link
                    href={`/candidates/${encodeURIComponent(candidate.id)}`}
                    className="block rounded-2xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-5 transition hover:-translate-y-0.5 hover:border-[color:var(--flow-soft-blue)] hover:bg-[color:var(--flow-blue)]/5"
                  >
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div>
                        <h3 className="font-brand-display text-lg font-semibold text-[color:var(--text-strong)]">{candidate.fullName}</h3>
                        <p className="text-sm text-[color:var(--text-muted)]">{candidate.email}</p>
                      </div>

                      <div className="text-sm text-[color:var(--text-muted)] sm:text-right">
                        <p>
                          <span className="font-medium">Position:</span> {candidate.position}
                        </p>
                        <p>
                          <span className="font-medium">Status:</span> {candidate.status}
                        </p>
                        <p>
                          <span className="font-medium">Stage:</span> {candidate.stage}
                        </p>
                      </div>
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        ) : null}
        </div>
      </div>

      {isCreateModalOpen ? (
        <CandidateCreateModal
          candidateFormValues={candidateFormValues}
          isCreatingCandidate={isCreatingCandidate}
          createCandidateError={createCandidateError}
          createCandidateSuccess={createCandidateSuccess}
          onClose={() => setIsCreateModalOpen(false)}
          onSubmit={handleCreateCandidate}
          onFieldChange={(field, value) => {
            setCreateCandidateError(null);
            setCreateCandidateSuccess(null);
            setCandidateFormValues((currentValues) => ({ ...currentValues, [field]: value }));
          }}
          onReset={() => {
            setCandidateFormValues(emptyCandidateFormValues());
            setCreateCandidateError(null);
            setCreateCandidateSuccess(null);
          }}
        />
      ) : null}
    </main>
  );
}
