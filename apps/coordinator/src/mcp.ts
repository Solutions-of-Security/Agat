import { createHash } from "node:crypto";

import {
  Client,
  StreamableHTTPClientTransport,
  type CallToolResult,
  type FetchLike,
  type Tool,
} from "@modelcontextprotocol/client";

import type { CoordinatorTelemetry } from "./telemetry.js";
import type {
  CredentialScope,
  CreateMcpServerInput,
  McpCatalogTool,
  McpDefaultPolicy,
  McpPolicyDecision,
  McpToolCallResponse,
  McpToolPolicy,
  McpToolRisk,
  UpdateMcpServerInput,
} from "./types.js";

export const MCP_PROTOCOL_VERSION = "2026-07-28";
const MCP_CLIENT_VERSION = "1.3.0";

export interface McpGatewayOptions {
  enabled: boolean;
  requestTimeoutSeconds: number;
  maxResponseBytes: number;
  approvalTtlSeconds: number;
}

export interface McpServerConnection {
  id: string;
  projectId: string;
  name: string;
  namespace: string;
  endpoint: string;
  credentialId: string | null;
  credential: null | {
    type: string;
    data: Record<string, string>;
    scope: CredentialScope;
  };
  enabled: boolean;
  trustAnnotations: boolean;
  allowInsecureHttp: boolean;
  defaultPolicy: McpDefaultPolicy;
  catalogTtlSeconds: number;
  catalog: McpCatalogTool[];
}

export interface ResolvedMcpLeaseTool {
  nodeId: string;
  leaseId: string;
  runId: string;
  stageId: string;
  projectId: string;
  traceparent: string | null;
  server: McpServerConnection;
  tool: McpCatalogTool;
  publicName: string;
  risk: McpToolRisk;
  policy: McpToolPolicy;
  decision: McpPolicyDecision;
}

export interface StoredMcpCall {
  callId: string;
  status: McpToolCallResponse["status"];
  result?: unknown;
  error?: string;
  requiredApprovals?: number;
  approvalCount?: number;
  approvers?: string[];
  isNew?: boolean;
}

export interface McpApprovalExecution extends ResolvedMcpLeaseTool {
  callId: string;
  arguments: Record<string, unknown>;
}

export interface McpApprovalResult {
  call: StoredMcpCall;
  execution: McpApprovalExecution | null;
}

export interface McpDecisionActor {
  subject: string;
  display: string;
}

export interface McpGatewayStore {
  listMcpServers(projectId: string): Array<Record<string, unknown>>;
  createMcpServer(input: CreateMcpServerInput, projectId: string): Record<string, unknown>;
  updateMcpServer(id: string, input: UpdateMcpServerInput, projectId: string): Record<string, unknown> | null;
  deleteMcpServer(id: string, projectId: string): boolean;
  setMcpToolPolicy(serverId: string, toolName: string, policy: McpToolPolicy, projectId: string): Record<string, unknown> | null;
  dueMcpServerIds(limit?: number): string[];
  getMcpServerConnection(id: string, projectId?: string): McpServerConnection | null;
  saveMcpCatalog(id: string, tools: McpCatalogTool[], ttlMs: number, cacheScope: string): void;
  saveMcpCatalogError(id: string, message: string): void;
  resolveMcpLeaseTool(nodeId: string, leaseId: string, publicName: string): ResolvedMcpLeaseTool | null;
  createOrGetMcpToolCall(input: {
    resolved: ResolvedMcpLeaseTool;
    clientCallId: string;
    arguments: Record<string, unknown>;
    status: "waiting_approval" | "executing" | "rejected";
    approvalTtlSeconds: number;
    traceparent: string | null;
  }): StoredMcpCall;
  getMcpToolCallForLease(nodeId: string, leaseId: string, callId: string): StoredMcpCall | null;
  approveMcpToolCall(
    callId: string,
    projectId: string,
    actor: McpDecisionActor,
    executionTtlSeconds: number,
  ): McpApprovalResult | null;
  rejectMcpToolCall(callId: string, projectId: string, actor: McpDecisionActor): boolean;
  cancelMcpToolCallForLease(nodeId: string, leaseId: string, callId: string): StoredMcpCall | null;
  completeMcpToolCall(callId: string, result: unknown): void;
  failMcpToolCall(callId: string, message: string): void;
  assertMcpToolCallExecutable(callId: string): void;
}

