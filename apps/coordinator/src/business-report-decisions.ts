import { createHash } from "node:crypto";

export const BUSINESS_REPORT_DECISION_PROFILE = "business-report-decision.v1";
export const BUSINESS_REPORT_DECISION_LIMIT = 1000;
export type BusinessReportDecision = "accepted" | "rework_requested";
export interface BusinessReportDecisionInput {
  requestId: string;
  expectedSha256: string;
  expectedRevision: number;
  decision: BusinessReportDecision;
  reason?: string;
}
export class BusinessReportDecisionError extends Error {
  constructor(readonly code: string, message: string, readonly status = 400) { super(message); this.name = "BusinessReportDecisionError"; }
}

export function normalizeBusinessReportDecision(raw: unknown) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new BusinessReportDecisionError("invalid_request", "Нужны версия отчёта и решение");
  const value = raw as Record<string, unknown>;
  if (Object.keys(value).some(key => !["requestId", "expectedSha256", "expectedRevision", "decision", "reason"].includes(key))
    || typeof value.requestId !== "string" || !/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(value.requestId)
    || typeof value.expectedSha256 !== "string" || !/^[a-f0-9]{64}$/.test(value.expectedSha256)
    || typeof value.expectedRevision !== "number" || !Number.isInteger(value.expectedRevision) || value.expectedRevision < 0 || value.expectedRevision > BUSINESS_REPORT_DECISION_LIMIT
    || (value.decision !== "accepted" && value.decision !== "rework_requested")
    || (value.reason !== undefined && typeof value.reason !== "string")) {
    throw new BusinessReportDecisionError("invalid_request", "Проверьте request ID, SHA-256, ревизию и вид решения");
  }
  const reason = String(value.reason ?? "").replace(/\r\n?/g, "\n").trim();
  if (reason.length > 4000 || Buffer.byteLength(reason) > 16000 || /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/u.test(reason)
    || (value.decision === "rework_requested" && !reason)) {
    throw new BusinessReportDecisionError("invalid_reason", "Для исправления нужен комментарий, до 4000 символов");
  }
  const input: Required<BusinessReportDecisionInput> = { requestId: value.requestId, expectedSha256: value.expectedSha256,
    expectedRevision: value.expectedRevision, decision: value.decision, reason };
  return { ...input, requestSha256: createHash("sha256").update(JSON.stringify(input)).digest("hex") };
}

export function normalizeDecisionHistoryCursor(value: unknown): number | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string" || !/^[1-9][0-9]{0,3}$/.test(value) || Number(value) > BUSINESS_REPORT_DECISION_LIMIT + 1) {
    throw new BusinessReportDecisionError("invalid_cursor", "Некорректная ревизия истории");
  }
  return Number(value);
}
