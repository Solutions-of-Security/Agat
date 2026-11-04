import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import { digest, generation, json, origin, primaryIdentity, validatePlan, workflowPhase,
  type Fixture } from "./lib/decision-primary-workflow.js";
import { embeddingIdentity, forwardEmbedding } from "./lib/decision-rag.js";

const { values } = parseArgs({ options: { plan: { type: "string" }, "primary-url": { type: "string" } } });
assert.ok(values.plan && values["primary-url"]);
const directory = path.dirname(path.resolve(values.plan)), frozen = fs.readFileSync(values.plan);
const plan = JSON.parse(frozen.toString()), primaryUrl = origin(values["primary-url"]!);
assert.equal(plan.schema, "agat.embedding.rag-plan.v1");
assert.ok(plan.ownedHttpProbe === undefined || plan.ownedHttpProbe === "agat.worker.owned-http.v1");
process.env.AGAT_HTTP_HELPER_PROBE = plan.ownedHttpProbe ? "1" : "0";
assert.deepEqual(plan.blocks.map((block: any) => block.transport), ["isolated", "session", "session", "isolated"]);
const fixture = plan.fixture as Fixture;
validatePlan(fixture, plan.blocks.map((block: any) => block.phase));
assert.ok(fixture.rag);
const profileJson = fs.readFileSync(plan.profilePath, "utf8").trim();
assert.equal(digest(fs.readFileSync(plan.profilePath)), plan.sourceSha256[plan.profilePath]);
const output = path.join(directory, "workflow.json");
assert.ok(!fs.existsSync(output));
const started = performance.now(), blocks: any[] = [];
let failure: string | null = null, step = "identity", primary: any, embedding: any, warmup: any;
async function identities() {
  return {
    primary: await primaryIdentity(primaryUrl, "qwen3:8b", plan.models["qwen3:8b"]),
    embedding: await embeddingIdentity(primaryUrl, fixture.rag!.embeddingModel, plan.models[fixture.rag!.embeddingModel], json),
  };
}
try {
  ({ primary, embedding } = await identities());
  step = "warmup";
  const began = performance.now();
  const warmed = await json(`${primaryUrl}/api/chat`, { model: primary.name,
    messages: [{ role: "user", content: "Ответь одним словом: готов." }], stream: false, keep_alive: "5m", ...generation }, 45_000);
  assert.ok(warmed.done && warmed.done_reason === "stop" && warmed.model === primary.name && warmed.message?.content?.trim());
  warmup = { primaryWallMs: performance.now() - began, outputSha256: digest(warmed.message.content),
    inputTokens: warmed.prompt_eval_count, outputTokens: warmed.eval_count };
  const vectors = await forwardEmbedding({ model: embedding.name, input: fixture.rag!.sources.map(source => source.content) },
    embedding.name, primaryUrl, json, new AbortController().signal);
  Object.assign(warmup, { embedding: vectors.evidence, totalMs: performance.now() - began });
  for (const block of plan.blocks) {
    step = block.phase.id;
    assert.deepEqual(await identities(), { primary, embedding });
    const remaining = 600_000 - (performance.now() - started);
    assert.ok(remaining > 1000);
    process.env.AGAT_EMBEDDING_TRANSPORT = block.transport;
    process.env.AGAT_EMBEDDING_PROBE_OUTPUT = path.join(directory, `${step}.worker.json`);
    console.log(`block ${step}: ${block.transport}, concurrency=2, workflows=3`);
    const workflow = await workflowPhase({ phase: block.phase, fixture, model: primary.name, primaryUrl,
      decisionUrl: primaryUrl, profileJson, metadataId: plan.metadataId, timeoutMs: Math.min(180_000, remaining) });
    const probePath = process.env.AGAT_EMBEDDING_PROBE_OUTPUT;
    const probe = fs.existsSync(probePath) ? JSON.parse(fs.readFileSync(probePath, "utf8")) : null;
    blocks.push({ transport: block.transport, workflow, probeSha256: probe ? digest(fs.readFileSync(probePath)) : null });
    assert.equal(workflow.status, "observed");
    assert.equal(probe?.transport, block.transport);
    assert.equal(probe?.exitCode, 0);
    assert.equal(probe?.failure, null);
    if (plan.ownedHttpProbe) {
      assert.equal(probe?.ownedHttp?.schema, plan.ownedHttpProbe);
      assert.equal(probe.ownedHttp.activeCalls, 0);
      assert.deepEqual(probe.ownedHttp.errors, []);
    }
    console.log(`block ${step}: completed, primary=${workflow.primaryCalls.length}, embedding=${workflow.embeddingCalls!.length}`);
  }
  step = "final_identity";
  assert.deepEqual(await identities(), { primary, embedding });
  step = "first_prompt";
  assert.equal(new Set(blocks.map(block => block.workflow.primaryCalls[0].messagesSha256)).size, 1);
} catch (error) {
  failure = error instanceof Error ? error.name : "experiment_failed";
  console.error(`incomplete ${step}: ${failure}`);
}
const result = { schema: "agat.embedding.rag-workflow.v1", planSha256: digest(frozen), createdAt: new Date().toISOString(),
  status: failure ? "incomplete" : "observed", failure, failureStep: failure ? step : null,
  elapsedMs: performance.now() - started, primary, embedding, warmup, blocks,
  qualification: "not_assessed", routingEnabled: false };
fs.writeFileSync(output, JSON.stringify(result, null, 2) + "\n", { flag: "wx" });
console.log(`${result.status}: ${output}`);
if (failure) process.exitCode = 1;
