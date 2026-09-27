import assert from "node:assert/strict";
import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { gzipSync } from "node:zlib";
import { AgatStore } from "../apps/coordinator/src/database.js";
import { loadConfig } from "../apps/coordinator/src/config.js";
import { createCoordinatorServer } from "../apps/coordinator/src/server.js";

const root = fileURLToPath(new URL("../", import.meta.url));
export function source(batch: number, item: number): string {
  if (batch === 1) return `Учебный документ ${item}. Статус заявки: выполнено. Сотрудник проверил источник.`;
  assert.equal(batch, 32);
  return Array.from({ length: 32 }, (_, i) => `Учебная запись ${item}-${i}. Статус: выполнено. `.padEnd(400, "я")).join("");
}
export type Phase = { id: string; batch: number; idleTimeout: number; idleSeconds: number };

async function stop(worker: ChildProcess | undefined) {
  if (!worker || worker.exitCode !== null || worker.signalCode !== null) return;
  const ended = new Promise<void>(resolve => worker.once("close", () => resolve()));
  worker.stdin?.end(); worker.kill("SIGTERM");
  const timer = setTimeout(() => worker.kill("SIGKILL"), 5_000);
  try { await ended; } finally { clearTimeout(timer); }
}
export async function runPhase(phase: Phase, modelUrl: string, model: string, output: string) {
  const address = new URL(modelUrl);
  assert.ok(address.protocol === "http:" && address.hostname === "127.0.0.1" && address.port && !address.username && !address.password);
  assert.ok(/^[a-z0-9-]+$/.test(phase.id) && [1, 32].includes(phase.batch));
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "agat-idle-worker-"));
  const store = new AgatStore(path.join(temporary, "state.sqlite"), { seedDemo: false, artifactsDir: path.join(temporary, "artifacts") });
  store.updateScheduler("sequential", 1);
  const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
    adminToken: "idle-probe-admin", enrollmentToken: "idle-probe-enrollment", oidcEnabled: false,
    mcpEnabled: false, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false }, store);
  let worker: ChildProcess | undefined, failure: string | null = null;
  const messages: any[] = [], rounds: any[] = [];
  let workerError: string | null = null, pending = "", diagnostic = "";
  const began = performance.now();
  const ensure = () => {
    assert.equal(workerError, null); assert.equal(worker!.exitCode, null); assert.equal(worker!.signalCode, null);
    assert.ok(performance.now() - began < 60_000, "Phase budget exceeded");
    assert.ok(!messages.some(item => item.kind === "control_error"));
  };
  const waitFor = async (predicate: () => boolean) => {
    while (!predicate()) { ensure(); await delay(20); }
  };
  const snapshot = async (name: string) => {
    worker!.stdin!.write(JSON.stringify({ snapshot: name }) + "\n");
    await waitFor(() => messages.some(item => item.kind === "snapshot" && item.name === name));
  };
  const collection = String(store.createKnowledgeCollection({ name: "Worker idle model fixture", embeddingModel: model,
    chunkSize: 400, chunkOverlap: 0 }, "default").id);
  try {
    await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
    const bound = server.address(); assert.ok(bound && typeof bound === "object");
    worker = spawn(process.env.AGAT_PROBE_PYTHON || "python3", ["scripts/embedding-idle-worker-probe.py",
      "--coordinator", `http://127.0.0.1:${bound.port}`, "--enrollment-token", "idle-probe-enrollment",
      "--credentials", path.join(temporary, "worker.json"), "--name", "idle-probe-worker", "--models", model,
      "--embedding-models", model, "--model-url", modelUrl, "--model-api-key", "probe-local", "--model-discovery", "off", "--no-web",
      "--poll-interval", "0.2", "--concurrency", "1", "--embedding-transport", "session", "--embedding-timeout", "30",
      "--embedding-idle-timeout", String(phase.idleTimeout)], { cwd: root, stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, AGAT_OTEL_ENABLED: "false", OTEL_SDK_DISABLED: "true", NO_PROXY: "127.0.0.1,localhost",
        AGAT_IDLE_PROBE_OUTPUT: path.join(output, `${phase.id}.worker.json.gz`) } });
    worker.once("error", error => { workerError = error.name; });
    worker.stdout!.setEncoding("utf8"); worker.stderr!.setEncoding("utf8");
    worker.stderr!.on("data", data => { diagnostic = (diagnostic + data).slice(-8192); });
    worker.stdout!.on("data", data => {
      pending += data; assert.ok(pending.length < 64 * 1024, "Unbounded probe line");
      const lines = pending.split("\n"); pending = lines.pop()!;
      for (const line of lines) if (line.startsWith("AGAT_IDLE_PROBE ")) messages.push(JSON.parse(line.slice(16)));
    });
    await waitFor(() => store.db.prepare("SELECT id FROM nodes WHERE name = ?").all("idle-probe-worker").length === 1);
    await snapshot("before");
    for (const round of [0, 1]) {
      const ids = [0, 1].map(item => {
        const document = store.ingestKnowledgeDocument(collection, { name: `Fixture ${round}-${item}`, content: source(phase.batch, item) });
        assert.equal(document.chunkCount, phase.batch); return String(document.id);
      });
      await waitFor(() => ids.every(id => store.db.prepare("SELECT status FROM knowledge_documents WHERE id = ?").get(id)?.status === "ready")
        && messages.filter(item => item.kind === "lease" && item.event === "return").length === (round + 1) * 2);
      rounds.push(ids.map(id => ({ document: store.db.prepare("SELECT id, content, content_sha256, status FROM knowledge_documents WHERE id = ?").get(id),
        chunks: store.db.prepare("SELECT id, ordinal, content, content_sha256, char_start, char_end, embedding_model, embedding_dimensions, embedding_json FROM knowledge_chunks WHERE document_id = ? ORDER BY ordinal").all(id),
        job: store.db.prepare("SELECT status, failures, lease_id FROM knowledge_embedding_jobs WHERE document_id = ?").get(id) })));
      await snapshot(round === 0 ? "afterFirst" : "afterSecond");
      if (round === 0) { await delay(phase.idleSeconds * 1000); await snapshot("afterIdle"); }
    }
    await stop(worker); assert.equal(worker.exitCode, 0); assert.equal(worker.signalCode, null);
  } catch (error) {
    failure = error instanceof Error ? `${error.name}: ${error.message}` : "experiment_failed";
    // Diagnostic only contains output from the synthetic worker fixture, no credentials.
    console.error(phase.id, failure, diagnostic.slice(-1000));
  } finally {
    await stop(worker); server.closeAllConnections();
    if (server.listening) await new Promise<void>(resolve => server.close(() => resolve()));
  }
  const events = store.db.prepare("SELECT id, type, data_json FROM events WHERE type LIKE 'knowledge.%' ORDER BY id").all();
  store.close(); fs.rmSync(temporary, { recursive: true, force: true });
  const result = { schema: "agat.embedding.idle-worker-store.v1", phase, status: failure ? "incomplete" : "observed", failure,
    elapsedMs: performance.now() - began, rounds, events, workerExitCode: worker?.exitCode, workerSignal: worker?.signalCode };
  fs.writeFileSync(path.join(output, `${phase.id}.store.json.gz`), gzipSync(JSON.stringify(result)), { flag: "wx" });
  assert.equal(failure, null);
  return { id: phase.id, elapsedMs: result.elapsedMs };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const planPath = path.resolve(process.argv[2]!); const plan = JSON.parse(fs.readFileSync(planPath, "utf8"));
  assert.equal(plan.schema, "agat.embedding.worker-idle-model.v1");
  for (const phase of plan.phases) {
    console.log(`phase ${phase.id}: started`);
    await runPhase(phase, plan.endpoint, plan.model, path.dirname(planPath));
    console.log(`phase ${phase.id}: completed`);
  }
}
