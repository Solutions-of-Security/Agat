export type SchedulerMode = "sequential" | "parallel" | "auto";
export type ResultDestination = "history" | "artifacts";
export type AgentRuntime = "single" | "langgraph";
export type AgentRuntimeProfile = "tool_loop_v1" | "specialist_team_v1";

export interface ToolLoopAgentRuntimeConfig {
  profile: "tool_loop_v1";
  maxIterations: number;
}

export interface SpecialistTeamAgentRuntimeConfig {
  profile: "specialist_team_v1";
  /** Bound applied independently to every specialist tool-loop subgraph. */
  maxIterations: number;
  /** Total supervisor-to-specialist transitions inside one lease attempt. */
  maxHandoffs: number;
  /** Known, worker-validated shared state contract. Arbitrary schemas are not executable. */
  stateSchema: "specialist_team_state_v1";
  /** Existing project-visible agents resolved to immutable snapshots at run creation. */
  specialistAgentIds: string[];
}

export type AgentRuntimeConfig = ToolLoopAgentRuntimeConfig | SpecialistTeamAgentRuntimeConfig;
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
  position: {
    x: number;
    y: number;
  };
  config: {
    agentId?: string;
    approvalRequired?: boolean;
    condition?: ProcessCondition;
    maxIterations?: number;
    inputTemplate?: string;
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
    /** Fork paired with this converging gateway. */
    forkId?: string;
    /** Stable public signal name. Correlation is rendered from process input/context. */
    signalName?: string;
    signalCorrelationKey?: string;
    signalTimeoutSeconds?: number;
    /** Published process invoked as a version-pinned subprocess. */
    subprocessProcessId?: string;
    subprocessVersion?: number;
    subprocessInputTemplate?: string;
    /** Header populated with a deterministic per-instance/per-visit key. */
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
  /** Explicit MCP public names required by this scenario. */
  requiredTools?: string[];
  requiredKnowledgeCollectionIds?: string[];
  /** Undefined inherits project tools; an empty list denies every external MCP tool. */
  mcpToolAllowlist?: string[];
  allowPartialStart?: boolean;
}

export interface CreateProcessInput {
  name: string;
  description?: string;
  graph?: ProcessGraph;
  templateId?: string;
  catalogTemplateId?: string;
  catalogTemplateVersion?: number;
  templateBindings?: Record<string, string>;
  isTemplate?: boolean;
}

export interface UpdateProcessInput {
  name: string;
  description?: string;
  graph: ProcessGraph;
  isTemplate?: boolean;
}

export interface StartProcessInput {
  input: string;
  version?: number;
  startMode?: "queue" | "now";
  priority?: number;
  resultDestination?: ResultDestination;
  artifactPath?: string;
  startNodeId?: string;
  knowledgeCollectionIds?: string[];
}

export interface ReplayProcessInstanceInput {
  /** safe reuses recorded external side-effect results; live performs them again with new idempotency keys. */
  mode?: "safe" | "live";
  priority?: number;
}

export interface ProcessVersionDiffEntry {
  kind: "node_added" | "node_removed" | "node_changed" | "edge_added" | "edge_removed" | "metadata_changed";
  id: string;
  before: unknown;
  after: unknown;
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
  entries: ProcessVersionDiffEntry[];
}

export type ProcessWebhookKind = "start" | "signal";

export interface CreateProcessWebhookInput {
  name: string;
  kind: ProcessWebhookKind;
  signalName?: string;
  defaultInput?: string;
}

export interface DeliverProcessWebhookInput {
  input?: unknown;
  payload?: unknown;
  instanceId?: string;
  correlationKey?: string;
  priority?: number;
}

export interface DurableProcessState {
  instanceId: string;
  status: RunStatus;
  currentNodeId: string | null;
  currentNodeType: ProcessNodeType | null;
  waitUntil: string | null;
  transitionCount: number;
  activeNodeIds?: string[];
  pendingSignalNames?: string[];
  updatedAt: string;
}

export interface DurableProcessStart {
  instanceId: string;
  processId: string;
  projectId: string;
  priority: number;
}

export interface TestProcessNodeInput {
  node: ProcessGraphNode;
  input: string;
}

export interface TestProcessNodeResult {
  kind: "queued" | "evaluated" | "passthrough";
  runId: string | null;
  status: string;
  output: string;
  branch: ProcessBranch | null;
}

