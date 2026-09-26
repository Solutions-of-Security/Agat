#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import net from "node:net";
import { fileURLToPath } from "node:url";
import { execFileSync, spawn } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import {
  QualificationError, requireEvidence, sha256, loopbackOrigin, temporalAddress,
  safeVersion, modelIdentity, validateVector, validateIngestion, validateCitations,
  reportRubric, historyEvidence,
} from "./lib/internal-report-qualification.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
const docRoot = path.join(root, "docs/qualification/internal-report");
const labels = {
  configuration: "Безопасная конфигурация и версии зависимостей",
  ollama: "Локальный Ollama",
  qwen3: "Установленная локальная модель Qwen3",
  embeddingModel: "Установленная embeddinggemma",
  embeddings: "Реальный запрос embeddings",
  temporal: "Temporal Server и namespace",
  build: "Сборка coordinator и Temporal worker",
  runtime: "Изолированный runtime и реальные pollers",
  installation: "Установка пакета и блокировка неготового запуска",
  ingestion: "Индексация синтетических источников",
  retrieval: "Три этапа Qwen3 и проверяемые цитаты",
  report: "Контрольные числа, формулы и ограничения",
  approval: "Согласование через API и защита итогового артефакта",
  recovery: "SIGKILL coordinator/worker и продолжение того же workflow",
  download: "Авторизованное скачивание и проверка SHA-256",
  history: "История реального Temporal и завершение workflow",
  stability: "Неизменность моделей и входных файлов",
  cleanup: "Завершение собственных процессов и удаление временного состояния",
};
const result = {
  schemaVersion: 1, qualification: "internal-report-real-qwen3-temporal-v1",
  status: "FAIL", startedAt: new Date().toISOString(), finishedAt: null,
  approvalActor: "automated-synthetic-qualification", humanReview: false,
  checks: Object.entries(labels).map(([id, title]) => ({ id, title, status: "NOT_RUN" })),
};
const children = new Set();
let evidenceDir, stateDir, connection, workflow, deadline, stopping = false;
const safeEnv = Object.fromEntries(["PATH", "HOME", "TMPDIR", "SYSTEMROOT"].filter((key) => process.env[key]).map((key) => [key, process.env[key]]));
Object.assign(safeEnv, { AGAT_OTEL_ENABLED: "false", OTEL_SDK_DISABLED: "true", PYTHONDONTWRITEBYTECODE: "1", NO_PROXY: "*" });

