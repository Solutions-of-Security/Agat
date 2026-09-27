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

for (const terminal of [false, true]) {
  test(`maintenance rolls back stage and run changes if its ${terminal ? "failure" : "retry"} event cannot persist`, () => {
    const store = new AgatStore(":memory:", { seedDemo: false });
    try {
      const worker = store.registerNode({ enrollmentToken: "test", name: "Maintenance fixture", platform: "test",
        models: ["test-model"], maxConcurrency: 1 }).id;
      const run = store.createRun({ name: "Maintenance rollback", input: "Fixture", agentIds: ["collector"], approvalRequired: false });
      const lease = store.leaseNext(worker)!; assert.ok(lease);
      store.db.prepare("UPDATE stages SET lease_expires_at = ?, max_attempts = ? WHERE lease_id = ?")
        .run("2000-01-01T00:00:00.000Z", terminal ? 1 : 3, lease.leaseId);
      const stage = () => store.db.prepare("SELECT * FROM stages WHERE id = ?").get(lease.stage.id)!;
      const runState = () => store.db.prepare("SELECT * FROM runs WHERE id = ?").get(run.id)!;
      const events = () => (store.getRunTrace(run.id)!.events as Array<{ type: string }>);
      const beforeStage = stage(), beforeRun = runState(), beforeEvents = events().length;
      const prepare = store.db.prepare.bind(store.db);
      let reachedWrite = false;
      store.db.prepare = sql => {
        const statement = prepare(sql);
        if (/INSERT INTO events\s*\(/.test(sql)) statement.get = () => {
          reachedWrite = true; throw new Error("Injected maintenance event failure");
        };
        return statement;
      };
      try { assert.throws(() => store.maintenanceTick(), /Injected maintenance event failure/); }
      finally { store.db.prepare = prepare; }
      assert.ok(reachedWrite);
      assert.deepEqual(stage(), beforeStage); assert.deepEqual(runState(), beforeRun);
      assert.equal(events().length, beforeEvents);
      store.maintenanceTick(); store.maintenanceTick();
      const expirationEvents = events().filter(e => e.type === (terminal ? "stage.failed" : "lease.expired"));
      assert.equal(expirationEvents.length, 1);
      if (terminal) {
        assert.equal(stage().status, "failed"); assert.equal(runState().status, "failed");
      } else {
        store.heartbeatNode(worker, {});
        const replacement = store.leaseNext(worker)!; assert.ok(replacement);
        assert.notEqual(replacement.leaseId, lease.leaseId);
        store.completeLease(worker, replacement.leaseId, "RECOVERED");
        assert.equal(runState().status, "completed");
      }
    } finally { store.close(); }
  });
}

for (const expiry of ["exact boundary", "invalid date"] as const) {
  test(`stage renewal rejects ${expiry} without reviving the old owner`, context => {
    context.mock.timers.enable({ apis: ["Date"], now: Date.now() });
    const store = new AgatStore(":memory:", { seedDemo: false });
    try {
      const worker = store.registerNode({ enrollmentToken: "test", name: "Renewal", platform: "test",
        models: ["test-model"], maxConcurrency: 1 }).id;
      const run = store.createRun({ name: "Renewal", input: "Fixture", agentIds: ["collector"], approvalRequired: false });
      const lease = store.leaseNext(worker)!; assert.ok(lease);
      store.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?")
        .run(expiry === "invalid date" ? "not-a-date" : new Date().toISOString(), lease.leaseId);
      const state = () => ({ stage: store.db.prepare("SELECT * FROM stages WHERE id = ?").get(lease.stage.id),
        run: store.db.prepare("SELECT * FROM runs WHERE id = ?").get(run.id),
        events: store.db.prepare("SELECT * FROM events WHERE run_id = ? ORDER BY id").all(run.id) });
      const before = state();
      const renewed = store.renewLease(worker, lease.leaseId);
      context.diagnostic(`Stage renewal/${expiry}: renewed=${renewed}`);
      assert.equal(renewed, false); assert.deepEqual(state(), before);
      // Malformed stored timestamps need repair before normal expiry cleanup.
      if (expiry === "invalid date") store.db.prepare("UPDATE stages SET lease_expires_at = ? WHERE lease_id = ?")
        .run(new Date().toISOString(), lease.leaseId);
      context.mock.timers.tick(1); store.maintenanceTick(); store.heartbeatNode(worker, {});
      const replacement = store.leaseNext(worker)!; assert.ok(replacement);
      assert.notEqual(replacement.leaseId, lease.leaseId);
      assert.equal(store.renewLease(worker, lease.leaseId), false);
      context.mock.timers.tick(1_000);
      assert.equal(store.renewLease(worker, replacement.leaseId), true);
      assert.ok(Date.parse(String(state().stage!.lease_expires_at)) > Date.parse(replacement.expiresAt));
      store.completeLease(worker, replacement.leaseId, "CURRENT OUTPUT");
      assert.equal(store.getRun(run.id)!.status, "completed");
    } finally { store.close(); }
  });
}
