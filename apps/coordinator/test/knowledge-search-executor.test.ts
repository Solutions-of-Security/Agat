import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { AgatStore } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import { KnowledgeSearchExecutor, KnowledgeSearchExecutorError } from "../src/knowledge-search-executor.js";
import type { KnowledgeSearchRequest } from "../src/types.js";

async function fixture(count = 1, timeoutMs = 30_000) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "agat-search-executor-"));
  const dbPath = path.join(directory, "state.sqlite");
  const options = { seedDemo: false, artifactsDir: path.join(directory, "artifacts"), coordinatorInstanceId: "executor-test" };
  const store = new AgatStore(dbPath, options);
  let executor: KnowledgeSearchExecutor | undefined;
  try {
    store.updateScheduler("parallel", 10);
    const nodes = Array.from({ length: count }, (_, i) => store.registerNode({ enrollmentToken: "unused", name: `executor-${i}`,
      platform: "test", models: ["test-model"], embeddingModels: ["embed"], maxConcurrency: 1 }));
    const collection = String(store.createKnowledgeCollection({ name: "Executor", embeddingModel: "embed" }).id);
    const document = store.ingestKnowledgeDocument(collection, { name: "Fact", content: "Checked source." });
    const embedding = store.leaseKnowledgeEmbedding(nodes[0]!.id)!; assert.ok(embedding);
    store.completeKnowledgeEmbedding(nodes[0]!.id, embedding.leaseId, embedding.chunks.map(chunk => ({ chunkId: chunk.id, embedding: [1, 0] })));
    const leases = nodes.map(node => {
      const run = store.createRun({ name: "Executor", input: "Query", agentIds: ["collector"], approvalRequired: false, knowledgeCollectionIds: [collection] });
      const lease = store.leaseNext(node.id)!; assert.equal(lease.run.id, run.id);
      return lease;
    });
    executor = await KnowledgeSearchExecutor.create(dbPath, options, { maxPending: 4, timeoutMs });
    const request: KnowledgeSearchRequest = { queries: [{ embeddingModel: "embed", collectionIds: [collection], vector: [1, 0], topK: 1 }] };
    const search = (index = 0, body = request) => executor!.search(nodes[index]!.token, nodes[index]!.id, leases[index]!.leaseId, body);
    return { store, executor, nodes, leases, document, request, search, dbPath, options, close: async () => {
      await executor!.close(); store.close(); fs.rmSync(directory, { recursive: true, force: true });
    } };
  } catch (error) {
    await executor?.close(); store.close(); fs.rmSync(directory, { recursive: true, force: true }); throw error;
  }
}

test("isolated HTTP retrieval returns committed provenance and distinct markers for overlapping requests", async () => {
  const f = await fixture();
  const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
    oidcEnabled: false, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false },
  f.store, undefined, undefined, undefined, undefined, f.executor);
  try {
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
    const address = server.address(); assert.ok(address && typeof address === "object");
    const healthUrl = `http://127.0.0.1:${address.port}/api/v1/health`;
    const health = await fetch(healthUrl); assert.equal(health.status, 200);
    assert.equal((await health.json() as { knowledgeSearch: { execution: string } }).knowledgeSearch.execution, "isolated");
    const route = `http://127.0.0.1:${address.port}/api/v1/leases/${f.leases[0]!.leaseId}/knowledge/search`;
    const search = (token: string) => fetch(route, { method: "POST", headers: { authorization: `Bearer ${token}`, "content-type": "application/json" }, body: JSON.stringify(f.request) });
    assert.equal((await search("invalid")).status, 401);
    const responses = await Promise.all([search(f.nodes[0]!.token), search(f.nodes[0]!.token)]);
    assert.deepEqual(responses.map(row => row.status), [200, 200]);
    const hits = await Promise.all(responses.map(async row => (await row.json() as { hits: Array<{ marker: string; provenance: { documentId: string } }> }).hits[0]!));
    assert.deepEqual(hits.map(hit => hit.marker).sort(), ["K1", "K2"]);
    assert.ok(hits.every(hit => hit.provenance.documentId === f.document.id));
    assert.equal(f.store.getRunKnowledgeSources(f.leases[0]!.run.id)!.length, 2);
    await f.executor.close();
    const unavailable = await fetch(healthUrl); assert.equal(unavailable.status, 503);
    assert.equal((await unavailable.json() as { status: string }).status, "degraded");
    assert.equal((await search(f.nodes[0]!.token)).status, 503);
  } finally {
    server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); await f.close();
  }
});

