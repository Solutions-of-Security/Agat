import { createHash } from "node:crypto";
import { renderValidatedBusinessReport } from "../src/business-report-markdown.js";
import { BUSINESS_REPORT_CONTRACT, type BusinessReport } from "../src/business-contracts.js";
import type { BusinessReportOfficeInput } from "../src/business-report-office-document.js";
import type { KnowledgeSource } from "../src/types.js";
import type { AgatStore } from "../src/database.js";
import { randomUUID } from "node:crypto";

export function officeReport(long = false): BusinessReport {
  return { schemaVersion: BUSINESS_REPORT_CONTRACT, status: "ready",
    summary: "Технический пример экспорта: число обращений изменилось со 100 до 120.",
    claims: [{ id: "volume", text: "В примере число обращений выросло на 20 процентов.", sourceRefs: ["K1"] }],
    calculations: Array.from({ length: long ? 25 : 1 }, (_, index) => ({ id: long ? `growth_${index}_${"long_identifier_".repeat(4)}` : "growth",
      operation: "percent_change", operands: [100, 120], result: 20, sourceRefs: ["K1"] })), artifactRefs: [],
    limitations: ["Причины изменения не установлены.", '<script>alert(1)</script> и [ссылка](javascript:alert(1)) остаются буквальным текстом.'] };
}

export function officeFixture(long = false): BusinessReportOfficeInput {
  const sourceText = long ? Array.from({ length: 50 }, (_, index) => `Строка ${index + 1}: технический пример содержит числа 100 и 120. Длинный фрагмент проверяет переносы, доступность кириллицы и сохранение всего текста.`).join("\n")
    : "Технический пример: 100 обращений в июле и 120 обращений в августе.";
  const source: KnowledgeSource = { marker: "K1", retrievalId: "fixture-retrieval", stageId: "fixture-stage", createdAt: "2026-10-25T00:00:00.000Z", excerpt: sourceText.slice(0, 200), content: sourceText,
    provenance: { projectId: "default", collectionId: "fixture-collection", collectionName: "Технические примеры", documentId: "fixture-document", documentName: "Числа для проверки.csv",
      documentSha256: "a".repeat(64), chunkId: "fixture-chunk", chunkOrdinal: 0, charStart: 0, charEnd: sourceText.length, chunkSha256: "b".repeat(64),
      originalSha256: "c".repeat(64), pageNumber: null, sourceUri: null,
      extraction: { profile: "csv-values.v1", reportSha256: "d".repeat(64), extractionRevision: "11111111-1111-4111-8111-111111111111", reviewSha256: null,
        decision: null, emptyPageCount: null, omittedPartsCount: null, features: [], fidelity: "not_assessed" } } };
  const rendered = renderValidatedBusinessReport(JSON.stringify(officeReport(long)), [source], { sourceMarkers: new Set(["K1"]), artifactSha256: () => null });
  return { format: "pdf", markdown: rendered.content, canonicalSha256: createHash("sha256").update(rendered.content).digest("hex"),
    canonicalArtifactId: "11111111-1111-4111-8111-111111111111", origin: "https://agat.example", createdAt: "2026-10-25T00:00:00.000Z" };
}

export function completedOfficeReport(store: AgatStore, project = "default") {
  const model = `office-${randomUUID()}`;
  const collection = String(store.createKnowledgeCollection({ name: model, embeddingModel: model }, project).id);
  const document = store.ingestKnowledgeDocument(collection, { name: "Технические числа", content: "Технический пример: 100 обращений в июле, 120 в августе." }, project);
  const worker = store.registerNode({ enrollmentToken: "test", name: model, platform: "test", models: [model], embeddingModels: [model] });
  for (let lease = store.leaseKnowledgeEmbedding(worker.id); lease; lease = store.leaseKnowledgeEmbedding(worker.id)) {
    store.completeKnowledgeEmbedding(worker.id, lease.leaseId, lease.chunks.map(chunk => ({ chunkId: chunk.id, embedding: [1, 0] })));
  }
  const agent = store.createAgent({ name: model, role: "Техническая проверка", systemPrompt: "Synthetic test fixture; not model qualification", model }, project);
  const process = store.createProcess({ name: model, graph: { requiredKnowledgeCollectionIds: [collection], nodes: [
    { id: "start", type: "start", name: "Start", position: { x: 0, y: 0 }, config: {} },
    { id: "report", type: "agent", name: "Report", position: { x: 100, y: 0 }, config: { agentId: String(agent.id), outputContract: BUSINESS_REPORT_CONTRACT } },
    { id: "review", type: "approval", name: "Review", position: { x: 200, y: 0 }, config: {} },
    { id: "artifact", type: "artifact", name: "Report file", position: { x: 300, y: 0 }, config: { artifactName: "report.md", reportFormat: "business-report-markdown.v1" } },
    { id: "end", type: "end", name: "End", position: { x: 400, y: 0 }, config: {} },
  ], edges: ["start", "report", "review", "artifact"].map((source, index) => ({ id: `e${index}`, source, target: ["report", "review", "artifact", "end"][index]!, branch: "default" })) } }, project);
  store.publishProcess(String(process.id), project);
  const instance = store.startProcess(String(process.id), { input: "Технический пример экспорта", knowledgeCollectionIds: [collection], resultDestination: "artifacts" }, project)!;
  const lease = store.leaseNext(worker.id)!;
  if (!lease) throw new Error("Office fixture could not lease its unique pinned model");
  store.searchKnowledge(worker.id, lease.leaseId, { queries: [{ embeddingModel: model, collectionIds: [collection], vector: [1, 0] }] });
  store.completeLease(worker.id, lease.leaseId, JSON.stringify(officeReport()));
  const approval = (store.getRun(String(instance.runId), project)!.stages as Array<{ id: string; status: string }>).find(stage => stage.status === "waiting_approval")!;
  store.decideApproval(approval.id, true, project);
  const canonical = store.listRunArtifacts(String(instance.runId)).find(artifact => artifact.kind === "process_artifact")!;
  return { runId: String(instance.runId), canonical, documentId: String(document.id), worker, collection };
}
