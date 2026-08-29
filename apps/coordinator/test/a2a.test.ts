import assert from "node:assert/strict";
import type { Server } from "node:http";
import { afterEach, describe, it } from "node:test";

import {
  A2AProtocolError,
  buildA2AAgentCard,
  normalizeA2ASendMessageRequest,
} from "../src/a2a.js";
import { AgatStore } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import type { A2AEndpointConnection, A2ASendMessageRequest } from "../src/types.js";

const stores: AgatStore[] = [];
const servers: Server[] = [];

afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => new Promise<void>((resolve) => server.close(() => resolve()))));
  while (stores.length) stores.pop()?.close();
});

function createStore(): AgatStore {
  const store = new AgatStore(":memory:", {
    seedDemo: false,
    credentialsKey: "a2a-test-credentials-key",
  });
  stores.push(store);
  return store;
}

function createEndpoint(
  store: AgatStore,
  overrides: Parameters<AgatStore["createA2AEndpoint"]>[0] = { agentId: "collector" },
  projectId = "default",
) {
  const created = store.createA2AEndpoint({ agentId: "collector", ...overrides }, projectId, "test-user");
  const endpointRecord = created.endpoint as { id: string };
  const endpoint = store.getA2AEndpointConnection(endpointRecord.id, projectId);
  assert.ok(endpoint);
  return { endpoint, accessToken: created.accessToken };
}

function sendRequest(
  messageId: string,
  text = "Проведи краткий анализ",
  contextId = "context-1",
): A2ASendMessageRequest {
  return {
    message: {
      messageId,
      contextId,
      role: "ROLE_USER",
      parts: [{ text, mediaType: "text/plain" }],
    },
    configuration: {
      acceptedOutputModes: ["text/plain"],
      historyLength: 1,
      returnImmediately: true,
    },
  };
}

