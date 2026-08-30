import assert from "node:assert/strict";
import { generateKeyPairSync, sign } from "node:crypto";
import { describe, it } from "node:test";

import { AgatStore } from "../src/database.js";
import type { WorkerReleaseManifest } from "../src/types.js";

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  return `{${Object.entries(value as Record<string, unknown>)
    .filter(([, field]) => field !== undefined)
    .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
    .map(([key, field]) => `${JSON.stringify(key)}:${canonicalJson(field)}`)
    .join(",")}}`;
}

function signedManifest(releaseId: string, version: string, digestCharacter: string): {
  manifest: WorkerReleaseManifest;
  signature: string;
  publicKey: string;
} {
  const keys = generateKeyPairSync("ed25519");
  const manifest: WorkerReleaseManifest = {
    schemaVersion: 1,
    releaseId,
    version,
    artifactDigest: `sha256:${digestCharacter.repeat(64)}`,
    platforms: ["linux"],
    issuedAt: new Date(Date.now() - 1_000).toISOString(),
    expiresAt: null,
    metadata: { channel: "stable" },
  };
  return {
    manifest,
    signature: sign(null, Buffer.from(canonicalJson(manifest)), keys.privateKey).toString("base64"),
    publicKey: keys.publicKey.export({ format: "pem", type: "spki" }).toString(),
  };
}

describe("Fleet and HA policy", () => {
  it("enforces regional queue quotas and exports redacted audit events", () => {
    const store = new AgatStore(":memory:", {
      region: "eu-central-1",
      residencyDomain: "eu",
      coordinatorInstanceId: "sqlite-test",
    });
    try {
      store.updateProjectFleetPolicy({
        homeRegion: "eu-central-1",
        allowedRegions: ["eu-central-1"],
        residencyDomain: "eu",
        queueName: "critical",
        maxQueuedTasks: 1,
        maxRunningTasks: 1,
        expectedRevision: 1,
      }, "default", "admin");
      const first = store.createRun({
        name: "bounded",
        input: "one",
        agentIds: ["collector"],
        approvalRequired: false,
      });
      assert.ok(first.id);
      assert.throws(() => store.createRun({
        name: "overflow",
        input: "two",
        agentIds: ["collector"],
        approvalRequired: false,
      }), /queue quota exceeded/i);
      assert.throws(() => store.createProject({
        id: "wrong-region",
        name: "Wrong region",
        homeRegion: "us-east-1",
        residencyDomain: "us",
      }), /HA-cell/);

      store.recordPlatformEvent("security.test", "token=must-not-leave-cell", {
        projectId: "default",
        actor: "operator@example.test",
        riskTier: "critical",
        reason: "must-not-leave-cell",
        input: "must-not-leave-cell",
        secret: "must-not-leave-cell",
      }, "warn");
      const batch = store.claimAuditExportBatch(500);
      const audit = batch.find((event) => event.type === "security.test");
      assert.ok(audit);
      assert.deepEqual(audit.metadata, {
        projectId: "default",
        actor: "operator@example.test",
        riskTier: "critical",
      });
      assert.equal(JSON.stringify(audit).includes("must-not-leave-cell"), false);
      assert.match(String(audit.messageSha256), /^[0-9a-f]{64}$/);
      assert.match(String(audit.reasonSha256), /^[0-9a-f]{64}$/);
      assert.match(String(audit.dataSha256), /^[0-9a-f]{64}$/);
      assert.equal(store.claimAuditExportBatch(500).length, 0);
      store.completeAuditExport(batch.map((event) => Number(event.id)), null);
      assert.equal(store.auditExportStatus().delivered, batch.length);
    } finally {
      store.close();
    }
  });

  it("verifies Ed25519 releases, stages a rollout and fail-closes revoked workers", () => {
    const first = signedManifest("worker-1.7.0", "1.7.0", "a");
    const second = signedManifest("worker-1.7.1", "1.7.1", "b");
    const store = new AgatStore(":memory:", {
      region: "eu-central-1",
      residencyDomain: "eu",
      workerReleasePublicKeys: {
        "release-key-a": first.publicKey,
        "release-key-b": second.publicKey,
      },
      requireSignedWorkerReleases: true,
    });
    try {
      assert.throws(() => store.registerWorkerRelease({
        manifest: first.manifest,
        keyId: "release-key-a",
        signature: `${first.signature.slice(0, -2)}AA`,
      }), /signature|base64/i);
      store.registerWorkerRelease({
        manifest: first.manifest,
        keyId: "release-key-a",
        signature: first.signature,
      }, "release-admin");
      const initial = store.updateWorkerRollout({
        releaseId: first.manifest.releaseId,
        region: "eu-central-1",
        ring: "stable",
        percentage: 100,
        expectedRevision: 0,
      }, "default", "release-admin");
      assert.equal(initial.revision, 1);

      const node = store.registerNode({
        enrollmentToken: "test",
        name: "signed-node",
        platform: "Linux 6",
        models: [],
        region: "eu-central-1",
        residencyDomain: "eu",
        release: {
          releaseId: first.manifest.releaseId,
          artifactDigest: first.manifest.artifactDigest,
          keyId: "release-key-a",
          signature: first.signature,
        },
      });
      store.createRun({ name: "signed", input: "execute", agentIds: ["collector"], approvalRequired: false });
      assert.ok(store.leaseNext(node.id, "1.7.0"));

      store.registerWorkerRelease({
        manifest: second.manifest,
        keyId: "release-key-b",
        signature: second.signature,
      }, "release-admin");
      const staged = store.updateWorkerRollout({
        releaseId: second.manifest.releaseId,
        region: "eu-central-1",
        ring: "stable",
        percentage: 10,
        expectedRevision: 1,
      }, "default", "release-admin");
      assert.equal(staged.fallbackReleaseId, first.manifest.releaseId);
      assert.equal(staged.percentage, 10);

      assert.equal(store.revokeWorkerRelease(first.manifest.releaseId, "vulnerability", "release-admin"), true);
      const revoked = store.authenticateNode(node.token);
      assert.equal(revoked, null);
    } finally {
      store.close();
    }
  });
});
