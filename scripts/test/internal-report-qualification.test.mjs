import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import {
  sha256, loopbackOrigin, temporalAddress, modelIdentity, validateVector,
  validateIngestion, validateCitations, reportRubric, historyEvidence,
} from "../lib/internal-report-qualification.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const fixture = JSON.parse(fs.readFileSync(path.join(root, "docs/qualification/internal-report/fixtures/sources.json")));
const golden = "Июль 2026: 100 заявок, август 2026: 120 (+20%). 400 / 100 = 4 ч; 360 / 120 = 3 ч (−25%). "
  + "8 / 100 * 100 = 8%; 6 / 120 * 100 = 5%; −3 п.п. Учебные данные. Причины изменений установить нельзя. [K1] [K2]";
const installation = { knowledgeCollectionIds: ["collection"], agentIds: { researcher: "a", analyst: "b", reviewer: "c" } };

function sourceExport() {
  return { collections: [{ id: "collection", embeddingModel: fixture.embeddingModel,
    documents: fixture.sources.map((source, index) => ({ sourceUri: source.uri, content: source.content.trim(),
      status: "ready", contentSha256: sha256(source.content.trim()), chunks: [{ id: `chunk-${index}`, content: source.content.trim(),
        charStart: 0, charEnd: source.content.trim().length,
        contentSha256: sha256(source.content.trim()), embeddingModel: fixture.embeddingModel, embeddingDimensions: 768, embeddedAt: "2026-09-20" }] })) }] };
}
function traceFixture() {
  return { truncated: false, run: { stages: Object.values(installation.agentIds).map((id) => ({ id: `stage-${id}`,
    status: "completed", agent: { id, model: "qwen3:8b" }, metrics: { model: "qwen3:8b", modelCalls: 1 }, output: golden })) },
  events: Object.values(installation.agentIds).map((id) => ({ type: "knowledge.retrieved", stageId: `stage-${id}`,
    data: { queries: [{ embeddingModel: "embeddinggemma", collectionIds: ["collection"], dimensions: 768, vectorSha256: "a".repeat(64) }],
      hits: fixture.sources.map((source, index) => ({ marker: `K${index + 1}`, score: 0.8, content: source.content.trim(),
        provenance: { sourceUri: source.uri, collectionId: "collection", documentSha256: sha256(source.content.trim()),
          chunkId: `chunk-${index}`, chunkSha256: sha256(source.content.trim()) } })) } })) };
}

test("qualification accepts only explicit loopback addresses, without credentials or redirects", () => {
  assert.equal(loopbackOrigin("http://127.0.0.1:11434"), "http://127.0.0.1:11434");
  assert.equal(temporalAddress("[::1]:7233"), "[::1]:7233");
  for (const endpoint of ["https://example.com", "http://secret@127.0.0.1:11434", "http://localhost:11434", "http://127.0.0.1/api", "http://127.0.0.1/?token=secret"]) {
    assert.throws(() => loopbackOrigin(endpoint), /LOCAL_ENDPOINT_REQUIRED/);
  }
  for (const address of ["temporal.example:7233", "127.0.0.1:0", "127.0.0.1:99999"]) assert.throws(() => temporalAddress(address));
});

test("model identity requires installed digest, Qwen3 architecture and local capability", () => {
  const tags = { models: [{ name: "qwen3:8b", digest: "a".repeat(64) }] };
  const show = { capabilities: ["completion"], details: { family: "qwen3", parameter_size: "8.2B", quantization_level: "Q4_K_M" } };
  assert.equal(modelIdentity(tags, show, "qwen3:8b", "completion").digest, "a".repeat(64));
  assert.throws(() => modelIdentity({ models: [] }, show, "qwen3:8b", "completion"), /MODEL_NOT_INSTALLED/);
  assert.throws(() => modelIdentity(tags, { ...show, remote_host: "https://remote.invalid" }, "qwen3:8b", "completion"), /LOCAL_MODEL/);
  assert.throws(() => modelIdentity(tags, { ...show, details: { ...show.details, family: "llama" } }, "qwen3:8b", "completion"), /QWEN3_REQUIRED/);
  assert.throws(() => validateVector([1, 0, 0]), /EMBEDDING_VECTOR_INVALID/);
  assert.throws(() => validateVector(Array(768).fill(0)), /EMBEDDING_VECTOR_INVALID/);
  assert.throws(() => validateVector(Array(768).fill(NaN)), /EMBEDDING_VECTOR_INVALID/);
});

test("ingestion fails for changed content, missing or incompatible embeddings", () => {
  assert.equal(validateIngestion(sourceExport(), installation, fixture, 768).length, 2);
  for (const mutate of [
    (doc) => { doc.content += "private data"; },
    (doc) => { doc.status = "queued"; },
    (doc) => { doc.chunks[0].embeddingDimensions = 3; },
    (doc) => { doc.chunks[0].embeddingModel = "fake"; },
    (doc) => { doc.chunks[0].contentSha256 = "b".repeat(64); },
  ]) {
    const exported = sourceExport(); mutate(exported.collections[0].documents[0]);
    assert.throws(() => validateIngestion(exported, installation, fixture, 768));
  }
});

