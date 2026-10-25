import type { Artifact, ReportEvalCandidate, ReportEvalImportReceipt, ReportEvalImportRequest } from "./types";
export type ReportEvalBasis = { revision: number; decisionId: string };
type Source = Pick<Artifact, "id" | "sha256" | "runId">;
const sha = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);

async function digest(value: string) {
  if (!globalThis.crypto?.subtle) throw new Error("В этом браузере недоступна проверка целостности. Откройте АГАТ через HTTPS или localhost.");
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

export async function verifiedReportEvalCandidate(value: ReportEvalCandidate, artifact: Source, basis: ReportEvalBasis, projectId: string) {
  const source = value?.source, example = value?.example, knowledge = value?.currentKnowledgeSnapshot;
  if (!value || value.profile !== "business-report-eval-candidate.v1" || value.purpose !== "development" || value.holdoutEligible !== false || value.referenceReviewRequired !== true
    || value.replay?.automatic !== false || value.replay.scope !== "single_agent" || value.replay.context !== "requires_review"
    || source?.artifactId !== artifact.id || source.sourceSha256 !== artifact.sha256 || source.runId !== artifact.runId || source.projectId !== projectId
    || source.decisionId !== basis.decisionId || source.decisionRevision !== basis.revision || !["accepted", "rework_requested"].includes(source.decision)
    || ![source.reportStageId, source.processInstanceId, source.processId, source.agentStageId, source.agentId, source.runtime, source.runtimeProfile].every(field => typeof field === "string" && field.length > 0)
    || !Number.isInteger(source.processVersion) || source.processVersion < 1 || typeof source.reason !== "string" || source.reason.length > 4000
    || (source.requestedModel !== null && typeof source.requestedModel !== "string")
    || ![source.graphSha256, source.promptSha256, source.definitionSha256, source.inputSha256, source.observedOutputSha256, source.schemaSha256, value.contentSha256].every(sha)
    || typeof example?.input !== "string" || !example.input.trim() || example.input.length > 100_000 || example.referenceOutput !== ""
    || typeof value.observedOutput !== "string" || !value.observedOutput || value.observedOutput.length > 100_000
    || !Array.isArray(example.knowledgeCollectionIds) || example.knowledgeCollectionIds.length > 32 || example.knowledgeCollectionIds.some(id => typeof id !== "string" || !/^[a-zA-Z0-9_-]{1,100}$/.test(id))
    || !Array.isArray(example.requiredTerms) || example.requiredTerms.length || !Array.isArray(example.forbiddenTerms) || example.forbiddenTerms.length
    || knowledge?.schemaVersion !== 1 || !sha(knowledge.fingerprint) || !Array.isArray(knowledge.missingCollectionIds) || knowledge.missingCollectionIds.length
    || JSON.stringify(knowledge.collectionIds) !== JSON.stringify(example.knowledgeCollectionIds)) throw new Error("Не удалось подтвердить происхождение этого примера");
  const { contentSha256, ...body } = value;
  const hashes = await Promise.all([digest(JSON.stringify(body)), digest(example.input), digest(value.observedOutput)]);
  if (hashes[0] !== contentSha256 || hashes[1] !== source.inputSha256 || hashes[2] !== source.observedOutputSha256) throw new Error("Содержимое примера не совпадает с сохранёнными hashes");
  return value;
}

export function reportEvalDraftError(name: string, reference: string, labels: string[], reviewed: boolean): string | null {
  if (!name.trim() || name.trim().length > 120) return "Введите название набора, до 120 символов";
  if (!reference.trim() || reference.length > 100_000 || new TextEncoder().encode(reference).length > 200_000) return "Подготовьте эталонный JSON, до 100000 символов";
  try { const value: unknown = JSON.parse(reference); if (!value || typeof value !== "object" || Array.isArray(value) || (value as { schemaVersion?: unknown }).schemaVersion !== "business-report.v1") return "Нужен эталон по контракту business-report.v1"; }
  catch { return "Исправьте синтаксис эталонного JSON"; }
  if (!labels.length || labels.length > 20 || labels.some(label => !label.trim() || label.trim().length > 120)) return "Заполните критерии проверки, от 1 до 20";
  if (!reviewed) return "Подтвердите проверку эталона, критериев и хранения примера";
  return null;
}

export function verifiedReportEvalReceipt(value: ReportEvalImportReceipt, request: ReportEvalImportRequest) {
  if (!value || !/^[a-f0-9]{8}-[a-f0-9]{4}-8[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(value.datasetId)
    || value.datasetVersion !== 1 || !sha(value.datasetContentSha256) || value.candidateSha256 !== request.expectedCandidateSha256 || typeof value.created !== "boolean") {
    throw new Error("Не получена подтверждённая квитанция dataset v1");
  }
  return value;
}
