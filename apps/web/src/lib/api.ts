import type {
  A2AEndpoint,
  A2AEndpointSecret,
  A2ARemote,
  A2AOutboundInvocationResponse,
  A2ASnapshot,
  Agent,
  ApprovalDecisionInput,
  AuthUser,
  ComputeNode,
  CreateCredentialRequest,
  CreateEvalExperimentRequest,
  CreateGoldenDatasetRequest,
  CreateKnowledgeCollectionRequest,
  CredentialSummary,
  EdgeControlCommand,
  CreateAgentRequest,
  CreateProcessRequest,
  CreateRunRequest,
  LaunchLocalWorkersRequest,
  IngestKnowledgeDocumentRequest,
  KnowledgeCollection,
  KnowledgeDocument,
  KnowledgeSnapshot,
  EvalSnapshot,
  FleetSnapshot,
  GoldenDataset,
  GoldenEvalExperiment,
  LocalWorkerLauncherSnapshot,
  LocalWorkerPool,
  McpServer,
  McpEmergencyDenyState,
  McpPolicyDocument,
  McpPolicyPreview,
  McpPolicySnapshot,
  McpToolPolicy,
  ModelRouterPolicy,
  Overview,
  ProcessDefinition,
  ProcessTemplateCatalog,
  ProcessGraphNode,
  ProcessInstance,
  ProcessSchedule,
  ProcessVersionDiff,
  ProcessVersionDocument,
  ProcessWebhook,
  ProcessWebhookSecret,
  ProjectSummary,
  ProjectFleetPolicy,
  RegisterWorkerReleaseRequest,
  ReplayRunRequest,
  ReplayRunResponse,
  PromptRegistryEntry,
  RunTrace,
  SaveMemoryRequest,
  SaveA2AEndpointRequest,
  SaveA2ARemoteRequest,
  UpdateA2ARemoteRequest,
  InvokeA2ARemoteRequest,
  MemoryEntry,
  SchedulerMode,
  SaveMcpServerRequest,
  StartProcessRequest,
  SaveProcessScheduleRequest,
  TestProcessNodeResult,
  UpdateProcessRequest,
} from "../types";
import { accessToken, activeProjectId, oidcEnabled } from "./auth";

const API_ROOT = "/api/v1";
const TOKEN_KEY = "agat.admin-token.v1";
type AdminTokenListener = (required: boolean) => void;
const adminTokenListeners = new Set<AdminTokenListener>();
let pendingAdminToken: Promise<string | null> | null = null;
let resolvePendingAdminToken: ((token: string | null) => void) | null = null;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.ok) {
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }
  const body = (await response.json().catch(() => ({ error: response.statusText }))) as { error?: string };
  throw new ApiError(response.status, body.error ?? "Ошибка запроса");
}

function notifyAdminTokenListeners(required: boolean) {
  for (const listener of adminTokenListeners) listener(required);
}

function requestAdminToken(): Promise<string | null> {
  if (pendingAdminToken) return pendingAdminToken;
  pendingAdminToken = new Promise<string | null>((resolve) => {
    resolvePendingAdminToken = resolve;
  });
  notifyAdminTokenListeners(true);
  return pendingAdminToken;
}

export function subscribeToAdminTokenRequests(listener: AdminTokenListener): () => void {
  adminTokenListeners.add(listener);
  if (pendingAdminToken) listener(true);
  return () => adminTokenListeners.delete(listener);
}

export function provideAdminToken(token: string | null): void {
  const normalized = token?.trim() ?? "";
  if (normalized) window.localStorage.setItem(TOKEN_KEY, normalized);
  else if (token !== null) window.localStorage.removeItem(TOKEN_KEY);
  const resolve = resolvePendingAdminToken;
  resolvePendingAdminToken = null;
  pendingAdminToken = null;
  notifyAdminTokenListeners(false);
  resolve?.(normalized || null);
}

async function authorizedFetch(path: string, init?: RequestInit, retryAuth = true): Promise<Response> {
  const token = window.localStorage.getItem(TOKEN_KEY);
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  const bearer = await accessToken();
  if (bearer) headers.set("authorization", `Bearer ${bearer}`);
  else if (token) headers.set("x-agat-admin-token", token);
  headers.set("x-agat-project-id", activeProjectId());

  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers });
  if (response.status === 401 && retryAuth && !oidcEnabled()) {
    window.localStorage.removeItem(TOKEN_KEY);
    const entered = await requestAdminToken();
    if (entered) return authorizedFetch(path, init, false);
  }
  return response;
}

