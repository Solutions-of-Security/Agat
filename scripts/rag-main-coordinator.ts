/** Own the actual compiled coordinator during the disposable HTTP qualification. */
import assert from "node:assert/strict";
import { fork } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export interface MainProbeMetrics {
  type: "probe-finished";
  id: string;
  maintenance: Array<{ durationMs: number; error: string | null }>;
  eventLoop: { maxMs: number; p99Ms: number; samples: number };
  memory: NodeJS.MemoryUsage;
  processLifetimePeakRssBytes: number;
}
type ObserverReply = MainProbeMetrics | { type: "probe-started"; id: string };

export async function startMainCoordinator(data: string, mode: "sync" | "isolated", compiledSha256: Record<string, string>, options: { instanceId?: string } = {}) {
  const instanceId = options.instanceId ?? "rag-http-main";
  assert.match(instanceId, /^[a-z][a-z0-9-]{0,79}$/);
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  assert.ok(Object.keys(compiledSha256).length > 0);
  for (const [name, expected] of Object.entries(compiledSha256)) {
    assert.ok(name.startsWith("apps/coordinator/dist/") && !name.includes(".."));
    assert.equal(createHash("sha256").update(fs.readFileSync(path.join(root, name))).digest("hex"), expected);
  }
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("AGAT_")));
  const child = fork(path.join(root, "apps/coordinator/dist/server.js"), [], {
    silent: true, execArgv: ["--import", path.join(root, "scripts/observe-rag-coordinator-main.mjs")],
    env: { ...env, AGAT_RAG_HTTP_DISPOSABLE: "1", AGAT_HOST: "127.0.0.1", AGAT_PORT: "0",
      AGAT_STATE_STORE_DRIVER: "postgresql", AGAT_ARTIFACT_STORE_DRIVER: "postgresql", AGAT_ARTIFACTS_DIR: path.join(data, "artifacts"),
      AGAT_POSTGRES_URL: process.env.AGAT_POSTGRES_URL, AGAT_POSTGRES_TENANT_URL: process.env.AGAT_POSTGRES_TENANT_URL,
      AGAT_POSTGRES_POOL_MAX: "4", AGAT_POSTGRES_SSL_MODE: "disable", AGAT_REGION: "eu-test-1", AGAT_RESIDENCY_DOMAIN: "eu-test",
      AGAT_COORDINATOR_INSTANCE_ID: instanceId, AGAT_KNOWLEDGE_SEARCH_EXECUTION: mode,
      AGAT_KNOWLEDGE_SEARCH_MAX_PENDING: "4", AGAT_KNOWLEDGE_SEARCH_TIMEOUT_MS: "30000", AGAT_KNOWLEDGE_SEARCH_MAX_CANDIDATES: "10000",
      AGAT_REQUIRE_SIGNED_WORKER_RELEASES: "false", AGAT_REQUIRE_WORKER_PROVENANCE: "false", AGAT_REQUIRE_WORKER_RUNTIME_ATTESTATION: "false",
      AGAT_SERVE_WEB: "false", AGAT_SEED_DEMO: "false", AGAT_LOCAL_WORKER_LAUNCHER: "false",
      AGAT_MCP_ENABLED: "false", AGAT_A2A_ENABLED: "false", AGAT_SANDBOX_ENABLED: "false", AGAT_SIEM_ENABLED: "false", AGAT_TEMPORAL_ENABLED: "false" },
  });
  let diagnostic = "";
  child.stdout!.setEncoding("utf8");
  child.stdout!.on("data", (chunk: string) => { diagnostic = (diagnostic + chunk).slice(-8192); process.stderr.write(chunk); });
  child.stderr!.pipe(process.stderr, { end: false });
  const exited = new Promise<{ code: number | null; signal: string | null }>(resolve => child.once("close", (code, signal) => resolve({ code, signal })));
  const stop = async () => {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
    const timer = setTimeout(() => child.kill("SIGKILL"), 5_000);
    try { return await exited; } finally { clearTimeout(timer); }
  };
  const sample = (type: "probe-start" | "probe-finish", id: string): Promise<ObserverReply> => new Promise((resolve, reject) => {
    const timer = setTimeout(() => done(new Error("Main observer reply timed out")), 30_000);
    const message = (value: ObserverReply) => { if (value.id === id && value.type === (type === "probe-start" ? "probe-started" : "probe-finished")) done(undefined, value); };
    const failed = () => done(new Error("Main exited before observer reply"));
    const done = (error?: Error, value?: ObserverReply) => {
      clearTimeout(timer); child.off("message", message); child.off("close", failed);
      if (error) reject(error); else resolve(value!);
    };
    child.on("message", message); child.once("close", failed);
    child.send({ type, id }, error => { if (error) done(error); });
  });
  try {
    const port = await new Promise<number>((resolve, reject) => {
      const timer = setTimeout(() => done(new Error("Main startup timed out")), 60_000);
      const data = () => { const found = /АГАТ слушает http:\/\/127\.0\.0\.1:(\d+)/.exec(diagnostic); if (found) done(undefined, Number(found[1])); };
      const failed = () => done(new Error("Main startup failed; see the captured diagnostic log"));
      const done = (error?: Error, value?: number) => {
        clearTimeout(timer); child.stdout!.off("data", data); child.off("error", failed); child.off("close", failed);
        if (error) reject(error); else resolve(value!);
      };
      child.stdout!.on("data", data); child.once("error", failed); child.once("close", failed);
    });
    assert.ok(port > 0 && child.pid);
    return { port, pid: child.pid, stop,
      begin: async (id: string) => { const reply = await sample("probe-start", id); assert.equal(reply.type, "probe-started"); },
      finish: async (id: string) => { const reply = await sample("probe-finish", id); assert.ok(reply.type === "probe-finished"); return reply; } };
  } catch (error) { await stop(); throw error; }
}
