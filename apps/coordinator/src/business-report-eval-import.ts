import { createHash } from "node:crypto";
import { BusinessReportEvalError } from "./business-report-eval.js";

export const BUSINESS_REPORT_EVAL_IMPORT_PROFILE = "business-report-eval-import.v1" as const;
const sha = (value: string) => createHash("sha256").update(value).digest("hex");

/** A private application namespace, using the RFC 9562 UUIDv8 hash layout. */
export function reportEvalDatasetId(project: string, actor: string, requestId: string) {
  const bytes = Buffer.from(sha(JSON.stringify([BUSINESS_REPORT_EVAL_IMPORT_PROFILE, project, actor, requestId])).slice(0, 32), "hex");
  bytes[6] = (bytes[6]! & 0x0f) | 0x80; bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function normalizeReportEvalImport(raw: unknown) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new BusinessReportEvalError("invalid_request", "Нужны проверенный пример, rubric и подтверждение review");
  const value = raw as Record<string, unknown>;
  if (Object.keys(value).some(key => !["requestId", "expectedSha256", "expectedRevision", "expectedCandidateSha256", "name", "description", "referenceOutput", "rubric", "reviewConfirmed"].includes(key))
    || typeof value.requestId !== "string" || !/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(value.requestId)
    || typeof value.expectedSha256 !== "string" || !/^[a-f0-9]{64}$/.test(value.expectedSha256)
    || typeof value.expectedCandidateSha256 !== "string" || !/^[a-f0-9]{64}$/.test(value.expectedCandidateSha256)
    || typeof value.expectedRevision !== "number" || !Number.isInteger(value.expectedRevision) || value.expectedRevision < 1 || value.expectedRevision > 1000
    || typeof value.name !== "string" || !value.name.trim() || value.name.trim().length > 120
    || (value.description !== undefined && typeof value.description !== "string") || String(value.description ?? "").trim().length > 2000
    || typeof value.referenceOutput !== "string" || !value.referenceOutput.trim() || value.referenceOutput.length > 100_000
    || Buffer.byteLength(value.referenceOutput) > 200_000 || !Array.isArray(value.rubric) || !value.rubric.length || value.rubric.length > 20
    || value.reviewConfirmed !== true) {
    throw new BusinessReportEvalError("invalid_request", "Проверьте UUID, версии, название, reference, rubric и явное подтверждение review");
  }
  return { requestId: value.requestId, expectedSha256: value.expectedSha256, expectedRevision: value.expectedRevision,
    expectedCandidateSha256: value.expectedCandidateSha256, name: value.name.trim(), description: String(value.description ?? "").trim(),
    referenceOutput: value.referenceOutput.trim(), rubric: value.rubric, reviewConfirmed: true as const };
}

export interface BusinessReportEvalImportReceipt {
  datasetId: string; datasetVersion: 1; datasetContentSha256: string; candidateSha256: string; created: boolean;
}
