import type { Artifact, ReportDecisionBinding, ReportDecisionHistory, ReportDecisionKind, ReportDecisionRecord, ReportReworkTask, RecordReportDecisionRequest, RecordReportDecisionResponse } from "./types";

type Source = Pick<Artifact, "id" | "sha256" | "runId">;
const sha = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
export const normalizeReviewReason = (reason: string) => reason.replace(/\r\n?/g, "\n").trim();

export function reportDecisionRequest(source: Source, revision: number, decision: ReportDecisionKind, rawReason: string, requestId: string): RecordReportDecisionRequest {
  const reason = normalizeReviewReason(rawReason);
  if (!["accepted", "rework_requested"].includes(decision)) throw new Error("Выберите решение о результате");
  if (!sha(source.sha256) || !Number.isInteger(revision) || revision < 0 || revision >= 1000) throw new Error("Обновите версию отчёта и историю решений");
  if (reason.length > 4000 || new TextEncoder().encode(reason).length > 16000 || /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/u.test(reason)) throw new Error("Комментарий: до 4000 символов, без управляющих знаков");
  if (decision === "rework_requested" && !reason) throw new Error("Опишите, что нужно исправить");
  return { requestId, expectedSha256: source.sha256, expectedRevision: revision, decision, reason };
}

function validBinding(binding: ReportDecisionBinding, source: Source) {
  return binding?.artifactId === source.id && binding.sourceSha256 === source.sha256 && binding.runId === source.runId
    && [binding.stageId, binding.processInstanceId, binding.processId].every(value => typeof value === "string" && value.length > 0)
    && Number.isInteger(binding.processVersion) && binding.processVersion > 0
    && sha(binding.graphSha256) && sha(binding.inputSha256) && sha(binding.schemaSha256);
}

function validDecision(row: ReportDecisionRecord, source: Source) {
  return row && row.profile === "business-report-decision.v1" && typeof row.id === "string" && row.artifactId === source.id && row.sourceSha256 === source.sha256
    && Number.isInteger(row.revision) && row.revision > 0 && row.revision <= 1000
    && ["accepted", "rework_requested"].includes(row.decision) && typeof row.reason === "string" && typeof row.actor === "string"
    && row.runId === source.runId && Number.isFinite(Date.parse(row.createdAt)) && validBinding(row.binding, source)
    && (row.decision === "accepted" ? row.reworkTaskId === null : typeof row.reworkTaskId === "string" && row.reworkTaskId.length > 0);
}

export function verifiedDecisionHistory(value: ReportDecisionHistory, source: Source, beforeRevision: number | null = null): ReportDecisionHistory {
  if (!value || value.artifactId !== source.id || value.sourceSha256 !== source.sha256 || typeof value.sourceReady !== "boolean"
    || !Number.isInteger(value.revision) || value.revision < 0 || value.revision > 1000 || !Array.isArray(value.decisions) || value.decisions.length > 50
    || value.decisions.some((row, index, rows) => !validDecision(row, source) || row.revision > value.revision
      || (beforeRevision !== null && row.revision >= beforeRevision) || (index > 0 && row.revision >= rows[index - 1]!.revision))
    || (beforeRevision === null && value.revision > 0 && value.decisions[0]?.revision !== value.revision)
    || (value.nextBeforeRevision !== null && (value.decisions.length !== 50 || value.nextBeforeRevision !== value.decisions.at(-1)?.revision))) {
    throw new Error("Не удалось подтвердить историю этой версии отчёта");
  }
  return value;
}

export function verifiedReworkTask(value: ReportReworkTask, source: Source, taskId: string): ReportReworkTask {
  if (!value || value.id !== taskId || value.kind !== "business_report_rework" || value.execution !== "manual"
    || value.artifactId !== source.id || value.sourceSha256 !== source.sha256 || !validBinding(value.binding, source) || !["open", "superseded"].includes(value.state)
    || typeof value.reason !== "string" || typeof value.sourceAvailable !== "boolean" || value.runId !== source.runId
    || typeof value.decisionId !== "string" || !value.decisionId || typeof value.requestedBy !== "string" || !Number.isFinite(Date.parse(value.createdAt))) {
    throw new Error("Не удалось подтвердить связь задачи с этим отчётом");
  }
  return value;
}

export function verifiedDecisionReceipt(value: RecordReportDecisionResponse, source: Source, request: RecordReportDecisionRequest): RecordReportDecisionResponse {
  if (!value || typeof value.created !== "boolean" || !validDecision(value.decision, source) || value.decision.decision !== request.decision
    || value.decision.reason !== request.reason || value.decision.revision !== request.expectedRevision + 1
    || (request.decision === "accepted" ? value.reworkTask !== null || value.decision.reworkTaskId !== null
      : !value.reworkTask || value.reworkTask.id !== value.decision.reworkTaskId || value.reworkTask.decisionId !== value.decision.id)) {
    throw new Error("Не получена подтверждённая квитанция решения");
  }
  if (value.reworkTask) verifiedReworkTask(value.reworkTask, source, value.decision.reworkTaskId!);
  return value;
}

export function correctionTaskBrief(task: ReportReworkTask, sourceName: string, origin: string): string {
  const address = new URL(origin);
  if (!["http:", "https:"].includes(address.protocol) || address.origin !== origin) throw new Error("Некорректный адрес приложения");
  return ["Задача: исправить отчёт", `Запрос: ${task.id}`, task.state === "open" ? "Статус: требуется ручное исправление" : "Статус: запрос заменён более новым решением",
    `Комментарий:\n${task.reason}`, `Исходный файл: ${sourceName}${task.sourceAvailable ? "" : " (сейчас недоступен)"}`,
    `SHA-256: ${task.sourceSha256}`, `Запуск: ${origin}/#runs/${encodeURIComponent(task.runId)}`,
    `Процесс: ${task.binding.processId}, версия ${task.binding.processVersion}`, "Исправление ещё не выполнено; результат требует отдельной проверки."].join("\n\n");
}
