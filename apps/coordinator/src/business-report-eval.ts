export const BUSINESS_REPORT_EVAL_PROFILE = "business-report-eval-candidate.v1" as const;

export class BusinessReportEvalError extends Error {
  constructor(readonly code: string, message: string, readonly status = 400) { super(message); this.name = "BusinessReportEvalError"; }
}

export function normalizeReportEvalRequest(raw: unknown) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new BusinessReportEvalError("invalid_request", "Нужны SHA-256 отчёта и прочитанная ревизия решения");
  const value = raw as Record<string, unknown>;
  if (Object.keys(value).some(key => !["expectedSha256", "expectedRevision"].includes(key))
    || typeof value.expectedSha256 !== "string" || !/^[a-f0-9]{64}$/.test(value.expectedSha256)
    || typeof value.expectedRevision !== "number" || !Number.isInteger(value.expectedRevision) || value.expectedRevision < 1 || value.expectedRevision > 1000) {
    throw new BusinessReportEvalError("invalid_request", "Нужны текущий SHA-256 отчёта и сохранённая ревизия решения");
  }
  return { expectedSha256: value.expectedSha256, expectedRevision: value.expectedRevision };
}

export interface BusinessReportEvalCandidate {
  profile: typeof BUSINESS_REPORT_EVAL_PROFILE;
  purpose: "development";
  holdoutEligible: false;
  referenceReviewRequired: true;
  replay: { automatic: false; scope: "single_agent"; context: "requires_review" };
  source: {
    projectId: string; artifactId: string; sourceSha256: string; runId: string; reportStageId: string;
    processInstanceId: string; processId: string; processVersion: number; graphSha256: string;
    decisionId: string; decisionRevision: number; decision: "accepted" | "rework_requested"; reason: string;
    agentStageId: string; agentId: string; promptSha256: string; definitionSha256: string;
    requestedModel: string | null; runtime: string; runtimeProfile: string;
    inputSha256: string; observedOutputSha256: string; schemaSha256: string;
  };
  example: { name: string; input: string; referenceOutput: ""; requiredTerms: string[]; forbiddenTerms: string[]; knowledgeCollectionIds: string[] };
  observedOutput: string;
  currentKnowledgeSnapshot: Record<string, unknown>;
  contentSha256: string;
}
