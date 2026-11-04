import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import { createInterface } from "node:readline";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { decisionProfile, digest, workflowPhase, type Fixture } from "../../../scripts/lib/decision-primary-workflow.js";
import { embeddingIdentity, forwardEmbedding, validateRag, verifyRetrieval } from "../../../scripts/lib/decision-rag.js";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const fixture = JSON.parse(fs.readFileSync(new URL("../../../docs/qualification/local-decisions/performance/rag-workflow.fixture.json", import.meta.url), "utf8")) as Fixture;

test("citation aliases are bound to earlier/current stages of the same trace and reject changed provenance", () => {
  const sources = ["one", "two"].map(id => ({ id, sourceUri: `agat://decision-rag/${id}`, documentSha256: digest(id),
    chunkId: id, chunkSha256: digest(id), dimensions: 3 }));
  const ingestion = { collectionId: "collection", embeddingModel: "embed", dimensions: 3, sources };
  const stages = [0, 1, 2].map(position => ({ id: `stage-${position}`, position, output: "[K1] [K2] [K5]" }));
  const events = stages.map(stage => ({ type: "knowledge.retrieved", stageId: stage.id, data: {
    queries: [{ embeddingModel: "embed", collectionIds: ["collection"], vectorSha256: "a".repeat(64), dimensions: 3 }],
    hits: sources.map((source, index) => ({ marker: `K${stage.position * 2 + index + 1}`, content: source.id, score: .9,
      provenance: { collectionId: "collection", sourceUri: source.sourceUri, documentSha256: source.documentSha256,
        chunkId: source.chunkId, chunkSha256: source.chunkSha256 } })) } }));
  const trace = { run: { stages }, events };
  const evidence = verifyRetrieval(trace, stages[1], ingestion);
  assert.deepEqual(evidence.unknownMarkers, ["K5"]);assert.ok(evidence.bothSourcesCited);
  assert.deepEqual(evidence.knownCitations.map(hit => hit.marker), ["K1", "K2", "K3", "K4"]);
  const repeated = structuredClone(trace);
  repeated.events.push(structuredClone(repeated.events[1]!));
  assert.throws(() => verifyRetrieval(repeated, stages[1], ingestion), /retrieval event count/);
  assert.equal(verifyRetrieval(repeated, stages[1], ingestion, 2).queries.length, 2);
  for (const attempt of [1, 3]) {
    const wrongQuery = structuredClone(repeated);
    wrongQuery.events[attempt]!.data.queries[0]!.dimensions = 4;
    assert.throws(() => verifyRetrieval(wrongQuery, stages[1], ingestion, 2));
    const wrongSource = structuredClone(repeated);
    wrongSource.events[attempt]!.data.hits[0]!.content = "corrupt";
    assert.throws(() => verifyRetrieval(wrongSource, stages[1], ingestion, 2), /provenance mismatch/);
  }
  for (const count of [0, -1, 1.5, 3]) assert.throws(() => verifyRetrieval(repeated, stages[1], ingestion, count));
  events[0]!.data.hits[0]!.provenance.documentSha256 = "b".repeat(64);
  assert.throws(() => verifyRetrieval(trace, stages[1], ingestion), /provenance mismatch/);
});

test("RAG rejects cloud identities, source truncation and invalid vectors before claiming retrieval", async () => {
  validateRag(fixture.rag!);
  const oversized = structuredClone(fixture.rag!);oversized.sources[0]!.content = "x".repeat(4001);
  assert.throws(() => validateRag(oversized));
  let calls = 0;
  const transport = async (url: string, body?: any) => {
    calls++;
    if (url.endsWith("/api/tags")) return { models: [{ name: "embed", digest: "a".repeat(64), remote_host: "cloud" }] };
    if (url.endsWith("/api/show")) return { capabilities: ["embedding"] };
    if (url.endsWith("/api/version")) return { version: "fixture" };
    assert.equal(body.truncate, false);assert.equal(body.options.num_ctx, 2048);
    return { model: "embed", embeddings: [[0, 0]], prompt_eval_count: 2, total_duration: 100, load_duration: 0 };
  };
  await assert.rejects(embeddingIdentity("http://127.0.0.1:1234", "embed", "a".repeat(64), transport));
  const before = calls;
  await assert.rejects(embeddingIdentity("https://example.com", "embed", "a".repeat(64), transport));
  assert.equal(calls, before);
  await assert.rejects(forwardEmbedding({ model: "embed", input: ["query"] }, "embed", "http://127.0.0.1:1234",
    transport, new AbortController().signal), /Invalid or mixed embedding/);
  await assert.rejects(forwardEmbedding({ model: "embed", input: ["query"], truncate: true }, "embed", "http://127.0.0.1:1234",
    transport, new AbortController().signal));
});

