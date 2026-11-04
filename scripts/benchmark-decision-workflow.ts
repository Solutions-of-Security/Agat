import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { decisionProfile, digest, generation, json, origin, primaryIdentity, validatePlan, workflowPhase,
  type Fixture, type Phase } from "./lib/decision-primary-workflow.js";
import { embeddingIdentity, forwardEmbedding } from "./lib/decision-rag.js";
import { createDecisionShadowLease, normalizeDecisionShadowConfig, validateDecisionShadowResult } from "../apps/coordinator/src/local-decisions.js";

const root = fileURLToPath(new URL("../", import.meta.url));
const { values } = parseArgs({ options: {
  "decision-url": { type: "string" }, "primary-url": { type: "string" }, "primary-model": { type: "string", default: "qwen3:8b" },
  "expected-primary-digest": { type: "string" }, output: { type: "string" }, "plan-output": { type: "string" },
  "expected-embedding-digest": { type: "string" },
  "embedding-transport": { type: "string", default: "isolated" },
  design: { type: "string", default: "sequence" },
  fixture: { type: "string", default: "docs/qualification/local-decisions/performance/workflow.fixture.json" },
} });
for (const key of ["decision-url", "primary-url", "expected-primary-digest", "output", "plan-output"] as const) {
  if (!values[key]) throw new Error(`Required --${key}`);
}
function newOutput(value: string) {
  const result = path.resolve(root, value), docs = path.join(root, "docs");
  if (!result.startsWith(docs + path.sep) || fs.existsSync(result)) throw new Error("Use a new output path under docs");
  fs.mkdirSync(path.dirname(result), { recursive: true });
  if (!fs.realpathSync(path.dirname(result)).startsWith(fs.realpathSync(docs) + path.sep)) throw new Error("Output outside docs");
  return result;
}
const output = newOutput(values.output!), planOutput = newOutput(values["plan-output"]!);
if (output === planOutput) throw new Error("Plan and result paths must differ");
const primaryUrl = origin(values["primary-url"]!), decisionUrl = origin(values["decision-url"]!);
const fixture = JSON.parse(fs.readFileSync(path.resolve(root, values.fixture!), "utf8")) as Fixture;
if (!["sequence", "paired-rag"].includes(values.design!)) throw new Error("Unsupported experiment design");
const embeddingTransport = values["embedding-transport"];
if (embeddingTransport !== "isolated" && embeddingTransport !== "session") throw new Error("Unsupported embedding transport");
const paired = values.design === "paired-rag";
if (paired && !fixture.rag) throw new Error("The paired design requires RAG");
const metadataId = paired ? "paired_rag" : undefined;
const phases: Phase[] = paired ? [
  { id: "control_c2_first", concurrency: 2, runs: 3, shadow: false },
  { id: "shadow_c2_first", concurrency: 2, runs: 3, shadow: true },
  { id: "shadow_c2_second", concurrency: 2, runs: 3, shadow: true },
  { id: "control_c2_last", concurrency: 2, runs: 3, shadow: false },
] : [
  { id: "control_c1", concurrency: 1, runs: 2, shadow: false },
  { id: "shadow_c1", concurrency: 1, runs: 2, shadow: true },
  { id: "control_c2", concurrency: 2, runs: 4, shadow: false },
  { id: "shadow_c2", concurrency: 2, runs: 4, shadow: true },
];
validatePlan(fixture, phases);
if (fixture.rag && !values["expected-embedding-digest"]) throw new Error("RAG requires --expected-embedding-digest");
if (!fixture.rag && values["expected-embedding-digest"]) throw new Error("Embedding digest requires a RAG fixture");
const model = values["primary-model"]!, expected = values["expected-primary-digest"]!;
const [primary, profile] = await Promise.all([primaryIdentity(primaryUrl, model, expected), decisionProfile(decisionUrl)]);
const embedding = fixture.rag ? await embeddingIdentity(primaryUrl, fixture.rag.embeddingModel, values["expected-embedding-digest"]!, json) : null;
const files = ["scripts/benchmark-decision-workflow.ts", "scripts/lib/decision-primary-workflow.ts", "scripts/lib/decision-shadow-proxy.ts", "scripts/lib/decision-rag.ts", "workers/agat_worker.py",
  "workers/local_decisions.py", "workers/embedding_http.py", "workers/embedding_transport.py", "workers/telemetry.py", "workers/web_tools.py",
  "apps/coordinator/src/local-decisions.ts", "apps/coordinator/src/database.ts",
  "apps/coordinator/src/server.ts", "apps/coordinator/src/process-engine.ts", "apps/coordinator/src/types.ts"];