test("queued retrieval rechecks credentials and expiry, enforces admission, and rolls back a malformed later query", async () => {
  const f = await fixture(4);
  let locked = false;
  try {
    f.store.db.exec("BEGIN IMMEDIATE"); locked = true;
    const bad = structuredClone(f.request); bad.queries.push({ ...bad.queries[0]!, vector: [1, 0, 0] });
    const results = Promise.allSettled([f.search(0), f.search(1), f.search(2), f.search(3, bad)]);
    const overflow = assert.rejects(f.search(0), (error: unknown) => error instanceof KnowledgeSearchExecutorError && error.status === 429);
    assert.deepEqual(f.executor.snapshot(), { active: 1, queued: 3, maxPending: 4, accepting: true });
    // The queued copy must not change when a caller edits its original object.
    bad.queries.pop();
    f.store.db.prepare("UPDATE nodes SET credential_state = 'revoked' WHERE id = ?").run(f.nodes[1]!.id);
    f.store.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?")
      .run(new Date(Date.now() - 1000).toISOString(), f.leases[2]!.leaseId);
    f.store.db.exec("COMMIT"); locked = false;
    await overflow;
    const rows = await results;
    assert.equal(rows[0]!.status, "fulfilled");
    for (const index of [1, 2, 3]) {
      const row = rows[index]!; assert.equal(row.status, "rejected");
      if (row.status === "rejected") assert.equal(row.reason.status, index === 1 ? 401 : 400);
      assert.equal(f.store.getRunKnowledgeSources(f.leases[index]!.run.id)!.length, 0);
      assert.equal((f.store.getRunTrace(f.leases[index]!.run.id)!.events as Array<{ type: string }>).filter(e => e.type === "knowledge.retrieved").length, 0);
    }
    assert.equal((await f.search(3)).hits[0]!.marker, "K1");
    assert.equal((await f.search(0)).hits[0]!.marker, "K2");
  } finally { if (locked) f.store.db.exec("ROLLBACK"); await f.close(); }
});

test("executor shutdown finishes its active transaction, rejects queued work and never retries it", async () => {
  const f = await fixture(2);
  try {
    const active = f.search(0);
    const queued = assert.rejects(f.search(1), (error: unknown) => error instanceof KnowledgeSearchExecutorError && error.status === 503);
    const closing = f.executor.close();
    assert.equal((await active).hits[0]!.marker, "K1");
    await queued; await closing;
    assert.equal(f.store.getRunKnowledgeSources(f.leases[0]!.run.id)!.length, 1);
    assert.equal(f.store.getRunKnowledgeSources(f.leases[1]!.run.id)!.length, 0);
    await assert.rejects(f.search(0), (error: unknown) => error instanceof KnowledgeSearchExecutorError && error.status === 503);
  } finally { await f.close(); }
});

test("an active deadline closes admission without replaying queued transactions", async () => {
  const f = await fixture(2, 100);
  let locked = false;
  try {
    f.store.db.exec("BEGIN IMMEDIATE"); locked = true;
    const result = await Promise.allSettled([f.search(0), f.search(1)]);
    assert.ok(result.every(row => row.status === "rejected" && row.reason.status === 504));
    assert.equal(f.executor.snapshot().accepting, false);
    f.store.db.exec("ROLLBACK"); locked = false;
    await f.executor.close();
    // A timeout does not establish whether the active operation committed.
    // The queued operation, however, must never have been sent or replayed.
    assert.equal(f.store.getRunKnowledgeSources(f.leases[1]!.run.id)!.length, 0);
  } finally { if (locked) f.store.db.exec("ROLLBACK"); await f.close(); }
});

test("an executor retains the supplied signed-release policy instead of trusting HTTP admission", async () => {
  const f = await fixture();
  let strict: KnowledgeSearchExecutor | undefined;
  try {
    assert.ok(f.store.authenticateNode(f.nodes[0]!.token));
    strict = await KnowledgeSearchExecutor.create(f.dbPath, { ...f.options, requireSignedWorkerReleases: true });
    await assert.rejects(strict.search(f.nodes[0]!.token, f.nodes[0]!.id, f.leases[0]!.leaseId, f.request),
      (error: unknown) => error instanceof KnowledgeSearchExecutorError && error.status === 401);
    assert.equal(f.store.getRunKnowledgeSources(f.leases[0]!.run.id)!.length, 0);
    assert.equal((await f.search()).hits[0]!.marker, "K1");
  } finally { await strict?.close(); await f.close(); }
});
