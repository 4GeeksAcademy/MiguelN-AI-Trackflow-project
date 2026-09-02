"use client";

import { StateMessage } from "@/components/common/StateMessage";
import { getApiBaseUrl } from "@/lib/api-client";
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
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useEffect, useMemo, useState } from "react";

type CandidateListItem = {
  id: string;
  fullName: string;
  email: string;
  position: string;
  status: string;
  stage: string;
};

const apiBaseUrl = getApiBaseUrl();

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
      const nextError = loadError instanceof Error ? loadError.message : "Unable to load candidates";
      setListError(nextError);
      setListStatus("error");
      throw new Error(nextError);
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

        const nextError = loadError instanceof Error ? loadError.message : "Unable to load candidates";
        setListError(nextError);
        setListStatus("error");
      }
    }

    void loadCandidatesOnMount();

    return () => {
      active = false;
    };
  }, []);

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

    try {
      await createCandidate(candidateFormValues);
      await refreshCandidates({ silent: true });
      setCandidateFormValues(emptyCandidateFormValues());
      setCreateCandidateSuccess("Candidate created successfully.");
    } catch (createError) {
      setCreateCandidateError(createError instanceof Error ? createError.message : "Unable to create candidate");
    } finally {
      setIsCreatingCandidate(false);
    }
  }

  return (
    <main className="px-4 py-8 sm:px-6 sm:py-10">
      <div className="mx-auto w-full max-w-6xl space-y-6">
        <section className="overflow-hidden rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)]/90 p-6 shadow-[0_24px_60px_-36px_rgba(37,99,235,0.45)] sm:p-8">
          <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-[color:var(--border-soft)] bg-[color:var(--flow-blue)]/10 px-4 py-2 text-xs font-semibold uppercase tracking-[0.16em] text-[color:var(--flow-blue)]">
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
              <p className="mt-2 text-sm font-semibold text-[color:var(--flow-accent)]">Faster routes, smarter deliveries</p>
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

          <select
            value={searchParams.get("status") ?? ""}
            onChange={(event) => updateQueryFilter("status", event.target.value)}
            className="rounded-xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] px-3 py-2 text-sm text-[color:var(--text-strong)] outline-none ring-[color:var(--flow-blue)] transition focus:ring-2"
          >
            <option value="">All statuses</option>
            {statusOptions.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>

          <select
            value={searchParams.get("stage") ?? ""}
            onChange={(event) => updateQueryFilter("stage", event.target.value)}
            className="rounded-xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] px-3 py-2 text-sm text-[color:var(--text-strong)] outline-none ring-[color:var(--flow-blue)] transition focus:ring-2"
          >
            <option value="">All stages</option>
            {stageOptions.map((stage) => (
              <option key={stage} value={stage}>
                {stage}
              </option>
            ))}
          </select>
        </section>

        {listStatus === "loading" ? (
          <StateMessage tone="info">
            Loading candidates from {apiBaseUrl ? `${apiBaseUrl}/records` : "NEXT_PUBLIC_API_URL/records"}...
          </StateMessage>
        ) : null}

        {listStatus === "error" && listError ? (
          <StateMessage tone="error">Could not load candidates: {listError}</StateMessage>
        ) : null}

        {listStatus === "success" && normalizedCandidates.length === 0 ? (
          <p className="text-[color:var(--text-muted)]">
            No candidates were returned by {apiBaseUrl ? `${apiBaseUrl}/records` : "NEXT_PUBLIC_API_URL/records"}.
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[color:var(--flow-blue)]/35 px-4 py-6">
          <div className="w-full max-w-2xl rounded-3xl border border-[color:var(--border-soft)] bg-[color:var(--surface)] p-6 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="font-brand-display text-xl font-semibold text-[color:var(--text-strong)]">Register new candidate</h2>
                <p className="mt-1 text-sm text-[color:var(--text-muted)]">
                  Create a candidate record with the required profile and pipeline fields.
                </p>
              </div>

              <button
                type="button"
                onClick={() => setIsCreateModalOpen(false)}
                className="rounded-xl border border-[color:var(--border-soft)] px-3 py-2 text-sm text-[color:var(--text-muted)] transition hover:bg-[color:var(--flow-blue)]/5"
              >
                Close
              </button>
            </div>

            <form className="mt-5 grid gap-4 sm:grid-cols-2" onSubmit={handleCreateCandidate}>
              <label className="block text-sm text-[color:var(--text-muted)] sm:col-span-2">
                <span className="mb-1 block font-medium">Full name</span>
                <input
                  value={candidateFormValues.fullName}
                  onChange={(event) => {
                    setCreateCandidateError(null);
                    setCreateCandidateSuccess(null);
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
                    setCreateCandidateError(null);
                    setCreateCandidateSuccess(null);
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
                    setCreateCandidateError(null);
                    setCreateCandidateSuccess(null);
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
                    setCreateCandidateError(null);
                    setCreateCandidateSuccess(null);
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
                    setCreateCandidateError(null);
                    setCreateCandidateSuccess(null);
                    setCandidateFormValues((currentValues) => ({ ...currentValues, stage: event.target.value }));
                  }}
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
                  onClick={() => {
                    setCandidateFormValues(emptyCandidateFormValues());
                    setCreateCandidateError(null);
                    setCreateCandidateSuccess(null);
                  }}
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
      ) : null}
    </main>
  );
}
