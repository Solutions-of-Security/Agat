import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import { createInterface } from "node:readline";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { decisionProfile, workflowPhase, type Fixture } from "../../../scripts/lib/decision-primary-workflow.js";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const fixture = JSON.parse(fs.readFileSync(new URL("../../../docs/qualification/local-decisions/performance/rag-workflow.fixture.json", import.meta.url), "utf8")) as Fixture;

test("real worker stops before primary/shadow on incompatible or unavailable query embeddings, then recovers with a valid index", { timeout: 60_000 }, async () => {
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
  const lines = createInterface({ input: backend.stdout! });
  let fault: "dimensions" | "unavailable" | "none" = "dimensions";
  let chatCalls = 0;const errors: unknown[] = [];
  const endpoint = http.createServer((req, res) => {
    let raw = "";req.on("data", chunk => { raw += chunk; });req.on("end", () => {
      try {
        const body = JSON.parse(raw);res.setHeader("content-type", "application/json");
        if (req.url === "/api/embed") {
          const isIndexing = body.input.every((text: string) => fixture.rag!.sources.some(source => source.content === text));
          if (!isIndexing && fault === "unavailable") { res.writeHead(503).end(JSON.stringify({ error: "fixture unavailable" }));return; }
          const size = !isIndexing && fault === "dimensions" ? 2 : 3;
          res.end(JSON.stringify({ model: body.model, embeddings: body.input.map(() => Array(size).fill(1)),
            prompt_eval_count: 20, total_duration: 1_000_000, load_duration: 1000 }));
        } else {
          assert.equal(req.url, "/api/chat");chatCalls++;
          const prompt = body.messages.map((message: any) => message.content).join("\n");
          for (const source of fixture.rag!.sources) assert.ok(prompt.includes(source.content));
          const markers = [...new Set([...prompt.matchAll(/\[(K[0-9]+)\]/g)].map(match => match[0]))];
          res.end(JSON.stringify({ model: "fixture-primary", done: true, done_reason: "stop", total_duration: 1_000_000,
            message: { content: `Учебные данные июля и августа ${markers.join(" ")}; причины неизвестны.` },
            prompt_eval_count: 100, eval_count: 20 }));
        }
      } catch (error) { errors.push(error);res.writeHead(502).end(); }
    });
  });
  try {
    const port = await new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Fixture startup timeout")), 5000);
      lines.once("line", line => { clearTimeout(timer);resolve(line); });
      backend.once("error", error => { clearTimeout(timer);reject(error); });
    });
    await new Promise<void>(resolve => endpoint.listen(0, "127.0.0.1", resolve));
    const address = endpoint.address();assert.ok(address && typeof address === "object");
    const decisionUrl = `http://127.0.0.1:${port}`, primaryUrl = `http://127.0.0.1:${address.port}`;
    const { profileJson } = await decisionProfile(decisionUrl);
    for (const mode of ["dimensions", "unavailable", "none"] as const) {
      fault = mode;
      const result = await workflowPhase({ phase: { id: `retrieval_${mode}`, shadow: true, runs: 1, concurrency: 1 },
        fixture, model: "fixture-primary", primaryUrl, decisionUrl, profileJson, timeoutMs: 15_000 });
      assert.ok(result.rag, "Both sources must be indexed before injecting query failure");
      if (mode === "none") {
        assert.equal(result.status, "observed");assert.equal(result.primaryCalls.length, 3);assert.equal(result.decisionCalls.length, 3);
        assert.ok(result.workflows[0]!.stages.every((stage: any) => stage.retrieval.bothSourcesCited));
      } else {
        assert.equal(result.status, "incomplete");assert.equal(result.primaryCalls.length, 0);
        assert.equal(result.decisionCalls.length, 0);assert.equal(chatCalls, 0);
        assert.ok(result.embeddingCalls!.length >= 3);
      }
    }
    assert.equal(chatCalls, 3);assert.deepEqual(errors, []);
  } finally {
    lines.close();backend.kill("SIGTERM");
    if (backend.exitCode === null && backend.signalCode === null) await new Promise(resolve => backend.once("exit", resolve));
    endpoint.closeAllConnections();if (endpoint.listening) await new Promise<void>(resolve => endpoint.close(() => resolve()));
  }
});
