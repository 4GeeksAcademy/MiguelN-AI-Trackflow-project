export type CandidateRecord = {
  id?: string | number;
  _id?: string | number;
  candidateId?: string | number;
  candidate_id?: string | number;
  fullName?: string;
  name?: string;
  full_name?: string;
  candidateName?: string;
  firstName?: string;
  first_name?: string;
  lastName?: string;
  last_name?: string;
  email?: string;
  emailAddress?: string;
  contactEmail?: string;
  position?: string;
  appliedPosition?: string;
  role?: string;
  jobTitle?: string;
  status?: string;
  currentStatus?: string;
  stage?: string;
  currentStage?: string;
  pipelineStage?: string;
  [key: string]: unknown;
};

export type CandidateFormValues = {
  fullName: string;
  email: string;
  position: string;
  status: string;
  stage: string;
};

export type CandidateListResponse = {
  candidates: CandidateRecord[];
  total: number;
  page: number;
  limit: number;
};

export type CandidateStatusStageUpdate = {
  status: string;
  stage: string;
};