export interface WorkerRegistration {
  enrollmentToken: string;
  name: string;
  platform: string;
  architecture?: string;
  endpoint?: string;
  models: string[];
  cpuCores?: number;
  memoryMb?: number;
  vramMb?: number;
  gpu?: string;
  maxConcurrency?: number;
  labels?: Record<string, string>;
  agentRuntimes?: AgentRuntime[];
  agentRuntimeProfiles?: AgentRuntimeProfile[];
  modelProfiles?: WorkerModelProfile[];
  embeddingModels?: string[];
  region?: string;
  residencyDomain?: string;
  release?: WorkerReleaseIdentity;
  runtimeChallengeId?: string;
  runtimeAttestation?: WorkerRuntimeAttestationEnvelope;
}

export interface WorkerMetrics {
  cpuPercent?: number;
  memoryPercent?: number;
  gpuPercent?: number;
  batteryPercent?: number;
  onBattery?: boolean;
  temperatureC?: number;
  vramUsedMb?: number;
  powerWatts?: number;
}

export interface WorkerCapabilities {
  endpoint?: string;
  models: string[];
  vramMb?: number;
  maxConcurrency?: number;
  labels?: Record<string, string>;
  agentRuntimes?: AgentRuntime[];
  agentRuntimeProfiles?: AgentRuntimeProfile[];
  modelProfiles?: WorkerModelProfile[];
  embeddingModels?: string[];
  region?: string;
  residencyDomain?: string;
  release?: WorkerReleaseIdentity;
}

export interface WorkerReleaseIdentity {
  releaseId: string;
  artifactDigest: string;
  keyId: string;
  signature: string;
  rolloutRing?: string;
}

export type EdgePlatform = "android" | "ios";
export type EdgeAttestationProvider = "play_integrity" | "app_attest";
export type NodeTrustKind = "shared_token" | "runtime_attested" | "hardware_attested";
export type NodeCredentialState = "active" | "wipe_pending" | "wiped" | "revoked";

export interface EdgeEnrollmentChallengeInput {
  enrollmentToken: string;
  name: string;
  platform: EdgePlatform;
  applicationId: string;
}

export interface EdgeEnrollmentChallenge {
  schemaVersion: 1;
  id: string;
  challenge: string;
  expiresAt: string;
  platform: EdgePlatform;
  applicationId: string;
}

export interface EdgeAttestationEvidence {
  provider: EdgeAttestationProvider;
  /** Play Integrity token or base64url-encoded App Attest attestation object. */
  token: string;
  /** Play Integrity installation key id or App Attest key id. */
  keyId: string;
  /** Explicitly bound application package/App ID. */
  applicationId: string;
}

export interface EdgeWorkerRegistration extends Omit<WorkerRegistration, "enrollmentToken" | "platform"> {
  challengeId: string;
  challenge: string;
  platform: EdgePlatform;
  attestation: EdgeAttestationEvidence;
}

export interface EdgeAttestationVerdict {
  schemaVersion: 1;
  valid: true;
  platform: EdgePlatform;
  provider: EdgeAttestationProvider;
  applicationId: string;
  keyId: string;
  challengeSha256: string;
  hardwareBacked: true;
  environment: "development" | "production";
  issuedAt: string;
  expiresAt: string;
  verdicts: string[];
}

export interface EdgeControlCommand {
  schemaVersion: 1;
  action: "none" | "wipe";
  generation: number;
  requestedAt: string | null;
  reason: string | null;
}

export interface EdgeWipeAcknowledgement {
  generation: number;
  credentialsDeleted: boolean;
  localDataDeleted: boolean;
}

