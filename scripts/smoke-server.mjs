import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

const root = fileURLToPath(new URL("../", import.meta.url));
const port = Number(process.env.AGAT_SMOKE_PORT || 8796);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid AGAT_SMOKE_PORT");
const directory = await mkdtemp(path.join(os.tmpdir(), "agat-browser-smoke-"));
const url = `http://127.0.0.1:${port}`;
const enrollmentToken = randomBytes(32).toString("hex");
// Never inherit a real deployment's database, credentials or telemetry settings.
const environment = Object.fromEntries(Object.entries(process.env)
  .filter(([key]) => !key.startsWith("AGAT_") && !key.startsWith("OTEL_")));
const children = [];
let stopping = false;

async function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) child.kill("SIGTERM");
  await Promise.all(children.map(async (child) => {
    for (let attempt = 0; attempt < 50 && child.exitCode === null && child.signalCode === null; attempt++) await delay(100);
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGKILL");
      await new Promise((resolve) => child.once("exit", resolve));
    }
  }));
  await rm(directory, { recursive: true, force: true });
  process.exit(code);
}
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => void stop());

function start(command, args, env) {
  const child = spawn(command, args, { cwd: root, env, stdio: "inherit" });
  children.push(child);
  child.on("error", (error) => { console.error(error.message); void stop(1); });
  child.on("exit", (code) => { if (!stopping) void stop(code || 1); });
  return child;
}

start(process.execPath, ["apps/coordinator/dist/server.js"], {
  ...environment, AGAT_HOST: "127.0.0.1", AGAT_PORT: String(port),
  AGAT_DB_PATH: path.join(directory, "agat.db"), AGAT_ARTIFACTS_DIR: path.join(directory, "artifacts"),
  AGAT_ENROLLMENT_TOKEN: enrollmentToken, AGAT_ADMIN_TOKEN: "", AGAT_SEED_DEMO: "false",
  AGAT_SERVE_WEB: "true", AGAT_OIDC_ENABLED: "false", AGAT_TEMPORAL_ENABLED: "false",
  AGAT_MCP_ENABLED: "false", AGAT_A2A_ENABLED: "false", AGAT_OTEL_ENABLED: "false",
});
let healthy = false;
for (let attempt = 0; attempt < 120; attempt++) {
  try {
    const response = await fetch(`${url}/api/v1/health`, { signal: AbortSignal.timeout(1000) });
    if (response.ok) { healthy = true; break; }
  } catch { /* Wait for the child to bind its port. */ }
  await delay(250);
}
if (!healthy) { console.error("Smoke coordinator did not become healthy"); await stop(1); }
start("python3", ["workers/agat_worker.py", "--coordinator", url,
  "--enrollment-token", enrollmentToken, "--name", "browser-smoke", "--models", "smoke-model",
  "--model-discovery", "off", "--credentials", path.join(directory, "worker.json"),
  "--poll-interval", "0.2", "--no-web", "--dry-run"], environment);
console.log(`Browser smoke: ${url}; isolated SQLite and dry-run worker (no model inference).`);
