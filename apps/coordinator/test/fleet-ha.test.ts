import assert from "node:assert/strict";
import { generateKeyPairSync, sign } from "node:crypto";
import { describe, it } from "node:test";

import { AgatStore } from "../src/database.js";
import type {
  WorkerProvenanceStatement,
  WorkerReleaseManifest,
  WorkerRuntimeAttestationStatement,
} from "../src/types.js";

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

  it("rejects one key reused across release, provenance and runtime trust boundaries", () => {
    const sharedKeys = generateKeyPairSync("ed25519");
    const sharedPublicKey = sharedKeys.publicKey.export({ format: "pem", type: "spki" }).toString();
    assert.throws(() => new AgatStore(":memory:", {
      workerReleasePublicKeys: { release: sharedPublicKey },
      requireSignedWorkerReleases: true,
      workerProvenancePublicKeys: { provenance: sharedPublicKey },
      requireWorkerProvenance: true,
      workerRuntimeAttestationPublicKeys: { runtime: sharedPublicKey },
      requireWorkerRuntimeAttestation: true,
    }), /разные trust roots/i);
  });

  it("binds OCI provenance to a fresh runtime-attested workload and rejects replay", () => {
    const release = signedManifest("worker-1.8.0", "1.8.0", "c");
    const provenanceKeys = generateKeyPairSync("ed25519");
    const runtimeKeys = generateKeyPairSync("ed25519");
    const provenance: WorkerProvenanceStatement = {
      schemaVersion: 1,
      policyId: "worker-slsa-v1",
      subjectDigest: release.manifest.artifactDigest,
      ociRepository: "registry.example.test/agat/worker",
      predicateType: "https://slsa.dev/provenance/v1",
      builderId: "https://ci.example.test/builders/hardened",
      buildType: "https://ci.example.test/build-types/oci-v1",
      sourceRepository: "https://git.example.test/agat/worker",
      sourceCommit: "d".repeat(40),
      sigstoreBundleSha256: "e".repeat(64),
      verifiedAt: new Date(Date.now() - 1_000).toISOString(),
      expiresAt: new Date(Date.now() + 60 * 60 * 1_000).toISOString(),
    };
    const provenanceSignature = sign(
      null,
      Buffer.from(canonicalJson(provenance)),
      provenanceKeys.privateKey,
    ).toString("base64");
    const store = new AgatStore(":memory:", {
      region: "eu-central-1",
      residencyDomain: "eu",
      workerReleasePublicKeys: { "release-key": release.publicKey },
      requireSignedWorkerReleases: true,
      workerProvenancePublicKeys: {
        "provenance-key": provenanceKeys.publicKey.export({ format: "pem", type: "spki" }).toString(),
      },
      requireWorkerProvenance: true,
      workerRuntimeAttestationPublicKeys: {
        "runtime-key": runtimeKeys.publicKey.export({ format: "pem", type: "spki" }).toString(),
      },
      requireWorkerRuntimeAttestation: true,
      workerRuntimeAttestationProviders: ["spiffe"],
      workerRuntimeIdentityPrefixes: ["spiffe://example.test/worker/"],
      workerRuntimeMaxLifetimeSeconds: 600,
    });
    try {
      assert.throws(() => store.registerWorkerRelease({
        manifest: release.manifest,
        keyId: "release-key",
        signature: release.signature,
      }), /provenance/i);
      const registeredRelease = store.registerWorkerRelease({
        manifest: release.manifest,
        keyId: "release-key",
        signature: release.signature,
        provenance: {
          statement: provenance,
          keyId: "provenance-key",
          signature: provenanceSignature,
        },
      });
      assert.equal(registeredRelease.provenanceVerified, true);
      const releaseIdentity = {
        releaseId: release.manifest.releaseId,
        artifactDigest: release.manifest.artifactDigest,
        keyId: "release-key",
        signature: release.signature,
      };
      const challenge = store.issueWorkerRuntimeAttestationChallenge({
        enrollmentToken: "test",
        name: "attested-node",
        platform: "Linux 6",
        architecture: "arm64",
        region: "eu-central-1",
        residencyDomain: "eu",
        release: releaseIdentity,
      });
      const statement: WorkerRuntimeAttestationStatement = {
        schemaVersion: 1,
        challengeSha256: challenge.challengeSha256,
        workerName: challenge.binding.workerName,
        platform: challenge.binding.platform,
        architecture: challenge.binding.architecture,
        region: challenge.binding.region,
        residencyDomain: challenge.binding.residencyDomain,
        releaseId: challenge.binding.releaseId,
        artifactDigest: challenge.binding.artifactDigest,
        provenanceSha256: challenge.binding.provenanceSha256,
        provider: "spiffe",
        workloadIdentity: "spiffe://example.test/worker/attested-node",
        selectorSha256: "f".repeat(64),
        imageDigest: challenge.binding.artifactDigest,
        hardwareBacked: false,
        issuedAt: new Date(Date.now() - 500).toISOString(),
        expiresAt: new Date(Date.now() + 5 * 60 * 1_000).toISOString(),
      };
      const runtimeSignature = sign(
        null,
        Buffer.from(canonicalJson(statement)),
        runtimeKeys.privateKey,
      ).toString("base64");
      const node = store.registerNode({
        enrollmentToken: "test",
        name: "attested-node",
        platform: "Linux 6",
        architecture: "arm64",
        models: [],
        region: "eu-central-1",
        residencyDomain: "eu",
        release: releaseIdentity,
        runtimeChallengeId: challenge.id,
        runtimeAttestation: { statement, keyId: "runtime-key", signature: runtimeSignature },
      });
      assert.ok(node.attestationExpiresAt);
      assert.ok(store.authenticateNode(node.token));
      assert.throws(() => store.registerNode({
        enrollmentToken: "test",
        name: "attested-node",
        platform: "Linux 6",
        architecture: "arm64",
        models: [],
        region: "eu-central-1",
        residencyDomain: "eu",
        release: releaseIdentity,
        runtimeChallengeId: challenge.id,
        runtimeAttestation: { statement, keyId: "runtime-key", signature: runtimeSignature },
      }), /already used|использован|закрыт/i);
      const refreshChallenge = store.issueWorkerRuntimeAttestationChallenge({
        enrollmentToken: "test",
        name: "attested-node",
        platform: "Linux 6",
        architecture: "arm64",
        region: "eu-central-1",
        residencyDomain: "eu",
        release: releaseIdentity,
      });
      const refreshedStatement: WorkerRuntimeAttestationStatement = {
        ...statement,
        challengeSha256: refreshChallenge.challengeSha256,
        issuedAt: new Date(Date.now() - 250).toISOString(),
        expiresAt: new Date(Date.now() + 6 * 60 * 1_000).toISOString(),
      };
      const refreshedSignature = sign(
        null,
        Buffer.from(canonicalJson(refreshedStatement)),
        runtimeKeys.privateKey,
      ).toString("base64");
      const refreshed = store.refreshWorkerRuntimeAttestation(
        node.id,
        refreshChallenge.id,
        { statement: refreshedStatement, keyId: "runtime-key", signature: refreshedSignature },
      );
      assert.equal(refreshed.attestationExpiresAt, refreshedStatement.expiresAt);
      assert.ok(store.authenticateNode(node.token), "attestation refresh must preserve node token");
      store.db.prepare(`
        UPDATE nodes SET runtime_attestation_expires_at = '1970-01-01T00:00:00.000Z'
        WHERE id = ?
      `).run(node.id);
      assert.equal(store.authenticateNode(node.token), null);
    } finally {
      store.close();
    }
  });

  it("dead-letters poison SIEM events, preserves redaction and requires controlled replay", () => {
    const store = new AgatStore(":memory:", {
      coordinatorInstanceId: "siem-dlq-test",
      siemMaxAttempts: 2,
      siemDeliveredRetentionDays: 1,
      siemDlqRetentionDays: 1,
    });
    try {
      const event = store.recordPlatformEvent("security.poison", "secret-message", {
        actor: "auditor@example.test",
        reason: "secret-reason",
        secret: "must-not-enter-dlq",
      }, "warn");
      let batch = store.claimAuditExportBatch(500);
      const eventId = Number(event.id);
      const otherIds = batch.map((row) => Number(row.id)).filter((id) => id !== eventId);
      store.completeAuditExport(otherIds, null, { responseSha256: "a".repeat(64) });
      store.completeAuditExport([eventId], "sink rejected payload", { reasonCode: "http_5xx" });
      store.db.prepare("UPDATE audit_export_outbox SET available_at = ? WHERE event_id = ?")
        .run(new Date(Date.now() - 1_000).toISOString(), eventId);
      batch = store.claimAuditExportBatch(500);
      assert.deepEqual(batch.map((row) => Number(row.id)), [eventId]);
      store.completeAuditExport([eventId], "sink rejected payload again", { reasonCode: "http_5xx" });

      const [letter] = store.listAuditExportDeadLetters();
      assert.ok(letter);
      assert.equal(letter.eventId, eventId);
      assert.equal(letter.status, "open");
      assert.equal(JSON.stringify(letter).includes("must-not-enter-dlq"), false);
      assert.equal(JSON.stringify(letter).includes("secret-reason"), false);
      assert.match(String(letter.payloadSha256), /^[0-9a-f]{64}$/);
      assert.equal(store.auditExportStatus().dead, 1);

      store.db.prepare("UPDATE audit_export_dead_letters SET retained_until = ? WHERE event_id = ?")
        .run("1970-01-01T00:00:00.000Z", eventId);
      assert.equal(store.runAuditExportRetention().deadLettersPurged, 0, "open DLQ must never expire silently");
      assert.equal(store.replayAuditExportDeadLetter(eventId, "admin", "sink mapping fixed"), true);
      batch = store.claimAuditExportBatch(500);
      assert.equal(batch.some((row) => Number(row.id) === eventId), true);
      store.completeAuditExport(
        batch.map((row) => Number(row.id)).filter((id) => id !== eventId),
        null,
        { responseSha256: "b".repeat(64) },
      );
      store.completeAuditExport([eventId], "permanent reject", {
        reasonCode: "http_4xx",
        terminal: true,
      });
      assert.equal(store.resolveAuditExportDeadLetter(eventId, "admin", "documented exception"), true);
      store.db.prepare("UPDATE audit_export_dead_letters SET retained_until = ? WHERE event_id = ?")
        .run("1970-01-01T00:00:00.000Z", eventId);
      assert.equal(store.runAuditExportRetention().deadLettersPurged, 1);
      assert.equal(store.listAuditExportDeadLetters().length, 0);
    } finally {
      store.close();
    }
  });
});
