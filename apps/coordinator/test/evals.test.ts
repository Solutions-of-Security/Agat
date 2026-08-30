import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { AgatStore } from "../src/database.js";

const stores: AgatStore[] = [];

afterEach(() => {
  while (stores.length) stores.pop()?.close();
});

function createStore(): AgatStore {
  const store = new AgatStore(":memory:", { seedDemo: false });
  stores.push(store);
  return store;
}

function addNode(store: AgatStore, name: string, models: string[]): string {
  return store.registerNode({
    enrollmentToken: "unused-at-store-layer",
    name,
    platform: "test",
    models,
    maxConcurrency: 1,
    agentRuntimes: ["single"],
  }).id;
}

describe("golden eval and prompt registry", () => {
  it("pins a prompt version across a batch and promotes prompt/model only after a passing gate", () => {
    const store = createStore();
    const agent = store.createAgent({
      name: "Golden agent",
      role: "Answers a fixed regression set",
      systemPrompt: "OLD PROMPT",
      model: "model-old",
    });
    const originalRun = store.createRun({
      name: "Before promotion",
      input: "Keep the old snapshot",
      agentIds: [String(agent.id)],
      approvalRequired: false,
    });
    const prompt = store.listPromptRegistry().find((candidate) => candidate.agentId === agent.id)!;
    const versioned = store.createPromptVersion(String(prompt.id), {
      content: "NEW GOLDEN PROMPT",
      changeNote: "Regression candidate",
    })!;
    assert.equal(versioned.activeVersion, 1);
    assert.equal((versioned.versions as Array<{ version: number }>)[0]?.version, 2);

    const dataset = store.createEvalDataset({
      name: "Golden answers",
      description: "Deterministic release gate",
      examples: [
        { name: "Alpha", input: "A", requiredTerms: ["approved"], forbiddenTerms: ["unsafe"] },
        { name: "Beta", input: "B", requiredTerms: ["approved"], forbiddenTerms: ["unsafe"] },
      ],
    });
    const experiment = store.createEvalExperiment({
      name: "Prompt v2 on model-new",
      datasetId: String(dataset.id),
      datasetVersion: 1,
      agentId: String(agent.id),
      promptId: String(prompt.id),
      promptVersion: 2,
      model: "model-new",
      minQualityScore: 100,
    });
    assert.throws(() => store.promotePrompt(String(prompt.id), {
      version: 2,
      model: "model-new",
      experimentId: String(experiment.id),
    }), /quality gate/iu);

    const oldNode = addNode(store, "old-node", ["model-old"]);
    const oldLease = store.leaseNext(oldNode)!;
    assert.equal(oldLease.run.id, originalRun.id);
    assert.equal(oldLease.agent.systemPrompt, "OLD PROMPT");
    assert.equal(oldLease.agent.model, "model-old");
    store.completeLease(oldNode, oldLease.leaseId, "old result");

    const candidateNode = addNode(store, "candidate-node", ["model-new"]);
    for (let index = 0; index < 2; index += 1) {
      const lease = store.leaseNext(candidateNode)!;
      assert.equal(lease.agent.systemPrompt, "NEW GOLDEN PROMPT");
      assert.equal(lease.agent.model, "model-new");
      assert.equal(lease.agent.id, agent.id);
      store.completeLease(candidateNode, lease.leaseId, "Approved response");
    }

    const completed = store.getEvalExperiment(String(experiment.id))!;
    assert.equal(completed.status, "completed");
    assert.equal(completed.qualityScore, 100);
    assert.deepEqual(completed.gates, {
      completion: "pass",
      quality: "pass",
      knowledge: "pass",
      overall: "pass",
    });
    const promoted = store.promotePrompt(String(prompt.id), {
      version: 2,
      model: "model-new",
      experimentId: String(experiment.id),
    })!;
    assert.equal(promoted.activeVersion, 2);
    assert.equal(promoted.activeModel, "model-new");
    const effectiveAgent = store.listAgents().find((candidate) => candidate.id === agent.id)!;
    assert.equal(effectiveAgent.systemPrompt, "NEW GOLDEN PROMPT");
    assert.equal(effectiveAgent.model, "model-new");
  });

  it("keeps human rubric reviews append-only and uses the latest human score", () => {
    const store = createStore();
    const agent = store.createAgent({
      name: "Rubric agent",
      role: "Gets reviewed by a person",
      systemPrompt: "ANSWER",
      model: "rubric-model",
    });
    const prompt = store.listPromptRegistry().find((candidate) => candidate.agentId === agent.id)!;
    const dataset = store.createEvalDataset({
      name: "Human rubric",
      rubric: [
        { id: "correctness", label: "Корректность", description: "Фактическая точность", weight: 2 },
        { id: "clarity", label: "Ясность", weight: 1 },
      ],
      examples: [{ input: "Explain", referenceOutput: "Reference answer" }],
    });
    const experiment = store.createEvalExperiment({
      name: "Human review required",
      datasetId: String(dataset.id),
      agentId: String(agent.id),
      promptId: String(prompt.id),
      promptVersion: 1,
      model: "rubric-model",
      minQualityScore: 80,
    });
    const node = addNode(store, "rubric-node", ["rubric-model"]);
    const lease = store.leaseNext(node)!;
    store.completeLease(node, lease.leaseId, "Candidate answer");
    const scoring = store.getEvalExperiment(String(experiment.id))!;
    assert.equal(scoring.status, "scoring");
    assert.equal((scoring.gates as { quality: string }).quality, "pending");
    const item = (scoring.items as Array<{ id: string }>)[0]!;

    const realDateNow = Date.now;
    const reviewTime = realDateNow();
    try {
      Date.now = () => reviewTime;
      const passed = store.reviewEvalItem(item.id, {
        scores: { correctness: 90, clarity: 80 },
        rationale: "Проверено по reference",
      }, "default", "human@example.test")!;
      assert.equal(passed.qualityScore, 86.67);
      assert.equal((passed.gates as { overall: string }).overall, "pass");

      const failed = store.reviewEvalItem(item.id, {
        scores: { correctness: 60, clarity: 70 },
        rationale: "Повторная проверка нашла ошибку",
      }, "default", "reviewer-2@example.test")!;
      assert.equal(failed.qualityScore, 63.33);
      assert.equal((failed.gates as { quality: string }).quality, "fail");
      const detailedItem = (failed.items as Array<{ reviews: Array<{ createdAt: string }>; qualitySource: string }>)[0]!;
      assert.equal(detailedItem.reviews.length, 2);
      assert.ok(detailedItem.reviews[0]!.createdAt > detailedItem.reviews[1]!.createdAt);
      assert.equal(detailedItem.qualitySource, "human");
    } finally {
      Date.now = realDateNow;
    }
  });

  it("runs an optional local model judge and audits its parsed score", () => {
    const store = createStore();
    const agent = store.createAgent({
      name: "Judge candidate",
      role: "Produces an answer for a judge",
      systemPrompt: "ANSWER CANDIDATE",
      model: "candidate-model",
    });
    const prompt = store.listPromptRegistry().find((candidate) => candidate.agentId === agent.id)!;
    const dataset = store.createEvalDataset({
      name: "Judge rubric",
      rubric: [{ id: "correctness", label: "Correctness", weight: 1 }],
      examples: [{ input: "Question", referenceOutput: "Expected" }],
    });
    const experiment = store.createEvalExperiment({
      name: "Judge experiment",
      datasetId: String(dataset.id),
      agentId: String(agent.id),
      promptId: String(prompt.id),
      promptVersion: 1,
      model: "candidate-model",
      minQualityScore: 80,
    });
    const candidateNode = addNode(store, "candidate", ["candidate-model"]);
    const candidateLease = store.leaseNext(candidateNode)!;
    store.completeLease(candidateNode, candidateLease.leaseId, "Candidate output");

    store.judgeEvalExperiment(String(experiment.id), { model: "judge-model" }, "default", "operator");
    const judgeNode = addNode(store, "judge", ["judge-model"]);
    const judgeLease = store.leaseNext(judgeNode)!;
    assert.equal(judgeLease.agent.id, "__agat_eval_judge__");
    assert.equal(judgeLease.agent.model, "judge-model");
    assert.match(judgeLease.run.input, /"candidateOutput":"Candidate output"/u);
    store.completeLease(judgeNode, judgeLease.leaseId, JSON.stringify({
      scores: { correctness: 92 },
      overallScore: 1,
      rationale: "Matches the reference intent",
    }));

    const judged = store.getEvalExperiment(String(experiment.id))!;
    assert.equal(judged.status, "completed");
    assert.equal(judged.qualityScore, 92);
    assert.equal((judged.gates as { overall: string }).overall, "pass");
    const item = (judged.items as Array<{
      qualitySource: string;
      judgeScore: number;
      reviews: Array<{ kind: string; model: string; rawOutputSha256: string }>;
    }>)[0]!;
    assert.equal(item.qualitySource, "model_judge");
    assert.equal(item.judgeScore, 92);
    assert.equal(item.reviews[0]?.kind, "model_judge");
    assert.equal(item.reviews[0]?.model, "judge-model");
    assert.equal(item.reviews[0]?.rawOutputSha256.length, 64);
  });

  it("detects knowledge drift instead of silently running a stale golden dataset", () => {
    const store = createStore();
    const collection = store.createKnowledgeCollection({
      name: "Golden knowledge",
      embeddingModel: "embed-model",
    });
    const dataset = store.createEvalDataset({
      name: "RAG regression",
      examples: [{ input: "Question", knowledgeCollectionIds: [String(collection.id)], requiredTerms: ["answer"] }],
    });
    store.ingestKnowledgeDocument(String(collection.id), {
      name: "Changed after snapshot",
      content: "New knowledge content",
    });
    const prompt = store.listPromptRegistry().find((candidate) => candidate.agentId === "collector")!;
    assert.throws(() => store.createEvalExperiment({
      name: "Stale RAG experiment",
      datasetId: String(dataset.id),
      agentId: "collector",
      promptId: String(prompt.id),
      promptVersion: 1,
      model: null,
    }), /Knowledge snapshot.*изменился/iu);
  });

  it("keeps prompt registries, datasets and experiment details project-scoped", () => {
    const store = createStore();
    store.createProject({ id: "research", name: "Research" });
    const researchAgent = store.createAgent({
      name: "Research evaluator",
      role: "Research only",
      systemPrompt: "RESEARCH PROMPT",
      model: null,
    }, "research");
    const prompt = store.listPromptRegistry("research").find((candidate) => candidate.agentId === researchAgent.id)!;
    const dataset = store.createEvalDataset({
      name: "Private research set",
      examples: [{ input: "Private question", requiredTerms: ["private"] }],
    }, "research");
    const experiment = store.createEvalExperiment({
      name: "Research gate",
      datasetId: String(dataset.id),
      agentId: String(researchAgent.id),
      promptId: String(prompt.id),
      promptVersion: 1,
      model: null,
    }, "research");

    assert.equal(store.getEvalSnapshot().datasets.some((candidate) => candidate.id === dataset.id), false);
    assert.equal(store.listPromptRegistry().some((candidate) => candidate.id === prompt.id), false);
    assert.equal(store.getEvalExperiment(String(experiment.id)), null);
    assert.equal(store.getEvalExperiment(String(experiment.id), "research")?.name, "Research gate");
  });
});
