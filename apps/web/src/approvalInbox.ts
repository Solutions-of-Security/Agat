import type { AgatEvent, Approval, McpRiskTier, McpToolPolicy, McpToolRisk, ProcessFormData, Run, StageApproval } from "./types";

export type ApprovalStateFilter = "pending" | "resolved" | "all";
export type ApprovalRiskFilter = "all" | "high" | "standard";
export type ApprovalKindFilter = "all" | Approval["kind"];
export type ApprovalDeadlineFilter = "all" | "soon" | "later" | "none";
export type ApprovalInboxStatus = "pending" | "approved" | "rejected";

export interface ApprovalInboxFilters {
  search: string;
  state: ApprovalStateFilter;
  risk: ApprovalRiskFilter;
  kind: ApprovalKindFilter;
  deadline: ApprovalDeadlineFilter;
}

export interface ApprovalAuditEntry {
  id: string;
  title: string;
  actor: string;
  note: string | null;
  createdAt: string;
  tone: "neutral" | "positive" | "negative" | "waiting";
}

export interface ApprovalInboxItem {
  id: string;
  approval: Approval;
  status: ApprovalInboxStatus;
  action: string;
  kindLabel: string;
  riskTier: McpRiskTier;
  riskLabel: string;
  requester: string;
  target: string;
  runName: string;
  effect: string;
  riskReason: string;
  requestedAt: string;
  expiresAt: string | null;
  decidedAt: string | null;
  decisionActor: string | null;
  decisionComment: string | null;
  rejectionReason: string | null;
  formData?: ProcessFormData;
  audit: ApprovalAuditEntry[];
}

interface ApprovalInboxSource {
  approvals: Approval[];
  runs: Run[];
  events: AgatEvent[];
  generatedAt: string;
}

const riskRank: Record<McpRiskTier, number> = {
  critical: 4,
  high: 3,
  elevated: 2,
  low: 1,
};

const riskCopy: Record<McpRiskTier, string> = {
  critical: "Критический",
  high: "Высокий",
  elevated: "Повышенный",
  low: "Низкий",
};

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function decisionFromEvent(event: AgatEvent): "approve" | "reject" | null {
  const decision = event.data?.decision;
  if (decision === "approve" || decision === "reject") return decision;
  if (event.type.endsWith(".approved")) return "approve";
  if (event.type.endsWith(".rejected")) return "reject";
  return null;
}

export function approvalIdentity(approval: Approval): string {
  return approval.kind === "mcp_tool" ? `mcp:${approval.callId}` : `stage:${approval.stageId}`;
}

function snapshotApproval(value: unknown): Approval | null {
  const candidate = record(value);
  if (!candidate) return null;
  const kind = candidate.kind;
  const stageId = text(candidate.stageId);
  const runId = text(candidate.runId);
  const runName = text(candidate.runName);
  const agentName = text(candidate.agentName);
  const summary = text(candidate.summary);
  if (!stageId || !runId || !runName || !agentName || !summary) return null;
  if (kind === "stage") return { kind, stageId, runId, runName, agentName, summary,
    ...(record(candidate.form) && Array.isArray((candidate.form as Record<string, unknown>).fields) ? { form: candidate.form as StageApproval["form"] } : {}),
    ...(candidate.mode === "input" || candidate.mode === "approval" ? { mode: candidate.mode } : {}),
  };
  if (kind !== "mcp_tool") return null;
  const callId = text(candidate.callId);
  const toolName = text(candidate.toolName);
  const createdAt = text(candidate.createdAt);
  const risk = candidate.risk;
  const riskTier = candidate.riskTier;
  const policy = candidate.policy;
  if (!callId || !toolName || !createdAt) return null;
  if (!(["read", "write", "destructive", "unknown"] as const).includes(risk as never)) return null;
  if (!(["low", "elevated", "high", "critical"] as const).includes(riskTier as never)) return null;
  if (!(["allow", "approval", "deny"] as const).includes(policy as never)) return null;
  return {
    kind,
    callId,
    stageId,
    runId,
    runName,
    agentName,
    summary,
    toolName,
    risk: risk as McpToolRisk,
    riskTier: riskTier as McpRiskTier,
    policy: policy as McpToolPolicy,
    requiredApprovals: Number(candidate.requiredApprovals ?? 1),
    approvalCount: Number(candidate.approvalCount ?? 0),
    approvers: Array.isArray(candidate.approvers) ? candidate.approvers.filter((item): item is string => typeof item === "string") : [],
    policyVersion: Number(candidate.policyVersion ?? 0),
    policySha256: text(candidate.policySha256) ?? "—",
    policyRuleId: text(candidate.policyRuleId),
    policyReason: text(candidate.policyReason) ?? "Требуется решение оператора",
    arguments: record(candidate.arguments) ?? {},
    previewDiff: Array.isArray(candidate.previewDiff)
      ? candidate.previewDiff.filter((item): item is Record<string, unknown> => record(item) !== null)
      : [],
    createdAt,
    expiresAt: text(candidate.expiresAt),
  } as Approval;
}

