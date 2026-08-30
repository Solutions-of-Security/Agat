import assert from "node:assert/strict";
import type { Server } from "node:http";
import { afterEach, describe, it } from "node:test";

import { AgatStore } from "../src/database.js";
import {
  BrokerEdgeAttestationVerifier,
  type EdgeAttestationRequest,
  type EdgeAttestationVerifier,
} from "../src/edge-attestation.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import type { EdgeAttestationVerdict, EdgeWorkerRegistration } from "../src/types.js";

const stores: AgatStore[] = [];
const servers: Server[] = [];

afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => new Promise<void>((resolve) => server.close(() => resolve()))));
  while (stores.length) stores.pop()?.close();
});

function store(): AgatStore {
  const value = new AgatStore(":memory:", {
    seedDemo: false,
    edgeChallengeTtlSeconds: 180,
    requireSignedWorkerReleases: true,
  });
  stores.push(value);
  return value;
}

function verdict(input: EdgeAttestationRequest): EdgeAttestationVerdict {
  const provider = input.registration.platform === "android" ? "play_integrity" : "app_attest";
  return {
    schemaVersion: 1,
    valid: true,
    platform: input.registration.platform,
    provider,
    applicationId: input.registration.attestation.applicationId,
    keyId: input.registration.attestation.keyId,
    challengeSha256: input.challenge.challengeSha256,
    hardwareBacked: true,
    environment: "production",
    issuedAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + 5 * 60_000).toISOString(),
    verdicts: input.registration.platform === "android"
      ? ["MEETS_DEVICE_INTEGRITY", "PLAY_RECOGNIZED"]
      : ["APP_ATTEST_VALID"],
  };
}

function registration(challengeId: string, challenge: string): EdgeWorkerRegistration {
  return {
    challengeId,
    challenge,
    name: "edge-iphone",
    platform: "ios",
    architecture: "arm64",
    models: ["edge-tiny"],
    maxConcurrency: 1,
    modelProfiles: [{ name: "edge-tiny", provider: "coreml", capabilities: ["text"] }],
    embeddingModels: ["malicious-embedding-claim"],
    attestation: {
      provider: "app_attest",
      token: "base64url-attestation-object",
      keyId: "app-attest-key-1",
      applicationId: "TEAMID.io.agat.edge",
    },
  };
}

