import { parseProcessFormData } from "./process-forms.js";
import type { ProcessFormData } from "./types.js";

export type ApprovalDecision = "approve" | "reject";

export interface ParsedApprovalDecision {
  decision: ApprovalDecision;
  comment: string | null;
  reason: string | null;
  formData?: ProcessFormData;
}

interface ApprovalDecisionAuditInput {
  projectId: string;
  approval: unknown;
  decision: ParsedApprovalDecision;
  outcome?: unknown;
  actorSubject: string;
  actorDisplay: string;
  mcpApprovalTtlSeconds: number;
}

function object(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function boundedString(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized ? normalized.slice(0, maxLength) : null;
}

function decisionText(value: unknown, field: "comment" | "reason"): string | null {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value !== "string") throw new Error(`${field} должен быть строкой`);
  const normalized = value.trim();
  if (!normalized) return null;
  if (normalized.length > 1_000) throw new Error(`${field} не должен превышать 1000 символов`);
  return normalized;
}

export function parseApprovalDecision(value: unknown): ParsedApprovalDecision {
  const input = object(value);
  if (!input || (input.decision !== "approve" && input.decision !== "reject")) {
    throw new Error("decision должен быть approve или reject");
  }
  const comment = decisionText(input.comment, "comment");
  const reason = decisionText(input.reason, "reason");
  if (input.decision === "reject" && !reason) throw new Error("Укажите причину отклонения");
  return {
    decision: input.decision,
    comment,
    reason: input.decision === "reject" ? reason : null,
    ...(input.decision === "approve" && input.formData !== undefined ? { formData: parseProcessFormData(input.formData) } : {}),
  };
}

function boundedValue(value: unknown, depth = 0): unknown {
  if (depth >= 6) return "[truncated]";
  if (value === null || typeof value === "boolean" || typeof value === "number") return value;
  if (typeof value === "string") return value.slice(0, 2_000);
  if (Array.isArray(value)) return value.slice(0, 50).map((item) => boundedValue(item, depth + 1));
  const source = object(value);
  if (!source) return null;
  return Object.fromEntries(Object.entries(source).slice(0, 50).map(([key, item]) => [key.slice(0, 120), boundedValue(item, depth + 1)]));
}

function boundedCount(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(0, Math.min(100, Math.trunc(value)))
    : fallback;
}

function decisionSnapshotSource(input: ApprovalDecisionAuditInput): unknown {
  const approval = object(input.approval);
  if (!approval || approval.kind !== "mcp_tool" || input.decision.decision !== "approve") return input.approval;
  const outcome = object(input.outcome);
  const existingApprovers = Array.isArray(approval.approvers)
    ? approval.approvers.filter((item): item is string => typeof item === "string")
    : [];
  const outcomeApprovers = Array.isArray(outcome?.approvers)
    ? outcome.approvers.filter((item): item is string => typeof item === "string")
    : null;
  const approvers = outcomeApprovers ?? [...new Set([...existingApprovers, input.actorDisplay.slice(0, 200)])];
  const currentCount = boundedCount(approval.approvalCount, existingApprovers.length);
  return {
    ...approval,
    approvalCount: boundedCount(outcome?.approvalCount, Math.max(currentCount + 1, approvers.length)),
    requiredApprovals: boundedCount(outcome?.requiredApprovals, boundedCount(approval.requiredApprovals, 1)),
    approvers,
  };
}

export function withApprovalDeadline(value: unknown, mcpApprovalTtlSeconds: number): Record<string, unknown> | null {
  const approval = object(value);
  if (!approval) return null;
  if (approval.kind !== "mcp_tool") return { ...approval, expiresAt: null };
  const createdAt = boundedString(approval.createdAt, 100);
  const createdTime = createdAt ? new Date(createdAt).getTime() : Number.NaN;
  const ttlSeconds = Math.max(30, Math.min(86_400, Math.trunc(mcpApprovalTtlSeconds)));
  return {
    ...approval,
    expiresAt: Number.isFinite(createdTime) ? new Date(createdTime + ttlSeconds * 1_000).toISOString() : null,
  };
}

export function approvalId(value: unknown): string | null {
  const approval = object(value);
  if (!approval) return null;
  if (approval.kind === "mcp_tool") {
    const callId = boundedString(approval.callId, 300);
    return callId ? `mcp:${callId}` : null;
  }
  if (approval.kind === "stage") {
    const stageId = boundedString(approval.stageId, 300);
    return stageId ? `stage:${stageId}` : null;
  }
  return null;
}

export function approvalDecisionAuditData(input: ApprovalDecisionAuditInput): Record<string, unknown> {
  const approval = withApprovalDeadline(decisionSnapshotSource(input), input.mcpApprovalTtlSeconds);
  if (!approval) throw new Error("Согласование не найдено");
  const id = approvalId(approval);
  const runId = boundedString(approval.runId, 300);
  const stageId = boundedString(approval.stageId, 300);
  if (!id || !runId || !stageId) throw new Error("Согласование содержит неполный контекст");
  const snapshotKeys = [
    "kind", "callId", "stageId", "runId", "runName", "agentName", "summary", "toolName",
    "risk", "riskTier", "policy", "requiredApprovals", "approvalCount", "approvers",
    "policyVersion", "policySha256", "policyRuleId", "policyReason", "arguments", "previewDiff",
    "createdAt", "expiresAt", "form", "mode",
  ];
  const snapshot = Object.fromEntries(snapshotKeys
    .filter((key) => approval[key] !== undefined)
    .map((key) => [key, boundedValue(approval[key])]));
  return {
    projectId: input.projectId,
    approvalId: id,
    kind: approval.kind,
    runId,
    stageId,
    callId: boundedString(approval.callId, 300),
    decision: input.decision.decision,
    actorSubject: input.actorSubject.slice(0, 300),
    actor: input.actorDisplay.slice(0, 200),
    comment: input.decision.comment,
    reason: input.decision.reason,
    ...(input.decision.formData ? { formData: input.decision.formData } : {}),
    snapshot,
  };
}
