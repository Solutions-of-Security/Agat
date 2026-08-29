export type SchedulerMode = "sequential" | "parallel" | "auto";
export type ResultDestination = "history" | "artifacts";
export type AgentRuntime = "single" | "langgraph";

export interface ToolLoopAgentRuntimeConfig {
  profile: "tool_loop_v1";
  maxIterations: number;
}

export interface SpecialistTeamAgentRuntimeConfig {
  profile: "specialist_team_v1";
  maxIterations: number;
  maxHandoffs: number;
  stateSchema: "specialist_team_state_v1";
  specialistAgentIds: string[];
}

export type AgentRuntimeConfig = ToolLoopAgentRuntimeConfig | SpecialistTeamAgentRuntimeConfig;

export interface SpecialistExecutionSnapshot {
  schemaVersion: 1;
  id: string;
  name: string;
  role: string;
  systemPrompt: string;
  model: string | null;
  runtimeConfig: ToolLoopAgentRuntimeConfig;
  promptVersion: string;
  definitionVersion: string;
  registryPromptId: string | null;
  registryPromptVersion: number | null;
}
export type RunStatus =
  | "queued"
  | "running"
  | "waiting_approval"
  | "waiting_external"
  | "compensating"
  | "completed"
  | "failed"
  | "cancelled";
export type StageStatus =
  | "pending"
  | "queued"
  | "running"
  | "waiting_approval"
  | "waiting_external"
  | "completed"
  | "failed"
  | "cancelled";
export type ProcessNodeType =
  | "start"
  | "agent"
  | "http"
  | "transform"
  | "wait"
  | "approval"
  | "artifact"
  | "condition"
  | "loop"
  | "parallel_fork"
  | "parallel_join"
  | "signal"
  | "subprocess"
  | "end";
export type ProcessBranch = "default" | "true" | "false" | "repeat" | "exit";
export type ProcessConditionOperator = "always" | "contains" | "not_contains" | "equals" | "not_equals";
export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface ProcessCondition {
  source: "last_output";
  operator: ProcessConditionOperator;
  value: string;
  caseSensitive: boolean;
}

export interface ProcessCompensationConfig {
  url: string;
  method: HttpMethod;
  headers: Record<string, string>;
  body: string;
  credentialId?: string;
  timeoutSeconds: number;
}

export interface ProcessGraphNode {
  id: string;
  type: ProcessNodeType;
  name: string;
  position: { x: number; y: number };
  config: {
    agentId?: string;
    approvalRequired?: boolean;
    condition?: ProcessCondition;
    maxIterations?: number;
    template?: string;
    url?: string;
    method?: HttpMethod;
    headers?: Record<string, string>;
    body?: string;
    credentialId?: string;
    timeoutSeconds?: number;
    waitSeconds?: number;
    approvalMessage?: string;
    artifactName?: string;
    artifactMediaType?: string;
    artifactContent?: string;
    forkId?: string;
    signalName?: string;
    signalCorrelationKey?: string;
    signalTimeoutSeconds?: number;
    subprocessProcessId?: string;
    subprocessVersion?: number;
    subprocessInputTemplate?: string;
    idempotencyHeader?: string;
    compensation?: ProcessCompensationConfig;
  };
}

export interface ProcessGraphEdge {
  id: string;
  source: string;
  target: string;
  branch: ProcessBranch;
}

export interface ProcessGraph {
  nodes: ProcessGraphNode[];
  edges: ProcessGraphEdge[];
}

export interface Stage {
  id: string;
  position: number;
  status: StageStatus;
  nodeId: string | null;
  nodeName: string | null;
  attempt: number;
  maxAttempts: number;
  requiresApproval: boolean;
  startedAt: string | null;
  completedAt: string | null;
  output: string | null;
  processNodeId: string | null;
  kind: "agent" | "http" | "transform" | "wait" | "approval" | "artifact" | "signal" | "subprocess" | "compensation";
  input: string | null;
  traceparent: string | null;
  metrics: WorkerExecutionMetrics;
  worker: Record<string, unknown> | null;
  routing: ModelRoutingDecision | null;
  agent: {
    id: string;
    name: string;
    role: string;
    model: string | null;
    runtime: AgentRuntime;
    runtimeConfig: AgentRuntimeConfig;
    specialists: SpecialistExecutionSnapshot[];
    promptVersion: string;
    definitionVersion: string;
    registryPromptId: string | null;
    registryPromptVersion: number | null;
  };
}