function eventApprovalId(event: AgatEvent): string | null {
  const explicit = text(event.data?.approvalId);
  if (explicit) return explicit;
  const callId = text(event.data?.callId);
  if (callId) return `mcp:${callId}`;
  if (event.stageId) return `stage:${event.stageId}`;
  const stageId = text(event.data?.stageId);
  return stageId ? `stage:${stageId}` : null;
}

function eventMatchesApproval(event: AgatEvent, approval: Approval): boolean {
  const identity = approvalIdentity(approval);
  if (eventApprovalId(event) === identity) return true;
  if (approval.kind === "mcp_tool") return text(event.data?.callId) === approval.callId;
  return event.stageId === approval.stageId || text(event.data?.stageId) === approval.stageId;
}

function requestEvent(approval: Approval, events: AgatEvent[]): AgatEvent | null {
  const candidates = events.filter((event) => eventMatchesApproval(event, approval)
    && (event.type === "approval.requested" || event.type === "mcp.approval.requested"));
  return candidates.sort((left, right) => left.createdAt.localeCompare(right.createdAt))[0] ?? null;
}

export function approvalRiskTier(approval: Approval): McpRiskTier {
  return approval.kind === "mcp_tool" ? approval.riskTier : "elevated";
}

export function approvalActionLabel(approval: Approval): string {
  if (approval.kind === "mcp_tool") return `Разрешить вызов ${approval.toolName}`;
  if (approval.mode === "input") return `Дополнить вводные для «${approval.runName}»`;
  if (approval.agentName === "Ручное подтверждение") return `Разрешить продолжить «${approval.runName}»`;
  return `Разрешить ${approval.agentName} продолжить «${approval.runName}»`;
}

function auditEntry(event: AgatEvent, requester: string): ApprovalAuditEntry | null {
  const actor = text(event.data?.actor) ?? text(event.data?.actorDisplay) ?? (event.type.includes("requested") ? requester : "Система");
  const comment = text(event.data?.comment);
  const reason = text(event.data?.reason);
  if (event.type === "approval.requested" || event.type === "mcp.approval.requested") {
    return { id: `event:${event.id}`, title: "Запрос создан", actor, note: null, createdAt: event.createdAt, tone: "waiting" };
  }
  if (event.type === "approval.decision.recorded") {
    const decision = decisionFromEvent(event);
    const rejectionNote = [reason, comment ? `Комментарий: ${comment}` : null].filter(Boolean).join(" · ") || null;
    return {
      id: `event:${event.id}`,
      title: decision === "reject" ? "Решение: отклонено" : "Решение: согласовано",
      actor,
      note: decision === "reject" ? rejectionNote : comment,
      createdAt: event.createdAt,
      tone: decision === "reject" ? "negative" : "positive",
    };
  }
  if (event.type === "mcp.approval.recorded") {
    const count = Number(event.data?.approvalCount ?? 0);
    const required = Number(event.data?.requiredApprovals ?? 0);
    return {
      id: `event:${event.id}`,
      title: `Подтверждение ${count}/${required}`,
      actor,
      note: null,
      createdAt: event.createdAt,
      tone: count >= required ? "positive" : "waiting",
    };
  }
  if (["approval.approved", "mcp.approval.approved"].includes(event.type)) {
    return { id: `event:${event.id}`, title: "Действие разрешено", actor, note: null, createdAt: event.createdAt, tone: "positive" };
  }
  if (["approval.rejected", "mcp.approval.rejected"].includes(event.type)) {
    return { id: `event:${event.id}`, title: "Действие отклонено", actor, note: reason, createdAt: event.createdAt, tone: "negative" };
  }
  return null;
}

