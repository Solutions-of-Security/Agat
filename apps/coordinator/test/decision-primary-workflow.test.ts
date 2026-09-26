import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import { createInterface } from "node:readline";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { decisionProfile, origin, validatePlan, workflowPhase, type Fixture } from "../../../scripts/lib/decision-primary-workflow.js";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const fixture = JSON.parse(fs.readFileSync(new URL("../../../docs/qualification/local-decisions/performance/workflow.fixture.json", import.meta.url), "utf8")) as Fixture;
test("workflow workload limits and explicit loopback URLs are enforced before workers", () => {
  for (const value of ["https://127.0.0.1:1234", "http://localhost:1234", "http://user@127.0.0.1:1234", "http://127.0.0.1:1234/path"]) {
    assert.throws(() => origin(value));
  }
  for (const phase of [{ concurrency: 3, runs: 1 }, { concurrency: 1, runs: 5 }, { concurrency: 0, runs: 1 }]) {
    assert.throws(() => validatePlan(fixture, [{ id: "probe", shadow: true, ...phase }]));
  }
});

test("real worker completes three stages with primary outputs preserved through observed shadow HTTP", { timeout: 45_000 }, async () => {
  const backend = spawn("python3", ["-u", "-c", `
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.server import make_server
class Backend:
    identity = {"repository":"fixture", "revision":"fixture", "artifactSha256":"1"*64,
                "tokenizerSha256":"2"*64,"implementationSha256":"3"*64,
                "promptVersion":"fixture","backend":"fixture","quantization":"none"}
    def score(self, request): return Scores([8.0,0.0],20)
with make_server(DecisionEngine(Backend()),0) as server:
    print(server.server_port,flush=True)
    server.serve_forever()
`], { cwd: root, stdio: ["ignore", "pipe", "pipe"] });
  const lines = createInterface({ input: backend.stdout! });let calls = 0, incomplete = false;
  const primary = http.createServer((req, res) => {
    assert.equal(req.url, "/api/chat");let raw = "";
    req.on("data", chunk => { raw += chunk; });req.on("end", () => {
      const body = JSON.parse(raw);assert.equal(body.think, false);assert.equal(body.options.num_predict, 384);
      assert.ok(Array.isArray(body.messages));const callNumber = ++calls;
      res.writeHead(200, { "content-type": "application/json" });
      setTimeout(() => res.end(JSON.stringify({ model: "fixture-primary", done: true, done_reason: incomplete ? "length" : "stop", total_duration: 1000000,
        message: { content: `Учебный primary-ответ ${callNumber}; июль 100, август 120.` }, prompt_eval_count: 20, eval_count: 12 })), 100);
    });
  });
  try {
    const port = await new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Fixture startup timeout")), 5000);
      lines.once("line", line => { clearTimeout(timer);resolve(line); });
      backend.once("error", error => { clearTimeout(timer);reject(error); });
    });
    await new Promise<void>(resolve => primary.listen(0, "127.0.0.1", resolve));
    const address = primary.address();assert.ok(address && typeof address === "object");
    const decisionUrl = `http://127.0.0.1:${port}`;
    const profile = await decisionProfile(decisionUrl);
    for (const shadow of [false, true]) {
      const result = await workflowPhase({ phase: { id: shadow ? "shadow" : "control", shadow, runs: 2, concurrency: 2 },
        fixture, model: "fixture-primary", primaryUrl: `http://127.0.0.1:${address.port}`, decisionUrl,
        profileJson: profile.profileJson, timeoutMs: 15_000 });
      assert.equal(result.workflows.length, 2);assert.equal(result.primaryCalls.length, 6);
      assert.equal(result.decisionCalls.length, shadow ? 6 : 0);assert.ok(result.checks.allPrimaryOutputsPreserved);
      assert.equal(result.maxPrimaryRequestsInFlight, 2);assert.ok(result.checks.configuredConcurrencyObserved);
      if (shadow) assert.ok(result.workflows.every(run => run.stages.every((stage: Record<string, any>) => stage.observation?.fallback === "primary")));
    }
    assert.equal(calls, 12);
    incomplete = true;
    const failed = await workflowPhase({ phase: { id: "truncated", shadow: true, runs: 1, concurrency: 1 },
      fixture, model: "fixture-primary", primaryUrl: `http://127.0.0.1:${address.port}`, decisionUrl,
      profileJson: profile.profileJson, timeoutMs: 15_000 });
    assert.equal(failed.status, "incomplete");assert.equal(failed.failure, "primary_adapter_failed");
    assert.equal(failed.primaryCalls.length, 1);assert.equal(failed.primaryCalls[0]!.doneReason, "length");
    assert.equal(failed.decisionCalls.length, 0);assert.equal(calls, 13);
  } finally {
    lines.close();backend.kill("SIGTERM");
    if (backend.exitCode === null && backend.signalCode === null) await new Promise(resolve => backend.once("exit", resolve));
    primary.closeAllConnections();if (primary.listening) await new Promise<void>(resolve => primary.close(() => resolve()));
  }
});
