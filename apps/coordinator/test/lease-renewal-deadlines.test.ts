import assert from "node:assert/strict";
import test from "node:test";
import { AgatStore } from "../src/database.js";

for (const kind of ["stage", "embedding"] as const) {
  for (const fault of ["expiry", "storage error"] as const) {
    test(`${kind} renewal rolls back its UPDATE on ${fault}`, context => {
      context.mock.timers.enable({ apis: ["Date"], now: Date.now() });
      const store = new AgatStore(":memory:", { seedDemo: false });
      try {
        const worker = store.registerNode({ enrollmentToken: "test", name: "Renewal write", platform: "test",
          models: ["test-model"], embeddingModels: ["embeddinggemma"], maxConcurrency: 1 }).id;
        if (kind === "stage") store.createRun({ name: "Renewal", input: "Fixture", agentIds: ["collector"], approvalRequired: false });
        else {
          const collection = String(store.createKnowledgeCollection({ name: "Source", embeddingModel: "embeddinggemma" }).id);
          store.ingestKnowledgeDocument(collection, { name: "Source", content: "Synthetic source." });
        }
        const lease = kind === "stage" ? store.leaseNext(worker)! : store.leaseKnowledgeEmbedding(worker)!;
        assert.ok(lease);
        context.mock.timers.tick(1_000);
        const table = kind === "stage" ? "stages" : "knowledge_embedding_jobs";
        const state = () => store.db.prepare(`SELECT * FROM ${table} WHERE lease_id = ?`).get(lease.leaseId);
        const before = state(), prepare = store.db.prepare.bind(store.db), injected = new Error("Injected renewal write error");
        const renew = () => kind === "stage" ? store.renewLease(worker, lease.leaseId) : store.renewKnowledgeEmbeddingLease(worker, lease.leaseId);
        let written = false;
        store.db.prepare = sql => {
          const statement = prepare(sql);
          if (new RegExp(`UPDATE ${table} SET lease_expires_at`).test(sql)) {
            const run = statement.run.bind(statement);
            statement.run = (...params) => {
              const result = run(...params); written = true;
              if (fault === "storage error") throw injected;
              context.mock.timers.tick(Date.parse(lease.expiresAt) - Date.now());
              return result;
            };
          }
          return statement;
        };
        try {
          if (fault === "expiry") assert.equal(renew(), false, "A renewal persisted after the old deadline must be rolled back");
          else assert.throws(renew, error => error === injected, "Storage failures must propagate, not become lease-not-found");
        } finally { store.db.prepare = prepare; }
        assert.ok(written, "The real UPDATE must execute before injecting the fault");
        assert.deepEqual(state(), before);
        if (fault === "storage error") {
          assert.equal(renew(), true);
          assert.ok(Date.parse(String(state()!.lease_expires_at)) > Date.parse(lease.expiresAt));
        }
      } finally { store.close(); }
    });
  }
}