function approvalAudit(approval: Approval, events: AgatEvent[], requester: string, requestedAt: string): ApprovalAuditEntry[] {
  const entries = events
    .filter((event) => eventMatchesApproval(event, approval))
    .map((event) => auditEntry(event, requester))
    .filter((entry): entry is ApprovalAuditEntry => entry !== null)
    .sort((left, right) => left.createdAt.localeCompare(right.createdAt));
  if (entries.some((entry) => entry.title === "Запрос создан")) return entries;
  return [{
    id: `request:${approvalIdentity(approval)}`,
    title: "Запрос создан",
    actor: requester,
    note: null,
    createdAt: requestedAt,
    tone: "waiting",
  }, ...entries];
}

function decisionEventFields(event: AgatEvent | null) {
  const decision = event ? decisionFromEvent(event) : null;
  return {
    status: decision === "reject" ? "rejected" as const : decision === "approve" ? "approved" as const : "pending" as const,
    decidedAt: decision ? event?.createdAt ?? null : null,
    decisionActor: decision ? text(event?.data?.actor) : null,
    decisionComment: decision ? text(event?.data?.comment) : null,
    rejectionReason: decision === "reject" ? text(event?.data?.reason) : null,
  };
}

function makeItem(approval: Approval, source: ApprovalInboxSource, pending: boolean, decisionEvent: AgatEvent | null): ApprovalInboxItem {
  const run = source.runs.find((candidate) => candidate.id === approval.runId);
  const stage = run?.stages.find((candidate) => candidate.id === approval.stageId);
  const requester = approval.kind === "mcp_tool" ? stage?.agent.name ?? "Агент" : approval.agentName;
  const runName = run?.name ?? (approval.kind === "mcp_tool" ? "Запуск" : approval.runName);
  const serverName = approval.kind === "mcp_tool" ? approval.runName.replace(/^MCP\s*·\s*/u, "") : null;
  const requested = requestEvent(approval, source.events);
  const requestedAt = approval.kind === "mcp_tool"
    ? approval.createdAt
    : requested?.createdAt ?? run?.updatedAt ?? run?.createdAt ?? source.generatedAt;
  const decision = decisionEventFields(decisionEvent);
  const riskTier = approvalRiskTier(approval);
  return {
    id: approvalIdentity(approval),
    approval,
    status: pending ? "pending" : decision.status,
    action: approvalActionLabel(approval),
    kindLabel: approval.kind === "mcp_tool" ? "MCP-вызов" : approval.mode === "input" ? "Ввод данных" : "Этап процесса",
    riskTier,
    riskLabel: riskCopy[riskTier],
    requester,
    target: approval.kind === "mcp_tool" ? `${serverName || "MCP"} · ${approval.toolName}` : runName,
    runName,
    effect: approval.kind === "mcp_tool"
      ? `Вызовет ${approval.toolName} через ${serverName || "MCP-сервер"}. После ответа запуск «${runName}» продолжится.`
      : `${approval.summary} После подтверждения запуск «${runName}» продолжит выполнение.`,
    riskReason: approval.kind === "mcp_tool"
      ? approval.policyReason
      : "Решение снимает ручную контрольную точку процесса и продолжает выполнение следующих этапов.",
    requestedAt,
    expiresAt: approval.kind === "mcp_tool" ? approval.expiresAt ?? null : null,
    decidedAt: decision.decidedAt,
    decisionActor: decision.decisionActor,
    decisionComment: decision.decisionComment,
    rejectionReason: decision.rejectionReason,
    ...(record(decisionEvent?.data?.formData) ? { formData: decisionEvent!.data!.formData as ProcessFormData } : {}),
    audit: approvalAudit(approval, source.events, requester, requestedAt),
  };
}