export interface WorkerExecutionMetrics {
  durationMs?: number;
  modelDurationMs?: number;
  modelCalls?: number;
  inputTokens?: number;
  outputTokens?: number;
  toolCalls?: number;
  model?: string;
  provider?: string;
  toolSchemaVersion?: string;
  energyJoules?: number;
}

export type ModelRouterStrategy = "balanced" | "performance" | "efficiency";

export interface ModelRouterPolicy {
  enabled: boolean;
  strategy: ModelRouterStrategy;
  minContextTokens: number;
  minQualityScore: number;
  minBatteryPercent: number;
  maxTemperatureC: number;
  allowUnknownProfiles: boolean;
}

export interface ModelBenchmark {
  samples: number;
  outputTokens: number;
  modelDurationMs: number;
  tokensPerSecond: number;
  joulesPer1kTokens: number | null;
  lastObservedAt: string;
}

export interface NodeModelProfile {
  name: string;
  provider?: string;
  contextWindow?: number;
  sizeBytes?: number;
  parameterCount?: number;
  parameterSize?: string;
  quantization?: string;
  capabilities?: string[];
  qualityScore?: number;
  discoveredAt?: string;
  profileKnown: boolean;
  benchmark: ModelBenchmark | null;
}

export interface ModelRoutingDecision {
  schemaVersion: 1;
  selectedAt: string;
  strategy: ModelRouterStrategy;
  requestedModel: string | null;
  selectedModel: string | null;
  nodeId: string;
  nodeName: string;
  alternativesConsidered: number;
  fallbackFrom: { nodeId: string; model: string | null } | null;
  reasons: string[];
  signals: {
    freeSlots: number;
    cpuPercent: number | null;
    memoryPercent: number | null;
    temperatureC: number | null;
    batteryPercent: number | null;
    onBattery: boolean;
    contextWindow: number | null;
    qualityScore: number | null;
    sizeBytes: number | null;
    parameterCount: number | null;
    tokensPerSecond: number | null;
    joulesPer1kTokens: number | null;
    benchmarkSamples: number;
    profileKnown: boolean;
  };
}

export interface Run {
  id: string;
  name: string;
  input: string;
  status: RunStatus;
  executionMode: SchedulerMode;
  priority: number;
  approvalRequired: boolean;
  resultDestination: ResultDestination;
  artifactPath: string | null;
  artifactBasePath: string | null;
  traceId: string;
  replayOfRunId: string | null;
  evaluationGroupId: string | null;
  variantName: string | null;
  knowledgeCollectionIds: string[];
  createdAt: string;
  updatedAt: string;
  startedAt: string | null;
  completedAt: string | null;
  process: {
    instanceId: string;
    processId: string;
    name: string;
    version: number;
  } | null;
  stages: Stage[];
}

export interface NodeMetrics {
  cpuPercent?: number;
  memoryPercent?: number;
  gpuPercent?: number;
  batteryPercent?: number;
  onBattery?: boolean;
  temperatureC?: number;
  vramUsedMb?: number;
  powerWatts?: number;
}

export interface ComputeNode {
  id: string;
  name: string;
  platform: string;
  architecture: string;
  endpoint: string;
  models: string[];
  embeddingModels: string[];
  modelProfiles: NodeModelProfile[];
  labels: Record<string, string>;
  agentRuntimes: AgentRuntime[];
  agentRuntimeProfiles: Array<"tool_loop_v1" | "specialist_team_v1">;
  cpuCores: number;
  memoryMb: number;
  vramMb: number;
  gpu: string;
  maxConcurrency: number;
  usedConcurrency: number;
  status: "online" | "sleeping" | "offline";
  metrics: NodeMetrics;
  lastSeen: string;
}

export interface KnowledgeCollection {
  id: string;
  name: string;
  description: string;
  embeddingModel: string;
  chunkSize: number;
  chunkOverlap: number;
  topK: number;
  documentCount: number;
  readyDocuments: number;
  chunkCount: number;
  embeddedChunks: number;
  pendingJobs: number;
  embeddingWorkers: number;
  createdAt: string;
  updatedAt: string;
}