async function request<T>(path: string, init?: RequestInit, retryAuth = true): Promise<T> {
  const response = await authorizedFetch(path, init, retryAuth);
  return parseResponse<T>(response);
}

async function requestBlob(path: string): Promise<Blob> {
  const response = await authorizedFetch(path);
  if (!response.ok) return parseResponse<never>(response);
  return response.blob();
}

export const api = {
  me: () => request<AuthUser>("/auth/me", undefined, false),
  projects: () => request<{ projects: ProjectSummary[]; activeProjectId: string }>("/projects", undefined, false),
  createProject: (name: string, id?: string) => request<ProjectSummary>("/projects", {
    method: "POST",
    body: JSON.stringify({ name, id }),
  }),
  fleet: (signal?: AbortSignal) => request<FleetSnapshot>("/fleet", { signal }),
  updateFleetPolicy: (payload: Omit<ProjectFleetPolicy, "revision"> & { expectedRevision: number }) =>
    request<ProjectFleetPolicy>("/fleet/project-policy", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  registerWorkerRelease: (payload: RegisterWorkerReleaseRequest) =>
    request<FleetSnapshot["releases"][number]>("/fleet/releases", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  revokeWorkerRelease: (releaseId: string, reason: string) =>
    request<void>(`/fleet/releases/${encodeURIComponent(releaseId)}/revoke`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  updateWorkerRollout: (payload: {
    releaseId: string;
    region: string;
    ring: string;
    percentage: number;
    expectedRevision: number;
  }) => request<FleetSnapshot["rollouts"][number]>("/fleet/rollouts", {
    method: "PUT",
    body: JSON.stringify(payload),
  }),
  setNodeRolloutRing: (nodeId: string, ring: string) =>
    request<ComputeNode>(`/fleet/nodes/${encodeURIComponent(nodeId)}/ring`, {
      method: "PATCH",
      body: JSON.stringify({ ring }),
    }),
  overview: (signal?: AbortSignal) => request<Overview>("/overview", { signal }, false),
  a2a: (signal?: AbortSignal) => request<A2ASnapshot>("/a2a", { signal }),
  createA2AEndpoint: (payload: SaveA2AEndpointRequest) =>
    request<A2AEndpointSecret>("/a2a/endpoints", { method: "POST", body: JSON.stringify(payload) }),
  updateA2AEndpoint: (endpointId: string, payload: SaveA2AEndpointRequest) =>
    request<A2AEndpoint>(`/a2a/endpoints/${encodeURIComponent(endpointId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteA2AEndpoint: (endpointId: string) =>
    request<void>(`/a2a/endpoints/${encodeURIComponent(endpointId)}`, { method: "DELETE" }),
  rotateA2AEndpointToken: (endpointId: string) =>
    request<A2AEndpointSecret>(`/a2a/endpoints/${encodeURIComponent(endpointId)}/token/rotate`, { method: "POST" }),
  createA2ARemote: (payload: SaveA2ARemoteRequest) =>
    request<A2ARemote>("/a2a/remotes", { method: "POST", body: JSON.stringify(payload) }),
  updateA2ARemote: (remoteId: string, payload: UpdateA2ARemoteRequest) =>
    request<A2ARemote>(`/a2a/remotes/${encodeURIComponent(remoteId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteA2ARemote: (remoteId: string) =>
    request<void>(`/a2a/remotes/${encodeURIComponent(remoteId)}`, { method: "DELETE" }),
  invokeA2ARemote: (remoteId: string, payload: InvokeA2ARemoteRequest) =>
    request<A2AOutboundInvocationResponse>(`/a2a/remotes/${encodeURIComponent(remoteId)}/message:send`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  evals: (signal?: AbortSignal) => request<EvalSnapshot>("/evals", { signal }),
  evalExperiment: (experimentId: string, signal?: AbortSignal) =>
    request<GoldenEvalExperiment>(`/evals/experiments/${encodeURIComponent(experimentId)}`, { signal }),
  createPrompt: (payload: { name: string; description?: string; agentId?: string | null; content: string }) =>
    request<PromptRegistryEntry>("/evals/prompts", { method: "POST", body: JSON.stringify(payload) }),
  createPromptVersion: (promptId: string, payload: { content: string; changeNote?: string }) =>
    request<PromptRegistryEntry>(`/evals/prompts/${encodeURIComponent(promptId)}/versions`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  promotePrompt: (promptId: string, payload: { version: number; model: string | null; experimentId: string }) =>
    request<PromptRegistryEntry>(`/evals/prompts/${encodeURIComponent(promptId)}/promote`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  createEvalDataset: (payload: CreateGoldenDatasetRequest) =>
    request<GoldenDataset>("/evals/datasets", { method: "POST", body: JSON.stringify(payload) }),
  createEvalDatasetVersion: (datasetId: string, payload: CreateGoldenDatasetRequest) =>
    request<GoldenDataset>(`/evals/datasets/${encodeURIComponent(datasetId)}/versions`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  createEvalExperiment: (payload: CreateEvalExperimentRequest) =>
    request<GoldenEvalExperiment>("/evals/experiments", { method: "POST", body: JSON.stringify(payload) }),
  reviewEvalItem: (
    itemId: string,
    payload: { scores?: Record<string, number>; overallScore?: number; rationale: string },
  ) => request<GoldenEvalExperiment>(`/evals/items/${encodeURIComponent(itemId)}/reviews`, {
    method: "POST",
    body: JSON.stringify(payload),
  }),
  judgeEvalExperiment: (experimentId: string, model: string) =>
    request<GoldenEvalExperiment>(`/evals/experiments/${encodeURIComponent(experimentId)}/judge`, {
      method: "POST",
      body: JSON.stringify({ model }),
    }),
  knowledge: (signal?: AbortSignal) => request<KnowledgeSnapshot>("/knowledge", { signal }),
  createKnowledgeCollection: (payload: CreateKnowledgeCollectionRequest) =>
    request<KnowledgeCollection>("/knowledge/collections", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  ingestKnowledgeDocument: (collectionId: string, payload: IngestKnowledgeDocumentRequest) =>
    request<KnowledgeDocument>(`/knowledge/collections/${encodeURIComponent(collectionId)}/documents`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteKnowledgeCollection: (collectionId: string) =>
    request<void>(`/knowledge/collections/${encodeURIComponent(collectionId)}`, { method: "DELETE" }),
  deleteKnowledgeDocument: (documentId: string) =>
    request<void>(`/knowledge/documents/${encodeURIComponent(documentId)}`, { method: "DELETE" }),
  saveMemory: (payload: SaveMemoryRequest) =>
    request<MemoryEntry>("/knowledge/memory", { method: "POST", body: JSON.stringify(payload) }),
  deleteMemory: (memoryId: string) =>
    request<void>(`/knowledge/memory/${encodeURIComponent(memoryId)}`, { method: "DELETE" }),
  exportKnowledge: () => requestBlob("/knowledge/export"),
  createRun: (payload: CreateRunRequest) =>
    request<{ id: string; status: string }>("/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  runTrace: (runId: string, signal?: AbortSignal) =>
    request<RunTrace>(`/runs/${encodeURIComponent(runId)}/trace`, { signal }),
  replayRun: (runId: string, payload: ReplayRunRequest) =>
    request<ReplayRunResponse>(`/runs/${encodeURIComponent(runId)}/replay`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  cancelRun: (runId: string) =>
    request<void>(`/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }),
  downloadArtifact: (artifactId: string) =>
    requestBlob(`/artifacts/${encodeURIComponent(artifactId)}/download`),
  localWorkers: (signal?: AbortSignal) =>
    request<LocalWorkerLauncherSnapshot>("/local-workers", { signal }),
  launchLocalWorkers: (payload: LaunchLocalWorkersRequest) =>
    request<LocalWorkerPool>("/local-workers", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  startLocalWorkerPool: (poolId: string) =>
    request<LocalWorkerPool>(`/local-workers/${encodeURIComponent(poolId)}/start`, { method: "POST" }),
  stopLocalWorkerPool: (poolId: string) =>
    request<LocalWorkerPool>(`/local-workers/${encodeURIComponent(poolId)}/stop`, { method: "POST" }),
  deleteLocalWorkerPool: (poolId: string) =>
    request<LocalWorkerPool>(`/local-workers/${encodeURIComponent(poolId)}`, { method: "DELETE" }),
  createAgent: (payload: CreateAgentRequest) =>
    request<Agent>("/agents", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateAgent: (agentId: string, payload: CreateAgentRequest) =>
    request<Agent>(`/agents/${encodeURIComponent(agentId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  credentials: () => request<{ credentials: CredentialSummary[] }>("/credentials"),
  createCredential: (payload: CreateCredentialRequest) =>
    request<CredentialSummary>("/credentials", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateCredential: (credentialId: string, payload: CreateCredentialRequest) =>
    request<CredentialSummary>(`/credentials/${encodeURIComponent(credentialId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteCredential: (credentialId: string) =>
    request<void>(`/credentials/${encodeURIComponent(credentialId)}`, { method: "DELETE" }),
  remoteWipeNode: (nodeId: string, reason: string) =>
    request<EdgeControlCommand>(`/nodes/${encodeURIComponent(nodeId)}/remote-wipe`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  createMcpServer: (payload: SaveMcpServerRequest) =>
    request<McpServer>("/mcp/servers", { method: "POST", body: JSON.stringify(payload) }),
  updateMcpServer: (serverId: string, payload: SaveMcpServerRequest) =>
    request<McpServer>(`/mcp/servers/${encodeURIComponent(serverId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteMcpServer: (serverId: string) =>
    request<void>(`/mcp/servers/${encodeURIComponent(serverId)}`, { method: "DELETE" }),
  syncMcpServer: (serverId: string) =>
    request<McpServer>(`/mcp/servers/${encodeURIComponent(serverId)}/sync`, { method: "POST" }),
  setMcpToolPolicy: (serverId: string, toolName: string, policy: McpToolPolicy) =>
    request<McpServer>(`/mcp/servers/${encodeURIComponent(serverId)}/tools/${encodeURIComponent(toolName)}`, {
      method: "PATCH",
      body: JSON.stringify({ policy }),
    }),
  decideMcpToolCall: (callId: string, decision: ApprovalDecisionInput) =>
    request<unknown>(`/mcp/tool-calls/${encodeURIComponent(callId)}/decision`, {
      method: "POST",
      body: JSON.stringify(decision),
    }),
  previewMcpPolicy: (document: McpPolicyDocument) =>
    request<McpPolicyPreview>("/mcp/policy/preview", {
      method: "POST",
      body: JSON.stringify({ document }),
    }),
  activateMcpPolicy: (document: McpPolicyDocument, baseSha256: string) =>
    request<McpPolicySnapshot>("/mcp/policy", {
      method: "PUT",
      body: JSON.stringify({ document, baseSha256 }),
    }),
  setMcpEmergencyDeny: (enabled: boolean, reason: string) =>
    request<McpEmergencyDenyState>("/mcp/emergency-deny", {
      method: "PUT",
      body: JSON.stringify({ enabled, reason }),
    }),
  processTemplates: () => request<ProcessTemplateCatalog>("/process-templates"),
  createProcess: (payload: CreateProcessRequest) =>
    request<ProcessDefinition>("/processes", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateProcess: (processId: string, payload: UpdateProcessRequest) =>
    request<ProcessDefinition>(`/processes/${encodeURIComponent(processId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  publishProcess: (processId: string) =>
    request<ProcessDefinition>(`/processes/${encodeURIComponent(processId)}/publish`, {
      method: "POST",
    }),
  processVersion: (processId: string, version: number | "draft") =>
    request<ProcessVersionDocument>(`/processes/${encodeURIComponent(processId)}/versions/${encodeURIComponent(String(version))}`),
  diffProcessVersions: (processId: string, from: number | "draft", to: number | "draft") =>
    request<ProcessVersionDiff>(`/processes/${encodeURIComponent(processId)}/diff?from=${encodeURIComponent(String(from))}&to=${encodeURIComponent(String(to))}`),
  exportProcessBpmn: (processId: string, version: number | "draft") =>
    requestBlob(`/processes/${encodeURIComponent(processId)}/export/bpmn?version=${encodeURIComponent(String(version))}`),
  importProcessBpmn: async (content: string, options: { name?: string; description?: string; isTemplate?: boolean } = {}) => {
    const query = new URLSearchParams();
    if (options.name) query.set("name", options.name);
    if (options.description) query.set("description", options.description);
    if (options.isTemplate) query.set("template", "true");
    const response = await authorizedFetch(`/processes/import/bpmn${query.size ? `?${query}` : ""}`, {
      method: "POST",
      headers: { "content-type": "application/bpmn+xml" },
      body: content,
    });
    return parseResponse<ProcessDefinition>(response);
  },
  processSchedule: (processId: string) =>
    request<ProcessSchedule>(`/processes/${encodeURIComponent(processId)}/schedule`),
  saveProcessSchedule: (processId: string, payload: SaveProcessScheduleRequest) =>
    request<ProcessSchedule>(`/processes/${encodeURIComponent(processId)}/schedule`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteProcessSchedule: (processId: string) =>
    request<void>(`/processes/${encodeURIComponent(processId)}/schedule`, { method: "DELETE" }),
  triggerProcessSchedule: (processId: string) =>
    request<ProcessSchedule>(`/processes/${encodeURIComponent(processId)}/schedule/trigger`, { method: "POST" }),
  processWebhooks: (processId: string) =>
    request<{ webhooks: ProcessWebhook[] }>(`/processes/${encodeURIComponent(processId)}/webhooks`),
  createProcessWebhook: (
    processId: string,
    payload: { name: string; kind: "start" | "signal"; signalName?: string; defaultInput?: string },
  ) => request<ProcessWebhookSecret>(`/processes/${encodeURIComponent(processId)}/webhooks`, {
    method: "POST",
    body: JSON.stringify(payload),
  }),
  rotateProcessWebhook: (webhookId: string) =>
    request<ProcessWebhookSecret>(`/process-webhooks/${encodeURIComponent(webhookId)}/rotate`, { method: "POST" }),
  deleteProcessWebhook: (webhookId: string) =>
    request<void>(`/process-webhooks/${encodeURIComponent(webhookId)}`, { method: "DELETE" }),
  deliverProcessSignal: (
    processId: string,
    signalName: string,
    payload: { instanceId?: string; correlationKey?: string; payload?: unknown },
  ) => request<{ delivered: boolean; instanceIds: string[] }>(
    `/processes/${encodeURIComponent(processId)}/signals/${encodeURIComponent(signalName)}`,
    { method: "POST", body: JSON.stringify(payload) },
  ),
  startProcess: (processId: string, payload: StartProcessRequest) =>
    request<ProcessInstance>(`/processes/${encodeURIComponent(processId)}/start`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  testProcessNode: (processId: string, node: ProcessGraphNode, input: string) =>
    request<TestProcessNodeResult>(`/processes/${encodeURIComponent(processId)}/test-node`, {
      method: "POST",
      body: JSON.stringify({ node, input }),
    }),
  cancelProcessInstance: (instanceId: string) =>
    request<void>(`/process-instances/${encodeURIComponent(instanceId)}/cancel`, {
      method: "POST",
    }),
  replayProcessInstance: (instanceId: string, mode: "safe" | "live") =>
    request<ProcessInstance>(`/process-instances/${encodeURIComponent(instanceId)}/replay`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  setScheduler: (mode: SchedulerMode) =>
    request<{ mode: SchedulerMode }>("/settings/scheduler", {
      method: "PATCH",
      body: JSON.stringify({ mode }),
    }),
  setModelRouter: (policy: Partial<ModelRouterPolicy>) =>
    request<ModelRouterPolicy>("/settings/model-router", {
      method: "PATCH",
      body: JSON.stringify(policy),
    }),
  decideApproval: (stageId: string, decision: ApprovalDecisionInput) =>
    request<void>(`/approvals/${encodeURIComponent(stageId)}`, {
      method: "POST",
      body: JSON.stringify(decision),
    }),
};

export function subscribeToEvents(onEvent: () => void): () => void {
  const controller = new AbortController();
  let reconnectTimer: number | null = null;

  async function connect() {
    try {
      const response = await authorizedFetch("/events", { signal: controller.signal }, false);
      if (!response.ok || !response.body) throw new Error(`Events HTTP ${response.status}`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!controller.signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          const message = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          if (message.split("\n").some((line) => line.startsWith("data:"))) onEvent();
          boundary = buffer.indexOf("\n\n");
        }
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      console.warn("Поток событий АГАТ временно недоступен", error);
    }
    if (!controller.signal.aborted) reconnectTimer = window.setTimeout(() => void connect(), 2_000);
  }

  void connect();
  return () => {
    controller.abort();
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
  };
}