function save() {
  if (!evidenceDir) return;
  const json = JSON.stringify(result, null, 2) + "\n";
  fs.writeFileSync(path.join(evidenceDir, "result.json"), json, { mode: 0o600 });
  const rows = result.checks.map((check) => `| ${check.title} | ${check.status} | ${check.code ?? "—"} |`).join("\n");
  fs.writeFileSync(path.join(evidenceDir, "result.md"), `# Квалификация внутреннего отчёта\n\nРезультат: **${result.status}**. Начало: ${result.startedAt}.\n\n`
    + `| Проверка | Статус | Код причины |\n|---|---|---|\n${rows}\n\n`
    + "NOT_RUN означает, что проверка не выполнена; это не успешная квалификация. Согласование автоматизировано только для синтетических данных и не заменяет оценку человеком.\n\n"
    + `Доказательства: [result.json](./result.json). SHA-256 JSON: \`${sha256(json)}\`.\n`, { mode: 0o600 });
}
async function check(id, action, fallback = "CHECK_FAILED") {
  const item = result.checks.find((candidate) => candidate.id === id);
  const start = Date.now();
  try {
    const details = await action();
    Object.assign(item, { status: "PASS", details });
    console.log(`PASS: ${labels[id]}`);
    return details;
  } catch (error) {
    Object.assign(item, { status: "FAIL", code: error instanceof QualificationError ? error.code : fallback });
    console.error(`FAIL: ${labels[id]} (${item.code})`);
    throw error;
  } finally { item.durationMs = Date.now() - start; save(); }
}
function inputFingerprint() {
  const files = ["package.json", "package-lock.json", "scripts/qualify-internal-report.mjs", "scripts/lib/internal-report-qualification.mjs",
    "docs/qualification/internal-report/fixtures/sources.json"];
  function visit(directory) {
    for (const item of fs.readdirSync(path.join(root, directory), { withFileTypes: true })) {
      const relative = `${directory}/${item.name}`;
      if (item.isDirectory()) visit(relative);
      else if (/\.(ts|py|json)$/.test(item.name)) files.push(relative);
    }
  }
  for (const directory of ["apps/coordinator/src", "apps/temporal-worker/src", "workers"]) visit(directory);
  for (const app of ["coordinator", "temporal-worker"]) files.push(`apps/${app}/package.json`, `apps/${app}/tsconfig.json`);
  const hashes = Object.fromEntries(files.sort().map((file) => [file, sha256(fs.readFileSync(path.join(root, file)))]));
  return { sha256: sha256(JSON.stringify(hashes)), files: hashes };
}
function command(file, args, timeout = 240_000) {
  return new Promise((resolve, reject) => {
    const child = spawn(file, args, { cwd: root, env: safeEnv, stdio: "ignore" });
    children.add(child);
    const timer = setTimeout(() => child.kill("SIGKILL"), timeout);
    child.once("error", (error) => { clearTimeout(timer); children.delete(child); reject(error); });
    child.once("exit", (code) => {
      clearTimeout(timer); children.delete(child);
      if (code === 0) resolve(); else reject(new QualificationError("COMMAND_FAILED"));
    });
  });
}
function start(file, args, env) {
  const child = spawn(file, args, { cwd: root, env: { ...safeEnv, ...env }, stdio: "ignore" });
  children.add(child);
  child.once("error", () => { child.startFailed = true; });
  return child;
}
async function stop(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null || child.startFailed) { children.delete(child); return; }
  await new Promise((resolve) => { child.once("exit", resolve); child.kill("SIGKILL"); });
  children.delete(child);
}
async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
async function poll(action, timeoutMs, code) {
  const end = Math.min(deadline, Date.now() + timeoutMs);
  while (!stopping && Date.now() < end) {
    for (const child of children) requireEvidence(!child.startFailed && child.exitCode === null && child.signalCode === null, "OWNED_PROCESS_EXITED");
    const value = await action();
    if (value) return value;
    await delay(400);
  }
  throw new QualificationError(code);
}
async function json(url, body, timeoutMs = 10_000) {
  const response = await fetch(url, { method: body === undefined ? "GET" : "POST", redirect: "error",
    headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs) });
  requireEvidence(response.ok, "SERVICE_HTTP_ERROR");
  return response.json();
}
async function clean() {
  if (workflow && connection) {
    try {
      await connection.withDeadline(Date.now() + 5_000, async () => {
        if ((await workflow.describe()).status.name === "RUNNING") await workflow.terminate("qualification cleanup");
      });
    } catch { throw new QualificationError("WORKFLOW_CLEANUP_FAILED"); }
  }
}