const plan = { schemaVersion: "agat.decision.workflow-plan.v1", createdAt: new Date().toISOString(), qualification: "not_assessed",
  routingEnabled: false, fixture, phases, primary, embeddingTransport, ...(embedding ? { embedding } : {}), decision: profile, endpoints: { primaryUrl, decisionUrl },
  timeBudgetMs: 600_000, warmupCalls: 1, primaryAdapter: "OpenAI-compatible worker request to native Ollama chat; no fixture outputs",
  executionOrder: "worker runs primary first, then shadow; overlap is possible across concurrent leases",
  coordinatorScheduler: "sequential with globalMaxConcurrency explicitly equal to phase.concurrency",
  ...(paired ? { design: { id: "paired-rag", order: "ABBA", modelVisibleMetadataId: metadataId,
    warmupEmbeddingItems: 2, warmupDecisionCalls: 1,
    promptChecks: "First primary message SHA must match across blocks; complete prompt/output multisets are recorded separately.",
    limitation: "Four preordered blocks, not randomized independent replications or a causal SLO estimate." } } : {}),
  files: Object.fromEntries(files.map(file => [file, digest(fs.readFileSync(path.join(root, file)))])) };
const frozen = JSON.stringify(plan, null, 2) + "\n";fs.writeFileSync(planOutput, frozen, { flag: "wx" });
const started = performance.now(), batches = [];let failure: string | null = null, warmup: Record<string, unknown> | null = null;
let comparison: Record<string, unknown> | null = null;
let step = "warmup";
try {
  const warmed = await json(`${primaryUrl}/api/chat`, { model, messages: [{ role: "user", content: "Ответь одним словом: готов." }],
    stream: false, keep_alive: "5m", ...generation }, 45_000);
  if (!warmed.done || warmed.done_reason !== "stop" || warmed.model !== model || !warmed.message?.content?.trim()) throw new Error("Warmup failed");
  warmup = { wallMs: Number((performance.now() - started).toFixed(3)), inputTokens: warmed.prompt_eval_count,
    outputTokens: warmed.eval_count, outputSha256: digest(warmed.message.content) };
  if (paired) {
    const vectors = await forwardEmbedding({ model: fixture.rag!.embeddingModel, input: fixture.rag!.sources.map(source => source.content) },
      fixture.rag!.embeddingModel, primaryUrl, json, new AbortController().signal);
    const config = normalizeDecisionShadowConfig({ mode: "shadow", profileJson: profile.profileJson, timeoutMs: 10_000, ...fixture.shadow });
    const lease = createDecisionShadowLease(config, "paired-rag-warmup", fixture.input);
    const answer = await json(`${decisionUrl}/v1/decisions`, lease.request, 10_000);
    const observation = validateDecisionShadowResult({ result: answer }, lease, profile.profileJson);
    if (!["ok", "abstain"].includes(observation.status)) throw new Error("Decision warmup failed");
    Object.assign(warmup, { embedding: vectors.evidence, decision: answer, totalWarmupMs: Number((performance.now() - started).toFixed(3)) });
  }
  for (const phase of phases) {
    step = `${phase.id}.preflight`;
    const remaining = 600_000 - (performance.now() - started);
    if (remaining < 1000) throw new Error("Experiment time budget exceeded");
    const current = await decisionProfile(decisionUrl);
    if (current.profileJson !== profile.profileJson) throw new Error("Decision profile changed");
    const identity = await primaryIdentity(primaryUrl, model, expected);
    if (JSON.stringify(identity) !== JSON.stringify(primary)) throw new Error("Primary identity changed");
    if (fixture.rag && JSON.stringify(await embeddingIdentity(primaryUrl, fixture.rag.embeddingModel, values["expected-embedding-digest"]!, json))
      !== JSON.stringify(embedding)) throw new Error("Embedding identity changed");
    console.log(`${phase.id}: ${phase.runs} workflows, 3 stages, worker concurrency ${phase.concurrency}`);
    step = `${phase.id}.workflow`;
    batches.push(await workflowPhase({ phase, fixture, model, primaryUrl, decisionUrl, profileJson: profile.profileJson,
      timeoutMs: Math.min(180_000, remaining), metadataId, embeddingTransport }));
    if (batches.at(-1)!.status !== "observed") throw new Error("Incomplete workflow batch");
    console.log(`${phase.id}: completed; primary calls=${batches.at(-1)!.primaryCalls.length}; shadow=${batches.at(-1)!.decisionCalls.length}`);
  }
  step = "final_profiles";
  if ((await decisionProfile(decisionUrl)).profileJson !== profile.profileJson) throw new Error("Final decision profile changed");
  if (JSON.stringify(await primaryIdentity(primaryUrl, model, expected)) !== JSON.stringify(primary)) throw new Error("Final primary identity changed");
  if (fixture.rag && JSON.stringify(await embeddingIdentity(primaryUrl, fixture.rag.embeddingModel, values["expected-embedding-digest"]!, json))
    !== JSON.stringify(embedding)) throw new Error("Final embedding identity changed");
  if (paired) {
    step = "prompt_comparison";
    const counts = (values: string[]) => Object.entries(values.reduce<Record<string, number>>((all, value) => {
      all[value] = (all[value] ?? 0) + 1;return all;
    }, {})).sort(([a], [b]) => a.localeCompare(b));
    const prompts = batches.map(batch => counts(batch.primaryCalls.map(call => call.messagesSha256)));
    const outputs = batches.map(batch => counts(batch.primaryCalls.map(call => call.outputSha256)));
    const first = batches.map(batch => batch.primaryCalls[0]!.messagesSha256);
    comparison = { firstPrimaryMessagesSha256: first, firstPrimaryMessagesIdentical: new Set(first).size === 1,
      promptMultisets: prompts, outputMultisets: outputs,
      allPrimaryPromptMultisetsIdentical: new Set(prompts.map(values => JSON.stringify(values))).size === 1,
      allPrimaryOutputMultisetsIdentical: new Set(outputs.map(values => JSON.stringify(values))).size === 1 };
    if (!comparison.firstPrimaryMessagesIdentical) throw new Error("First model prompts differ across blocks");
  }
} catch (error) {
  // Errors are intentionally bounded and do not include HTTP/model response bodies.
  failure = error instanceof Error && error.name === "AssertionError" ? "experiment_assertion_failed" : "experiment_failed";
  console.error(failure);
}
const result = { schemaVersion: "agat.decision.workflow-result.v1", createdAt: new Date().toISOString(),
  status: failure ? "incomplete" : "observed", qualification: "not_assessed", routingEnabled: false,
  planSha256: digest(frozen), elapsedMs: Number((performance.now() - started).toFixed(3)), warmup, batches, failure,
  ...(paired ? { comparison } : {}),
  failureStep: failure ? step : null,
  limitations: ["One authored task fixture reused in twelve workflows; no independent accuracy or human acceptance claim.",
    "One pass with order effects; phase latency differences are descriptive, not causal overhead estimates.",
    fixture.rag ? "Two authored local RAG documents, real indexing/query embeddings and recorded retrieval provenance; Temporal and external business systems are not exercised."
      : "Local SQLite coordinator, three single-agent stages; RAG, Temporal and external business systems are not exercised.",
    "Primary generation uses an explicit native Ollama adapter with frozen limits; production defaults may differ."] };
fs.writeFileSync(output, JSON.stringify({ ...result, sha256: digest(JSON.stringify(result)) }, null, 2) + "\n", { flag: "wx" });
console.log(`${result.status}: ${output}`);
if (failure) process.exitCode = 1;
