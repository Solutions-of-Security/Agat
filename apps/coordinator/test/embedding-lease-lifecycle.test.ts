import assert from "node:assert/strict";
import test from "node:test";
import { AgatStore } from "../src/database.js";

function fixture() {
  const store = new AgatStore(":memory:", { seedDemo: false });
  const worker = store.registerNode({ enrollmentToken: "test", name: "Embedding lease", platform: "test",
    models: ["test-model"], embeddingModels: ["embeddinggemma"], maxConcurrency: 1 }).id;
  const collection = String(store.createKnowledgeCollection({ name: "Source", embeddingModel: "embeddinggemma" }).id);
  const document = String(store.ingestKnowledgeDocument(collection, { name: "Document", content: "Synthetic source." }).id);
  const lease = store.leaseKnowledgeEmbedding(worker)!; assert.ok(lease);
  const results = lease.chunks.map(chunk => ({ chunkId: chunk.id, embedding: [1, 0] }));
  const snapshot = () => ({
    job: store.db.prepare("SELECT * FROM knowledge_embedding_jobs WHERE document_id = ?").get(document)!,
    document: store.db.prepare("SELECT * FROM knowledge_documents WHERE id = ?").get(document)!,
    chunks: store.db.prepare("SELECT * FROM knowledge_chunks WHERE document_id = ? ORDER BY ordinal").all(document),
    events: Number(store.db.prepare("SELECT COUNT(*) AS count FROM events").get()!.count),
  });
  const recover = () => {
    store.heartbeatNode(worker, {}); store.maintenanceTick();
    const replacement = store.leaseKnowledgeEmbedding(worker)!; assert.ok(replacement);
    assert.notEqual(replacement.leaseId, lease.leaseId);
    assert.equal(store.renewKnowledgeEmbeddingLease(worker, lease.leaseId), false);
    assert.throws(() => store.completeKnowledgeEmbedding(worker, lease.leaseId, results), /embedding-аренда/);
    assert.throws(() => store.failKnowledgeEmbedding(worker, lease.leaseId, "STALE"), /embedding-аренда/);
    assert.equal(store.renewKnowledgeEmbeddingLease(worker, replacement.leaseId), true);
    assert.deepEqual(store.completeKnowledgeEmbedding(worker, replacement.leaseId, results), { completed: true, remainingChunks: 0 });
    assert.equal(snapshot().document.status, "ready");
    assert.equal(snapshot().job.status, "completed");
    assert.equal(snapshot().chunks[0]!.embedding_json, "[1,0]");
  };
  return { store, worker, document, lease, results, snapshot, recover };
}

for (const action of ["renew", "complete", "fail"] as const) {
  for (const boundary of ["before request", "invalid expiry", ...(action === "renew" ? [] : ["during job read"])] as const) {
    test(`embedding ${action} rejects an invalid lease (${boundary}) without changing the index`, context => {
      context.mock.timers.enable({ apis: ["Date"], now: Date.now() });
      const f = fixture();
      try {
        if (boundary !== "during job read") f.store.db.prepare("UPDATE knowledge_embedding_jobs SET lease_expires_at = ? WHERE lease_id = ?")
          .run(boundary === "invalid expiry" ? "not-a-date" : new Date().toISOString(), f.lease.leaseId);
        const before = f.snapshot(), prepare = f.store.db.prepare.bind(f.store.db);
        let reachedRead = false;
        if (boundary === "during job read") f.store.db.prepare = sql => {
          const statement = prepare(sql);
          if (/SELECT j\.\*/.test(sql) && /FROM knowledge_embedding_jobs j/.test(sql)) {
            const get = statement.get.bind(statement);
            statement.get = (...params) => {
              const row = get(...params); reachedRead = true;
              context.mock.timers.tick(Date.parse(f.lease.expiresAt) - Date.now()); return row;
            };
          }
          return statement;
        };
        let accepted = false, error: unknown;
        try {
          if (action === "renew") accepted = f.store.renewKnowledgeEmbeddingLease(f.worker, f.lease.leaseId);
          else {
            if (action === "complete") f.store.completeKnowledgeEmbedding(f.worker, f.lease.leaseId, f.results);
            else f.store.failKnowledgeEmbedding(f.worker, f.lease.leaseId, "EXPIRED FAILURE");
            accepted = true;
          }
        } catch (caught) { error = caught; }
        finally { f.store.db.prepare = prepare; }
        context.diagnostic(`${action}/${boundary}: accepted=${accepted}, job=${f.snapshot().job.status}, document=${f.snapshot().document.status}`);
        if (boundary === "during job read") assert.ok(reachedRead);
        assert.equal(accepted, false, "An expired embedding lease must not renew, finish or fail the current job");
        if (action !== "renew") { assert.ok(error instanceof Error); assert.match(error.message, /Активная embedding-аренда не найдена/); }
        assert.deepEqual(f.snapshot(), before);
        if (boundary === "invalid expiry") f.store.db.prepare("UPDATE knowledge_embedding_jobs SET lease_expires_at = ? WHERE lease_id = ?")
          .run(new Date().toISOString(), f.lease.leaseId);
        context.mock.timers.tick(1); f.recover();
      } finally { f.store.close(); }
    });
  }
}