test("citations must resolve to both ingested sources on each real model stage", () => {
  const ingested = validateIngestion(sourceExport(), installation, fixture, 768);
  assert.equal(validateCitations(traceFixture(), installation, ingested, "qwen3:8b").length, 3);
  for (const mutate of [
    (trace) => { trace.run.stages[2].output += " [K999]"; },
    (trace) => { trace.run.stages[2].output = "Нет ссылок"; },
    (trace) => { trace.run.stages[2].output = "Один источник [K1]"; },
    (trace) => { trace.events[0].data.hits[0].provenance.collectionId = "foreign"; },
    (trace) => { trace.events[0].data.hits[0].content = "wrong chunk"; },
    (trace) => { trace.events[0].data.queries[0].dimensions = 3; },
    (trace) => { trace.events[0].data.queries[0].vector = [1, 0, 0]; },
    (trace) => { trace.run.stages[0].metrics.modelCalls = 0; },
    (trace) => { trace.run.stages[0].metrics.model = "mock"; },
    (trace) => { trace.truncated = true; },
    (trace) => { trace.events.push(trace.events[0]); },
  ]) {
    const trace = traceFixture(); mutate(trace);
    assert.throws(() => validateCitations(trace, installation, ingested, "qwen3:8b"));
  }
});

test("synthetic rubric rejects wrong numbers, percentage-point confusion and absent limitations", () => {
  assert.equal(reportRubric(golden).passed, true);
  assert.equal(reportRubric(golden.replace("(−25%)", "(сократилось на 25%)")).passed, true);
  assert.equal(reportRubric(golden.replace("= 4 ч", "= 4.00 ч")).passed, true);
  for (const text of [golden.replace("= 4", "= 5"), golden.replace("−3 п.п.", "−3%"),
    golden.replace("= 4", "= 4.5"), golden.replace("+20%", "+200%"), golden.replace("+20%", "рост на 120%"),
    golden.replace("400 /", "1400 /"), golden.replace("8 / 100", "18 / 100"),
    golden.replace("−25%", "снижение на 125%"), golden.replace("−3 п.п.", "снижение на 13 п.п."),
    golden.replace("−25%", "−20%"), golden.replace("Причины изменений установить нельзя.", ""), golden.replace("Учебные", "Боевые")]) {
    assert.equal(reportRubric(text).passed, false);
  }
});

test("history evidence excludes payloads, secrets, failures and host identities", () => {
  const evidence = historyEvidence({ events: [{ eventId: 1, eventType: 7, workflowTaskCompletedEventAttributes: {
    identity: "secret-host", payload: "secret-token", }, memo: "private data", failure: { stackTrace: "/Users/private" } }] });
  assert.deepEqual(evidence, [{ eventId: "1", type: 7, qualificationWorker: "redacted" }]);
});

function runGate(env) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["scripts/qualify-internal-report.mjs"], { cwd: root,
      env: { PATH: process.env.PATH, ...env }, stdio: ["ignore", "pipe", "pipe"] });
    let output = "";
    child.stdout.on("data", (chunk) => { output += chunk; }); child.stderr.on("data", (chunk) => { output += chunk; });
    child.on("error", reject); child.on("exit", (code) => resolve({ code, output }));
  });
}

test("absent infrastructure is a nonzero FAIL with evidence, never skip or PASS", { timeout: 30_000 }, async () => {
  const output = `docs/qualification/internal-report/evidence/local-test-${randomUUID()}`;
  try {
    const run = await runGate({ AGAT_QUALIFICATION_OUTPUT: output,
      AGAT_QUALIFICATION_OLLAMA_URL: "http://127.0.0.1:1", AGAT_QUALIFICATION_TEMPORAL_ADDRESS: "127.0.0.1:1" });
    assert.equal(run.code, 2, run.output);
    const evidence = JSON.parse(fs.readFileSync(path.join(root, output, "result.json")));
    assert.equal(evidence.status, "FAIL");
    assert.ok(evidence.checks.some((check) => check.id === "ollama" && check.status === "FAIL"));
    assert.ok(evidence.checks.some((check) => check.id === "temporal" && check.status === "FAIL"));
    assert.equal(evidence.checks.find((check) => check.id === "download").status, "NOT_RUN");
    const before = fs.readFileSync(path.join(root, output, "result.json"), "utf8");
    assert.notEqual((await runGate({ AGAT_QUALIFICATION_OUTPUT: output })).code, 0);
    assert.equal(fs.readFileSync(path.join(root, output, "result.json"), "utf8"), before, "existing evidence is immutable");
    assert.doesNotMatch(before, /secret-token|Authorization|\/Users\/|stackTrace|"vector":/);
  } finally { fs.rmSync(path.join(root, output), { recursive: true, force: true }); }
});