describe("A2A adapter", () => {
  it("publishes a minimal 1.0 Agent Card without internal prompts, tools or memory", () => {
    const store = createStore();
    const { endpoint } = createEndpoint(store, {
      agentId: "collector",
      name: "Research agent",
      description: "Проверяет факты в локальном контуре",
      skillId: "research.verify",
      skillName: "Fact verification",
      skillDescription: "Возвращает проверенный текст",
      tags: ["research", "local"],
      examples: ["Проверь тезис"],
      inputModes: ["text/plain", "application/json"],
    });

    const card = buildA2AAgentCard(endpoint, "https://agents.example.test") as {
      supportedInterfaces: Array<Record<string, unknown>>;
      capabilities: Record<string, unknown>;
      defaultInputModes: string[];
      defaultOutputModes: string[];
      securityRequirements: unknown[];
      skills: Array<Record<string, unknown>>;
    };
    assert.deepEqual(card.supportedInterfaces, [{
      url: `https://agents.example.test/a2a/v1/endpoints/${endpoint.id}`,
      protocolBinding: "HTTP+JSON",
      protocolVersion: "1.0",
    }]);
    assert.deepEqual(card.capabilities, {
      streaming: true,
      pushNotifications: false,
      extendedAgentCard: false,
    });
    assert.deepEqual(card.defaultInputModes, ["text/plain", "application/json"]);
    assert.deepEqual(card.defaultOutputModes, ["text/plain"]);
    assert.equal(card.securityRequirements.length, 1);
    assert.equal(card.skills[0]?.id, "research.verify");
    const serialized = JSON.stringify(card).toLowerCase();
    assert.equal(serialized.includes("system_prompt"), false);
    assert.equal(serialized.includes("systemprompt"), false);
    assert.equal(serialized.includes("memory_entries"), false);
    assert.equal(serialized.includes("mcp__"), false);
  });

  it("accepts only declared inline text or JSON and rejects files, follow-ups and push config", () => {
    const endpoint: Pick<A2AEndpointConnection, "inputModes" | "maxInputCharacters"> = {
      inputModes: ["text/plain"],
      maxInputCharacters: 2_000,
    };
    const normalized = normalizeA2ASendMessageRequest(sendRequest("message-1"), endpoint);
    assert.equal(normalized.input, "Проведи краткий анализ");
    assert.equal(normalized.contextId, "context-1");

    assert.throws(
      () => normalizeA2ASendMessageRequest({
        message: { messageId: "raw", role: "ROLE_USER", parts: [{ raw: "AAAA", mediaType: "application/pdf" }] },
      }, endpoint),
      (error: unknown) => error instanceof A2AProtocolError && error.reason === "CONTENT_TYPE_NOT_SUPPORTED",
    );
    assert.throws(
      () => normalizeA2ASendMessageRequest({
        message: { messageId: "json", role: "ROLE_USER", parts: [{ data: { secret: true } }] },
      }, endpoint),
      /не принимает application\/json/,
    );
    assert.throws(
      () => normalizeA2ASendMessageRequest({
        message: { ...sendRequest("follow-up").message, taskId: "existing-task" },
      }, endpoint),
      (error: unknown) => error instanceof A2AProtocolError && error.reason === "UNSUPPORTED_OPERATION",
    );
    assert.throws(
      () => normalizeA2ASendMessageRequest({
        ...sendRequest("push"),
        configuration: { taskPushNotificationConfig: { url: "https://callback.invalid" } },
      }, endpoint),
      (error: unknown) => error instanceof A2AProtocolError && error.reason === "PUSH_NOTIFICATION_NOT_SUPPORTED",
    );
    assert.throws(
      () => normalizeA2ASendMessageRequest({
        ...sendRequest("bad-config"),
        configuration: [] as never,
      }, endpoint),
      /configuration должен быть JSON-объектом/,
    );
    assert.throws(
      () => normalizeA2ASendMessageRequest({
        message: {
          ...sendRequest("bad-extensions").message,
          extensions: "https://example.test/ext" as never,
        },
      }, endpoint),
      /message\.extensions должен быть массивом/,
    );
  });

  it("maps a task to the scheduler, preserves idempotency and inherits W3C trace context", () => {
    const store = createStore();
    const { endpoint } = createEndpoint(store);
    const traceparent = "00-11111111111111111111111111111111-2222222222222222-01";
    const normalized = normalizeA2ASendMessageRequest(sendRequest("message-lifecycle"), endpoint);

    const accepted = store.createA2ATask(endpoint, normalized, traceparent) as {
      id: string;
      status: { state: string };
      metadata: { "io.agat.a2a": { traceId: string } };
    };
    assert.equal(accepted.status.state, "TASK_STATE_SUBMITTED");
    assert.equal(accepted.metadata["io.agat.a2a"].traceId, "11111111111111111111111111111111");

    const duplicate = store.createA2ATask(endpoint, normalized, traceparent) as { id: string };
    assert.equal(duplicate.id, accepted.id);
    const conflicting = normalizeA2ASendMessageRequest(sendRequest("message-lifecycle", "Другой текст"), endpoint);
    assert.throws(() => store.createA2ATask(endpoint, conflicting, traceparent), /messageId уже использован/);

    const nodeId = store.registerNode({
      enrollmentToken: "unused",
      name: "a2a-worker",
      platform: "test",
      models: ["test-model"],
    }).id;
    const lease = store.leaseNext(nodeId);
    assert.ok(lease);
    assert.equal(lease.agent.id, endpoint.agentId);
    assert.equal(lease.run.input, "Проведи краткий анализ");
    assert.equal(lease.traceContext.traceId, "11111111111111111111111111111111");
    store.completeLease(nodeId, lease.leaseId, "Проверенный результат");

    const completed = store.getA2ATask(endpoint.id, accepted.id, 1, true) as {
      status: { state: string };
      artifacts: Array<{ parts: Array<{ text: string; mediaType: string }> }>;
      history: Array<{ messageId: string }>;
    };
    assert.equal(completed.status.state, "TASK_STATE_COMPLETED");
    assert.deepEqual(completed.artifacts[0]?.parts, [{ text: "Проверенный результат", mediaType: "text/plain" }]);
    assert.equal(completed.history[0]?.messageId, "message-lifecycle");
    assert.equal(store.cancelA2ATask(endpoint.id, accepted.id).kind, "not_cancelable");
  });

  it("hashes and rotates endpoint tokens and keeps endpoints project-scoped", () => {
    const store = createStore();
    store.createProject({ id: "research", name: "Research" });
    const primary = createEndpoint(store, { agentId: "collector" });
    const research = createEndpoint(store, { agentId: "collector", skillId: "research.collector" }, "research");

    assert.equal(store.authenticateA2AEndpoint(primary.endpoint.id, primary.accessToken)?.projectId, "default");
    assert.equal(store.getA2AEndpointConnection(primary.endpoint.id, "research"), null);
    assert.equal(store.authenticateA2AEndpoint(research.endpoint.id, primary.accessToken), null);
    assert.equal(JSON.stringify(store.getA2ASnapshot("default")).includes(primary.accessToken), false);

    const rotated = store.rotateA2AEndpointToken(primary.endpoint.id, "default", "operator");
    assert.ok(rotated);
    assert.equal(store.authenticateA2AEndpoint(primary.endpoint.id, primary.accessToken), null);
    assert.equal(store.authenticateA2AEndpoint(primary.endpoint.id, rotated.accessToken)?.id, primary.endpoint.id);
  });

  it("exposes approval as AUTH_REQUIRED, enforces active-task limits and supports cancellation and cursors", () => {
    const store = createStore();
    const { endpoint } = createEndpoint(store, {
      agentId: "editor",
      approvalRequired: true,
      maxActiveTasks: 1,
    });
    const first = store.createA2ATask(
      endpoint,
      normalizeA2ASendMessageRequest(sendRequest("approval-1"), endpoint),
      null,
    ) as { id: string; status: { state: string } };
    assert.equal(first.status.state, "TASK_STATE_AUTH_REQUIRED");
    assert.throws(
      () => store.createA2ATask(
        endpoint,
        normalizeA2ASendMessageRequest(sendRequest("approval-2"), endpoint),
        null,
      ),
      /лимит активных A2A tasks/,
    );
    assert.equal(store.cancelA2ATask(endpoint.id, first.id).kind, "cancelled");
    assert.equal(
      (store.getA2ATask(endpoint.id, first.id) as { status: { state: string } }).status.state,
      "TASK_STATE_CANCELED",
    );

    const second = store.createA2ATask(
      endpoint,
      normalizeA2ASendMessageRequest(sendRequest("approval-2", "Вторая задача", "context-2"), endpoint),
      null,
    ) as { id: string };
    assert.equal(store.cancelA2ATask(endpoint.id, second.id).kind, "cancelled");
    const firstPage = store.listA2ATasks(endpoint.id, { pageSize: 1, includeArtifacts: false }) as {
      tasks: unknown[];
      nextPageToken: string;
      totalSize: number;
    };
    assert.equal(firstPage.tasks.length, 1);
    assert.equal(firstPage.totalSize, 2);
    assert.ok(firstPage.nextPageToken);
    const secondPage = store.listA2ATasks(endpoint.id, {
      pageSize: 1,
      pageToken: firstPage.nextPageToken,
      includeArtifacts: false,
    }) as { tasks: unknown[]; totalSize: number };
    assert.equal(secondPage.tasks.length, 1);
    assert.equal(secondPage.totalSize, 2);
  });

  it("serves the HTTP+JSON 1.0 contract with scoped bearer authentication", async () => {
    const store = createStore();
    const { endpoint, accessToken } = createEndpoint(store);
    const config = {
      ...loadConfig(),
      host: "127.0.0.1",
      port: 0,
      serveWeb: false,
      a2aEnabled: true,
      a2aPublicBaseUrl: "https://agents.example.test",
      mcpEnabled: false,
    };
    const server = createCoordinatorServer(config, store);
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", () => resolve());
    });
    servers.push(server);
    const address = server.address();
    assert.ok(address && typeof address === "object");
    const base = `http://127.0.0.1:${address.port}/a2a/v1/endpoints/${endpoint.id}`;

    const cardResponse = await fetch(`${base}/agent-card.json`);
    assert.equal(cardResponse.status, 200);
    assert.equal(cardResponse.headers.get("a2a-version"), "1.0");
    const card = await cardResponse.json() as { supportedInterfaces: Array<{ url: string }> };
    assert.equal(card.supportedInterfaces[0]?.url, `https://agents.example.test/a2a/v1/endpoints/${endpoint.id}`);

    const unauthenticated = await fetch(`${base}/tasks/missing`, {
      headers: { "A2A-Version": "1.0" },
    });
    assert.equal(unauthenticated.status, 401);
    assert.equal(unauthenticated.headers.get("www-authenticate"), 'Bearer realm="agat-a2a"');
    assert.match(unauthenticated.headers.get("content-type") ?? "", /^application\/a2a\+json/);

    const wrongVersion = await fetch(`${base}/tasks/missing`, {
      headers: { authorization: `Bearer ${accessToken}`, "A2A-Version": "0.3" },
    });
    assert.equal(wrongVersion.status, 400);
    assert.equal(
      ((await wrongVersion.json() as { error: { details: Array<{ reason: string }> } }).error.details[0]?.reason),
      "VERSION_NOT_SUPPORTED",
    );

    const sendResponse = await fetch(`${base}/message:send`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${accessToken}`,
        "A2A-Version": "1.0",
        "content-type": "application/a2a+json",
        traceparent: "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
      },
      body: JSON.stringify(sendRequest("http-message")),
    });
    assert.equal(sendResponse.status, 200);
    const sent = await sendResponse.json() as { task: { id: string; status: { state: string } } };
    assert.equal(sent.task.status.state, "TASK_STATE_SUBMITTED");

    const getResponse = await fetch(`${base}/tasks/${sent.task.id}?historyLength=1`, {
      headers: { authorization: `Bearer ${accessToken}`, "A2A-Version": "1.0" },
    });
    assert.equal(getResponse.status, 200);
    assert.equal(((await getResponse.json() as { id: string }).id), sent.task.id);

    const queryVersionResponse = await fetch(`${base}/tasks/${sent.task.id}?A2A-Version=1.0`, {
      headers: { authorization: `Bearer ${accessToken}` },
    });
    assert.equal(queryVersionResponse.status, 200);

    const cancelResponse = await fetch(`${base}/tasks/${sent.task.id}:cancel`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${accessToken}`,
        "A2A-Version": "1.0",
        "content-type": "application/a2a+json",
      },
      body: "{}",
    });
    assert.equal(cancelResponse.status, 200);
    assert.equal(
      ((await cancelResponse.json() as { status: { state: string } }).status.state),
      "TASK_STATE_CANCELED",
    );

    const streamResponse = await fetch(`${base}/message:stream`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${accessToken}`,
        "A2A-Version": "1.0",
        "content-type": "application/a2a+json",
      },
      body: JSON.stringify(sendRequest("http-stream")),
    });
    assert.equal(streamResponse.status, 200);
    assert.match(streamResponse.headers.get("content-type") ?? "", /^text\/event-stream/);
    const reader = streamResponse.body?.getReader();
    assert.ok(reader);
    const firstEvent = await reader.read();
    assert.match(Buffer.from(firstEvent.value ?? []).toString("utf8"), /"task"/);
    const nodeId = store.registerNode({
      enrollmentToken: "unused",
      name: "stream-worker",
      platform: "test",
      models: ["test-model"],
    }).id;
    const lease = store.leaseNext(nodeId);
    assert.ok(lease);
    store.completeLease(nodeId, lease.leaseId, "Streaming result");
    let terminalEvents = "";
    for (let attempt = 0; attempt < 10 && !terminalEvents.includes("TASK_STATE_COMPLETED"); attempt += 1) {
      const next = await reader.read();
      terminalEvents += Buffer.from(next.value ?? []).toString("utf8");
      if (next.done) break;
    }
    assert.match(terminalEvents, /"artifactUpdate"/);
    assert.match(terminalEvents, /"TASK_STATE_COMPLETED"/);
    await reader.cancel();
  });
});