for (const action of ["complete", "fail"] as const) {
  test(`embedding ${action} rolls back its index and event if persistence outlasts the lease`, context => {
    context.mock.timers.enable({ apis: ["Date"], now: Date.now() });
    const f = fixture();
    try {
      const before = f.snapshot(), prepare = f.store.db.prepare.bind(f.store.db);
      let written = false;
      f.store.db.prepare = sql => {
        const statement = prepare(sql);
        if (/INSERT INTO events\s*\(/.test(sql)) {
          const get = statement.get.bind(statement);
          statement.get = (...params) => {
            const result = get(...params); written = true;
            context.mock.timers.tick(Date.parse(f.lease.expiresAt) - Date.now()); return result;
          };
        }
        return statement;
      };
      let error: unknown;
      try {
        if (action === "complete") f.store.completeKnowledgeEmbedding(f.worker, f.lease.leaseId, f.results);
        else f.store.failKnowledgeEmbedding(f.worker, f.lease.leaseId, "LATE FAILURE");
      } catch (caught) { error = caught; }
      finally { f.store.db.prepare = prepare; }
      assert.ok(written, "The real event INSERT must finish before advancing time");
      assert.ok(error instanceof Error); assert.match(error.message, /Активная embedding-аренда не найдена/);
      assert.deepEqual(f.snapshot(), before);
      context.mock.timers.tick(1); f.recover();
    } finally { f.store.close(); }
  });
}

for (const terminal of [false, true]) {
  test(`embedding maintenance rolls back ${terminal ? "terminal failure" : "retry"} if the document update fails`, () => {
    const f = fixture();
    try {
      f.store.db.prepare("UPDATE knowledge_embedding_jobs SET lease_expires_at = ?, max_failures = ? WHERE lease_id = ?")
        .run("2000-01-01T00:00:00.000Z", terminal ? 1 : 3, f.lease.leaseId);
      const before = f.snapshot(), prepare = f.store.db.prepare.bind(f.store.db);
      let reachedWrite = false;
      f.store.db.prepare = sql => {
        const statement = prepare(sql);
        if (/UPDATE knowledge_documents SET status/.test(sql)) statement.run = () => {
          reachedWrite = true; throw new Error("Injected document update failure");
        };
        return statement;
      };
      try { assert.throws(() => f.store.maintenanceTick(), /Injected document update failure/); }
      finally { f.store.db.prepare = prepare; }
      assert.ok(reachedWrite); assert.deepEqual(f.snapshot(), before);
      f.store.maintenanceTick(); f.store.maintenanceTick();
      assert.equal(f.snapshot().job.failures, 1);
      assert.equal(f.snapshot().job.status, terminal ? "failed" : "pending");
      assert.equal(f.snapshot().document.status, terminal ? "failed" : "pending");
      if (!terminal) f.recover();
    } finally { f.store.close(); }
  });
}
