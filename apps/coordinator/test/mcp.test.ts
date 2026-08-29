import assert from "node:assert/strict";
import fs from "node:fs";
import { afterEach, describe, it } from "node:test";
import { DatabaseSync } from "node:sqlite";

import type { Tool } from "@modelcontextprotocol/client";

import { AgatStore } from "../src/database.js";
import {
  McpGateway,
  normalizeCatalogTools,
  SdkMcpUpstreamClient,
  type McpCatalogResult,
  type McpServerConnection,
  type McpUpstreamClient,
} from "../src/mcp.js";
import { CoordinatorTelemetry } from "../src/telemetry.js";

const stores: AgatStore[] = [];
const temporaryFiles: string[] = [];
let restoreFetch: (() => void) | null = null;

afterEach(() => {
  restoreFetch?.();
  restoreFetch = null;
  while (stores.length) stores.pop()?.close();
  while (temporaryFiles.length) fs.rmSync(temporaryFiles.pop()!, { force: true });
});

class FakeMcpUpstream implements McpUpstreamClient {
  calls: Array<{ serverId: string; tool: string; arguments: Record<string, unknown> }> = [];
  tools: Tool[] = [];
  beforeCall: (() => void) | null = null;

  async listTools(server: McpServerConnection): Promise<McpCatalogResult> {
    return {
      tools: normalizeCatalogTools(server.namespace, this.tools),
      ttlMs: 60_000,
      cacheScope: "private",
    };
  }

  async callTool(
    server: McpServerConnection,
    tool: { name: string },
    argumentsValue: Record<string, unknown>,
    assertAllowed?: () => void,
  ): Promise<unknown> {
    this.beforeCall?.();
    assertAllowed?.();
    this.calls.push({ serverId: server.id, tool: tool.name, arguments: argumentsValue });
    return { resultType: "complete", content: [{ type: "text", text: `ok:${tool.name}` }] };
  }
}

function setup() {
  const store = new AgatStore(":memory:", { seedDemo: false, credentialsKey: "mcp-test-key" });
  stores.push(store);
  const upstream = new FakeMcpUpstream();
  const telemetry = new CoordinatorTelemetry({ enabled: false, serviceName: "test", exporterEndpoint: "" });
  const gateway = new McpGateway(store, telemetry, {
    enabled: true,
    requestTimeoutSeconds: 10,
    maxResponseBytes: 1_000_000,
    approvalTtlSeconds: 300,
  }, upstream);
  return { store, upstream, gateway };
}

function addWorkerAndLease(store: AgatStore, projectId = "default") {
  const nodeId = store.registerNode({
    enrollmentToken: "unused",
    name: `mcp-worker-${projectId}`,
    platform: "test",
    models: ["test-model"],
  }).id;
  store.createRun({
    name: "MCP run",
    input: "Use a governed tool",
    approvalRequired: false,
    agentIds: ["collector"],
  }, projectId);
  const lease = store.leaseNext(nodeId);
  assert.ok(lease);
  return { nodeId, lease };
}

function readTool(name = "find_customer"): Tool {
  return {
    name,
    description: "Find a customer",
    inputSchema: { type: "object", properties: { id: { type: "string" } }, required: ["id"] },
    annotations: { readOnlyHint: true },
  };
}

