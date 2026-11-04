import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import http from "node:http";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

export const root = fileURLToPath(new URL("../../../../", import.meta.url));
export const cleanEnv = () => Object.fromEntries(Object.entries(process.env).filter(([key]) =>
  !key.startsWith("AGAT_") && !key.startsWith("OTEL_")));

export async function within<T>(promise: Promise<T>, message: string, timeout = 20_000): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([promise, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), timeout);
    })]);
  } finally { clearTimeout(timer); }
}

export async function eventually(check: () => boolean | Promise<boolean>, message: string, timeout = 20_000): Promise<void> {
  const until = performance.now() + timeout;
  while (performance.now() < until) { if (await check()) return; await delay(25); }
  assert.fail(message);
}

export function processChild(command: string, args: string[], env: NodeJS.ProcessEnv) {
  const child = spawn(command, args, { cwd: root, env, stdio: ["ignore", "pipe", "pipe"] });
  let log = "";
  child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8");
  child.stdout.on("data", chunk => { log = (log + chunk).slice(-32_000); });
  child.stderr.on("data", chunk => { log = (log + chunk).slice(-32_000); });
  let spawnError: Error | undefined;
  child.on("error", error => { spawnError = error; });
  const closed = new Promise<{ code: number | null; signal: string | null }>(resolve => {
    child.once("close", (code, signal) => resolve({ code, signal }));
  });
  return {
    child, closed, log: () => log,
    ready: async (pattern: RegExp) => {
      let match: RegExpExecArray | null = null;
      await eventually(() => {
        if (spawnError) throw spawnError;
        assert.equal(child.exitCode, null, log); assert.equal(child.signalCode, null, log);
        match = pattern.exec(log); return Boolean(match);
      }, `Process did not start: ${args[0]}`);
      return match!;
    },
    stop: async (signal: NodeJS.Signals = "SIGTERM") => {
      if (child.exitCode === null && child.signalCode === null) child.kill(signal);
      const timer = setTimeout(() => child.kill("SIGKILL"), 5_000);
      try { return await closed; } finally { clearTimeout(timer); }
    },
  };
}

export async function listen(server: http.Server) {
  await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const bound = server.address(); assert.ok(bound && typeof bound === "object");
  return `http://127.0.0.1:${bound.port}`;
}

export async function close(server: http.Server) {
  server.closeAllConnections();
  if (server.listening) await new Promise<void>(resolve => server.close(() => resolve()));
}
