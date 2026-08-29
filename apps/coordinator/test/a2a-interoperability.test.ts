import assert from "node:assert/strict";
import { createSign, generateKeyPairSync } from "node:crypto";
import fs from "node:fs";
import http, { type Server } from "node:http";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, it } from "node:test";

import {
  buildA2AAgentCard,
  normalizeA2APushNotificationConfig,
  normalizeA2ASendMessageRequest,
  validateA2AOutboundResponse,
} from "../src/a2a.js";
import {
  discoverA2ARemote,
  invokeA2ARemote,
  outboundAuthorization,
  validateA2AOutboundTarget,
  type A2AOutboundPolicy,
} from "../src/a2a-transport.js";
import { loadConfig } from "../src/config.js";
import { AgatStore } from "../src/database.js";
import { createCoordinatorServer } from "../src/server.js";
import type { A2ARemoteConnection } from "../src/types.js";

const stores: AgatStore[] = [];
const artifactDirs: string[] = [];
const servers: Server[] = [];
const { privateKey: oidcPrivateKey, publicKey: oidcPublicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const oidcPublicJwk = { ...oidcPublicKey.export({ format: "jwk" }), kid: "a2a-rbac-key", use: "sig", alg: "RS256" };

function oidcToken(issuer: string, role: "admin" | "designer"): string {
  const now = Math.floor(Date.now() / 1_000);
  const header = Buffer.from(JSON.stringify({ alg: "RS256", typ: "JWT", kid: "a2a-rbac-key" })).toString("base64url");
  const payload = Buffer.from(JSON.stringify({
    iss: issuer,
    sub: `${role}-subject`,
    preferred_username: `${role}-user`,
    exp: now + 300,
    nbf: now - 5,
    aud: ["agat-web"],
    azp: "agat-web",
    realm_access: { roles: [role] },
    resource_access: {},
    agat_projects: ["default"],
  })).toString("base64url");
  const signer = createSign("RSA-SHA256");
  signer.update(`${header}.${payload}`);
  signer.end();
  return `${header}.${payload}.${signer.sign(oidcPrivateKey).toString("base64url")}`;
}

afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => new Promise<void>((resolve) => server.close(() => resolve()))));
  while (stores.length) stores.pop()?.close();
  while (artifactDirs.length) fs.rmSync(artifactDirs.pop()!, { recursive: true, force: true });
});

function createStore(): AgatStore {
  const artifactsDir = fs.mkdtempSync(path.join(os.tmpdir(), "agat-a2a-interop-"));
  artifactDirs.push(artifactsDir);
  const store = new AgatStore(":memory:", {
    seedDemo: false,
    artifactsDir,
    credentialsKey: "a2a-interop-test-key",
  });
  stores.push(store);
  return store;
}

function listen(server: Server): Promise<number> {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      assert.ok(address && typeof address === "object");
      resolve(address.port);
    });
  });
}

const loopbackPolicy: A2AOutboundPolicy = {
  allowLoopback: true,
  timeoutMs: 2_000,
  maxResponseBytes: 1_048_576,
};