export interface McpCatalogResult {
  tools: McpCatalogTool[];
  ttlMs: number;
  cacheScope: string;
}

export interface McpUpstreamClient {
  listTools(server: McpServerConnection): Promise<McpCatalogResult>;
  callTool(
    server: McpServerConnection,
    tool: McpCatalogTool,
    argumentsValue: Record<string, unknown>,
    assertAllowed?: () => void,
  ): Promise<unknown>;
}

function boundedInteger(value: unknown, min: number, max: number, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(min, Math.min(max, Math.trunc(value)))
    : fallback;
}

function requiredText(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${field} обязателен`);
  const normalized = value.trim();
  if (normalized.length > maxLength) throw new Error(`${field}: максимум ${maxLength} символов`);
  return normalized;
}

export function normalizeMcpServerInput(
  input: CreateMcpServerInput,
  current?: McpServerConnection,
): Required<Omit<CreateMcpServerInput, "credentialId">> & { credentialId: string | null } {
  const name = requiredText(input.name ?? current?.name, "Название MCP-сервера", 100);
  const namespace = requiredText(input.namespace ?? current?.namespace, "Namespace", 24).toLowerCase();
  if (!/^[a-z][a-z0-9_]{0,23}$/.test(namespace)) {
    throw new Error("Namespace должен начинаться с буквы и содержать только a-z, 0-9 и _");
  }
  const endpoint = requiredText(input.endpoint ?? current?.endpoint, "Endpoint", 2_000);
  let parsed: URL;
  try {
    parsed = new URL(endpoint);
  } catch {
    throw new Error("Некорректный URL MCP endpoint");
  }
  if (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.hash) {
    throw new Error("MCP endpoint должен быть HTTP(S) URL без credentials и fragment");
  }
  for (const key of parsed.searchParams.keys()) {
    if (/(?:access[_-]?token|api[_-]?key|authorization|credential|password|secret|token)/i.test(key)) {
      throw new Error("Секреты MCP нельзя передавать в query-параметрах endpoint");
    }
  }
  const allowInsecureHttp = input.allowInsecureHttp ?? current?.allowInsecureHttp ?? false;
  if (parsed.protocol === "http:" && !allowInsecureHttp) {
    throw new Error("Для HTTP endpoint нужно явно разрешить небезопасный транспорт");
  }
  const defaultPolicy = input.defaultPolicy ?? current?.defaultPolicy ?? "deny";
  if (!(["deny", "approval", "auto"] as const).includes(defaultPolicy)) {
    throw new Error("Неизвестная default policy MCP-сервера");
  }
  const credentialId = input.credentialId === undefined
    ? current?.credentialId ?? null
    : input.credentialId === null || input.credentialId === ""
      ? null
      : requiredText(input.credentialId, "Credentials", 100);
  return {
    name,
    namespace,
    endpoint: parsed.toString(),
    credentialId,
    enabled: input.enabled ?? current?.enabled ?? true,
    trustAnnotations: input.trustAnnotations ?? current?.trustAnnotations ?? false,
    allowInsecureHttp,
    defaultPolicy,
    catalogTtlSeconds: boundedInteger(input.catalogTtlSeconds ?? current?.catalogTtlSeconds, 30, 86_400, 300),
  };
}

export function normalizeMcpServerPatch(
  input: UpdateMcpServerInput,
  current: McpServerConnection,
): ReturnType<typeof normalizeMcpServerInput> {
  return normalizeMcpServerInput({
    name: input.name ?? current.name,
    namespace: input.namespace ?? current.namespace,
    endpoint: input.endpoint ?? current.endpoint,
    credentialId: input.credentialId === undefined ? undefined : input.credentialId,
    enabled: input.enabled ?? current.enabled,
    trustAnnotations: input.trustAnnotations ?? current.trustAnnotations,
    allowInsecureHttp: input.allowInsecureHttp ?? current.allowInsecureHttp,
    defaultPolicy: input.defaultPolicy ?? current.defaultPolicy,
    catalogTtlSeconds: input.catalogTtlSeconds ?? current.catalogTtlSeconds,
  }, current);
}

export function mcpToolRisk(
  annotations: Record<string, unknown> | null,
  trustAnnotations: boolean,
): McpToolRisk {
  if (!trustAnnotations || !annotations) return "unknown";
  if (annotations.destructiveHint === true) return "destructive";
  if (annotations.readOnlyHint === true) return "read";
  if (annotations.idempotentHint === true) return "write";
  return "unknown";
}

export function effectiveMcpPolicy(
  defaultPolicy: McpDefaultPolicy,
  override: McpToolPolicy | null,
  risk: McpToolRisk,
): McpToolPolicy {
  if (override) return override;
  if (defaultPolicy === "deny") return "deny";
  if (defaultPolicy === "approval") return "approval";
  return risk === "read" ? "allow" : "approval";
}

function shortHash(value: string): string {
  return createHash("sha256").update(value).digest("hex").slice(0, 8);
}

export function publicMcpToolName(namespace: string, upstreamName: string): string {
  const safe = upstreamName.replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || "tool";
  const prefix = `${namespace}__`;
  const unchanged = safe === upstreamName;
  if (unchanged && prefix.length + safe.length <= 64) return `${prefix}${safe}`;
  const room = Math.max(1, 64 - prefix.length - 10);
  return `${prefix}${safe.slice(0, room)}_${shortHash(upstreamName)}`;
}

function asObject(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export function normalizeCatalogTools(namespace: string, tools: readonly Tool[]): McpCatalogTool[] {
  const seenNames = new Set<string>();
  const seenPublicNames = new Set<string>();
  const normalized: McpCatalogTool[] = [];
  for (const raw of tools) {
    if (typeof raw.name !== "string" || !raw.name || raw.name.length > 256 || seenNames.has(raw.name)) continue;
    const publicName = publicMcpToolName(namespace, raw.name);
    if (seenPublicNames.has(publicName)) throw new Error(`Коллизия public tool name для ${raw.name}`);
    seenNames.add(raw.name);
    seenPublicNames.add(publicName);
    normalized.push({
      name: raw.name,
      publicName,
      title: typeof raw.title === "string" ? raw.title.slice(0, 200) : null,
      description: typeof raw.description === "string" ? raw.description.slice(0, 8_000) : "",
      inputSchema: asObject(raw.inputSchema) ?? { type: "object", properties: {} },
      outputSchema: asObject(raw.outputSchema),
      annotations: asObject(raw.annotations),
    });
  }
  return normalized.sort((left, right) => left.publicName.localeCompare(right.publicName));
}

export function scopedMcpServerCredential(
  server: McpServerConnection,
  operation: "catalog" | "tool",
  tool?: McpCatalogTool,
): McpServerConnection {
  if (!server.credential) return server;
  const scope = server.credential.scope ?? {
    kind: "project" as const,
    serverNamespaces: [],
    toolPatterns: [],
    risks: ["read", "write", "destructive", "unknown"] as McpToolRisk[],
    allowCatalog: true,
    expiresAt: null,
  };
  if (scope.kind === "mcp") {
    if (scope.expiresAt && scope.expiresAt <= new Date().toISOString()) {
      throw new Error("Scope credentials MCP истёк");
    }
    if (!scope.serverNamespaces.includes(server.namespace)) {
      throw new Error(`Scope credentials не разрешает MCP namespace ${server.namespace}`);
    }
    if (operation === "catalog" && !scope.allowCatalog) return { ...server, credential: null };
    if (operation === "tool") {
      if (!tool) throw new Error("Для проверки scope отсутствует MCP tool");
      const risk = mcpToolRisk(tool.annotations, server.trustAnnotations);
      if (!scope.risks.includes(risk)) throw new Error(`Scope credentials не разрешает риск ${risk}`);
      if (!scope.toolPatterns.some((pattern) => {
        const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replaceAll("*", ".*").replaceAll("?", ".");
        return new RegExp(`^${escaped}$`).test(tool.name);
      })) {
        throw new Error(`Scope credentials не разрешает MCP tool ${tool.name}`);
      }
    }
  }
  return server;
}

function credentialHeaders(server: McpServerConnection, operation: "catalog" | "tool", tool?: McpCatalogTool): HeadersInit {
  const scoped = scopedMcpServerCredential(server, operation, tool);
  if (!scoped.credential) return {};
  if (scoped.credential.type === "api_key") {
    const apiKey = scoped.credential.data.apiKey;
    if (!apiKey) throw new Error("API key MCP-сервера отсутствует");
    return { Authorization: `Bearer ${apiKey}` };
  }
  if (scoped.credential.type === "http_header") {
    const headerName = scoped.credential.data.headerName;
    const headerValue = scoped.credential.data.headerValue;
    if (!headerName || !headerValue) throw new Error("HTTP header credentials MCP-сервера неполны");
    if (isReservedMcpCredentialHeader(headerName)) {
      throw new Error("Этот HTTP-заголовок управляется MCP gateway и не может быть credentials");
    }
    return { [headerName]: headerValue };
  }
  throw new Error("Тип credentials MCP-сервера не поддерживается");
}

const RESERVED_MCP_HEADERS = new Set([
  "accept",
  "connection",
  "content-length",
  "content-type",
  "host",
  "mcp-method",
  "mcp-name",
  "mcp-protocol-version",
  "mcp-session-id",
  "traceparent",
  "tracestate",
  "transfer-encoding",
  "x-mcp-header",
]);

export function isReservedMcpCredentialHeader(name: string): boolean {
  return RESERVED_MCP_HEADERS.has(name.toLowerCase());
}

function boundedFetch(timeoutMs: number, maxResponseBytes: number): FetchLike {
  return async (url, init) => {
    const timeoutSignal = AbortSignal.timeout(timeoutMs);
    const signal = init?.signal
      ? AbortSignal.any([init.signal, timeoutSignal])
      : timeoutSignal;
    const response = await fetch(url, { ...init, signal, redirect: "error" });
    const contentLength = Number.parseInt(response.headers.get("content-length") ?? "", 10);
    if (Number.isFinite(contentLength) && contentLength > maxResponseBytes) {
      await response.body?.cancel();
      throw new Error(`MCP response превышает лимит ${maxResponseBytes} байт`);
    }
    if (!response.body) return response;
    let received = 0;
    const boundedBody = response.body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        received += chunk.byteLength;
        if (received > maxResponseBytes) {
          controller.error(new Error(`MCP response превышает лимит ${maxResponseBytes} байт`));
          return;
        }
        controller.enqueue(chunk);
      },
    }));
    return new Response(boundedBody, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  };
}

export class SdkMcpUpstreamClient implements McpUpstreamClient {
  constructor(private readonly options: Pick<McpGatewayOptions, "requestTimeoutSeconds" | "maxResponseBytes">) {}

  private async useClient<T>(
    server: McpServerConnection,
    credentialOperation: "catalog" | "tool",
    tool: McpCatalogTool | undefined,
    operation: (client: Client) => Promise<T>,
  ): Promise<T> {
    const client = new Client({ name: "agat-mcp-gateway", version: MCP_CLIENT_VERSION }, {
      versionNegotiation: { mode: { pin: MCP_PROTOCOL_VERSION } },
      inputRequired: { autoFulfill: false },
      enforceStrictCapabilities: true,
    });
    const transport = new StreamableHTTPClientTransport(new URL(server.endpoint), {
      requestInit: { headers: credentialHeaders(server, credentialOperation, tool), redirect: "error" },
      fetch: boundedFetch(this.options.requestTimeoutSeconds * 1_000, this.options.maxResponseBytes),
    });
    await client.connect(transport);
    try {
      return await operation(client);
    } finally {
      await client.close().catch(() => undefined);
    }
  }

  async listTools(server: McpServerConnection): Promise<McpCatalogResult> {
    return this.useClient(server, "catalog", undefined, async (client) => {
      const result = await client.listTools(undefined, { cacheMode: "refresh" });
      return {
        tools: normalizeCatalogTools(server.namespace, result.tools),
        ttlMs: boundedInteger(result.ttlMs, 30_000, 86_400_000, server.catalogTtlSeconds * 1_000),
        cacheScope: result.cacheScope === "private" ? "private" : "public",
      };
    });
  }

  async callTool(
    server: McpServerConnection,
    tool: McpCatalogTool,
    argumentsValue: Record<string, unknown>,
    assertAllowed?: () => void,
  ): Promise<CallToolResult> {
    return this.useClient(server, "tool", tool, (client) => {
      assertAllowed?.();
      return client.callTool({
        name: tool.name,
        arguments: argumentsValue,
      }, {
        toolDefinition: {
          name: tool.name,
          ...(tool.title ? { title: tool.title } : {}),
          ...(tool.description ? { description: tool.description } : {}),
          inputSchema: tool.inputSchema as Tool["inputSchema"],
          ...(tool.outputSchema ? { outputSchema: tool.outputSchema } : {}),
          ...(tool.annotations ? { annotations: tool.annotations } : {}),
        },
      });
    });
  }
}

export class McpGateway {
  private readonly upstream: McpUpstreamClient;

  constructor(
    private readonly store: McpGatewayStore,
    private readonly telemetry: CoordinatorTelemetry,
    private readonly options: McpGatewayOptions,
    upstream?: McpUpstreamClient,
  ) {
    this.upstream = upstream ?? new SdkMcpUpstreamClient(options);
  }

  listServers(projectId: string): Array<Record<string, unknown>> {
    return this.store.listMcpServers(projectId);
  }

  async createServer(input: CreateMcpServerInput, projectId: string): Promise<Record<string, unknown>> {
    if (!this.options.enabled) throw new Error("MCP gateway выключен");
    const created = this.store.createMcpServer(input, projectId);
    await this.syncServer(String(created.id), projectId).catch(() => undefined);
    return this.store.listMcpServers(projectId).find((server) => server.id === created.id) ?? created;
  }

  updateServer(id: string, input: UpdateMcpServerInput, projectId: string): Record<string, unknown> | null {
    if (!this.options.enabled) throw new Error("MCP gateway выключен");
    return this.store.updateMcpServer(id, input, projectId);
  }

  deleteServer(id: string, projectId: string): boolean {
    return this.store.deleteMcpServer(id, projectId);
  }

  setToolPolicy(serverId: string, toolName: string, policy: McpToolPolicy, projectId: string): Record<string, unknown> | null {
    if (!(["allow", "approval", "deny"] as const).includes(policy)) throw new Error("Неизвестная policy инструмента");
    return this.store.setMcpToolPolicy(serverId, toolName, policy, projectId);
  }

  async syncServer(id: string, projectId?: string): Promise<Record<string, unknown> | null> {
    if (!this.options.enabled) throw new Error("MCP gateway выключен");
    const server = this.store.getMcpServerConnection(id, projectId);
    if (!server) return null;
    if (!server.enabled) throw new Error("MCP-сервер выключен");
    try {
      const result = await this.upstream.listTools(scopedMcpServerCredential(server, "catalog"));
      this.store.saveMcpCatalog(id, result.tools, result.ttlMs, result.cacheScope);
    } catch (error) {
      this.store.saveMcpCatalogError(id, error instanceof Error ? error.message : "Ошибка синхронизации MCP");
      throw error;
    }
    return this.store.listMcpServers(server.projectId).find((candidate) => candidate.id === id) ?? null;
  }

  async refreshDueServers(): Promise<void> {
    if (!this.options.enabled) return;
    for (const id of this.store.dueMcpServerIds(10)) {
      await this.syncServer(id).catch(() => undefined);
    }
  }

  async callLeaseTool(input: {
    nodeId: string;
    leaseId: string;
    publicName: string;
    clientCallId: string;
    arguments: Record<string, unknown>;
    traceparent?: string | null;
  }): Promise<McpToolCallResponse> {
    if (!this.options.enabled) throw new Error("MCP gateway выключен");
    if (!input.clientCallId || input.clientCallId.length > 200) throw new Error("Некорректный clientCallId");
    if (!input.arguments || typeof input.arguments !== "object" || Array.isArray(input.arguments)) {
      throw new Error("arguments должен быть JSON-объектом");
    }
    const resolved = this.store.resolveMcpLeaseTool(input.nodeId, input.leaseId, input.publicName);
    if (!resolved) throw new Error("MCP-инструмент не разрешён для этой аренды");
    const desiredStatus = resolved.decision.effect === "deny"
      ? "rejected"
      : resolved.decision.effect === "approval"
        ? "waiting_approval"
        : "executing";
    const stored = this.store.createOrGetMcpToolCall({
      resolved,
      clientCallId: input.clientCallId,
      arguments: input.arguments,
      status: desiredStatus,
      approvalTtlSeconds: desiredStatus === "waiting_approval"
        ? this.options.approvalTtlSeconds
        : this.options.requestTimeoutSeconds + 30,
      traceparent: input.traceparent ?? resolved.traceparent,
    });
    if (!stored.isNew || stored.status !== "executing") return stored;
    return this.executeCall({
      ...resolved,
      callId: stored.callId,
      arguments: input.arguments,
      traceparent: input.traceparent ?? resolved.traceparent,
    });
  }

  getLeaseCall(nodeId: string, leaseId: string, callId: string): McpToolCallResponse | null {
    return this.store.getMcpToolCallForLease(nodeId, leaseId, callId);
  }

  cancelLeaseCall(nodeId: string, leaseId: string, callId: string): McpToolCallResponse | null {
    return this.store.cancelMcpToolCallForLease(nodeId, leaseId, callId);
  }

  async decideCall(
    callId: string,
    decision: "approve" | "reject",
    projectId: string,
    actorInput: McpDecisionActor | string,
  ): Promise<McpToolCallResponse> {
    const actor = typeof actorInput === "string"
      ? { subject: actorInput, display: actorInput }
      : actorInput;
    if (decision === "reject") {
      if (!this.store.rejectMcpToolCall(callId, projectId, actor)) throw new Error("MCP approval не найден или уже обработан");
      return { callId, status: "rejected", error: "Вызов отклонён оператором" };
    }
    const approved = this.store.approveMcpToolCall(
      callId,
      projectId,
      actor,
      this.options.requestTimeoutSeconds + 30,
    );
    if (!approved) throw new Error("MCP approval не найден, истёк или уже обработан");
    if (!approved.execution) return approved.call;
    return this.executeCall(approved.execution);
  }

  private async executeCall(execution: McpApprovalExecution): Promise<McpToolCallResponse> {
    try {
      this.store.assertMcpToolCallExecutable(execution.callId);
      const scopedServer = scopedMcpServerCredential(execution.server, "tool", execution.tool);
      const result = await this.telemetry.withMcpRequest({
        serverName: execution.server.name,
        toolName: execution.publicName,
        callId: execution.callId,
        risk: execution.risk,
        riskTier: execution.decision.tier,
        requiredApprovals: execution.decision.approvals,
        policyVersion: execution.decision.policyVersion,
        policySha256: execution.decision.policySha256,
        traceparent: execution.traceparent,
      }, () => this.upstream.callTool(
        scopedServer,
        execution.tool,
        execution.arguments,
        () => this.store.assertMcpToolCallExecutable(execution.callId),
      ));
      this.store.completeMcpToolCall(execution.callId, result);
      return { callId: execution.callId, status: "completed", result };
    } catch (error) {
      const message = error instanceof Error ? error.message.slice(0, 4_000) : "MCP tool call failed";
      this.store.failMcpToolCall(execution.callId, message);
      return this.store.getMcpToolCallForLease(execution.nodeId, execution.leaseId, execution.callId)
        ?? { callId: execution.callId, status: "failed", error: message };
    }
  }
}