export interface KnowledgeDocument {
  id: string;
  collectionId: string;
  collectionName: string | null;
  embeddingModel: string | null;
  name: string;
  sourceUri: string | null;
  mediaType: string;
  contentSha256: string;
  status: "pending" | "indexing" | "ready" | "failed";
  error: string | null;
  chunkCount: number;
  embeddedCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface MemoryEntry {
  id: string;
  kind: "working" | "episodic";
  content: string;
  contentSha256: string;
  agentId: string | null;
  agentName: string | null;
  expiresAt: string | null;
  explicitlySaved: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface KnowledgeOverview {
  collections: KnowledgeCollection[];
  counts: {
    collections: number;
    documents: number;
    readyDocuments: number;
    chunks: number;
    embeddedChunks: number;
    pendingJobs: number;
    activeMemory: number;
  };
}

export interface KnowledgeSnapshot extends KnowledgeOverview {
  documents: KnowledgeDocument[];
  memory: MemoryEntry[];
}

export interface CreateKnowledgeCollectionRequest {
  name: string;
  description?: string;
  embeddingModel: string;
  chunkSize?: number;
  chunkOverlap?: number;
  topK?: number;
}

export interface IngestKnowledgeDocumentRequest {
  name: string;
  sourceUri?: string;
  mediaType?: string;
  content: string;
}

export interface SaveMemoryRequest {
  kind: "working" | "episodic";
  content: string;
  agentId?: string | null;
  ttlSeconds?: number;
}

export interface LocalModelInfo {
  name: string;
  sizeBytes: number | null;
  modifiedAt: string | null;
}

export type LocalWorkerPoolStatus = "starting" | "ready" | "degraded" | "stopped";

export interface LocalWorkerPool {
  id: string;
  name: string;
  model: string;
  workers: number;
  desiredWorkers: number;
  readyWorkers: number;
  concurrency: number;
  webEnabled: boolean;
  status: LocalWorkerPoolStatus;
  createdAt: string;
  workerNames: string[];
}

export interface LocalWorkerLauncherSnapshot {
  available: boolean;
  reason: string | null;
  runtime: "docker-desktop-kubernetes";
  workerImage: string;
  modelBaseUrl: string;
  maxWorkersPerLaunch: number;
  defaultWebEnabled: boolean;
  models: LocalModelInfo[];
  modelDiscoveryError: string | null;
  pools: LocalWorkerPool[];
}

export interface LaunchLocalWorkersRequest {
  name: string;
  model: string;
  workers: number;
  concurrency: number;
  webEnabled: boolean;
}

export interface Agent {
  id: string;
  name: string;
  role: string;
  systemPrompt: string;
  model: string | null;
  registryPromptId: string | null;
  registryPromptVersion: number | null;
  runtime: AgentRuntime;
  runtimeConfig: AgentRuntimeConfig;
  isBuiltIn: boolean;
  createdAt: string;
  updatedAt: string;
  totalRuns: number;
  activeRuns: number;
  lastRunAt: string | null;
}

export type CredentialType = "http_header" | "api_key";

export interface CredentialScope {
  kind: "project" | "mcp";
  serverNamespaces: string[];
  toolPatterns: string[];
  risks: McpToolRisk[];
  allowCatalog: boolean;
  expiresAt: string | null;
}

export interface CredentialSummary {
  id: string;
  name: string;
  type: CredentialType;
  fields: string[];
  scope: CredentialScope;
  createdAt: string;
  updatedAt: string;
}

export type AgatRole = "admin" | "designer" | "operator" | "viewer" | "auditor";

export interface AuthUser {
  subject: string;
  username: string;
  email: string | null;
  roles: AgatRole[];
  projectIds: string[];
  activeProjectId: string;
  local: boolean;
}

export interface ProjectSummary {
  id: string;
  name: string;
  processCount: number;
  agentCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface CreateCredentialRequest {
  name: string;
  type: CredentialType;
  data: Record<string, string>;
  scope: CredentialScope;
}

export interface ProcessDefinition {
  id: string;
  name: string;
  description: string;
  isTemplate: boolean;
  status: "draft" | "published";
  draftGraph: ProcessGraph;
  publishedVersion: number;
  hasUnpublishedChanges: boolean;
  versions: Array<{ version: number; publishedAt: string }>;
  totalInstances: number;
  activeInstances: number;
  lastStartedAt: string | null;
  createdAt: string;
  updatedAt: string;
  publishedAt: string | null;
}

export interface ProcessInstance {
  id: string;
  processId: string;
  processName: string;
  processVersion: number;
  runId: string;
  runName: string;
  input: string;
  status: RunStatus;
  currentNode: {
    id: string;
    type: ProcessNodeType;
    name: string;
  } | null;
  activeNodes: Array<{ id: string; type: ProcessNodeType; name: string }>;
  pendingSignals: Array<{ name: string; correlationKey: string | null; expiresAt: string | null }>;
  compensations: Array<{ processNodeId: string; sequence: number; status: string; error: string | null }>;
  embeddedSubprocesses: Array<{
    id: string;
    processId: string;
    processVersion: number;
    status: RunStatus;
    runtime: "embedded";
    linkStatus: string;
    createdAt: string;
    completedAt: string | null;
  }>;
  loopCounts: Record<string, number>;
  currentIteration: number;
  maxIterations: number | null;
  transitionCount: number;
  runtime: "database" | "temporal" | "embedded";
  workflowId: string | null;
  replayOfInstanceId: string | null;
  replayMode: "safe" | "live";
  compensationError: string | null;
  error: string | null;
  createdAt: string;
  updatedAt: string;
  startedAt: string | null;
  completedAt: string | null;
}

export interface StageApproval {
  kind: "stage";
  stageId: string;
  runId: string;
  runName: string;
  agentName: string;
  summary: string;
}

export interface McpToolApproval {
  kind: "mcp_tool";
  callId: string;
  stageId: string;
  runId: string;
  runName: string;
  agentName: string;
  summary: string;
  toolName: string;
  risk: McpToolRisk;
  riskTier: McpRiskTier;
  policy: McpToolPolicy;
  requiredApprovals: number;
  approvalCount: number;
  approvers: string[];
  policyVersion: number;
  policySha256: string;
  policyRuleId: string | null;
  policyReason: string;
  arguments: Record<string, unknown>;
  previewDiff: Array<Record<string, unknown>>;
  createdAt: string;
}

export type Approval = StageApproval | McpToolApproval;

export type McpDefaultPolicy = "deny" | "approval" | "auto";
export type McpToolPolicy = "allow" | "approval" | "deny";
export type McpToolRisk = "read" | "write" | "destructive" | "unknown";
export type McpRiskTier = "low" | "elevated" | "high" | "critical";
export type McpPolicyEffect = "allow" | "approval" | "deny";

export interface McpRiskDecision {
  tier: McpRiskTier;
  effect: McpPolicyEffect;
  approvals: 0 | 1 | 2;
}

export interface McpPolicyRule {
  id: string;
  description: string;
  match: {
    serverNamespaces: string[];
    toolPatterns: string[];
    risks: McpToolRisk[];
  };
  decision: McpRiskDecision;
}

export interface McpPolicyDocument {
  schemaVersion: 1;
  name: string;
  defaults: Record<McpToolRisk, McpRiskDecision>;
  rules: McpPolicyRule[];
}

export interface McpPolicyDecision extends McpRiskDecision {
  legacyPolicy: McpToolPolicy;
  ruleId: string | null;
  reason: string;
  policyVersion: number;
  policySha256: string;
}

export interface McpEmergencyDenyState {
  enabled: boolean;
  reason: string;
  actor: string | null;
  changedAt: string | null;
  pendingCallsDenied: number;
  executingCalls: number;
}

export interface McpPolicySnapshot {
  version: number;
  sha256: string;
  document: McpPolicyDocument;
  actor: string;
  createdAt: string;
  emergencyDeny: McpEmergencyDenyState;
}

export interface McpPolicyPreview {
  baseVersion: number;
  baseSha256: string;
  candidateSha256: string;
  document: McpPolicyDocument;
  changed: boolean;
  summary: {
    toolsEvaluated: number;
    toolsChanged: number;
    newlyAllowed: number;
    newlyDenied: number;
    approvalsIncreased: number;
    approvalsDecreased: number;
  };
  changes: Array<{
    serverId: string;
    serverName: string;
    namespace: string;
    toolName: string;
    publicName: string;
    risk: McpToolRisk;
    before: McpPolicyDecision;
    after: McpPolicyDecision;
  }>;
}
export type McpToolCallStatus = "waiting_approval" | "executing" | "completed" | "failed" | "rejected" | "expired";

export interface McpTool {
  name: string;
  publicName: string;
  title: string | null;
  description: string;
  inputSchema: Record<string, unknown>;
  outputSchema: Record<string, unknown> | null;
  annotations: Record<string, unknown> | null;
  risk: McpToolRisk;
  riskTier: McpRiskTier;
  legacyPolicy: McpToolPolicy;
  policy: McpToolPolicy;
  policyOverride: McpToolPolicy | null;
  decision: McpPolicyDecision;
  requiredApprovals: number;
  policyVersion: number;
  policySha256: string;
  policyReason: string;
}

export interface McpServer {
  id: string;
  name: string;
  namespace: string;
  endpoint: string;
  credentialId: string | null;
  hasCredential: boolean;
  enabled: boolean;
  trustAnnotations: boolean;
  allowInsecureHttp: boolean;
  protocolVersion: string;
  defaultPolicy: McpDefaultPolicy;
  catalogTtlSeconds: number;
  catalogScope: "public" | "private";
  catalogExpiresAt: string | null;
  lastSyncAt: string | null;
  lastError: string | null;
  tools: McpTool[];
  totalCalls: number;
  waitingCalls: number;
  createdAt: string;
  updatedAt: string;
}

export interface McpToolCall {
  callId: string;
  runId: string;
  stageId: string;
  serverId: string;
  serverName: string;
  toolName: string;
  publicName: string;
  risk: McpToolRisk;
  riskTier: McpRiskTier;
  legacyPolicy: McpToolPolicy;
  policy: McpToolPolicy;
  requiredApprovals: number;
  approvalCount: number;
  approvers: string[];
  policyVersion: number;
  policySha256: string;
  policyRuleId: string | null;
  policyReason: string;
  status: McpToolCallStatus;
  arguments: Record<string, unknown>;
  previewDiff: Array<Record<string, unknown>>;
  resultSha256: string | null;
  resultBytes: number | null;
  error: string | null;
  decisionActor: string | null;
  decisionAt: string | null;
  createdAt: string;
  startedAt: string | null;
  completedAt: string | null;
  updatedAt: string;
}

export interface McpOverview {
  enabled: boolean;
  servers: McpServer[];
  recentCalls: McpToolCall[];
  policy: McpPolicySnapshot;
}

export interface SaveMcpServerRequest {
  name: string;
  namespace: string;
  endpoint: string;
  credentialId: string | null;
  enabled: boolean;
  trustAnnotations: boolean;
  allowInsecureHttp: boolean;
  defaultPolicy: McpDefaultPolicy;
  catalogTtlSeconds: number;
}

export interface AgatEvent {
  id: number;
  runId: string | null;
  stageId: string | null;
  nodeId: string | null;
  level: "debug" | "info" | "warn" | "error" | string;
  type: string;
  message: string;
  data: Record<string, unknown> | null;
  createdAt: string;
}

export interface Artifact {
  id: string;
  runId: string;
  stageId: string | null;
  name: string;
  kind: "result" | "stage_output" | "agent_artifact" | string;
  mediaType: string;
  relativePath: string;
  sizeBytes: number;
  sha256: string;
  createdAt: string;
}

export interface RunTrace {
  run: Run;
  events: AgatEvent[];
  artifacts: Artifact[];
  manifest: ExecutionManifest;
  comparison: EvaluationComparison | null;
  goldenEvaluation: {
    experimentId: string;
    experimentName: string;
    itemId: string;
    runKind: "candidate" | "model_judge";
    datasetId: string;
    datasetVersion: number;
    promptId: string;
    promptVersion: number;
    minQualityScore: number;
    qualityScore: number | null;
    qualitySource: "deterministic" | "human" | "model_judge" | null;
    gates: GoldenEvalGates;
  } | null;
  truncated: boolean;
  tracePolicy: {
    rawReasoningStored: false;
    observableProgressStored: boolean;
    exactInputsStored: boolean;
    exactOutputsStored: boolean;
    toolArguments: "redacted" | "full";
  };
}

export interface ExecutionManifest {
  schemaVersion: 3;
  runId: string;
  traceId: string;
  sourceRunId: string | null;
  inputSha256: string;
  executionMode: SchedulerMode;
  knowledgeCollectionIds: string[];
  createdAt: string;
  stages: Array<{
    stageId: string;
    position: number;
    kind: Stage["kind"];
    processNodeId: string | null;
    stageInputSha256: string | null;
    agent: {
      schemaVersion: 3;
      capturedAt: string;
      source: "run_creation" | "process_queue" | "migration_backfill" | "replay" | "evaluation" | "model_judge";
      id: string;
      name: string;
      role: string;
      systemPrompt: string;
      model: string | null;
      runtime: AgentRuntime;
      runtimeConfig: AgentRuntimeConfig;
      specialists: SpecialistExecutionSnapshot[];
      promptVersion: string;
      definitionVersion: string;
      registryPromptId: string | null;
      registryPromptVersion: number | null;
    };
    worker: Record<string, unknown> | null;
    routing: ModelRoutingDecision | null;
    metrics: WorkerExecutionMetrics;
  }>;
  guarantees: {
    promptAndAgentConfigImmutable: boolean;
    secretsIncluded: boolean;
    externalToolResultsDeterministic: boolean;
    processSideEffectsReplayable: boolean;
  };
  manifestSha256: string;
}

export type EvaluationGate = "pass" | "fail" | "pending" | "not_evaluated";

export interface EvaluationComparison {
  evaluationGroupId: string;
  baselineRunId: string | null;
  runs: Array<{
    runId: string;
    sourceRunId: string | null;
    variantName: string;
    status: RunStatus;
    wallDurationMs: number | null;
    stageDurationMs: number | null;
    modelCalls: number;
    inputTokens: number | null;
    outputTokens: number | null;
    toolCalls: number;
    outputCharacters: number;
    outputSha256: string | null;
    gates: null | {
      completion: EvaluationGate;
      latency: EvaluationGate;
      tokenBudget: EvaluationGate;
      quality: EvaluationGate;
    };
  }>;
  gatePolicy: {
    latencyMetric: string;
    latencyRatioMax: number;
    outputTokenRatioMax: number;
    quality: string;
  };
}

export interface ReplayRunRequest {
  variants: Array<{
    name: string;
    modelOverrides?: Record<string, string | null>;
  }>;
}

export interface ReplayRunResponse {
  evaluationGroupId: string;
  runs: Array<{ id: string; status: RunStatus; variantName: string }>;
}

export type GoldenEvalGate = "pass" | "fail" | "pending";

export interface GoldenEvalGates {
  completion: GoldenEvalGate;
  quality: GoldenEvalGate;
  knowledge: GoldenEvalGate;
  overall: GoldenEvalGate;
}

export interface PromptRegistryVersion {
  version: number;
  content: string;
  contentSha256: string;
  changeNote: string;
  createdBy: string;
  createdAt: string;
  active: boolean;
}

export interface PromptRegistryEntry {
  id: string;
  name: string;
  description: string;
  agentId: string | null;
  agentName: string | null;
  agentBuiltIn: boolean;
  activeVersion: number;
  activeModel: string | null;
  versions: PromptRegistryVersion[];
  createdAt: string;
  updatedAt: string;
}

export interface EvalRubricCriterion {
  id: string;
  label: string;
  description: string;
  weight: number;
}

export interface GoldenEvalExample {
  id: string;
  position: number;
  name: string;
  input: string;
  referenceOutput: string;
  requiredTerms: string[];
  forbiddenTerms: string[];
  knowledgeCollectionIds: string[];
}

export interface GoldenDatasetVersion {
  version: number;
  description: string;
  changeNote: string;
  rubric: EvalRubricCriterion[];
  knowledgeSnapshot: {
    schemaVersion?: number;
    collectionIds?: string[];
    missingCollectionIds?: string[];
    fingerprint?: string;
  };
  contentSha256: string;
  createdBy: string;
  createdAt: string;
  examples: GoldenEvalExample[];
  active: boolean;
}

export interface GoldenDataset {
  id: string;
  name: string;
  description: string;
  currentVersion: number;
  versions: GoldenDatasetVersion[];
  createdAt: string;
  updatedAt: string;
}

export interface GoldenEvalReview {
  id: string;
  kind: "human" | "model_judge";
  reviewer: string;
  model: string | null;
  scores: Record<string, number>;
  overallScore: number;
  rationale: string;
  rawOutputSha256: string | null;
  runId: string | null;
  createdAt: string;
}

export interface GoldenEvalItem {
  id: string;
  exampleId: string;
  position: number;
  name: string;
  input?: string;
  referenceOutput?: string;
  requiredTerms: string[];
  forbiddenTerms: string[];
  knowledgeCollectionIds: string[];
  runId: string;
  runStatus: RunStatus;
  runStartedAt: string | null;
  runCompletedAt: string | null;
  output?: string;
  outputCharacters: number;
  outputTruncated?: boolean;
  outputSha256: string | null;
  metrics: WorkerExecutionMetrics;
  deterministicScore: number | null;
  deterministicDetails: Array<{ kind: string; term: string; pass: boolean }>;
  judgeRunId: string | null;
  judgeStatus: RunStatus | null;
  judgeError: string | null;
  humanScore: number | null;
  judgeScore: number | null;
  qualityScore: number | null;
  qualitySource: "deterministic" | "human" | "model_judge" | null;
  reviews?: GoldenEvalReview[];
}

export interface GoldenEvalExperiment {
  id: string;
  name: string;
  status: "queued" | "running" | "scoring" | "completed" | "failed";
  datasetId: string;
  datasetName: string;
  datasetVersion: number;
  agentId: string;
  agentName: string;
  promptId: string;
  promptName: string;
  promptVersion: number;
  model: string | null;
  minQualityScore: number;
  knowledgeSnapshot: { collectionIds?: string[]; fingerprint?: string };
  createdBy: string;
  createdAt: string;
  updatedAt: string;
  startedAt: string | null;
  completedAt: string | null;
  items: GoldenEvalItem[];
  qualityScore: number | null;
  gates: GoldenEvalGates;
  knowledgeDrift: boolean;
  currentKnowledgeFingerprint: string;
  counts: {
    total: number;
    queued: number;
    running: number;
    completed: number;
    failed: number;
    scored: number;
    humanReviewed: number;
    judgeReviewed: number;
  };
}

export interface EvalSnapshot {
  generatedAt: string;
  prompts: PromptRegistryEntry[];
  datasets: GoldenDataset[];
  experiments: GoldenEvalExperiment[];
  counts: {
    prompts: number;
    promptVersions: number;
    datasets: number;
    datasetVersions: number;
    experiments: number;
    passingExperiments: number;
  };
}

export interface CreateGoldenDatasetRequest {
  name: string;
  description: string;
  changeNote?: string;
  rubric: Array<Partial<EvalRubricCriterion> & Pick<EvalRubricCriterion, "label">>;
  examples: Array<{
    name?: string;
    input: string;
    referenceOutput?: string;
    requiredTerms?: string[];
    forbiddenTerms?: string[];
    knowledgeCollectionIds?: string[];
  }>;
}

export interface CreateEvalExperimentRequest {
  name: string;
  datasetId: string;
  datasetVersion: number;
  agentId: string;
  promptId: string;
  promptVersion: number;
  model: string | null;
  minQualityScore: number;
}

export interface Overview {
  generatedAt: string;
  project: ProjectSummary | null;
  processRuntime: {
    mode: "database" | "temporal";
    connected: boolean;
    namespace: string | null;
    taskQueue: string | null;
  };
  scheduler: {
    mode: SchedulerMode;
    globalMaxConcurrency: number;
    activeLeases: number;
  };
  modelRouter: {
    policy: ModelRouterPolicy;
    profiledModels: number;
    benchmarkedModels: number;
    lastBenchmarkAt: string | null;
  };
  counts: {
    queued: number;
    activeRuns: number;
    waitingApprovals: number;
    completedRuns: number;
    failedRuns: number;
    totalRuns: number;
    agents: number;
    readyAgents: number;
    nodes: number;
    onlineNodes: number;
    models: number;
    totalModels: number;
    processes: number;
    publishedProcesses: number;
    activeProcessInstances: number;
  };
  agents: Agent[];
  credentials: CredentialSummary[];
  processes: ProcessDefinition[];
  processInstances: ProcessInstance[];
  runs: Run[];
  nodes: ComputeNode[];
  models: string[];
  approvals: Approval[];
  mcp: McpOverview;
  knowledge: KnowledgeOverview;
  events: AgatEvent[];
}

export interface CreateRunRequest {
  name: string;
  input: string;
  executionMode: SchedulerMode;
  priority: number;
  approvalRequired: boolean;
  agentIds: string[];
  resultDestination: ResultDestination;
  artifactPath: string;
  knowledgeCollectionIds: string[];
}

export interface CreateAgentRequest {
  name: string;
  role: string;
  systemPrompt: string;
  model: string | null;
  runtime: AgentRuntime;
  runtimeConfig: AgentRuntimeConfig;
}

export interface CreateProcessRequest {
  name: string;
  description: string;
  graph?: ProcessGraph;
  templateId?: string;
  isTemplate?: boolean;
}

export interface UpdateProcessRequest {
  name: string;
  description: string;
  graph: ProcessGraph;
  isTemplate?: boolean;
}

export interface StartProcessRequest {
  input: string;
  priority: number;
  resultDestination: ResultDestination;
  artifactPath: string;
  startNodeId?: string;
  knowledgeCollectionIds?: string[];
}

export interface TestProcessNodeResult {
  kind: "queued" | "evaluated" | "passthrough";
  runId: string | null;
  status: string;
  output: string;
  branch: ProcessBranch | null;
}

export interface ProcessVersionDocument {
  processId: string;
  version: number | "draft";
  name: string;
  description: string;
  graph: ProcessGraph;
  publishedAt: string | null;
}

export interface ProcessVersionDiff {
  processId: string;
  from: number | "draft";
  to: number | "draft";
  summary: {
    addedNodes: number;
    removedNodes: number;
    changedNodes: number;
    addedEdges: number;
    removedEdges: number;
    metadataChanged: boolean;
  };
  entries: Array<{
    kind: "node_added" | "node_removed" | "node_changed" | "edge_added" | "edge_removed" | "metadata_changed";
    id: string;
    before: unknown;
    after: unknown;
  }>;
}

export interface ProcessSchedule {
  scheduleId: string;
  processId: string;
  projectId: string;
  input: string;
  kind: "interval" | "cron" | "calendar";
  everySeconds: number;
  cronExpression: string;
  calendar: Record<string, unknown> | null;
  timezone: string;
  priority: number;
  paused: boolean;
  knowledgeCollectionIds: string[];
  nextActionTimes: string[];
  recentWorkflowIds: string[];
}

export interface SaveProcessScheduleRequest {
  input: string;
  kind: ProcessSchedule["kind"];
  everySeconds?: number;
  cronExpression?: string;
  calendar?: Record<string, unknown>;
  timezone?: string;
  priority: number;
  paused: boolean;
  knowledgeCollectionIds: string[];
}

export interface ProcessWebhook {
  id: string;
  processId: string;
  name: string;
  kind: "start" | "signal";
  signalName: string | null;
  defaultInput: string;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
  lastUsedAt: string | null;
}

export interface ProcessWebhookSecret extends ProcessWebhook {
  token: string;
}

export type A2AInputMode = "text/plain" | "application/json";

export type A2ATaskState =
  | "TASK_STATE_UNSPECIFIED"
  | "TASK_STATE_SUBMITTED"
  | "TASK_STATE_WORKING"
  | "TASK_STATE_COMPLETED"
  | "TASK_STATE_FAILED"
  | "TASK_STATE_CANCELED"
  | "TASK_STATE_INPUT_REQUIRED"
  | "TASK_STATE_REJECTED"
  | "TASK_STATE_AUTH_REQUIRED";

export interface A2AEndpoint {
  id: string;
  projectId: string;
  agentId: string;
  agentName: string;
  agentRole: string;
  name: string;
  description: string;
  version: string;
  skillId: string;
  skillName: string;
  skillDescription: string;
  tags: string[];
  examples: string[];
  inputModes: A2AInputMode[];
  knowledgeCollectionIds: string[];
  approvalRequired: boolean;
  enabled: boolean;
  priority: number;
  maxInputCharacters: number;
  maxActiveTasks: number;
  tokenSuffix: string;
  tokenRotatedAt: string;
  createdAt: string;
  updatedAt: string;
  totalTasks: number;
  activeTasks: number;
  lastTaskAt: string | null;
  agentCardUrl?: string;
  interfaceUrl?: string;
}

export interface A2ATaskSummary {
  id: string;
  endpointId: string;
  endpointName: string;
  agentId: string;
  runId: string;
  contextId: string;
  clientMessageId: string;
  state: A2ATaskState;
  traceId: string;
  hasExternalTraceparent: boolean;
  protocolVersion: string;
  adapterVersion: string;
  createdAt: string;
  updatedAt: string;
}

export interface A2ASnapshot {
  enabled: boolean;
  publicBaseUrl: string;
  protocolVersion: string;
  adapterVersion: string;
  endpoints: A2AEndpoint[];
  tasks: A2ATaskSummary[];
  counts: {
    endpoints: number;
    enabledEndpoints: number;
    tasks: number;
    activeTasks: number;
    completedTasks: number;
    failedTasks: number;
  };
}

export interface SaveA2AEndpointRequest {
  agentId: string;
  name?: string;
  description?: string;
  version?: string;
  skillId?: string;
  skillName?: string;
  skillDescription?: string;
  tags?: string[];
  examples?: string[];
  inputModes?: A2AInputMode[];
  knowledgeCollectionIds?: string[];
  approvalRequired?: boolean;
  enabled?: boolean;
  priority?: number;
  maxInputCharacters?: number;
  maxActiveTasks?: number;
}

export interface A2AEndpointSecret {
  endpoint: A2AEndpoint;
  accessToken: string;
}

export type ViewId = "overview" | "agents" | "runs" | "processes" | "knowledge" | "evals" | "tools" | "a2a" | "nodes" | "models";