describe("native edge enrollment and credential lifecycle", () => {
  it("consumes a bound challenge once and protects an attested name from shared-token takeover", () => {
    const value = store();
    const challenge = value.issueEdgeEnrollmentChallenge({
      name: "edge-iphone",
      platform: "ios",
      applicationId: "TEAMID.io.agat.edge",
    });
    const claimed = value.claimEdgeEnrollmentChallenge({
      id: challenge.id,
      challenge: challenge.challenge,
      name: "edge-iphone",
      platform: "ios",
      applicationId: "TEAMID.io.agat.edge",
    });
    assert.ok(claimed);
    assert.equal(value.claimEdgeEnrollmentChallenge({
      id: challenge.id,
      challenge: challenge.challenge,
      name: "edge-iphone",
      platform: "ios",
      applicationId: "TEAMID.io.agat.edge",
    }), null);

    const input = registration(challenge.id, challenge.challenge);
    const created = value.registerEdgeNode(input, verdict({ challenge: claimed, registration: input }));
    assert.equal(value.authenticateNode(created.token)?.trust_kind, "hardware_attested");
    const node = (value.getOverview().nodes as Array<Record<string, unknown>>).find((candidate) => candidate.id === created.id);
    assert.deepEqual(node?.embeddingModels, []);
    assert.throws(() => value.registerNode({
      enrollmentToken: "shared",
      name: "edge-iphone",
      platform: "darwin",
      models: [],
    }), /нельзя перерегистрировать/);
  });

  it("blocks scheduling immediately, limits the pending token to control, and revokes it after wipe ack", () => {
    const value = store();
    const challenge = value.issueEdgeEnrollmentChallenge({
      name: "edge-iphone",
      platform: "ios",
      applicationId: "TEAMID.io.agat.edge",
    });
    const input = registration(challenge.id, challenge.challenge);
    const claimed = value.claimEdgeEnrollmentChallenge({
      id: challenge.id,
      challenge: challenge.challenge,
      name: input.name,
      platform: input.platform,
      applicationId: input.attestation.applicationId,
    });
    assert.ok(claimed);
    const created = value.registerEdgeNode(input, verdict({ challenge: claimed, registration: input }));
    value.createRun({
      name: "Edge run",
      input: "local input",
      agentIds: ["collector"],
      approvalRequired: false,
    });
    assert.ok(value.leaseNext(created.id, "ios/1.7.0"));

    const command = value.requestEdgeRemoteWipe(created.id, "Device lost", "admin-1");
    assert.equal(command.action, "wipe");
    assert.equal(value.authenticateNode(created.token), null);
    assert.equal(value.authenticateNode(created.token, true)?.id, created.id);
    assert.equal(value.leaseNext(created.id), null);
    assert.equal(value.edgeControl(created.id).generation, command.generation);

    value.acknowledgeEdgeRemoteWipe(created.id, {
      generation: command.generation,
      credentialsDeleted: true,
      localDataDeleted: true,
    });
    assert.equal(value.authenticateNode(created.token, true), null);
    const node = (value.getOverview().nodes as Array<Record<string, unknown>>).find((candidate) => candidate.id === created.id);
    assert.equal(node?.credentialState, "wiped");
    assert.equal((node?.wipe as Record<string, unknown>).generation, 1);

    const secondChallenge = value.issueEdgeEnrollmentChallenge({
      name: "edge-iphone",
      platform: "ios",
      applicationId: "TEAMID.io.agat.edge",
    });
    const secondInput = registration(secondChallenge.id, secondChallenge.challenge);
    const secondClaim = value.claimEdgeEnrollmentChallenge({
      id: secondChallenge.id,
      challenge: secondChallenge.challenge,
      name: secondInput.name,
      platform: secondInput.platform,
      applicationId: secondInput.attestation.applicationId,
    });
    assert.ok(secondClaim);
    assert.throws(
      () => value.registerEdgeNode(secondInput, verdict({ challenge: secondClaim, registration: secondInput })),
      /Отозванный attestation key/,
    );
  });
});

