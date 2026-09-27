import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { AgatStore } from "../src/database.js";

for (const action of ["complete", "fail"] as const) {
  for (const boundary of ["before request", "during stage read", "invalid expiry"] as const) {
    test(`${action} rejects an invalid lease at admission (${boundary}) before state or artifact writes`, context => {
      context.mock.timers.enable({ apis: ["Date"], now: Date.now() });
      const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "agat-terminal-lease-"));
      const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: artifacts });
      try {
        const worker = store.registerNode({ enrollmentToken: "test", name: "Lease fixture", platform: "test",
          models: ["test-model"], maxConcurrency: 1 }).id;
        const run = store.createRun({ name: "Terminal lease", input: "Fixture", agentIds: ["collector"],
          approvalRequired: false, resultDestination: "artifacts" });
        const lease = store.leaseNext(worker)!; assert.ok(lease);
        if (boundary !== "during stage read") store.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?")
          .run(boundary === "invalid expiry" ? "not-a-date" : new Date(Date.now()).toISOString(), lease.leaseId);
        const stage = () => store.db.prepare("SELECT * FROM stages WHERE id = ?").get(lease.stage.id)!;
        const before = stage();
        const events = () => (store.getRunTrace(run.id)!.events as unknown[]).length;
        const beforeEvents = events();
        const beforeFiles = fs.readdirSync(artifacts, { recursive: true });
        const prepare = store.db.prepare.bind(store.db);
        let reachedRead = false;
        if (boundary === "during stage read") store.db.prepare = sql => {
          const statement = prepare(sql);
          if (/SELECT/.test(sql) && /FROM stages/.test(sql) && /lease_id = \?/.test(sql)) {
            const get = statement.get.bind(statement);
            statement.get = (...params) => {
              const row = get(...params); reachedRead = true;
              context.mock.timers.tick(Date.parse(lease.expiresAt) - Date.now());
              return row;
            };
          }
          return statement;
        };
        let error: unknown;
        try {
          if (action === "complete") store.completeLease(worker, lease.leaseId, "EXPIRED OUTPUT", [
            { name: "expired.txt", content: "EXPIRED ARTIFACT" }]);
          else store.failLease(worker, lease.leaseId, "EXPIRED FAILURE");
        } catch (caught) { error = caught; }
        finally { store.db.prepare = prepare; }
        context.diagnostic(`After ${action} (${boundary}): stage=${String(stage().status)}, artifacts=${store.listRunArtifacts(run.id).length}`);
        if (boundary === "during stage read") assert.ok(reachedRead, "The actual stage read must precede expiry");
        assert.ok(error instanceof Error, "The expired or invalid lease must fail before any terminal transition");
        assert.match(error.message, /Активная аренда не найдена/);
        assert.deepEqual(stage(), before); assert.equal(events(), beforeEvents);
        assert.deepEqual(fs.readdirSync(artifacts, { recursive: true }), beforeFiles);
        assert.equal(store.listRunArtifacts(run.id).length, 0);
        // Restore a valid stored timestamp for reclaiming the corrupt-date fixture.
        if (boundary === "invalid expiry") store.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?")
          .run(new Date(Date.now()).toISOString(), lease.leaseId);
        context.mock.timers.tick(1); store.maintenanceTick(); store.heartbeatNode(worker, {});
        const replacement = store.leaseNext(worker)!; assert.ok(replacement);
        assert.notEqual(replacement.leaseId, lease.leaseId);
        assert.throws(() => store.completeLease(worker, lease.leaseId, "STALE"), /аренда/);
        assert.throws(() => store.failLease(worker, lease.leaseId, "STALE"), /аренда/);
        store.completeLease(worker, replacement.leaseId, "CURRENT OUTPUT", [{ name: "current.txt", content: "CURRENT ARTIFACT" }]);
        assert.equal(store.getRun(run.id)!.status, "completed");
        assert.equal(stage().output, "CURRENT OUTPUT");
        assert.equal(store.listRunArtifacts(run.id).length, 3);
      } finally { store.close(); fs.rmSync(artifacts, { recursive: true, force: true }); }
    });
  }
}