export interface WorkerModelProfile {
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

export interface LaunchLocalWorkersInput {
  name?: string;
  model: string;
  workers?: number;
  concurrency?: number;
  webEnabled?: boolean;
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

export interface CreateRunInput {
  name: string;
  input: string;
  executionMode?: SchedulerMode;
  priority?: number;
  approvalRequired?: boolean;
  agentIds?: string[];
  resultDestination?: ResultDestination;
  artifactPath?: string;
  knowledgeCollectionIds?: string[];
}

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

export interface AgentExecutionSnapshot {
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
  /** Ordered, version-pinned subgraphs for specialist_team_v1; empty for other profiles. */
  specialists: SpecialistExecutionSnapshot[];
  promptVersion: string;
  definitionVersion: string;
  registryPromptId: string | null;
  registryPromptVersion: number | null;
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

export interface ReplayVariantInput {
  name: string;
  modelOverrides?: Record<string, string | null>;
}

export interface ReplayRunInput {
  variants?: ReplayVariantInput[];
}

export interface CreatePromptInput {
  name: string;
  description?: string;
  agentId?: string | null;
  content: string;
}

export interface CreatePromptVersionInput {
  content: string;
  changeNote?: string;
}

export interface EvalRubricCriterionInput {
  id?: string;
  label: string;
  description?: string;
  weight?: number;
}

export interface EvalExampleInput {
  name?: string;
  input: string;
  referenceOutput?: string;
  requiredTerms?: string[];
  forbiddenTerms?: string[];
  knowledgeCollectionIds?: string[];
}

export interface CreateEvalDatasetInput {
  name: string;
  description?: string;
  changeNote?: string;
  rubric?: EvalRubricCriterionInput[];
  examples: EvalExampleInput[];
}

export interface CreateEvalDatasetVersionInput {
  description?: string;
  changeNote?: string;
  rubric?: EvalRubricCriterionInput[];
  examples: EvalExampleInput[];
}

export interface CreateEvalExperimentInput {
  name: string;
  datasetId: string;
  datasetVersion?: number;
  agentId: string;
  promptId: string;
  promptVersion: number;
  model?: string | null;
  minQualityScore?: number;
}

export interface HumanEvalReviewInput {
  scores?: Record<string, number>;
  overallScore?: number;
  rationale: string;
}

export interface JudgeEvalExperimentInput {
  model: string;
}

export interface PromotePromptInput {
  version: number;
  model?: string | null;
  experimentId: string;
}

export interface WorkerArtifactInput {
  name: string;
  mediaType?: string;
  content: string;
  encoding?: "utf8" | "base64";
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

export interface CreateCredentialInput {
  name: string;
  type: CredentialType;
  data: Record<string, string>;
  scope?: Partial<CredentialScope>;
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

export interface McpPolicyVersion {
  version: number;
  sha256: string;
  document: McpPolicyDocument;
  actor: string;
  createdAt: string;
}

export interface McpEmergencyDenyState {
  enabled: boolean;
  reason: string;
  actor: string | null;
  changedAt: string | null;
  pendingCallsDenied: number;
  executingCalls: number;
}

export interface McpPolicySnapshot extends McpPolicyVersion {
  emergencyDeny: McpEmergencyDenyState;
}

export interface McpPolicyDiffEntry {
  serverId: string;
  serverName: string;
  namespace: string;
  toolName: string;
  publicName: string;
  risk: McpToolRisk;
  before: McpPolicyDecision;
  after: McpPolicyDecision;
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
  changes: McpPolicyDiffEntry[];
}
export type McpToolCallStatus =
  | "waiting_approval"
  | "executing"
  | "completed"
  | "failed"
  | "rejected"
  | "expired";

export type McpTransport = "http" | "wasi" | "container";

export interface McpSandboxEgressRule {
  ip: string;
  port: number;
}

export interface McpIsolatedToolDefinition {
  name: string;
  title?: string | null;
  description?: string;
  inputSchema: Record<string, unknown>;
  outputSchema?: Record<string, unknown> | null;
  annotations?: Record<string, unknown> | null;
}

export interface McpSandboxProfileInput {
  tool: McpIsolatedToolDefinition;
  moduleBase64?: string | null;
  image?: string | null;
  command?: string[];
  timeoutSeconds?: number;
  cpuMillis?: number;
  memoryMiB?: number;
  egress?: McpSandboxEgressRule[];
}

export interface McpSandboxProfile {
  tool: McpIsolatedToolDefinition;
  moduleBase64: string | null;
  moduleSha256: string | null;
  image: string | null;
  command: string[];
  timeoutSeconds: number;
  cpuMillis: number;
  memoryMiB: number;
  egress: McpSandboxEgressRule[];
  profileSha256: string;
}

export interface McpSandboxSummary extends Omit<McpSandboxProfile, "moduleBase64"> {}

export interface CreateMcpServerInput {
  name: string;
  namespace: string;
  transport?: McpTransport;
  endpoint?: string;
  sandbox?: McpSandboxProfileInput | null;
  credentialId?: string | null;
  enabled?: boolean;
  trustAnnotations?: boolean;
  allowInsecureHttp?: boolean;
  defaultPolicy?: McpDefaultPolicy;
  catalogTtlSeconds?: number;
}

export interface UpdateMcpServerInput extends Partial<CreateMcpServerInput> {}

export interface McpCatalogTool {
  name: string;
  publicName: string;
  title: string | null;
  description: string;
  inputSchema: Record<string, unknown>;
  outputSchema: Record<string, unknown> | null;
  annotations: Record<string, unknown> | null;
}

export interface McpLeaseTool {
  publicName: string;
  serverId: string;
  serverName: string;
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  risk: McpToolRisk;
  policy: "allow" | "approval";
  riskTier: McpRiskTier;
  requiredApprovals: 0 | 1 | 2;
  policyVersion: number;
  policySha256: string;
}

export interface McpToolCallResponse {
  callId: string;
  status: McpToolCallStatus;
  result?: unknown;
  error?: string;
  requiredApprovals?: number;
  approvalCount?: number;
  approvers?: string[];
}

export type A2AInputMode = string;
export type A2ARemoteAuthMode = "none" | "bearer" | "oauth2_token_exchange";
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

export interface CreateA2AEndpointInput {
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
  outputModes?: string[];
  knowledgeCollectionIds?: string[];
  approvalRequired?: boolean;
  streamingEnabled?: boolean;
  pushNotificationsEnabled?: boolean;
  fileArtifactsEnabled?: boolean;
  enabled?: boolean;
  priority?: number;
  maxInputCharacters?: number;
  maxActiveTasks?: number;
  maxFileBytes?: number;
  maxFiles?: number;
}

export interface UpdateA2AEndpointInput extends Partial<CreateA2AEndpointInput> {}

export interface A2APart {
  text?: string;
  raw?: string;
  url?: string;
  data?: unknown;
  filename?: string;
  mediaType?: string;
  metadata?: Record<string, unknown>;
}

export interface A2APushAuthentication {
  scheme: string;
  credentials?: string;
}

export interface A2ATaskPushNotificationConfig {
  id?: string;
  taskId?: string;
  url: string;
  token?: string;
  authentication?: A2APushAuthentication;
}

export interface A2AMessage {
  messageId: string;
  contextId?: string;
  taskId?: string;
  role: "ROLE_USER" | "ROLE_AGENT" | "ROLE_UNSPECIFIED";
  parts: A2APart[];
  metadata?: Record<string, unknown>;
  extensions?: string[];
  referenceTaskIds?: string[];
}

export interface A2ASendMessageRequest {
  tenant?: string;
  message: A2AMessage;
  configuration?: {
    acceptedOutputModes?: string[];
    historyLength?: number;
    returnImmediately?: boolean;
    taskPushNotificationConfig?: A2ATaskPushNotificationConfig;
  };
  metadata?: Record<string, unknown>;
}

export interface A2AEndpointConnection {
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
  outputModes: string[];
  knowledgeCollectionIds: string[];
  approvalRequired: boolean;
  streamingEnabled: boolean;
  pushNotificationsEnabled: boolean;
  fileArtifactsEnabled: boolean;
  enabled: boolean;
  priority: number;
  maxInputCharacters: number;
  maxActiveTasks: number;
  maxFileBytes: number;
  maxFiles: number;
  tokenSuffix: string;
  tokenRotatedAt: string;
  createdAt: string;
  updatedAt: string;
}

export interface A2ANormalizedMessage {
  message: A2AMessage;
  input: string;
  contextId: string;
  historyLength: number;
  returnImmediately: boolean;
  requestSha256: string;
  files: A2ANormalizedFile[];
  pushNotificationConfig: A2ANormalizedPushConfig | null;
}

export interface A2ANormalizedFile {
  filename: string;
  mediaType: string;
  bytes: Buffer;
  sha256: string;
}

export interface A2ANormalizedPushConfig {
  id: string;
  url: string;
  token: string;
  authentication: {
    scheme: "Bearer";
    credentials: string;
  } | null;
}

export interface CreateA2ARemoteInput {
  agentCardUrl: string;
  name?: string;
  skillId?: string;
  enabled?: boolean;
  allowFileArtifacts?: boolean;
  maxResponseBytes?: number;
  auth: {
    mode: A2ARemoteAuthMode;
    bearerToken?: string;
    tokenUrl?: string;
    audience?: string;
    scopes?: string[];
    clientId?: string;
    clientSecret?: string;
  };
}

export interface UpdateA2ARemoteInput extends Partial<Omit<CreateA2ARemoteInput, "agentCardUrl">> {
  auth?: CreateA2ARemoteInput["auth"];
}

export interface A2ARemoteConnection {
  id: string;
  projectId: string;
  name: string;
  description: string;
  agentCardUrl: string;
  interfaceUrl: string;
  protocolVersion: string;
  tenant: string | null;
  skillId: string;
  skillName: string;
  inputModes: string[];
  outputModes: string[];
  capabilities: {
    streaming: boolean;
    pushNotifications: boolean;
  };
  authMode: A2ARemoteAuthMode;
  authSuffix: string;
  tokenEndpointOrigin: string | null;
  enabled: boolean;
  allowFileArtifacts: boolean;
  maxResponseBytes: number;
  createdAt: string;
  updatedAt: string;
}

export interface A2ARemoteAuthMaterial {
  mode: A2ARemoteAuthMode;
  bearerToken?: string;
  tokenUrl?: string;
  audience?: string;
  scopes?: string[];
  clientId?: string;
  clientSecret?: string;
}

export interface A2AOutboundInvocationInput {
  message: A2AMessage;
  configuration?: A2ASendMessageRequest["configuration"];
  metadata?: Record<string, unknown>;
}

export interface CreateProjectInput {
  name: string;
  id?: string;
  homeRegion?: string;
  allowedRegions?: string[];
  residencyDomain?: string;
  queueName?: string;
  maxQueuedTasks?: number;
  maxRunningTasks?: number;
}

export interface ProjectFleetPolicyInput {
  homeRegion: string;
  allowedRegions: string[];
  residencyDomain: string;
  queueName: string;
  maxQueuedTasks: number;
  maxRunningTasks: number;
  expectedRevision: number;
}

export interface WorkerReleaseManifest {
  schemaVersion: 1;
  releaseId: string;
  version: string;
  artifactDigest: string;
  platforms: string[];
  issuedAt: string;
  expiresAt?: string | null;
  metadata?: Record<string, string>;
}

/**
 * Compact admission statement produced only after the release pipeline has
 * verified the OCI signature and SLSA provenance bundle with cosign.
 */
export interface WorkerProvenanceStatement {
  schemaVersion: 1;
  policyId: string;
  subjectDigest: string;
  ociRepository: string;
  predicateType: "https://slsa.dev/provenance/v1";
  builderId: string;
  buildType: string;
  sourceRepository: string;
  sourceCommit: string;
  sigstoreBundleSha256: string;
  verifiedAt: string;
  expiresAt: string;
}

export interface WorkerProvenanceEnvelope {
  statement: WorkerProvenanceStatement;
  keyId: string;
  signature: string;
}

export interface WorkerRuntimeAttestationStatement {
  schemaVersion: 1;
  challengeSha256: string;
  workerName: string;
  platform: string;
  architecture: string;
  region: string;
  residencyDomain: string;
  releaseId: string;
  artifactDigest: string;
  provenanceSha256: string;
  provider: string;
  workloadIdentity: string;
  selectorSha256: string;
  imageDigest: string;
  hardwareBacked: boolean;
  issuedAt: string;
  expiresAt: string;
}

export interface WorkerRuntimeAttestationEnvelope {
  statement: WorkerRuntimeAttestationStatement;
  keyId: string;
  signature: string;
}

export interface WorkerRuntimeAttestationChallengeInput {
  enrollmentToken: string;
  name: string;
  platform: string;
  architecture?: string;
  region?: string;
  residencyDomain?: string;
  release?: WorkerReleaseIdentity;
}

export interface WorkerRuntimeAttestationChallenge {
  schemaVersion: 1;
  id: string;
  challenge: string;
  challengeSha256: string;
  expiresAt: string;
  binding: {
    workerName: string;
    platform: string;
    architecture: string;
    region: string;
    residencyDomain: string;
    releaseId: string;
    artifactDigest: string;
    provenanceSha256: string;
  };
}

export interface RegisterWorkerReleaseInput {
  manifest: WorkerReleaseManifest;
  keyId: string;
  signature: string;
  provenance?: WorkerProvenanceEnvelope;
}

export interface WorkerRolloutInput {
  releaseId: string;
  region: string;
  ring: string;
  percentage: number;
  expectedRevision: number;
}

export interface CreateKnowledgeCollectionInput {
  name: string;
  description?: string;
  embeddingModel: string;
  chunkSize?: number;
  chunkOverlap?: number;
  topK?: number;
}

export interface IngestKnowledgeDocumentInput {
  name: string;
  sourceUri?: string;
  mediaType?: string;
  content: string;
}

export interface UploadKnowledgeDocumentInput {
  name: string;
  sourceUri?: string;
  mediaType: string;
  contentBase64: string;
}

export interface KnowledgePageLocation {
  pageNumber: number;
  charStart: number;
  charEnd: number;
}

export type KnowledgeDocumentStatus = "pending" | "indexing" | "ready" | "failed";
export type MemoryKind = "working" | "episodic";

export interface SaveMemoryInput {
  kind: MemoryKind;
  content: string;
  agentId?: string | null;
  ttlSeconds?: number;
}

export interface KnowledgeEmbeddingLease {
  leaseId: string;
  expiresAt: string;
  projectId: string;
  collection: {
    id: string;
    name: string;
    embeddingModel: string;
  };
  document: {
    id: string;
    name: string;
  };
  chunks: Array<{
    id: string;
    ordinal: number;
    content: string;
  }>;
}

export interface KnowledgeEmbeddingResult {
  chunkId: string;
  embedding: number[];
}

export interface KnowledgeSearchQuery {
  embeddingModel: string;
  collectionIds: string[];
  vector: number[];
  topK?: number;
}

export interface KnowledgeSearchRequest {
  queries: KnowledgeSearchQuery[];
}

export interface KnowledgeProvenance {
  projectId: string;
  collectionId: string;
  collectionName: string;
  documentId: string;
  documentName: string;
  sourceUri: string | null;
  documentSha256: string;
  chunkId: string;
  chunkOrdinal: number;
  charStart: number;
  charEnd: number;
  chunkSha256: string;
  pageNumber: number | null;
  originalSha256: string | null;
}

export interface KnowledgeSearchHit {
  marker: string;
  score: number;
  content: string;
  provenance: KnowledgeProvenance;
}

export interface KnowledgeSource {
  retrievalId: string;
  stageId: string;
  createdAt: string;
  marker: string;
  excerpt: string;
  content?: string;
  provenance: KnowledgeProvenance;
}

export interface CreateAgentInput {
  name: string;
  role: string;
  systemPrompt: string;
  model?: string | null;
  runtime?: AgentRuntime;
  runtimeConfig?: Partial<AgentRuntimeConfig>;
}

export interface LeasePayload {
  leaseId: string;
  expiresAt: string;
  traceContext: {
    traceId: string;
    traceparent: string;
  };
  run: {
    id: string;
    name: string;
    input: string;
    resultDestination: ResultDestination;
    artifactPath: string | null;
  };
  stage: {
    id: string;
    position: number;
    attempt: number;
    processNodeId: string | null;
  };
  agent: {
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
  };
  context: Array<{
    agentName: string;
    output: string;
  }>;
  routing: ModelRoutingDecision | null;
  mcpTools: McpLeaseTool[];
  knowledge: {
    groups: Array<{
      embeddingModel: string;
      collectionIds: string[];
      topK: number;
    }>;
    memory: Array<{
      id: string;
      kind: MemoryKind;
      content: string;
      agentId: string | null;
      expiresAt: string | null;
      createdAt: string;
    }>;
  };
  activity?: {
    kind: "http";
    request: {
      method: HttpMethod;
      url: string;
      headers: Record<string, string>;
      body: string | null;
      timeoutSeconds: number;
      maxResponseBytes: number;
    };
  };
}

export interface EventRecord {
  id: number;
  runId: string | null;
  stageId: string | null;
  nodeId: string | null;
  level: string;
  type: string;
  message: string;
  data: Record<string, unknown> | null;
  createdAt: string;
}