describe("edge attestation boundary", () => {
  it("accepts only a fresh broker verdict bound to challenge, app and hardware policy", async () => {
    const challenge = {
      id: "challenge-1",
      challenge: "random-challenge",
      challengeSha256: "fac65912a90fbaf5d14aa927e9fb1c633ae1e9fd77580bbb2c3dec1586d4742d",
      platform: "ios" as const,
      applicationId: "TEAMID.io.agat.edge",
      nodeName: "edge-iphone",
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    };
    const input = { challenge, registration: registration(challenge.id, challenge.challenge) };
    const valid = verdict(input);
    const verifier = new BrokerEdgeAttestationVerifier({
      url: "https://attestation.example.test/v1/verify",
      token: "broker-secret",
      timeoutSeconds: 2,
      allowDevelopment: false,
      androidRequiredVerdicts: ["MEETS_DEVICE_INTEGRITY", "PLAY_RECOGNIZED"],
      iosRequiredVerdicts: ["APP_ATTEST_VALID"],
      fetcher: async () => new Response(JSON.stringify(valid), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    });
    assert.equal((await verifier.verify(input)).hardwareBacked, true);

    const mismatched = new BrokerEdgeAttestationVerifier({
      url: "https://attestation.example.test/v1/verify",
      token: "broker-secret",
      timeoutSeconds: 2,
      allowDevelopment: false,
      androidRequiredVerdicts: [],
      iosRequiredVerdicts: ["APP_ATTEST_VALID"],
      fetcher: async () => new Response(JSON.stringify({ ...valid, challengeSha256: "0".repeat(64) }), { status: 200 }),
    });
    await assert.rejects(() => mismatched.verify(input), /не привязан/);

    const invertedValidity = new BrokerEdgeAttestationVerifier({
      url: "https://attestation.example.test/v1/verify",
      token: "broker-secret",
      timeoutSeconds: 2,
      allowDevelopment: false,
      androidRequiredVerdicts: [],
      iosRequiredVerdicts: ["APP_ATTEST_VALID"],
      fetcher: async () => new Response(JSON.stringify({
        ...valid,
        issuedAt: new Date(Date.now() + 4 * 60_000).toISOString(),
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
      }), { status: 200 }),
    });
    await assert.rejects(() => invertedValidity.verify(input), /недопустимый срок/);
  });

  it("exposes the wipe command only to the attested node control channel", async () => {
    const value = store();
    const fake: EdgeAttestationVerifier = {
      available: true,
      mode: "test",
      reason: null,
      verify: async (input) => verdict(input),
    };
    const config = {
      ...loadConfig(),
      host: "127.0.0.1",
      port: 0,
      serveWeb: false,
      adminToken: "admin-token",
      enrollmentToken: "enrollment-token",
      edgeEnabled: true,
      edgeAttestationMode: "disabled" as const,
      edgeAndroidApplicationId: "io.agat.edge",
      edgeIosApplicationId: "TEAMID.io.agat.edge",
      mcpEnabled: false,
    };
    const server = createCoordinatorServer(config, value, undefined, undefined, undefined, fake);
    servers.push(server);
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    assert.ok(address && typeof address === "object");
    const base = `http://127.0.0.1:${address.port}`;

    const challengeResponse = await fetch(`${base}/api/v1/edge/enrollment/challenges`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        enrollmentToken: "enrollment-token",
        name: "edge-iphone",
        platform: "ios",
        applicationId: "TEAMID.io.agat.edge",
      }),
    });
    assert.equal(challengeResponse.status, 201);
    const challenge = await challengeResponse.json() as { id: string; challenge: string };
    const enrollResponse = await fetch(`${base}/api/v1/edge/enroll`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(registration(challenge.id, challenge.challenge)),
    });
    assert.equal(enrollResponse.status, 201);
    const enrolled = await enrollResponse.json() as { id: string; token: string };

    const unauthorizedWipe = await fetch(`${base}/api/v1/nodes/${enrolled.id}/remote-wipe`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ reason: "lost" }),
    });
    assert.equal(unauthorizedWipe.status, 401);
    const wipe = await fetch(`${base}/api/v1/nodes/${enrolled.id}/remote-wipe`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-agat-admin-token": "admin-token" },
      body: JSON.stringify({ reason: "lost" }),
    });
    assert.equal(wipe.status, 202);

    const work = await fetch(`${base}/api/v1/workers/lease`, {
      method: "POST",
      headers: { authorization: `Bearer ${enrolled.token}`, "content-type": "application/json" },
      body: "{}",
    });
    assert.equal(work.status, 401);
    const control = await fetch(`${base}/api/v1/edge/control`, {
      headers: { authorization: `Bearer ${enrolled.token}` },
    });
    assert.equal(control.status, 200);
    const command = await control.json() as { action: string; generation: number };
    assert.equal(command.action, "wipe");

    const acknowledged = await fetch(`${base}/api/v1/edge/control/wipe-ack`, {
      method: "POST",
      headers: { authorization: `Bearer ${enrolled.token}`, "content-type": "application/json" },
      body: JSON.stringify({ generation: command.generation, credentialsDeleted: true, localDataDeleted: true }),
    });
    assert.equal(acknowledged.status, 200);
    const revoked = await fetch(`${base}/api/v1/edge/control`, {
      headers: { authorization: `Bearer ${enrolled.token}` },
    });
    assert.equal(revoked.status, 401);
  });
});