export function buildApprovalInboxItems(source: ApprovalInboxSource): ApprovalInboxItem[] {
  const decisionEvents = source.events
    .filter((event) => event.type === "approval.decision.recorded" && decisionFromEvent(event) !== null)
    .sort((left, right) => left.createdAt.localeCompare(right.createdAt));
  const latestDecisions = new Map<string, AgatEvent>();
  for (const event of decisionEvents) {
    const id = eventApprovalId(event);
    if (id) latestDecisions.set(id, event);
  }

  const items = new Map<string, ApprovalInboxItem>();
  for (const approval of source.approvals) {
    const id = approvalIdentity(approval);
    items.set(id, makeItem(approval, source, true, latestDecisions.get(id) ?? null));
  }
  for (const [id, event] of latestDecisions) {
    if (items.has(id)) continue;
    const approval = snapshotApproval(event.data?.snapshot);
    if (!approval) continue;
    items.set(id, makeItem(approval, source, false, event));
  }

  return [...items.values()].sort((left, right) => {
    if (left.status !== right.status) return left.status === "pending" ? -1 : 1;
    const riskDifference = riskRank[right.riskTier] - riskRank[left.riskTier];
    if (riskDifference !== 0) return riskDifference;
    if (left.expiresAt && right.expiresAt) return left.expiresAt.localeCompare(right.expiresAt);
    if (left.expiresAt) return -1;
    if (right.expiresAt) return 1;
    return left.requestedAt.localeCompare(right.requestedAt);
  });
}

export function approvalDeadlineState(item: ApprovalInboxItem, now = Date.now()): Exclude<ApprovalDeadlineFilter, "all"> {
  if (!item.expiresAt) return "none";
  const remaining = new Date(item.expiresAt).getTime() - now;
  return remaining <= 15 * 60_000 ? "soon" : "later";
}

export function filterApprovalInboxItems(
  items: ApprovalInboxItem[],
  filters: ApprovalInboxFilters,
  now = Date.now(),
): ApprovalInboxItem[] {
  const query = filters.search.trim().toLocaleLowerCase("ru");
  return items.filter((item) => {
    if (filters.state === "pending" && item.status !== "pending") return false;
    if (filters.state === "resolved" && item.status === "pending") return false;
    if (filters.risk === "high" && !["high", "critical"].includes(item.riskTier)) return false;
    if (filters.risk === "standard" && ["high", "critical"].includes(item.riskTier)) return false;
    if (filters.kind !== "all" && item.approval.kind !== filters.kind) return false;
    if (filters.deadline !== "all" && approvalDeadlineState(item, now) !== filters.deadline) return false;
    if (!query) return true;
    const haystack = [
      item.action,
      item.requester,
      item.target,
      item.runName,
      item.approval.summary,
      item.approval.kind === "mcp_tool" ? item.approval.policyReason : "",
    ].join(" ").toLocaleLowerCase("ru");
    return haystack.includes(query);
  });
}

export function formatApprovalWait(requestedAt: string, now = Date.now()): string {
  const elapsedMinutes = Math.max(0, Math.floor((now - new Date(requestedAt).getTime()) / 60_000));
  if (elapsedMinutes < 1) return "только что";
  if (elapsedMinutes < 60) return `${elapsedMinutes} мин`;
  const hours = Math.floor(elapsedMinutes / 60);
  if (hours < 24) return `${hours} ч`;
  return `${Math.floor(hours / 24)} д`;
}

export function formatApprovalDeadline(expiresAt: string | null, now = Date.now()): string {
  if (!expiresAt) return "Срок не задан";
  const remainingMinutes = Math.ceil((new Date(expiresAt).getTime() - now) / 60_000);
  if (remainingMinutes <= 0) return "Срок истёк";
  if (remainingMinutes < 60) return `Осталось ${remainingMinutes} мин`;
  const hours = Math.ceil(remainingMinutes / 60);
  if (hours < 24) return `Осталось ${hours} ч`;
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
    .format(new Date(expiresAt));
}