describe("MCP gateway and risk policy", () => {
  it("migrates legacy MCP calls to risk-tier policy snapshots", () => {
    const dbPath = `/tmp/agat-mcp-policy-migration-${Date.now()}-${Math.random().toString(16).slice(2)}.db`;
    temporaryFiles.push(dbPath);
    const legacy = new DatabaseSync(dbPath);
    legacy.exec(`
      CREATE TABLE mcp_tool_calls (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        lease_id TEXT NOT NULL,
        status TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        risk TEXT NOT NULL,
        policy TEXT NOT NULL
      );
      INSERT INTO mcp_tool_calls(
        id, project_id, lease_id, status, expires_at, created_at, risk, policy
      ) VALUES (
        'legacy-call', 'default', 'legacy-lease', 'completed',
        '2026-01-01T00:00:00.000Z', '2025-01-01T00:00:00.000Z',
        'destructive', 'allow'
      );
      PRAGMA user_version = 13;
    `);
    legacy.close();

    const migrated = new AgatStore(dbPath, { seedDemo: false, credentialsKey: "migration-key" });
    stores.push(migrated);
    const row = migrated.db.prepare(`
      SELECT risk_tier, legacy_policy, required_approvals, policy_version,
        policy_sha256, policy_rule_id, policy_reason, preview_diff_json
      FROM mcp_tool_calls WHERE id = 'legacy-call'
    `).get() as Record<string, unknown>;
    assert.deepEqual({ ...row }, {
      risk_tier: "critical",
      legacy_policy: "allow",
      required_approvals: 2,
      policy_version: 0,
      policy_sha256: "",
      policy_rule_id: null,
      policy_reason: "",
      preview_diff_json: "[]",
    });
    const version = migrated.db.prepare("PRAGMA user_version").get() as { user_version: number };
    assert.equal(version.user_version, 17);
  });

  it("rejects endpoint secrets and transport-owned credential headers", async () => {
    const { store, gateway } = setup();
    await assert.rejects(
      gateway.createServer({
        name: "Unsafe endpoint",
        namespace: "unsafe",
        endpoint: "https://mcp.example.test/mcp?auth_token=secret",
      }, "default"),
      /Секреты MCP нельзя передавать/,
    );
    assert.throws(() => store.createCredential({
      name: "Header override",
      type: "http_header",
      data: { headerName: "Mcp-Protocol-Version", headerValue: "legacy" },
    }), /зарезервирован транспортом/);
  });

  it("uses the pinned 2026 Streamable HTTP wire contract through the official SDK", async () => {
    const requests: Array<{ method: string; headers: Record<string, string | string[] | undefined>; body: Record<string, unknown> }> = [];
    const originalFetch = globalThis.fetch;
    restoreFetch = () => {
      globalThis.fetch = originalFetch;
    };
    globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
      const headers = Object.fromEntries(new Headers(init?.headers).entries());
      requests.push({ method: init?.method ?? "", headers, body });
      const method = String(body.method ?? "");
      let result: Record<string, unknown>;
      if (method === "server/discover") {
        result = {
          resultType: "complete",
          ttlMs: 60_000,
          cacheScope: "private",
          supportedVersions: ["2026-07-28"],
          capabilities: { tools: {} },
          _meta: { "io.modelcontextprotocol/serverInfo": { name: "test-mcp", version: "1.0.0" } },
        };
      } else if (method === "tools/list") {
        result = {
          resultType: "complete",
          ttlMs: 45_000,
          cacheScope: "private",
          tools: [readTool("echo")],
        };
      } else if (method === "tools/call") {
        result = {
          resultType: "complete",
          content: [{ type: "text", text: "sdk-ok" }],
        };
      } else {
        return new Response(
          JSON.stringify({ jsonrpc: "2.0", id: body.id, error: { code: -32601, message: "not found" } }),
          { status: 404, headers: { "content-type": "application/json" } },
        );
      }
      return new Response(JSON.stringify({ jsonrpc: "2.0", id: body.id, result }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;
    const connection: McpServerConnection = {
      id: "sdk-server",
      projectId: "default",
      name: "SDK server",
      namespace: "sdk",
      endpoint: "http://mcp-sdk.test/mcp",
      credentialId: "sdk-credential",
      credential: { type: "api_key", data: { apiKey: "sdk-secret" } },
      enabled: true,
      trustAnnotations: true,
      allowInsecureHttp: true,
      defaultPolicy: "auto",
      catalogTtlSeconds: 300,
      catalog: [],
    };
    const sdk = new SdkMcpUpstreamClient({ requestTimeoutSeconds: 5, maxResponseBytes: 100_000 });

    const catalog = await sdk.listTools(connection);
    assert.equal(catalog.tools[0]?.publicName, "sdk__echo");
    const result = await sdk.callTool(connection, catalog.tools[0]!, { id: "42" });
    assert.equal(result.content[0]?.type, "text");
    assert.deepEqual(requests.map((entry) => entry.body.method), [
      "server/discover",
      "tools/list",
      "server/discover",
      "tools/call",
    ]);
    assert.ok(requests.every((entry) => entry.headers.authorization === "Bearer sdk-secret"));
    assert.ok(requests.every((entry) => entry.headers["mcp-protocol-version"] === "2026-07-28"));
    assert.deepEqual(requests.map((entry) => entry.headers["mcp-method"]), [
      "server/discover",
      "tools/list",
      "server/discover",
      "tools/call",
    ]);
    assert.equal(requests.at(-1)?.headers["mcp-name"], "echo");
  });

  it("syncs a deterministic catalog and auto-runs only trusted read tools", async () => {
    const { store, upstream, gateway } = setup();
    upstream.tools = [readTool("z_tool"), readTool("a_tool")];
    const server = await gateway.createServer({
      name: "CRM",
      namespace: "crm",
      endpoint: "http://mcp.internal/mcp",
      allowInsecureHttp: true,
      trustAnnotations: true,
      defaultPolicy: "auto",
    }, "default");

    assert.deepEqual(
      (server.tools as Array<{ publicName: string }>).map((tool) => tool.publicName),
      ["crm__a_tool", "crm__z_tool"],
    );
    const { nodeId, lease } = addWorkerAndLease(store);
    assert.deepEqual(lease.mcpTools.map((tool) => tool.publicName), ["crm__a_tool", "crm__z_tool"]);
    assert.equal(lease.mcpTools[0]?.risk, "read");
    assert.equal(lease.mcpTools[0]?.policy, "allow");

    const result = await gateway.callLeaseTool({
      nodeId,
      leaseId: lease.leaseId,
      publicName: "crm__a_tool",
      clientCallId: "model-call-1",
      arguments: { id: "42" },
    });
    assert.equal(result.status, "completed");
    assert.equal(upstream.calls.length, 1);
    const duplicate = await gateway.callLeaseTool({
      nodeId,
      leaseId: lease.leaseId,
      publicName: "crm__a_tool",
      clientCallId: "model-call-1",
      arguments: { id: "changed" },
    });
    assert.equal(duplicate.status, "completed");
    assert.equal(upstream.calls.length, 1, "lease/client call id must prevent duplicate execution");
  });

  it("treats untrusted annotations as unknown and executes only after operator approval", async () => {
    const { store, upstream, gateway } = setup();
    upstream.tools = [readTool()];
    await gateway.createServer({
      name: "Untrusted CRM",
      namespace: "untrusted",
      endpoint: "http://mcp.internal/mcp",
      allowInsecureHttp: true,
      trustAnnotations: false,
      defaultPolicy: "auto",
    }, "default");
    const { nodeId, lease } = addWorkerAndLease(store);
    assert.equal(lease.mcpTools[0]?.risk, "unknown");
    assert.equal(lease.mcpTools[0]?.policy, "approval");

    const pending = await gateway.callLeaseTool({
      nodeId,
      leaseId: lease.leaseId,
      publicName: "untrusted__find_customer",
      clientCallId: "approval-call",
      arguments: { id: "customer-7", apiToken: "must-not-leak" },
    });
    assert.equal(pending.status, "waiting_approval");
    assert.equal(upstream.calls.length, 0);
    const overview = store.getOverview();
    const approval = (overview.approvals as Array<Record<string, unknown>>).find((item) => item.kind === "mcp_tool");
    assert.ok(approval);
    assert.equal(JSON.stringify(approval).includes("must-not-leak"), false);

    const completed = await gateway.decideCall(pending.callId, "approve", "default", "operator@example.test");
    assert.equal(completed.status, "completed");
    assert.equal(upstream.calls.length, 1);
    assert.deepEqual(upstream.calls[0]?.arguments, { id: "customer-7", apiToken: "must-not-leak" });
    const stored = store.db.prepare("SELECT arguments_blob, arguments_summary_json, result_blob FROM mcp_tool_calls WHERE id = ?")
      .get(pending.callId) as { arguments_blob: string; arguments_summary_json: string; result_blob: string };
    assert.equal(stored.arguments_blob.includes("must-not-leak"), false);
    assert.equal(stored.result_blob.includes("ok:find_customer"), false);
    assert.equal(stored.arguments_summary_json.includes("must-not-leak"), false);
    assert.equal(stored.arguments_summary_json.includes("[redacted]"), true);
  });

  it("rejects an approval without contacting the upstream", async () => {
    const { store, upstream, gateway } = setup();
    upstream.tools = [readTool()];
    await gateway.createServer({
      name: "Approval CRM",
      namespace: "approval",
      endpoint: "http://mcp.internal/mcp",
      allowInsecureHttp: true,
      defaultPolicy: "approval",
    }, "default");
    const { nodeId, lease } = addWorkerAndLease(store);
    const pending = await gateway.callLeaseTool({
      nodeId,
      leaseId: lease.leaseId,
      publicName: "approval__find_customer",
      clientCallId: "reject-call",
      arguments: { id: "7" },
    });

    const rejected = await gateway.decideCall(pending.callId, "reject", "default", "operator");
    assert.equal(rejected.status, "rejected");
    assert.equal(upstream.calls.length, 0);
    assert.equal(gateway.getLeaseCall(nodeId, lease.leaseId, pending.callId)?.status, "rejected");
  });

  it("cancels an orphaned approval and prevents any later operator execution", async () => {
    const { store, upstream, gateway } = setup();
    upstream.tools = [readTool()];
    await gateway.createServer({
      name: "Cancelable CRM",
      namespace: "cancelable",
      endpoint: "http://mcp.internal/mcp",
      allowInsecureHttp: true,
      defaultPolicy: "approval",
    }, "default");
    const { nodeId, lease } = addWorkerAndLease(store);
    const pending = await gateway.callLeaseTool({
      nodeId,
      leaseId: lease.leaseId,
      publicName: "cancelable__find_customer",
      clientCallId: "cancel-call",
      arguments: { id: "7" },
    });

    const cancelled = gateway.cancelLeaseCall(nodeId, lease.leaseId, pending.callId);
    assert.equal(cancelled?.status, "expired");
    await assert.rejects(
      gateway.decideCall(pending.callId, "approve", "default", "late-operator"),
      /не найден, истёк/,
    );
    assert.equal(upstream.calls.length, 0);
  });

  it("will not complete a lease while an approved MCP call is still executing", async () => {
    const { store, upstream, gateway } = setup();
    upstream.tools = [readTool()];
    await gateway.createServer({
      name: "Running CRM",
      namespace: "running",
      endpoint: "http://mcp.internal/mcp",
      allowInsecureHttp: true,
      defaultPolicy: "approval",
    }, "default");
    const { nodeId, lease } = addWorkerAndLease(store);
    const pending = await gateway.callLeaseTool({
      nodeId,
      leaseId: lease.leaseId,
      publicName: "running__find_customer",
      clientCallId: "running-call",
      arguments: { id: "7" },
    });
    const execution = store.approveMcpToolCall(pending.callId, "default", "operator", 90);
    assert.ok(execution);
    assert.throws(
      () => store.completeLease(nodeId, lease.leaseId, "premature"),
      /MCP-вызов ещё выполняется/,
    );
    store.failMcpToolCall(pending.callId, "test cleanup");
    assert.doesNotThrow(() => store.completeLease(nodeId, lease.leaseId, "safe"));
  });

  it("keeps servers project-scoped and never exposes upstream credentials in a lease", async () => {
    const { store, upstream, gateway } = setup();
    upstream.tools = [readTool()];
    store.createProject({ id: "team-b", name: "Team B" });
    const credential = store.createCredential({
      name: "MCP bearer",
      type: "api_key",
      data: { apiKey: "top-secret-mcp-key" },
    });
    await gateway.createServer({
      name: "Private CRM",
      namespace: "private_crm",
      endpoint: "http://mcp.internal/mcp",
      allowInsecureHttp: true,
      credentialId: String(credential.id),
      trustAnnotations: true,
      defaultPolicy: "auto",
    }, "default");

    assert.equal(gateway.listServers("team-b").length, 0);
    const { lease } = addWorkerAndLease(store);
    assert.equal(JSON.stringify(lease).includes("top-secret-mcp-key"), false);
    assert.equal(JSON.stringify(gateway.listServers("default")).includes("top-secret-mcp-key"), false);
    assert.throws(() => store.deleteCredential(String(credential.id)), /MCP-сервером/);
  });

  it("disables stage retry after a non-read MCP call with an uncertain side effect", async () => {
    const { store, upstream, gateway } = setup();
    upstream.tools = [{
      name: "send_message",
      description: "Send message",
      inputSchema: { type: "object", properties: { text: { type: "string" } } },
    }];
    const server = await gateway.createServer({
      name: "Messenger",
      namespace: "msg",
      endpoint: "http://mcp.internal/mcp",
      allowInsecureHttp: true,
      defaultPolicy: "deny",
    }, "default");
    gateway.setToolPolicy(String(server.id), "send_message", "allow", "default");
    const { nodeId, lease } = addWorkerAndLease(store);
    const pending = await gateway.callLeaseTool({
      nodeId,
      leaseId: lease.leaseId,
      publicName: "msg__send_message",
      clientCallId: "write-call",
      arguments: { text: "hello" },
    });
    assert.equal(pending.status, "waiting_approval");
    const called = await gateway.decideCall(pending.callId, "approve", "default", "operator");
    assert.equal(called.status, "completed");

    const failed = store.failLease(nodeId, lease.leaseId, "model failed after tool call");
    assert.equal(failed.retrying, false);
    assert.equal(store.getRun(lease.run.id)?.status, "failed");
  });

  it("requires two distinct identities for destructive side effects and stores a redacted preview diff", async () => {
    const { store, upstream, gateway } = setup();
    upstream.tools = [{
      name: "delete_customer",
      description: "Delete customer",
      inputSchema: { type: "object", properties: {} },
      annotations: { destructiveHint: true },
    }];
    await gateway.createServer({
      name: "Critical CRM",
      namespace: "critical",
      endpoint: "http://mcp.internal/mcp",
      allowInsecureHttp: true,
      trustAnnotations: true,
      defaultPolicy: "auto",
    }, "default");
    const { nodeId, lease } = addWorkerAndLease(store);
    const pending = await gateway.callLeaseTool({
      nodeId,
      leaseId: lease.leaseId,
      publicName: "critical__delete_customer",
      clientCallId: "critical-call",
      arguments: {
        before: { status: "active", apiToken: "before-secret" },
        after: { status: "deleted", apiToken: "after-secret" },
      },
    });
    assert.equal(pending.status, "waiting_approval");
    assert.equal(pending.requiredApprovals, 2);

    const first = await gateway.decideCall(pending.callId, "approve", "default", {
      subject: "oidc-subject-a",
      display: "operator-a",
    });
    assert.equal(first.status, "waiting_approval");
    assert.equal(first.approvalCount, 1);
    assert.equal(upstream.calls.length, 0);
    await assert.rejects(
      gateway.decideCall(pending.callId, "approve", "default", {
        subject: "oidc-subject-a",
        display: "operator-a-renamed",
      }),
      /нужен другой approver/,
    );

    const completed = await gateway.decideCall(pending.callId, "approve", "default", {
      subject: "oidc-subject-b",
      display: "operator-b",
    });
    assert.equal(completed.status, "completed");
    assert.equal(upstream.calls.length, 1);
    const recent = store.listRecentMcpToolCalls("default");
    const call = recent.find((item) => item.callId === pending.callId)!;
    assert.equal(call.riskTier, "critical");
    assert.equal(call.requiredApprovals, 2);
    assert.deepEqual(call.approvers, ["operator-a", "operator-b"]);
    assert.equal(JSON.stringify(call.previewDiff).includes("secret"), false);
    assert.match(JSON.stringify(call.previewDiff), /replace/);
  });

  it("previews and activates immutable project policy versions with optimistic concurrency", async () => {
    const { store, upstream, gateway } = setup();
    upstream.tools = [readTool()];
    await gateway.createServer({
      name: "Policy CRM",
      namespace: "policycrm",
      endpoint: "http://mcp.internal/mcp",
      allowInsecureHttp: true,
      trustAnnotations: true,
      defaultPolicy: "auto",
    }, "default");
    const current = store.getMcpPolicySnapshot("default");
    const candidate = {
      ...current.document,
      name: "deny-customer-lookup",
      rules: [{
        id: "deny_customer_lookup",
        description: "Policy test",
        match: { serverNamespaces: ["policycrm"], toolPatterns: ["find_*"] , risks: ["read"] },
        decision: { tier: "high", effect: "deny", approvals: 0 },
      }],
    };
    const preview = store.previewMcpPolicy(candidate, "default");
    assert.equal(preview.changed, true);
    assert.equal(preview.summary.toolsChanged, 1);
    assert.equal(preview.summary.newlyDenied, 1);
    assert.throws(() => store.activateMcpPolicy(candidate, "stale", "default", {
      subject: "designer-subject",
      display: "designer",
    }), /preview повторно/);
    const activated = store.activateMcpPolicy(candidate, preview.baseSha256, "default", {
      subject: "designer-subject",
      display: "designer",
    });
    assert.equal(activated.version, 1);
    assert.equal(activated.sha256, preview.candidateSha256);
    const server = gateway.listServers("default")[0] as { tools: Array<{ policy: string; policyVersion: number }> };
    assert.equal(server.tools[0]?.policy, "deny");
    assert.equal(server.tools[0]?.policyVersion, 1);
  });

  it("central emergency deny rejects pending and stale-lease calls without contacting upstream", async () => {
    const { store, upstream, gateway } = setup();
    upstream.tools = [readTool()];
    await gateway.createServer({
      name: "Emergency CRM",
      namespace: "emergency",
      endpoint: "http://mcp.internal/mcp",
      allowInsecureHttp: true,
      defaultPolicy: "approval",
    }, "default");
    const { nodeId, lease } = addWorkerAndLease(store);
    const pending = await gateway.callLeaseTool({
      nodeId,
      leaseId: lease.leaseId,
      publicName: "emergency__find_customer",
      clientCallId: "pending-before-kill",
      arguments: { id: "7" },
    });
    assert.equal(pending.status, "waiting_approval");
    const state = store.setMcpEmergencyDeny(true, "credential compromise", {
      subject: "admin-subject",
      display: "admin",
    });
    assert.equal(state.enabled, true);
    assert.equal(state.pendingCallsDenied, 1);
    assert.equal(gateway.getLeaseCall(nodeId, lease.leaseId, pending.callId)?.status, "rejected");

    const blocked = await gateway.callLeaseTool({
      nodeId,
      leaseId: lease.leaseId,
      publicName: "emergency__find_customer",
      clientCallId: "stale-lease-after-kill",
      arguments: { id: "8" },
    });
    assert.equal(blocked.status, "rejected");
    assert.match(blocked.error ?? "", /emergency-deny/);
    assert.equal(upstream.calls.length, 0);
  });

  it("rechecks emergency deny in the final preflight immediately before the upstream tool call", async () => {
    const { store, upstream, gateway } = setup();
    upstream.tools = [readTool()];
    await gateway.createServer({
      name: "Preflight CRM",
      namespace: "preflight",
      endpoint: "http://mcp.internal/mcp",
      allowInsecureHttp: true,
      trustAnnotations: true,
      defaultPolicy: "auto",
    }, "default");
    const { nodeId, lease } = addWorkerAndLease(store);
    upstream.beforeCall = () => {
      store.setMcpEmergencyDeny(true, "race before tools/call", {
        subject: "admin-subject",
        display: "admin",
      });
    };
    const blocked = await gateway.callLeaseTool({
      nodeId,
      leaseId: lease.leaseId,
      publicName: "preflight__find_customer",
      clientCallId: "preflight-kill",
      arguments: { id: "9" },
    });
    assert.equal(blocked.status, "rejected");
    assert.match(blocked.error ?? "", /emergency-deny/);
    assert.equal(upstream.calls.length, 0);
  });

  it("enforces MCP credential scopes at binding and before an upstream call", async () => {
    const { store, upstream, gateway } = setup();
    upstream.tools = [readTool(), {
      name: "delete_customer",
      inputSchema: { type: "object", properties: {} },
      annotations: { destructiveHint: true },
    }];
    const credential = store.createCredential({
      name: "Scoped CRM token",
      type: "api_key",
      data: { apiKey: "scoped-secret" },
      scope: {
        kind: "mcp",
        serverNamespaces: ["scopedcrm"],
        toolPatterns: ["find_*"],
        risks: ["read"],
        allowCatalog: true,
        expiresAt: null,
      },
    });
    await assert.rejects(gateway.createServer({
      name: "Wrong namespace",
      namespace: "wrong",
      endpoint: "http://mcp.internal/mcp",
      allowInsecureHttp: true,
      credentialId: String(credential.id),
    }, "default"), /Scope credentials не разрешает/);
    await gateway.createServer({
      name: "Scoped CRM",
      namespace: "scopedcrm",
      endpoint: "http://mcp.internal/mcp",
      allowInsecureHttp: true,
      credentialId: String(credential.id),
      trustAnnotations: true,
      defaultPolicy: "auto",
    }, "default");
    const { nodeId, lease } = addWorkerAndLease(store);
    const allowed = await gateway.callLeaseTool({
      nodeId,
      leaseId: lease.leaseId,
      publicName: "scopedcrm__find_customer",
      clientCallId: "scoped-read",
      arguments: { id: "1" },
    });
    assert.equal(allowed.status, "completed");
    assert.equal(upstream.calls.length, 1);

    const server = gateway.listServers("default")[0] as { id: string };
    gateway.setToolPolicy(server.id, "delete_customer", "allow", "default");
    const destructive = await gateway.callLeaseTool({
      nodeId,
      leaseId: lease.leaseId,
      publicName: "scopedcrm__delete_customer",
      clientCallId: "scoped-delete",
      arguments: {},
    });
    await gateway.decideCall(destructive.callId, "approve", "default", { subject: "a", display: "a" });
    const deniedByScope = await gateway.decideCall(destructive.callId, "approve", "default", { subject: "b", display: "b" });
    assert.equal(deniedByScope.status, "failed");
    assert.match(deniedByScope.error ?? "", /Scope credentials/);
    assert.equal(upstream.calls.length, 1);
  });
});