async function main() {
  // Create a fresh evidence directory before probing anything: a previous PASS
  // can never survive a failed run. Output stays under /docs, without symlinks.
  const target = path.resolve(root, process.env.AGAT_QUALIFICATION_OUTPUT ?? `docs/qualification/internal-report/evidence/local-${Date.now()}-${randomUUID()}`);
  const relative = path.relative(path.join(root, "docs"), target);
  requireEvidence(relative && !relative.startsWith("..") && !path.isAbsolute(relative), "OUTPUT_MUST_BE_UNDER_DOCS");
  let current = path.join(root, "docs");
  for (const part of relative.split(path.sep)) {
    current = path.join(current, part);
    requireEvidence(!fs.existsSync(current) || (!fs.lstatSync(current).isSymbolicLink() && fs.statSync(current).isDirectory()), "UNSAFE_OUTPUT_PATH");
  }
  requireEvidence(!fs.existsSync(target), "FRESH_OUTPUT_DIRECTORY_REQUIRED");
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  fs.mkdirSync(target, { mode: 0o700 });
  evidenceDir = target;
  save();
  const fixture = JSON.parse(fs.readFileSync(path.join(docRoot, "fixtures/sources.json"), "utf8"));
  const settings = await check("configuration", () => {
    requireEvidence(process.argv.length === 2, "UNKNOWN_ARGUMENT");
    const model = process.env.AGAT_QUALIFICATION_MODEL ?? "qwen3:8b";
    requireEvidence(/^qwen3(?::|-)[a-zA-Z0-9:._-]{1,80}$/.test(model), "QWEN3_REQUIRED");
    const namespace = process.env.AGAT_QUALIFICATION_NAMESPACE ?? "default";
    requireEvidence(/^[a-zA-Z0-9_-]{1,80}$/.test(namespace), "INVALID_NAMESPACE");
    const timeoutSeconds = Number(process.env.AGAT_QUALIFICATION_TIMEOUT_SECONDS ?? 1800);
    requireEvidence(Number.isInteger(timeoutSeconds) && timeoutSeconds >= 60 && timeoutSeconds <= 7200, "INVALID_TIMEOUT");
    deadline = Date.now() + timeoutSeconds * 1000;
    for (const key of ["AGAT_QUALIFICATION_QWEN_SHA256", "AGAT_QUALIFICATION_EMBEDDING_SHA256"]) {
      requireEvidence(!process.env[key] || /^[a-f0-9]{64}$/.test(process.env[key]), "INVALID_EXPECTED_DIGEST");
    }
    const lock = JSON.parse(fs.readFileSync(path.join(root, "package-lock.json"), "utf8"));
    const sdk = Object.fromEntries(["client", "common", "worker", "workflow", "activity", "proto"].map((name) => {
      const key = `node_modules/@temporalio/${name}`;
      const version = JSON.parse(fs.readFileSync(path.join(root, key, "package.json"))).version;
      requireEvidence(version === lock.packages[key].version, "SDK_LOCK_MISMATCH");
      return [name, safeVersion(version)];
    }));
    requireEvidence(new Set(Object.values(sdk)).size === 1, "SDK_VERSION_MISMATCH");
    result.versions = { node: process.version, platform: process.platform, architecture: process.arch,
      python: safeVersion(execFileSync("python3", ["--version"], { env: safeEnv, encoding: "utf8", timeout: 5000 }).trim()), temporalSdk: sdk };
    result.inputs = inputFingerprint();
    result.git = { commit: execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim(),
      dirty: Boolean(execFileSync("git", ["status", "--porcelain"], { cwd: root, encoding: "utf8" }).trim()) };
    return { model, embeddingModel: fixture.embeddingModel, namespace, timeoutSeconds,
      ollama: loopbackOrigin(process.env.AGAT_QUALIFICATION_OLLAMA_URL ?? "http://127.0.0.1:11434"),
      temporal: temporalAddress(process.env.AGAT_QUALIFICATION_TEMPORAL_ADDRESS ?? "127.0.0.1:7233") };
  }, "DEPENDENCIES_UNAVAILABLE");
  let tags, chatIdentity, embeddingIdentity, embeddingProbe;
  // Collect every infrastructure failure, rather than skipping to a green suite.
  try {
    await check("ollama", async () => {
      const version = safeVersion((await json(`${settings.ollama}/api/version`)).version);
      tags = await json(`${settings.ollama}/api/tags`);
      result.versions.ollama = version;
      return { version };
    }, "OLLAMA_UNAVAILABLE");
  } catch { /* recorded */ }
  for (const [id, name, capability] of [["qwen3", settings.model, "completion"], ["embeddingModel", fixture.embeddingModel, "embedding"]]) {
    try {
      const identity = await check(id, async () => {
        requireEvidence(tags, "OLLAMA_UNAVAILABLE");
        requireEvidence(tags.models?.some((item) => item.name === name || item.name === `${name}:latest`), "MODEL_NOT_INSTALLED");
        const identity = modelIdentity(tags, await json(`${settings.ollama}/api/show`, { model: name }), name, capability);
        const expected = process.env[id === "qwen3" ? "AGAT_QUALIFICATION_QWEN_SHA256" : "AGAT_QUALIFICATION_EMBEDDING_SHA256"];
        requireEvidence(!expected || identity.digest === expected, "MODEL_DIGEST_MISMATCH");
        return identity;
      }, "MODEL_UNAVAILABLE");
      if (id === "qwen3") chatIdentity = identity; else embeddingIdentity = identity;
    } catch { /* recorded */ }
  }
  try {
    embeddingProbe = await check("embeddings", async () => {
      requireEvidence(embeddingIdentity, "EMBEDDING_MODEL_UNAVAILABLE");
      const response = await json(`${settings.ollama}/v1/embeddings`, { model: fixture.embeddingModel,
        input: ["Учебные заявки: июль 100, август 120."] }, 120_000);
      return validateVector(response.data?.[0]?.embedding);
    }, "EMBEDDING_SERVICE_UNAVAILABLE");
  } catch { /* recorded */ }
  let client;
  try {
    await check("temporal", async () => {
      const { Client, Connection } = await import("@temporalio/client");
      connection = await Connection.connect({ address: settings.temporal, connectTimeout: 5000 });
      const system = await connection.withDeadline(Date.now() + 5000, () => connection.workflowService.getSystemInfo({}));
      await connection.withDeadline(Date.now() + 5000, () => connection.workflowService.describeNamespace({ namespace: settings.namespace }));
      client = new Client({ connection, namespace: settings.namespace, identity: "qualification-client" });
      result.versions.temporalServer = safeVersion(system.serverVersion);
      return { version: result.versions.temporalServer, namespace: settings.namespace };
    }, "TEMPORAL_UNAVAILABLE");
  } catch { /* recorded */ }
  requireEvidence(result.checks.every((item) => item.status !== "FAIL"), "INFRASTRUCTURE_REQUIRED");

  await check("build", async () => {
    await command("npm", ["run", "build", "--workspace", "@agat/coordinator"]);
    await command("npm", ["run", "build", "--workspace", "@agat/temporal-worker"]);
    return { workflowBundleSha256: sha256(fs.readFileSync(path.join(root, "apps/temporal-worker/dist/workflow-bundle.js"))) };
  });
  stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "agat-report-qualification-"));
  fs.chmodSync(stateDir, 0o700);
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const queue = `agat-qualification-${randomUUID()}`;
  const adminToken = randomBytes(32).toString("hex");
  const enrollmentToken = randomBytes(32).toString("hex");
  const env = { AGAT_DB_PATH: path.join(stateDir, "state.db"), AGAT_ARTIFACTS_DIR: path.join(stateDir, "artifacts"),
    AGAT_HOST: "127.0.0.1", AGAT_PORT: String(port), AGAT_ADMIN_TOKEN: adminToken, AGAT_ENROLLMENT_TOKEN: enrollmentToken,
    AGAT_CREDENTIALS_KEY: randomBytes(32).toString("hex"), AGAT_SEED_DEMO: "false", AGAT_SERVE_WEB: "false",
    AGAT_MCP_ENABLED: "false", AGAT_A2A_ENABLED: "false", AGAT_OIDC_ENABLED: "false", AGAT_LOCAL_WORKER_LAUNCHER: "false",
    AGAT_TEMPORAL_ENABLED: "true", AGAT_TEMPORAL_ADDRESS: settings.temporal, AGAT_TEMPORAL_NAMESPACE: settings.namespace,
    AGAT_TEMPORAL_TASK_QUEUE: queue, AGAT_TEMPORAL_INTERNAL_TOKEN: randomBytes(32).toString("hex"), AGAT_TEMPORAL_TARGET: "local",
    AGAT_TEMPORAL_TLS: "false", AGAT_TEMPORAL_VERSIONING_ENABLED: "false", AGAT_TEMPORAL_METRICS_ADDRESS: "127.0.0.1:0",
    AGAT_COORDINATOR_INTERNAL_URL: base };
  let coordinator, temporalWorker;
  const api = async (route, body, status = 200, token = adminToken, method = body === undefined ? "GET" : "POST") => {
    const response = await fetch(`${base}/api/v1${route}`, { method, redirect: "error",
      headers: { "x-agat-admin-token": token, "x-agat-project-id": "qualification", "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15_000) });
    requireEvidence(response.status === status, `API_STATUS_${response.status}_EXPECTED_${status}`);
    return status === 204 || status === 401 || status === 400 || status === 409 ? null : response.json();
  };
  async function readyCoordinator() {
    return poll(async () => {
      try { return await json(`${base}/api/v1/health`); } catch { return false; }
    }, 30_000, "COORDINATOR_UNAVAILABLE");
  }
  async function readyPollers(identity) {
    for (const taskQueueType of [1, 2]) {
      await poll(async () => {
        const response = await connection.withDeadline(Date.now() + 5000, () => connection.workflowService.describeTaskQueue({
          namespace: settings.namespace, taskQueue: { name: queue, kind: 1 }, taskQueueType,
        }));
        return response.pollers?.some((poller) => poller.identity === identity);
      }, 60_000, "TEMPORAL_POLLER_MISSING");
    }
  }
  await check("runtime", async () => {
    coordinator = start(process.execPath, ["apps/coordinator/dist/server.js"], env);
    const health = await readyCoordinator();
    requireEvidence(health.processRuntime.mode === "temporal" && health.processRuntime.connected && health.processRuntime.taskQueue === queue, "TEMPORAL_RUNTIME_REQUIRED");
    result.versions.coordinator = safeVersion(health.version);
    temporalWorker = start(process.execPath, ["apps/temporal-worker/dist/worker.js"], { ...env, AGAT_TEMPORAL_WORKER_ID: "qualification-worker-1" });
    await readyPollers("qualification-worker-1");
    await api("/projects", { id: "qualification", name: "Синтетическая квалификация" }, 201);
    await api("/settings/model-router", { enabled: false }, 200, adminToken, "PATCH");
    return { mode: "temporal", taskQueue: queue, pollers: ["workflow", "activity"], stateStore: "sqlite", isolated: true };
  });
  let manifest, installation, installInput;
  await check("installation", async () => {
    const catalog = await api("/process-packs");
    manifest = catalog.packs.find((item) => item.id === "internal-report");
    requireEvidence(manifest && manifest.defaults.embeddingModel === fixture.embeddingModel, "PACK_NOT_FOUND");
    requireEvidence(manifest.knowledge.documents.every((doc) => fixture.sources.some((source) => source.uri === doc.sourceUri && source.content === doc.content)), "FIXTURE_DRIFT");
    installInput = { version: manifest.version, manifestSha256: manifest.manifestSha256, model: settings.model };
    const installed = await api("/process-packs/internal-report/install", installInput, 201);
    installation = installed.installation;
    requireEvidence(!installed.preflight.runnableNow && !installed.preflight.scenarioVerified, "UNREADY_PACK_ACCEPTED");
    await api(`/processes/${installation.processId}/start`, { input: manifest.sample.input, version: installation.processVersion, startMode: "now" }, 409);
    const repeat = await api("/process-packs/internal-report/install", installInput);
    requireEvidence(JSON.stringify(repeat.installation) === JSON.stringify(installation), "INSTALLATION_NOT_IDEMPOTENT");
    return { pack: manifest.id, version: manifest.version, manifestSha256: manifest.manifestSha256, processVersion: installation.processVersion,
      unreadyStartStatus: 409, idempotent: true };
  });
  let ingestion;
  await check("ingestion", async () => {
    start("python3", ["workers/agat_worker.py", "--coordinator", base, "--name", "qualification-model-worker", "--models", settings.model,
      "--embedding-models", fixture.embeddingModel, "--model-url", `${settings.ollama}/v1`, "--model-api-key", "",
      "--model-discovery", "off", "--credentials", path.join(stateDir, "worker.json"), "--poll-interval", "0.2", "--no-web"], env);
    await poll(async () => (await api("/process-packs/internal-report/preflight", installInput)).preflight.runnableNow, 180_000, "INGESTION_TIMEOUT");
    ingestion = validateIngestion(await api("/knowledge/export"), installation, fixture, embeddingProbe.dimensions);
    return { sources: ingestion };
  });
  let instance, trace, approvalStage, beforeDescription, citations, originalOutputs;
  await check("retrieval", async () => {
    const qualificationInput = `${manifest.sample.input}\n\n${fixture.inputSuffix}`;
    await api(`/processes/${installation.processId}/start`, { input: manifest.sample.input, version: installation.processVersion,
      startMode: "now", startNodeId: "artifact" }, 400);
    instance = await api(`/processes/${installation.processId}/start`, { input: qualificationInput, version: installation.processVersion,
      startMode: "now", knowledgeCollectionIds: installation.knowledgeCollectionIds, resultDestination: "history" }, 201);
    workflow = client.workflow.getHandle(`agat-process-${instance.id}`);
    await poll(async () => {
      const state = await api(`/process-instances/${instance.id}`);
      requireEvidence(!["failed", "cancelled"].includes(state.status), "PROCESS_FAILED");
      requireEvidence(state.runtime === "temporal" && state.runId === instance.runId, "TEMPORAL_INSTANCE_REQUIRED");
      return state.status === "waiting_approval";
    }, settings.timeoutSeconds * 1000, "GENERATION_TIMEOUT");
    trace = await api(`/runs/${instance.runId}/trace`);
    citations = validateCitations(trace, installation, ingestion, settings.model);
    approvalStage = trace.run.stages.find((stage) => stage.status === "waiting_approval");
    requireEvidence(approvalStage && trace.artifacts.length === 0, "ARTIFACT_BEFORE_APPROVAL");
    originalOutputs = citations.map((item) => item.outputSha256);
    await poll(async () => (await connection.withDeadline(Date.now() + 10_000,
      () => workflow.query("processState")))?.status === "waiting_approval", 30_000, "TEMPORAL_APPROVAL_STATE_MISSING");
    beforeDescription = await workflow.describe();
    return { instanceId: instance.id, runId: instance.runId, inputSha256: sha256(qualificationInput), workflowId: beforeDescription.workflowId,
      temporalRunId: beforeDescription.runId, stages: citations };
  });
  const reviewer = trace.run.stages.find((stage) => stage.agent.id === installation.agentIds.reviewer);
  try {
    await check("report", () => {
      const rubric = reportRubric(reviewer.output);
      // Only booleans and numeric tokens leave the model output buffer.
      result.reportRubric = rubric;
      requireEvidence(rubric.passed, "REPORT_RUBRIC_FAILED");
      return { outputSha256: sha256(reviewer.output), criteria: rubric.criteria };
    });
  } catch (error) {
    await check("approval", async () => {
      await api(`/approvals/${approvalStage.id}`, { decision: "reject" }, 401, "invalid-qualification-token");
      await api(`/approvals/${approvalStage.id}`, { decision: "reject", reason: "Синтетический отчёт не прошёл квалификационную рубрику" }, 204);
      requireEvidence((await api(`/runs/${instance.runId}/trace`)).artifacts.length === 0, "ARTIFACT_AFTER_REJECTION");
      requireEvidence((await api(`/process-instances/${instance.id}`)).status === "cancelled", "REJECTION_NOT_APPLIED");
      return { decision: "reject", reason: "REPORT_RUBRIC_FAILED", decisionStatus: 204, unauthorizedDecisionStatus: 401, artifactsAfter: 0 };
    });
    throw error;
  }
  await check("approval", async () => {
    await api(`/approvals/${approvalStage.id}`, { decision: "approve" }, 401, "invalid-qualification-token");
    requireEvidence((await api(`/runs/${instance.runId}/trace`)).artifacts.length === 0, "UNAUTHORIZED_ARTIFACT");
    return { statusBefore: "waiting_approval", artifactsBefore: 0, unauthorizedDecisionStatus: 401, partialStartStatus: 400 };
  });
  await check("recovery", async () => {
    await stop(temporalWorker);
    await stop(coordinator);
    requireEvidence(temporalWorker.signalCode === "SIGKILL" && coordinator.signalCode === "SIGKILL", "FAULT_NOT_INJECTED");
    coordinator = start(process.execPath, ["apps/coordinator/dist/server.js"], env);
    await readyCoordinator();
    const restored = await api(`/process-instances/${instance.id}`);
    requireEvidence(restored.status === "waiting_approval" && restored.runId === instance.runId, "APPROVAL_NOT_DURABLE");
    requireEvidence((await api(`/runs/${instance.runId}/trace`)).artifacts.length === 0, "ARTIFACT_BEFORE_APPROVAL");
    await api(`/approvals/${approvalStage.id}`, { decision: "approve" }, 204);
    // In AGAT synchronous graph transitions (including artifact creation) are
    // committed by coordinator. Temporal records the durable lifecycle via tick
    // activities/updates. Its execution must remain open until a worker returns.
    for (let attempt = 0; attempt < 3; attempt++) {
      requireEvidence((await workflow.describe()).status.name === "RUNNING", "TEMPORAL_NOT_WAITING_FOR_WORKER");
      await delay(500);
    }
    temporalWorker = start(process.execPath, ["apps/temporal-worker/dist/worker.js"], { ...env, AGAT_TEMPORAL_WORKER_ID: "qualification-worker-2" });
    await readyPollers("qualification-worker-2");
    await poll(async () => (await api(`/process-instances/${instance.id}`)).status === "completed", 90_000, "RECOVERY_TIMEOUT");
    trace = await api(`/runs/${instance.runId}/trace`);
    const after = validateCitations(trace, installation, ingestion, settings.model);
    requireEvidence(JSON.stringify(after.map((item) => item.outputSha256)) === JSON.stringify(originalOutputs), "GENERATION_REPEATED_ON_RECOVERY");
    requireEvidence((await workflow.describe()).runId === beforeDescription.runId, "WORKFLOW_REPLACED");
    requireEvidence((await api("/process-packs/internal-report/preflight", installInput)).preflight.scenarioVerified, "PACK_NOT_VERIFIED");
    return { fault: "SIGKILL", targets: ["temporal-worker", "coordinator"], persistedApproval: true,
      decisionStatus: 204, temporalPendingWithoutWorker: true, sameTemporalRunId: true, unchangedStageOutputs: true, statusAfter: "completed" };
  });
  await check("download", async () => {
    const artifacts = trace.artifacts.filter((artifact) => artifact.name === "internal-report.md");
    requireEvidence(artifacts.length === 1, "REPORT_ARTIFACT_COUNT");
    const artifact = artifacts[0];
    await api(`/artifacts/${artifact.id}/download`, undefined, 401, "invalid-qualification-token");
    const response = await fetch(`${base}/api/v1/artifacts/${artifact.id}/download`, { redirect: "error",
      headers: { "x-agat-admin-token": adminToken, "x-agat-project-id": "qualification" }, signal: AbortSignal.timeout(15_000) });
    requireEvidence(response.status === 200 && /attachment/.test(response.headers.get("content-disposition") ?? "")
      && /internal-report\.md/.test(response.headers.get("content-disposition") ?? ""), "DOWNLOAD_HEADERS_INVALID");
    const bytes = Buffer.from(await response.arrayBuffer());
    requireEvidence(bytes.length === artifact.sizeBytes && sha256(bytes) === artifact.sha256
      && bytes.toString("utf8") === `# Внутренний отчёт\n\n${reviewer.output}`, "DOWNLOAD_CONTENT_MISMATCH");
    return { filename: "internal-report.md", status: 200, unauthorizedStatus: 401, sizeBytes: bytes.length,
      sha256: sha256(bytes), artifactSha256: artifact.sha256, matchesApprovedOutput: true };
  });
  await check("history", async () => {
    await poll(async () => (await workflow.describe()).status.name === "COMPLETED", 30_000, "TEMPORAL_COMPLETION_TIMEOUT");
    const raw = await workflow.fetchHistory();
    const durableResult = await workflow.result();
    requireEvidence(durableResult.status === "completed" && durableResult.instanceId === instance.id, "TEMPORAL_RESULT_MISMATCH");
    const events = historyEvidence(raw);
    requireEvidence(events.some((event) => event.qualificationWorker === "qualification-worker-1")
      && events.some((event) => event.qualificationWorker === "qualification-worker-2"), "REPLAY_ON_REPLACEMENT_WORKER_MISSING");
    requireEvidence(raw.events.some((event) => event.workflowExecutionCompletedEventAttributes)
      && !raw.events.some((event) => event.workflowExecutionFailedEventAttributes), "WORKFLOW_NOT_COMPLETED");
    return { workflowId: beforeDescription.workflowId, runId: beforeDescription.runId, status: "COMPLETED",
      processStatus: durableResult.status, transitionCount: durableResult.transitionCount, events };
  });
  await check("stability", async () => {
    const latest = await json(`${settings.ollama}/api/tags`);
    for (const [identity, capability] of [[chatIdentity, "completion"], [embeddingIdentity, "embedding"]]) {
      const current = modelIdentity(latest, await json(`${settings.ollama}/api/show`, { model: identity.name }), identity.name, capability);
      requireEvidence(JSON.stringify(current) === JSON.stringify(identity), "MODEL_CHANGED_DURING_RUN");
    }
    requireEvidence(inputFingerprint().sha256 === result.inputs.sha256, "SOURCE_CHANGED_DURING_RUN");
    return { modelDigestsUnchanged: true, inputHashesUnchanged: true };
  });
}

const signalHandler = () => { stopping = true; };
process.on("SIGINT", signalHandler);
process.on("SIGTERM", signalHandler);
let exitCode = 1;
try {
  await main();
  exitCode = 0;
} catch (error) {
  const infraIds = ["configuration", "ollama", "qwen3", "embeddingModel", "embeddings", "temporal"];
  exitCode = result.checks.some((item) => infraIds.includes(item.id) && item.status === "FAIL") ? 2 : 1;
  console.error(`Квалификация не пройдена: ${error instanceof QualificationError ? error.code : "EXECUTION_FAILED"}`);
} finally {
  if (evidenceDir && fs.existsSync(evidenceDir)) {
    try {
      await check("cleanup", async () => {
        let workflowError;
        try { await clean(); } catch (error) { workflowError = error; }
        for (const child of [...children]) await stop(child);
        if (connection) await connection.close();
        if (stateDir) fs.rmSync(stateDir, { recursive: true, force: true });
        if (workflowError) throw workflowError;
        return { processesStopped: true, temporaryStateRemoved: true, rawEvidenceRetained: false };
      });
    } catch { exitCode = 1; }
    if (result.checks.some((item) => item.status !== "PASS")) exitCode ||= 1;
    result.status = exitCode === 0 ? "PASS" : "FAIL";
    result.exitCode = exitCode;
    result.finishedAt = new Date().toISOString();
    save();
    console.log(`Результат: ${result.status}; код выхода: ${exitCode}; доказательства: ${path.relative(root, evidenceDir)}`);
  }
  process.exitCode = exitCode;
}