test("real workers index both documents, retrieve every stage and preserve primary outputs with shadow", { timeout: 45_000 }, async () => {
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
  const lines = createInterface({ input: backend.stdout! });let chatCalls = 0, embedCalls = 0, invalidVectors = false;
  const inheritedTransport = process.env.AGAT_EMBEDDING_TRANSPORT;
  // Explicit experiment modes must override an inherited, unusable default.
  process.env.AGAT_EMBEDDING_TRANSPORT = "invalid-inherited-transport";
  const serverErrors: unknown[] = [];
  let nextEmbeddingResponse = 0, primaryBarrierReleased = false;
  const firstPrimaryResponses: Array<() => void> = [];
  const primary = http.createServer((req, res) => {
    let raw = "";req.on("data", chunk => { raw += chunk; });req.on("end", () => {
      try {
      const body = JSON.parse(raw);
      res.setHeader("content-type", "application/json");
      if (req.url === "/api/embed") {
        embedCalls++;assert.equal(body.model, fixture.rag!.embeddingModel);assert.equal(body.truncate, false);
        assert.ok(body.input.length >= 1 && body.input.length <= 2);
        const embeddings = body.input.map((text: string) => invalidVectors ? [0, 0, 0] : [1, text.length % 7 + 1, 1]);
        // A model server may serialize embedding batches. Pace valid replies so
        // primary concurrency cannot depend on two subprocesses starting together.
        nextEmbeddingResponse = Math.max(performance.now(), nextEmbeddingResponse) + 250;
        setTimeout(() => res.end(JSON.stringify({ model: body.model, embeddings, prompt_eval_count: 20,
          total_duration: 1_000_000, load_duration: 1000 })), nextEmbeddingResponse - performance.now());
      } else {
        assert.equal(req.url, "/api/chat");chatCalls++;
        const prompt = body.messages.map((message: any) => message.content).join("\n");
        assert.ok(prompt.includes("Primary workflow fixed_fixture"));assert.ok(!prompt.includes("rag_shadow"));
        for (const source of fixture.rag!.sources) assert.ok(prompt.includes(source.content));
        const markers = [...new Set([...prompt.matchAll(/\[(K[0-9]+)\]/g)].map(match => match[0]))];
        assert.ok(markers.length >= 2);
        const complete = () => res.end(JSON.stringify({ model: "fixture-primary", done: true, done_reason: "stop", total_duration: 1_000_000,
          message: { content: `Учебные данные июля и августа ${markers.join(" ")}; причины неизвестны.` },
          prompt_eval_count: 100, eval_count: 20 }));
        if (chatCalls <= 2) {
          // Hold the first request until the second real worker slot reaches
          // primary HTTP. This proves overlap despite serialized embeddings.
          firstPrimaryResponses.push(complete);
          if (firstPrimaryResponses.length === 2) {
            primaryBarrierReleased = true;
            const replies = firstPrimaryResponses.splice(0);
            setTimeout(() => replies.forEach(reply => reply()), 100);
          }
        } else setTimeout(complete, 100);
      }
      } catch (error) { serverErrors.push(error);res.writeHead(502).end(); }
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
    const primaryUrl = `http://127.0.0.1:${address.port}`, decisionUrl = `http://127.0.0.1:${port}`;
    const profile = await decisionProfile(decisionUrl);
    const result = await workflowPhase({ phase: { id: "rag_shadow", shadow: true, runs: 2, concurrency: 2 },
      fixture, model: "fixture-primary", primaryUrl, decisionUrl, profileJson: profile.profileJson, timeoutMs: 15_000,
      metadataId: "fixed_fixture", embeddingTransport: "isolated" });
    assert.equal(result.status, "observed", JSON.stringify({ failure: result.failure,
      maxPrimaryRequestsInFlight: result.maxPrimaryRequestsInFlight, primaryCalls: result.primaryCalls.length,
      decisionCalls: result.decisionCalls.length, embeddingCalls: result.embeddingCalls?.length }));
    assert.deepEqual(serverErrors, []);
    assert.equal(primaryBarrierReleased, true);assert.equal(firstPrimaryResponses.length, 0);
    assert.equal(result.workflows.length, 2);assert.equal(chatCalls, 6);assert.equal(embedCalls, 8);
    assert.equal(result.embeddingCalls!.length, 8);assert.equal(result.rag!.sources.length, 2);
    assert.equal(result.maxPrimaryRequestsInFlight, 2);
    for (const run of result.workflows) for (const stage of run.stages) {
      assert.ok(stage.retrieval.bothSourcesCited);assert.equal(stage.retrieval.hits.length, 2);
      assert.deepEqual(stage.retrieval.unknownMarkers, []);assert.equal(stage.observation.fallback, "primary");
    }
    invalidVectors = true;
    const failed = await workflowPhase({ phase: { id: "invalid_index", shadow: true, runs: 1, concurrency: 1 },
      fixture, model: "fixture-primary", primaryUrl, decisionUrl, profileJson: profile.profileJson, timeoutMs: 10_000,
      embeddingTransport: "session" });
    assert.equal(failed.status, "incomplete");assert.equal(failed.failure, "embedding_adapter_failed");
    assert.equal(failed.primaryCalls.length, 0);assert.equal(failed.decisionCalls.length, 0);assert.equal(chatCalls, 6);
  } finally {
    if (inheritedTransport === undefined) delete process.env.AGAT_EMBEDDING_TRANSPORT;
    else process.env.AGAT_EMBEDDING_TRANSPORT = inheritedTransport;
    lines.close();backend.kill("SIGTERM");
    if (backend.exitCode === null && backend.signalCode === null) await new Promise(resolve => backend.once("exit", resolve));
    primary.closeAllConnections();if (primary.listening) await new Promise<void>(resolve => primary.close(() => resolve()));
  }
});
