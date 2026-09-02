import type {
  CandidateFormValues,
  CandidateListResponse,
  CandidateRecord,
  CandidateStatusStageUpdate,
} from "@/types/candidate";

export function toDisplayText(value: unknown): string {
  if (value === null || value === undefined) {
    return "-";
  }

  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }

  return JSON.stringify(value);
}

export function candidateId(candidate: CandidateRecord, index: number): string {
  const possibleId = candidate.id ?? candidate._id ?? candidate.candidateId ?? candidate.candidate_id;
  return possibleId ? String(possibleId) : String(index);
}

export function parseNumericValue(value: unknown, fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  return fallback;
}

export function getStringField(candidate: CandidateRecord | null, keys: string[]): string {
  if (!candidate) {
    return "";
  }

  for (const key of keys) {
    const value = candidate[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }

  return "";
}

export function getCandidateFullName(candidate: CandidateRecord | null): string {
  const fullName = getStringField(candidate, ["fullName", "name", "full_name", "candidateName"]);
  if (fullName) {
    return fullName;
  }

  const firstName = getStringField(candidate, ["firstName", "first_name"]);
  const lastName = getStringField(candidate, ["lastName", "last_name"]);
  const joined = `${firstName} ${lastName}`.trim();

  return joined || "Unnamed candidate";
}

export function getCandidateStatus(candidate: CandidateRecord | null): string {
  return getStringField(candidate, ["status", "currentStatus"]);
}

export function getCandidateStage(candidate: CandidateRecord | null): string {
  return getStringField(candidate, ["stage", "currentStage", "pipelineStage"]);
}

export function emptyCandidateFormValues(): CandidateFormValues {
  return {
    fullName: "",
    email: "",
    position: "",
    status: "",
    stage: "",
  };
}

export function validateCandidateForm(values: CandidateFormValues): string | null {
  if (!values.fullName.trim()) {
    return "Full name is required.";
  }

  if (!values.email.trim()) {
    return "Email is required.";
  }

  if (!values.position.trim()) {
    return "Position is required.";
  }

  if (!values.status.trim()) {
    return "Status is required.";
  }

  if (!values.stage.trim()) {
    return "Stage is required.";
  }

  return null;
}

export function buildCandidatePayload(values: CandidateFormValues): CandidateRecord {
  const normalizedName = values.fullName.trim();
  const normalizedEmail = values.email.trim();
  const normalizedPosition = values.position.trim();
  const normalizedStatus = values.status.trim();
  const normalizedStage = values.stage.trim();

  return {
    fullName: normalizedName,
    name: normalizedName,
    email: normalizedEmail,
    position: normalizedPosition,
    status: normalizedStatus,
    currentStatus: normalizedStatus,
    stage: normalizedStage,
    currentStage: normalizedStage,
  };
}

export function applyStatusAndStage(candidate: CandidateRecord, update: CandidateStatusStageUpdate): CandidateRecord {
  const nextCandidate: CandidateRecord = { ...candidate };

  if ("status" in nextCandidate || !("currentStatus" in nextCandidate)) {
    nextCandidate.status = update.status;
  }
  if ("currentStatus" in nextCandidate) {
    nextCandidate.currentStatus = update.status;
  }

  if ("stage" in nextCandidate || (!("currentStage" in nextCandidate) && !("pipelineStage" in nextCandidate))) {
    nextCandidate.stage = update.stage;
  }
  if ("currentStage" in nextCandidate) {
    nextCandidate.currentStage = update.stage;
  }
  if ("pipelineStage" in nextCandidate) {
    nextCandidate.pipelineStage = update.stage;
  }

  return nextCandidate;
}

export function getCandidateFormValues(candidate: CandidateRecord | null): CandidateFormValues {
  return {
    fullName: getCandidateFullName(candidate),
    email: getStringField(candidate, ["email", "emailAddress", "contactEmail"]),
    position: getStringField(candidate, ["position", "appliedPosition", "role", "jobTitle"]),
    status: getCandidateStatus(candidate),
    stage: getCandidateStage(candidate),
  };
}

export function applyCandidateFormValues(candidate: CandidateRecord, values: CandidateFormValues): CandidateRecord {
  const nextCandidate: CandidateRecord = applyStatusAndStage(candidate, {
    status: values.status.trim(),
    stage: values.stage.trim(),
  });
  const normalizedName = values.fullName.trim();
  const normalizedEmail = values.email.trim();
  const normalizedPosition = values.position.trim();
  const nameParts = normalizedName.split(/\s+/).filter(Boolean);

  if ("fullName" in nextCandidate || !("name" in nextCandidate)) {
    nextCandidate.fullName = normalizedName;
  }
  if ("name" in nextCandidate) {
    nextCandidate.name = normalizedName;
  }
  if ("full_name" in nextCandidate) {
    nextCandidate.full_name = normalizedName;
  }
  if ("candidateName" in nextCandidate) {
    nextCandidate.candidateName = normalizedName;
  }
  if ("firstName" in nextCandidate) {
    nextCandidate.firstName = nameParts[0] ?? "";
  }
  if ("first_name" in nextCandidate) {
    nextCandidate.first_name = nameParts[0] ?? "";
  }
  if ("lastName" in nextCandidate) {
    nextCandidate.lastName = nameParts.slice(1).join(" ");
  }
  if ("last_name" in nextCandidate) {
    nextCandidate.last_name = nameParts.slice(1).join(" ");
  }

  if ("email" in nextCandidate || (!("emailAddress" in nextCandidate) && !("contactEmail" in nextCandidate))) {
    nextCandidate.email = normalizedEmail;
  }
  if ("emailAddress" in nextCandidate) {
    nextCandidate.emailAddress = normalizedEmail;
  }
  if ("contactEmail" in nextCandidate) {
    nextCandidate.contactEmail = normalizedEmail;
  }

  if (
    "position" in nextCandidate ||
    (!("appliedPosition" in nextCandidate) && !("role" in nextCandidate) && !("jobTitle" in nextCandidate))
  ) {
    nextCandidate.position = normalizedPosition;
  }
  if ("appliedPosition" in nextCandidate) {
    nextCandidate.appliedPosition = normalizedPosition;
  }
  if ("role" in nextCandidate) {
    nextCandidate.role = normalizedPosition;
  }
  if ("jobTitle" in nextCandidate) {
    nextCandidate.jobTitle = normalizedPosition;
  }

  return nextCandidate;
}

export function normalizeCandidatesPayload(payload: unknown): CandidateListResponse {
  const normalized =
    typeof payload === "object" && payload !== null && Array.isArray((payload as { data?: unknown }).data)
      ? ((payload as { data: unknown[] }).data ?? [])
      : [];

  const total =
    typeof payload === "object" && payload !== null
      ? parseNumericValue((payload as { total?: unknown }).total, normalized.length)
      : normalized.length;
  const page =
    typeof payload === "object" && payload !== null
      ? parseNumericValue((payload as { page?: unknown }).page, 1)
      : 1;
  const limit =
    typeof payload === "object" && payload !== null
      ? parseNumericValue((payload as { limit?: unknown }).limit, normalized.length)
      : normalized.length;

  return {
    candidates: normalized.filter((item): item is CandidateRecord => typeof item === "object" && item !== null),
    total,
    page,
    limit,
  };
}

export function normalizeCandidatePayload(payload: unknown): CandidateRecord {
  if (typeof payload === "object" && payload !== null && typeof (payload as { data?: unknown }).data === "object") {
    const inner = (payload as { data?: unknown }).data;
    if (inner && typeof inner === "object") {
      return inner as CandidateRecord;
    }
  }

  if (typeof payload === "object" && payload !== null) {
    return payload as CandidateRecord;
  }

  return { value: payload };
}