describe("A2A 1.4 interoperability", () => {
  it("persists bounded inline files and publishes binary worker artifacts without URL dereference", () => {
    const store = createStore();
    const created = store.createA2AEndpoint({
      agentId: "collector",
      inputModes: ["text/plain", "image/png"],
      outputModes: ["text/plain", "image/png"],
      fileArtifactsEnabled: true,
      maxFileBytes: 64_000,
      maxFiles: 2,
    }, "default", "tester");
    const endpointId = String((created.endpoint as { id: string }).id);
    const endpoint = store.getA2AEndpointConnection(endpointId, "default");
    assert.ok(endpoint);
    const inputBytes = Buffer.from([1, 2, 3, 4]);
    const normalized = normalizeA2ASendMessageRequest({
      message: {
        messageId: "file-message",
        contextId: "file-context",
        role: "ROLE_USER",
        parts: [
          { text: "Сохрани вложение", mediaType: "text/plain" },
          { raw: inputBytes.toString("base64"), filename: "input.png", mediaType: "image/png" },
        ],
      },
      configuration: { acceptedOutputModes: ["text/plain", "image/png"], returnImmediately: true },
    }, endpoint);
    assert.equal(normalized.files.length, 1);
    assert.equal(normalized.files[0]?.sha256.length, 64);

    const task = store.createA2ATask(endpoint, normalized, null) as { id: string };
    const mapping = store.db.prepare("SELECT run_id FROM a2a_tasks WHERE id = ?").get(task.id) as { run_id: string };
    assert.equal(store.listRunArtifacts(mapping.run_id).some((artifact) => artifact.kind === "a2a_input"), true);

    const nodeId = store.registerNode({
      enrollmentToken: "unused",
      name: "file-worker",
      platform: "test",
      models: ["test-model"],
    }).id;
    const lease = store.leaseNext(nodeId);
    assert.ok(lease);
    const outputBytes = Buffer.from([137, 80, 78, 71]);
    store.completeLease(nodeId, lease.leaseId, "Файл готов", [{
      name: "output.png",
      mediaType: "image/png",
      encoding: "base64",
      content: outputBytes.toString("base64"),
    }]);

    const completed = store.getA2ATask(endpoint.id, task.id, 1, true) as {
      artifacts: Array<{ parts: Array<{ raw?: string; mediaType: string }> }>;
    };
    const binary = completed.artifacts.find((artifact) => artifact.parts[0]?.mediaType === "image/png");
    assert.equal(binary?.parts[0]?.raw, outputBytes.toString("base64"));
  });

  it("keeps push credentials encrypted and advances a durable retry outbox by task state", () => {
    const store = createStore();
    const created = store.createA2AEndpoint({
      agentId: "collector",
      pushNotificationsEnabled: true,
    }, "default", "tester");
    const endpoint = store.getA2AEndpointConnection(String((created.endpoint as { id: string }).id), "default");
    assert.ok(endpoint);
    const normalized = normalizeA2ASendMessageRequest({
      message: {
        messageId: "push-message",
        role: "ROLE_USER",
        parts: [{ text: "Долгая задача", mediaType: "text/plain" }],
      },
      configuration: { returnImmediately: true },
    }, endpoint);
    const task = store.createA2ATask(endpoint, normalized, null) as { id: string };
    const config = normalizeA2APushNotificationConfig({
      id: "push-config-1",
      url: "http://127.0.0.1:9999/callback",
      token: "routing-token",
      authentication: { scheme: "Bearer", credentials: "push-super-secret" },
    });
    const stored = store.createA2APushConfig(endpoint.id, task.id, config) as {
      authentication: { scheme: string; credentialsSuffix: string };
    };
    assert.deepEqual(stored.authentication, { scheme: "Bearer", credentialsSuffix: "secret" });
    const raw = store.db.prepare("SELECT callback_blob FROM a2a_push_configs WHERE id = ?").get(config.id) as { callback_blob: string };
    assert.equal(raw.callback_blob.includes("push-super-secret"), false);

    const submitted = store.claimA2APushDeliveries();
    assert.equal(submitted.length, 1);
    assert.equal(submitted[0]?.authorization, "Bearer push-super-secret");
    assert.equal((submitted[0]?.payload.statusUpdate as { status: { state: string } }).status.state, "TASK_STATE_SUBMITTED");
    store.completeA2APushDelivery(submitted[0]!.deliveryId, null);

    const nodeId = store.registerNode({
      enrollmentToken: "unused",
      name: "push-worker",
      platform: "test",
      models: ["test-model"],
    }).id;
    const lease = store.leaseNext(nodeId);
    assert.ok(lease);
    store.updateA2AEndpoint(endpoint.id, { pushNotificationsEnabled: false }, "default", "tester");
    assert.equal(store.claimA2APushDeliveries().length, 0);
    store.updateA2AEndpoint(endpoint.id, { pushNotificationsEnabled: true }, "default", "tester");
    const working = store.claimA2APushDeliveries();
    assert.equal((working[0]?.payload.statusUpdate as { status: { state: string } }).status.state, "TASK_STATE_WORKING");
    store.completeA2APushDelivery(working[0]!.deliveryId, "temporary failure");
    assert.equal(store.claimA2APushDeliveries().length, 0);
    store.completeLease(nodeId, lease.leaseId, "Готово");
    const completed = store.claimA2APushDeliveries();
    assert.equal((completed[0]?.payload.statusUpdate as { status: { state: string } }).status.state, "TASK_STATE_COMPLETED");
  });

  it("stores outbound peer secrets project-scoped and mirrors only redacted task metadata", () => {
    const store = createStore();
    const remote = store.createA2ARemote({
      agentCardUrl: "https://peer.example/agent-card.json",
      auth: { mode: "bearer", bearerToken: "peer-secret-token" },
    }, {
      name: "Peer",
      description: "External peer",
      agentCardUrl: "https://peer.example/agent-card.json",
      interfaceUrl: "https://peer.example/a2a",
      protocolVersion: "1.0",
      tenant: null,
      skillId: "research",
      skillName: "Research",
      inputModes: ["text/plain"],
      outputModes: ["text/plain"],
      capabilities: { streaming: true, pushNotifications: false },
      cardSha256: "a".repeat(64),
    }, "default", "tester") as A2ARemoteConnection;
    assert.equal(JSON.stringify(store.getA2ASnapshot("default")).includes("peer-secret-token"), false);
    assert.equal(store.getA2ARemoteAuth(remote.id, "default")?.bearerToken, "peer-secret-token");
    const response = {
      task: {
        id: "remote-task-1",
        contextId: "remote-context",
        status: { state: "TASK_STATE_SUBMITTED", timestamp: new Date().toISOString() },
      },
    };
    validateA2AOutboundResponse(response, remote);
    const mirrored = store.recordA2AOutboundTask(remote, {
      message: { messageId: "outbound-message", role: "ROLE_USER", parts: [{ text: "hello" }] },
    }, response, { subject: "user-1", display: "tester" }, false);
    assert.equal(mirrored.remoteTaskId, "remote-task-1");
    assert.equal(JSON.stringify(mirrored).includes("hello"), false);
  });

  it("discovers a loopback peer and exchanges the caller token without persisting it", async () => {
    let port = 0;
    let tokenExchangeBody = "";
    let outboundAuthorizationHeader = "";
    let outboundRequestBody: Record<string, unknown> = {};
    const peer = http.createServer(async (request, response) => {
      if (request.url === "/agent-card.json") {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify({
          name: "Loopback peer",
          description: "Test peer",
          supportedInterfaces: [{
            url: `http://127.0.0.1:${port}/a2a`,
            protocolBinding: "HTTP+JSON",
            protocolVersion: "1.0",
            tenant: "tenant-one",
          }],
          capabilities: { streaming: true, pushNotifications: true },
          defaultInputModes: ["TEXT/PLAIN"],
          defaultOutputModes: ["text/plain"],
          skills: [{ id: "echo", name: "Echo", inputModes: ["TEXT/PLAIN"], outputModes: ["TEXT/PLAIN"] }],
        }));
        return;
      }
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
      if (request.url === "/oauth/token") {
        tokenExchangeBody = Buffer.concat(chunks).toString("utf8");
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify({ access_token: "delegated-peer-token", token_type: "Bearer" }));
        return;
      }
      if (request.url === "/a2a/tenant-one/message:send") {
        outboundAuthorizationHeader = String(request.headers.authorization ?? "");
        outboundRequestBody = JSON.parse(Buffer.concat(chunks).toString("utf8")) as Record<string, unknown>;
        response.writeHead(200, { "content-type": "application/a2a+json" });
        response.end(JSON.stringify({
          task: {
            id: "peer-task",
            contextId: "peer-context",
            status: { state: "TASK_STATE_SUBMITTED", timestamp: new Date().toISOString() },
          },
        }));
        return;
      }
      response.writeHead(404).end();
    });
    servers.push(peer);
    port = await listen(peer);

    const discovered = await discoverA2ARemote(`http://127.0.0.1:${port}/agent-card.json`, "echo", loopbackPolicy);
    assert.equal(discovered.interfaceUrl, `http://127.0.0.1:${port}/a2a`);
    assert.equal(discovered.tenant, "tenant-one");
    assert.deepEqual(discovered.inputModes, ["text/plain"]);
    assert.deepEqual(discovered.outputModes, ["text/plain"]);
    const remote: A2ARemoteConnection = {
      id: "peer",
      projectId: "default",
      ...discovered,
      authMode: "oauth2_token_exchange",
      authSuffix: "secret",
      tokenEndpointOrigin: `http://127.0.0.1:${port}`,
      enabled: true,
      allowFileArtifacts: false,
      maxResponseBytes: 1_048_576,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    const auth = {
      mode: "oauth2_token_exchange" as const,
      tokenUrl: `http://127.0.0.1:${port}/oauth/token`,
      audience: "peer-api",
      scopes: ["tasks.write"],
      clientId: "agat",
      clientSecret: "client-secret",
    };
    assert.equal(await outboundAuthorization(auth, "user-access-token", loopbackPolicy), "Bearer delegated-peer-token");
    const upstream = await invokeA2ARemote(remote, auth, "user-access-token", "message:send", {
      tenant: "caller-must-not-override-card",
      message: { messageId: "delegated-message", role: "ROLE_USER", parts: [{ text: "hello" }] },
    }, loopbackPolicy);
    validateA2AOutboundResponse(upstream, remote);
    assert.match(tokenExchangeBody, /subject_token=user-access-token/);
    assert.equal(outboundAuthorizationHeader, "Bearer delegated-peer-token");
    assert.equal(outboundRequestBody.tenant, "tenant-one");
  });

  it("rejects private and IPv4-mapped IPv6 targets while normalizing bracketed loopback", async () => {
    await assert.rejects(
      validateA2AOutboundTarget("https://[::ffff:7f00:1]/callback", loopbackPolicy),
      /запрещённый сетевой диапазон/,
    );
    await assert.rejects(
      validateA2AOutboundTarget("https://192.168.1.10/callback", loopbackPolicy),
      /запрещённый сетевой диапазон/,
    );
    await validateA2AOutboundTarget("http://[::1]:9876/callback", loopbackPolicy);
    assert.match(normalizeA2APushNotificationConfig({ url: "http://[::1]:9876/callback" }).url, /^http:\/\/\[::1\]:9876/);
  });

  it("rejects malformed peer states, server messages and artifacts before persistence", () => {
    const remote: A2ARemoteConnection = {
      id: "validation-peer",
      projectId: "default",
      name: "Validation peer",
      description: "",
      agentCardUrl: "https://peer.example/agent-card.json",
      interfaceUrl: "https://peer.example/a2a",
      protocolVersion: "1.0",
      tenant: null,
      skillId: "validate",
      skillName: "Validate",
      inputModes: ["text/plain", "image/png"],
      outputModes: ["text/plain"],
      capabilities: { streaming: false, pushNotifications: false },
      authMode: "none",
      authSuffix: "",
      tokenEndpointOrigin: null,
      enabled: true,
      allowFileArtifacts: false,
      maxResponseBytes: 1_048_576,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    assert.throws(
      () => validateA2AOutboundResponse({
        task: { id: "task", status: { state: "TASK_STATE_INVENTED" } },
      }, remote),
      /status.state/,
    );
    assert.throws(
      () => validateA2AOutboundResponse({
        task: {
          id: "task",
          status: { state: "TASK_STATE_COMPLETED" },
          artifacts: [{ parts: [{ text: "missing id" }] }],
        },
      }, remote),
      /artifactId/,
    );
    assert.throws(
      () => validateA2AOutboundResponse({
        message: { messageId: "message", contextId: "context", role: "ROLE_USER", parts: [{ text: "wrong role" }] },
      }, remote),
      /role/,
    );
    assert.doesNotThrow(() => validateA2AOutboundResponse({
      task: {
        id: "task",
        status: { state: "TASK_STATE_WORKING", timestamp: new Date().toISOString() },
        history: [{
          messageId: "input",
          role: "ROLE_USER",
          parts: [{ raw: Buffer.from("png").toString("base64"), filename: "input.png", mediaType: "image/png" }],
        }],
      },
    }, { ...remote, allowFileArtifacts: true }));
  });

  it("advertises opt-in streaming, push and file modes in the Agent Card", () => {
    const store = createStore();
    const created = store.createA2AEndpoint({
      agentId: "collector",
      inputModes: ["text/plain", "application/pdf"],
      outputModes: ["text/plain", "application/pdf"],
      streamingEnabled: true,
      pushNotificationsEnabled: true,
      fileArtifactsEnabled: true,
    });
    const endpoint = store.getA2AEndpointConnection(String((created.endpoint as { id: string }).id));
    assert.ok(endpoint);
    const card = buildA2AAgentCard(endpoint, "https://agents.example.test") as {
      capabilities: Record<string, boolean>;
      defaultInputModes: string[];
      defaultOutputModes: string[];
    };
    assert.deepEqual(card.capabilities, { streaming: true, pushNotifications: true, extendedAgentCard: false });
    assert.deepEqual(card.defaultInputModes, ["text/plain", "application/pdf"]);
    assert.deepEqual(card.defaultOutputModes, ["text/plain", "application/pdf"]);
  });

  it("reserves delegated OAuth trust for admins and exposes only its approved origin", async () => {
    let identityPort = 0;
    let tokenExchangeRequests = 0;
    const identityAndPeer = http.createServer((request, response) => {
      if (request.url === "/jwks") {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify({ keys: [oidcPublicJwk] }));
        return;
      }
      if (request.url === "/agent-card.json") {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify({
          name: "RBAC peer",
          description: "Delegated OAuth boundary fixture",
          supportedInterfaces: [{
            url: `http://127.0.0.1:${identityPort}/a2a`,
            protocolBinding: "HTTP+JSON",
            protocolVersion: "1.0",
          }],
          capabilities: { streaming: false, pushNotifications: false },
          defaultInputModes: ["text/plain"],
          defaultOutputModes: ["text/plain"],
          skills: [
            { id: "public", name: "Public", inputModes: ["text/plain"], outputModes: ["text/plain"] },
            { id: "delegated", name: "Delegated", inputModes: ["text/plain"], outputModes: ["text/plain"] },
          ],
        }));
        return;
      }
      if (request.url === "/oauth/token") {
        tokenExchangeRequests += 1;
        response.writeHead(500).end();
        return;
      }
      response.writeHead(404).end();
    });
    servers.push(identityAndPeer);
    identityPort = await listen(identityAndPeer);
    const issuer = `http://127.0.0.1:${identityPort}`;

    const store = createStore();
    const coordinator = createCoordinatorServer({
      ...loadConfig(),
      host: "127.0.0.1",
      port: 0,
      serveWeb: false,
      mcpEnabled: false,
      oidcEnabled: true,
      oidcIssuer: issuer,
      oidcClientId: "agat-web",
      oidcJwksUrl: `${issuer}/jwks`,
      a2aEnabled: true,
      a2aOutboundEnabled: true,
      a2aAllowLoopbackOutbound: true,
      a2aOutboundTimeoutSeconds: 2,
    }, store);
    servers.push(coordinator);
    const coordinatorPort = await listen(coordinator);
    const remotesUrl = `http://127.0.0.1:${coordinatorPort}/api/v1/a2a/remotes`;
    const designerAuthorization = `Bearer ${oidcToken(issuer, "designer")}`;
    const adminAuthorization = `Bearer ${oidcToken(issuer, "admin")}`;
    const delegatedAuth = {
      mode: "oauth2_token_exchange",
      tokenUrl: `${issuer}/oauth/token`,
      audience: "peer-api",
      scopes: ["a2a.invoke"],
      clientId: "agat",
      clientSecret: "client-super-secret",
    };

    const deniedCreate = await fetch(remotesUrl, {
      method: "POST",
      headers: { authorization: designerAuthorization, "content-type": "application/json" },
      body: JSON.stringify({
        agentCardUrl: `${issuer}/agent-card.json`,
        skillId: "delegated",
        auth: delegatedAuth,
      }),
    });
    assert.equal(deniedCreate.status, 403);

    const allowedDesignerCreate = await fetch(remotesUrl, {
      method: "POST",
      headers: { authorization: designerAuthorization, "content-type": "application/json" },
      body: JSON.stringify({
        agentCardUrl: `${issuer}/agent-card.json`,
        skillId: "public",
        auth: { mode: "none" },
      }),
    });
    assert.equal(allowedDesignerCreate.status, 201);

    const allowedAdminCreate = await fetch(remotesUrl, {
      method: "POST",
      headers: { authorization: adminAuthorization, "content-type": "application/json" },
      body: JSON.stringify({
        agentCardUrl: `${issuer}/agent-card.json`,
        skillId: "delegated",
        auth: delegatedAuth,
      }),
    });
    assert.equal(allowedAdminCreate.status, 201);
    const delegatedRemote = await allowedAdminCreate.json() as A2ARemoteConnection;
    assert.equal(delegatedRemote.tokenEndpointOrigin, issuer);
    assert.equal(JSON.stringify(delegatedRemote).includes("client-super-secret"), false);

    const deniedReplacement = await fetch(`${remotesUrl}/${delegatedRemote.id}`, {
      method: "PATCH",
      headers: { authorization: designerAuthorization, "content-type": "application/json" },
      body: JSON.stringify({ auth: { ...delegatedAuth, clientSecret: "replacement-secret" } }),
    });
    assert.equal(deniedReplacement.status, 403);

    const allowedToggle = await fetch(`${remotesUrl}/${delegatedRemote.id}`, {
      method: "PATCH",
      headers: { authorization: designerAuthorization, "content-type": "application/json" },
      body: JSON.stringify({ enabled: false }),
    });
    assert.equal(allowedToggle.status, 200);
    const toggledRemote = await allowedToggle.json() as A2ARemoteConnection;
    assert.equal(toggledRemote.enabled, false);
    assert.equal(toggledRemote.tokenEndpointOrigin, issuer);

    const creationAudit = store.listEvents(0, 1_000).find((event) =>
      event.type === "a2a.remote.created" && event.data?.remoteId === delegatedRemote.id
    );
    assert.equal(creationAudit?.data?.tokenEndpointOrigin, issuer);
    assert.equal(JSON.stringify(creationAudit).includes("client-super-secret"), false);
    assert.equal(tokenExchangeRequests, 0);
  });

  it("exercises dashboard outbound discovery/invoke and authenticated push delivery over HTTP+JSON", async () => {
    let peerPort = 0;
    let resolvePush!: (value: { authorization: string; payload: Record<string, unknown> }) => void;
    const receivedPush = new Promise<{ authorization: string; payload: Record<string, unknown> }>((resolve) => {
      resolvePush = resolve;
    });
    const peer = http.createServer(async (request, response) => {
      if (request.url === "/agent-card.json") {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify({
          name: "HTTP integration peer",
          description: "A2A route fixture",
          version: "1.0.0",
          supportedInterfaces: [{
            url: `http://127.0.0.1:${peerPort}/a2a`,
            protocolBinding: "HTTP+JSON",
            protocolVersion: "1.0",
          }],
          capabilities: { streaming: false, pushNotifications: false },
          defaultInputModes: ["text/plain"],
          defaultOutputModes: ["text/plain"],
          skills: [{ id: "echo", name: "Echo", inputModes: ["text/plain"], outputModes: ["text/plain"] }],
        }));
        return;
      }
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
      if (request.url === "/a2a/message:send") {
        response.writeHead(200, { "content-type": "application/a2a+json" });
        response.end(JSON.stringify({
          task: {
            id: "http-peer-task",
            contextId: "http-peer-context",
            status: { state: "TASK_STATE_SUBMITTED", timestamp: new Date().toISOString() },
          },
        }));
        return;
      }
      if (request.url === "/callback") {
        resolvePush({
          authorization: String(request.headers.authorization ?? ""),
          payload: JSON.parse(Buffer.concat(chunks).toString("utf8")) as Record<string, unknown>,
        });
        response.writeHead(204).end();
        return;
      }
      response.writeHead(404).end();
    });
    servers.push(peer);
    peerPort = await listen(peer);

    const store = createStore();
    const inbound = store.createA2AEndpoint({ agentId: "collector", pushNotificationsEnabled: true });
    const endpoint = inbound.endpoint as { id: string };
    const config = {
      ...loadConfig(),
      host: "127.0.0.1",
      port: 0,
      serveWeb: false,
      mcpEnabled: false,
      a2aEnabled: true,
      a2aOutboundEnabled: true,
      a2aAllowLoopbackOutbound: true,
      a2aOutboundTimeoutSeconds: 2,
      a2aPublicBaseUrl: "https://agents.example.test",
    };
    const coordinator = createCoordinatorServer(config, store);
    servers.push(coordinator);
    const coordinatorPort = await listen(coordinator);
    const dashboardBase = `http://127.0.0.1:${coordinatorPort}/api/v1`;

    const createRemoteResponse = await fetch(`${dashboardBase}/a2a/remotes`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        agentCardUrl: `http://127.0.0.1:${peerPort}/agent-card.json`,
        skillId: "echo",
        auth: { mode: "none" },
      }),
    });
    assert.equal(createRemoteResponse.status, 201);
    const remote = await createRemoteResponse.json() as { id: string; interfaceUrl: string };
    assert.equal(remote.interfaceUrl, `http://127.0.0.1:${peerPort}/a2a`);

    const invokeResponse = await fetch(`${dashboardBase}/a2a/remotes/${remote.id}/message:send`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        message: { messageId: "http-dashboard-message", role: "ROLE_USER", parts: [{ text: "hello" }] },
        configuration: { acceptedOutputModes: ["text/plain"], returnImmediately: true },
      }),
    });
    assert.equal(invokeResponse.status, 200);
    const invoked = await invokeResponse.json() as { outboundTask: { remoteTaskId: string } };
    assert.equal(invoked.outboundTask.remoteTaskId, "http-peer-task");

    const inboundBase = `http://127.0.0.1:${coordinatorPort}/a2a/v1/endpoints/${endpoint.id}`;
    const sendResponse = await fetch(`${inboundBase}/message:send`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${inbound.accessToken}`,
        "a2a-version": "1.0",
        "content-type": "application/a2a+json",
      },
      body: JSON.stringify({
        message: { messageId: "http-push-message", role: "ROLE_USER", parts: [{ text: "notify me" }] },
        configuration: {
          returnImmediately: true,
          taskPushNotificationConfig: {
            id: "http-push-config",
            url: `http://127.0.0.1:${peerPort}/callback`,
            authentication: { scheme: "Bearer", credentials: "callback-secret" },
          },
        },
      }),
    });
    assert.equal(sendResponse.status, 200);
    const sent = await sendResponse.json() as { task: { id: string } };
    let pushTimeout: NodeJS.Timeout | undefined;
    const push = await Promise.race([
      receivedPush,
      new Promise<never>((_, reject) => {
        pushTimeout = setTimeout(() => reject(new Error("push timeout")), 3_000);
      }),
    ]).finally(() => {
      if (pushTimeout) clearTimeout(pushTimeout);
    });
    assert.equal(push.authorization, "Bearer callback-secret");
    assert.equal((push.payload.statusUpdate as { taskId: string }).taskId, sent.task.id);

    const pushPath = `${inboundBase}/tasks/${sent.task.id}/pushNotificationConfigs/http-push-config`;
    const getPush = await fetch(pushPath, {
      headers: { authorization: `Bearer ${inbound.accessToken}`, "a2a-version": "1.0" },
    });
    assert.equal(getPush.status, 200);
    assert.equal(JSON.stringify(await getPush.json()).includes("callback-secret"), false);
    const createPush = await fetch(`${inboundBase}/tasks/${sent.task.id}/pushNotificationConfigs`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${inbound.accessToken}`,
        "a2a-version": "1.0",
        "content-type": "application/a2a+json",
      },
      body: JSON.stringify({
        id: "http-push-config-2",
        taskId: sent.task.id,
        url: `http://127.0.0.1:${peerPort}/callback`,
      }),
    });
    assert.equal(createPush.status, 200);
    store.updateA2AEndpoint(endpoint.id, { pushNotificationsEnabled: false }, "default", "tester");
    const disabledPushRead = await fetch(
      `${inboundBase}/tasks/${sent.task.id}/pushNotificationConfigs/http-push-config-2`,
      { headers: { authorization: `Bearer ${inbound.accessToken}`, "a2a-version": "1.0" } },
    );
    assert.equal(disabledPushRead.status, 400);
    assert.equal(
      ((await disabledPushRead.json() as { error: { details: Array<{ reason: string }> } }).error.details[0]?.reason),
      "PUSH_NOTIFICATION_NOT_SUPPORTED",
    );
    store.updateA2AEndpoint(endpoint.id, { pushNotificationsEnabled: true }, "default", "tester");
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const deleted = await fetch(pushPath, {
        method: "DELETE",
        headers: { authorization: `Bearer ${inbound.accessToken}`, "a2a-version": "1.0" },
      });
      assert.equal(deleted.status, 200);
    }

    const snapshotResponse = await fetch(`${dashboardBase}/a2a`);
    assert.equal(snapshotResponse.status, 200);
    const snapshot = await snapshotResponse.json() as { remotes: unknown[]; outboundTasks: unknown[] };
    assert.equal(snapshot.remotes.length, 1);
    assert.equal(snapshot.outboundTasks.length, 1);
    assert.equal(JSON.stringify(snapshot).includes("callback-secret"), false);
  });
});
