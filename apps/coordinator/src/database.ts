import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

import {
  defaultProcessGraph,
  evaluateProcessCondition,
  normalizeProcessDraftGraph,
  normalizeProcessGraph,
  normalizeProcessIdentity,
  outgoingEdge,
  outgoingEdges,
} from "./process-engine.js";
import { exportProcessBpmn as serializeProcessBpmn, importProcessBpmn as parseProcessBpmn } from "./process-bpmn.js";
import { diffProcessDocuments } from "./process-versioning.js";
import { decryptCredential, encryptCredential } from "./credentials.js";
import { renderProcessTemplate } from "./process-expressions.js";
import { createToken, hashToken, tokensEqual } from "./security.js";
import { CoordinatorTelemetry } from "./telemetry.js";
import {
  chunkKnowledgeText,
  cosineSimilarity,
  KNOWLEDGE_EMBEDDING_BATCH_SIZE,
  KNOWLEDGE_MAX_SEARCH_CANDIDATES,
  normalizeEmbeddingVector,
  normalizeKnowledgeCollectionIds,
  normalizeKnowledgeCollectionInput,
  normalizeKnowledgeDocumentInput,
  normalizeMemoryInput,
} from "./knowledge.js";
import {
  MCP_PROTOCOL_VERSION,
  effectiveMcpPolicy,
  isReservedMcpCredentialHeader,
  mcpToolRisk,
  normalizeMcpServerInput,
  normalizeMcpServerPatch,
  type McpApprovalExecution,
  type McpApprovalResult,
  type McpDecisionActor,
  type McpServerConnection,
  type ResolvedMcpLeaseTool,
  type StoredMcpCall,
} from "./mcp.js";
import {
  DEFAULT_MCP_POLICY,
  evaluateMcpPolicy,
  mcpArgumentsPreviewDiff,
  mcpPolicySha256,
  normalizeMcpPolicyDocument,
  serializeMcpPolicy,
} from "./mcp-policy.js";
import {
  A2A_ADAPTER_VERSION,
  A2A_PROTOCOL_VERSION,
  normalizeA2AEndpointInput,
  runStatusToA2AState,
} from "./a2a.js";
import type {
  A2AEndpointConnection,
  A2AMessage,
  A2ANormalizedMessage,
  A2ANormalizedPushConfig,
  A2ARemoteAuthMaterial,
  A2ARemoteConnection,
  A2ATaskState,
  AgentExecutionSnapshot,
  AgentRuntime,
  AgentRuntimeConfig,
  AgentRuntimeProfile,
  SpecialistExecutionSnapshot,
  ToolLoopAgentRuntimeConfig,
  CreateCredentialInput,
  CredentialScope,
  CreateAgentInput,
  CreateA2AEndpointInput,
  CreateA2ARemoteInput,
  CreateEvalDatasetInput,
  CreateEvalDatasetVersionInput,
  CreateEvalExperimentInput,
  CreateKnowledgeCollectionInput,
  CreatePromptInput,
  CreatePromptVersionInput,
  CreateProcessInput,
  CreateProjectInput,
  CreateRunInput,
  CreateMcpServerInput,
  DurableProcessStart,
  DurableProcessState,
  EventRecord,
  HttpMethod,
  LeasePayload,
  IngestKnowledgeDocumentInput,
  HumanEvalReviewInput,
  JudgeEvalExperimentInput,
  KnowledgeEmbeddingLease,
  KnowledgeEmbeddingResult,
  KnowledgeSearchHit,
  KnowledgeSearchRequest,
  McpCatalogTool,
  McpLeaseTool,
  McpPolicyDecision,
  McpPolicyDocument,
  McpPolicyPreview,
  McpPolicySnapshot,
  McpRiskDecision,
  McpToolPolicy,
  McpToolRisk,
  ModelRouterPolicy,
  ModelRouterStrategy,
  ModelRoutingDecision,
  ProcessBranch,
  ProcessGraph,
  ProcessGraphNode,
  ProcessVersionDiff,
  ReplayProcessInstanceInput,
  CreateProcessWebhookInput,
  DeliverProcessWebhookInput,
  ReplayRunInput,
  PromotePromptInput,
  ResultDestination,
  SaveMemoryInput,
  SchedulerMode,
  StartProcessInput,
  TestProcessNodeInput,
  TestProcessNodeResult,
  UpdateProcessInput,
  UpdateMcpServerInput,
  UpdateA2AEndpointInput,
  UpdateA2ARemoteInput,
  WorkerArtifactInput,
  WorkerCapabilities,
  WorkerExecutionMetrics,
  WorkerMetrics,
  WorkerModelProfile,
  WorkerRegistration,
} from "./types.js";

type SqlScalar = string | number | bigint | Uint8Array | null;
type Row = Record<string, SqlScalar>;
type ActiveMcpPolicy = {
  version: number;
  sha256: string;
  document: McpPolicyDocument;
  actor: string;
  createdAt: string;
};

function nowIso(): string {
  return new Date().toISOString();
}

function futureIso(seconds: number): string {
  return new Date(Date.now() + seconds * 1_000).toISOString();
}

function parseJson<T>(value: unknown, fallback: T): T {
  if (typeof value !== "string" || value.length === 0) return fallback;
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

function sha256Text(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

const MCP_SENSITIVE_KEY = /(authorization|credential|password|secret|token|api[_-]?key|private[_-]?key)/i;

function redactMcpValue(value: unknown, depth = 0): unknown {
  if (depth >= 4) return "[truncated]";
  if (typeof value === "string") return value.length > 160 ? `${value.slice(0, 157)}…` : value;
  if (typeof value === "number" || typeof value === "boolean" || value === null) return value;
  if (Array.isArray(value)) return value.slice(0, 20).map((item) => redactMcpValue(item, depth + 1));
  if (!value || typeof value !== "object") return String(value);
  return Object.fromEntries(Object.entries(value as Record<string, unknown>).slice(0, 40).map(([key, field]) => [
    key,
    MCP_SENSITIVE_KEY.test(key) ? "[redacted]" : redactMcpValue(field, depth + 1),
  ]));
}

function encryptJson(value: unknown, secret: string): string {
  return encryptCredential({ payload: JSON.stringify(value) }, secret);
}

function decryptJson(value: unknown, secret: string): unknown {
  if (typeof value !== "string" || !value) return null;
  return JSON.parse(decryptCredential(value, secret).payload ?? "null") as unknown;
}

function agentSnapshot(
  row: Record<string, unknown>,
  source: AgentExecutionSnapshot["source"],
  capturedAt = nowIso(),
  modelOverride?: string | null,
  specialists: SpecialistExecutionSnapshot[] = [],
): AgentExecutionSnapshot {
  const runtime = normalizeAgentRuntime(row.runtime);
  const runtimeConfig = normalizeAgentRuntimeConfig(parseJson<Record<string, unknown>>(row.runtime_config_json, {}));
  const model = modelOverride === undefined
    ? (typeof row.model === "string" && row.model ? row.model : null)
    : modelOverride;
  const definition = {
    schemaVersion: 3 as const,
    id: String(row.id),
    name: String(row.name),
    role: String(row.role),
    systemPrompt: String(row.system_prompt),
    model,
    runtime,
    runtimeConfig,
    specialists,
    registryPromptId: typeof row.registry_prompt_id === "string" ? row.registry_prompt_id : null,
    registryPromptVersion: typeof row.registry_prompt_version === "number"
      ? row.registry_prompt_version
      : typeof row.registry_prompt_version === "bigint"
        ? Number(row.registry_prompt_version)
        : null,
  };
  return {
    ...definition,
    capturedAt,
    source,
    promptVersion: sha256Text(definition.systemPrompt),
    definitionVersion: sha256Text(JSON.stringify(definition)),
  };
}

function specialistExecutionSnapshot(row: Record<string, unknown>): SpecialistExecutionSnapshot {
  const runtimeConfig = normalizeAgentRuntimeConfig(parseJson<Record<string, unknown>>(row.runtime_config_json, {}));
  if (runtimeConfig.profile !== "tool_loop_v1") throw new Error("Вложенные specialist teams запрещены");
  const model = typeof row.model === "string" && row.model ? row.model : null;
  const definition = {
    schemaVersion: 1 as const,
    id: String(row.id),
    name: String(row.name),
    role: String(row.role),
    systemPrompt: String(row.system_prompt),
    model,
    runtimeConfig: runtimeConfig as ToolLoopAgentRuntimeConfig,
    registryPromptId: typeof row.registry_prompt_id === "string" ? row.registry_prompt_id : null,
    registryPromptVersion: typeof row.registry_prompt_version === "number"
      ? row.registry_prompt_version
      : typeof row.registry_prompt_version === "bigint"
        ? Number(row.registry_prompt_version)
        : null,
  };
  return {
    ...definition,
    promptVersion: sha256Text(definition.systemPrompt),
    definitionVersion: sha256Text(JSON.stringify(definition)),
  };
}

function parseAgentSnapshot(value: unknown): AgentExecutionSnapshot | null {
  const snapshot = parseJson<Partial<AgentExecutionSnapshot> | null>(value, null);
  if (!snapshot || ![1, 2, 3].includes(Number(snapshot.schemaVersion)) || typeof snapshot.id !== "string") return null;
  if (typeof snapshot.name !== "string" || typeof snapshot.role !== "string" || typeof snapshot.systemPrompt !== "string") {
    return null;
  }
  try {
    const runtimeConfig = normalizeAgentRuntimeConfig(snapshot.runtimeConfig);
    const rawSpecialists = Array.isArray(snapshot.specialists) ? snapshot.specialists : [];
    const specialists = rawSpecialists.map((candidate): SpecialistExecutionSnapshot => {
      if (!candidate || typeof candidate !== "object") throw new Error("Некорректный snapshot specialist");
      const raw = candidate as Partial<SpecialistExecutionSnapshot>;
      const memberConfig = normalizeAgentRuntimeConfig(raw.runtimeConfig);
      if (memberConfig.profile !== "tool_loop_v1") throw new Error("Вложенные specialist teams запрещены");
      if (typeof raw.id !== "string" || typeof raw.name !== "string" || typeof raw.role !== "string"
        || typeof raw.systemPrompt !== "string") {
        throw new Error("Некорректный snapshot specialist");
      }
      const model = typeof raw.model === "string" && raw.model ? raw.model : null;
      const definition = {
        schemaVersion: 1 as const,
        id: raw.id,
        name: raw.name,
        role: raw.role,
        systemPrompt: raw.systemPrompt,
        model,
        runtimeConfig: memberConfig,
        registryPromptId: typeof raw.registryPromptId === "string" ? raw.registryPromptId : null,
        registryPromptVersion: typeof raw.registryPromptVersion === "number"
          && Number.isInteger(raw.registryPromptVersion)
          && raw.registryPromptVersion > 0
          ? raw.registryPromptVersion
          : null,
      };
      return {
        ...definition,
        promptVersion: typeof raw.promptVersion === "string"
          ? raw.promptVersion
          : sha256Text(raw.systemPrompt),
        definitionVersion: typeof raw.definitionVersion === "string"
          ? raw.definitionVersion
          : sha256Text(JSON.stringify(definition)),
      };
    });
    if (runtimeConfig.profile === "specialist_team_v1") {
      if (specialists.length !== runtimeConfig.specialistAgentIds.length
        || specialists.some((specialist, index) => specialist.id !== runtimeConfig.specialistAgentIds[index])) {
        throw new Error("Snapshot команды не соответствует runtime config");
      }
    } else if (specialists.length > 0) {
      throw new Error("Обычный agent snapshot не должен содержать specialists");
    }
    return {
      schemaVersion: 3,
      capturedAt: typeof snapshot.capturedAt === "string" ? snapshot.capturedAt : nowIso(),
      source: snapshot.source ?? "migration_backfill",
      id: snapshot.id,
      name: snapshot.name,
      role: snapshot.role,
      systemPrompt: snapshot.systemPrompt,
      model: typeof snapshot.model === "string" && snapshot.model ? snapshot.model : null,
      runtime: normalizeAgentRuntime(snapshot.runtime),
      runtimeConfig,
      specialists,
      promptVersion: typeof snapshot.promptVersion === "string"
        ? snapshot.promptVersion
        : sha256Text(snapshot.systemPrompt),
      definitionVersion: typeof snapshot.definitionVersion === "string"
        ? snapshot.definitionVersion
        : sha256Text(JSON.stringify(snapshot)),
      registryPromptId: typeof snapshot.registryPromptId === "string" ? snapshot.registryPromptId : null,
      registryPromptVersion: typeof snapshot.registryPromptVersion === "number"
        && Number.isInteger(snapshot.registryPromptVersion)
        && snapshot.registryPromptVersion > 0
        ? snapshot.registryPromptVersion
        : null,
    };
  } catch {
    return null;
  }
}

function pinnedAgentModels(snapshot: AgentExecutionSnapshot): string[] {
  return [...new Set([
    snapshot.model,
    ...snapshot.specialists.map((specialist) => specialist.model),
  ].filter((model): model is string => typeof model === "string" && model.length > 0))];
}

function normalizeExecutionMetrics(value: unknown): WorkerExecutionMetrics {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const raw = value as Record<string, unknown>;
  const metric = (key: string, max: number): number | undefined => {
    const candidate = raw[key];
    return typeof candidate === "number" && Number.isFinite(candidate) && candidate >= 0
      ? Math.min(max, Math.trunc(candidate))
      : undefined;
  };
  const text = (key: string, max: number): string | undefined => {
    const candidate = raw[key];
    return typeof candidate === "string" && candidate.trim()
      ? candidate.trim().slice(0, max)
      : undefined;
  };
  const decimalMetric = (key: string, max: number): number | undefined => {
    const candidate = raw[key];
    return typeof candidate === "number" && Number.isFinite(candidate) && candidate >= 0
      ? Math.min(max, Math.round(candidate * 1_000) / 1_000)
      : undefined;
  };
  return {
    durationMs: metric("durationMs", 86_400_000),
    modelDurationMs: metric("modelDurationMs", 86_400_000),
    modelCalls: metric("modelCalls", 10_000),
    inputTokens: metric("inputTokens", 1_000_000_000),
    outputTokens: metric("outputTokens", 1_000_000_000),
    toolCalls: metric("toolCalls", 10_000),
    model: text("model", 200),
    provider: text("provider", 100),
    toolSchemaVersion: text("toolSchemaVersion", 200),
    energyJoules: decimalMetric("energyJoules", 1_000_000_000),
  };
}

export const DEFAULT_MODEL_ROUTER_POLICY: ModelRouterPolicy = {
  enabled: true,
  strategy: "balanced",
  minContextTokens: 0,
  minQualityScore: 0,
  minBatteryPercent: 30,
  maxTemperatureC: 85,
  allowUnknownProfiles: true,
};

function normalizeModelRouterPolicy(value: unknown): ModelRouterPolicy {
  const raw = value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
  const strategy: ModelRouterStrategy = ["balanced", "performance", "efficiency"].includes(String(raw.strategy))
    ? String(raw.strategy) as ModelRouterStrategy
    : DEFAULT_MODEL_ROUTER_POLICY.strategy;
  const integer = (key: string, min: number, max: number, fallback: number): number => {
    const candidate = raw[key];
    return typeof candidate === "number" && Number.isFinite(candidate)
      ? Math.min(max, Math.max(min, Math.trunc(candidate)))
      : fallback;
  };
  return {
    enabled: typeof raw.enabled === "boolean" ? raw.enabled : DEFAULT_MODEL_ROUTER_POLICY.enabled,
    strategy,
    minContextTokens: integer("minContextTokens", 0, 2_000_000, DEFAULT_MODEL_ROUTER_POLICY.minContextTokens),
    minQualityScore: integer("minQualityScore", 0, 100, DEFAULT_MODEL_ROUTER_POLICY.minQualityScore),
    minBatteryPercent: integer("minBatteryPercent", 0, 100, DEFAULT_MODEL_ROUTER_POLICY.minBatteryPercent),
    maxTemperatureC: integer("maxTemperatureC", 0, 150, DEFAULT_MODEL_ROUTER_POLICY.maxTemperatureC),
    allowUnknownProfiles: typeof raw.allowUnknownProfiles === "boolean"
      ? raw.allowUnknownProfiles
      : DEFAULT_MODEL_ROUTER_POLICY.allowUnknownProfiles,
  };
}

function normalizeModelProfiles(value: unknown, models: string[]): WorkerModelProfile[] {
  if (!Array.isArray(value)) return [];
  const advertised = new Set(models);
  const profiles = new Map<string, WorkerModelProfile>();
  const boundedNumber = (candidate: unknown, min: number, max: number): number | undefined =>
    typeof candidate === "number" && Number.isFinite(candidate) && candidate >= min
      ? Math.min(max, Math.round(candidate))
      : undefined;
  const boundedText = (candidate: unknown, max: number): string | undefined =>
    typeof candidate === "string" && candidate.trim() ? candidate.trim().slice(0, max) : undefined;

  for (const candidate of value.slice(0, 128)) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) continue;
    const raw = candidate as Record<string, unknown>;
    const name = boundedText(raw.name, 200);
    if (!name || !advertised.has(name) || profiles.has(name)) continue;
    const capabilities = Array.isArray(raw.capabilities)
      ? [...new Set(raw.capabilities
        .filter((item): item is string => typeof item === "string")
        .map((item) => item.trim().toLowerCase().slice(0, 60))
        .filter(Boolean))].slice(0, 32)
      : undefined;
    const discoveredAt = boundedText(raw.discoveredAt, 40);
    profiles.set(name, {
      name,
      ...(boundedText(raw.provider, 80) ? { provider: boundedText(raw.provider, 80) } : {}),
      ...(boundedNumber(raw.contextWindow, 1, 2_000_000) !== undefined
        ? { contextWindow: boundedNumber(raw.contextWindow, 1, 2_000_000) }
        : {}),
      ...(boundedNumber(raw.sizeBytes, 0, Number.MAX_SAFE_INTEGER) !== undefined
        ? { sizeBytes: boundedNumber(raw.sizeBytes, 0, Number.MAX_SAFE_INTEGER) }
        : {}),
      ...(boundedNumber(raw.parameterCount, 0, Number.MAX_SAFE_INTEGER) !== undefined
        ? { parameterCount: boundedNumber(raw.parameterCount, 0, Number.MAX_SAFE_INTEGER) }
        : {}),
      ...(boundedText(raw.parameterSize, 40) ? { parameterSize: boundedText(raw.parameterSize, 40) } : {}),
      ...(boundedText(raw.quantization, 40) ? { quantization: boundedText(raw.quantization, 40) } : {}),
      ...(capabilities ? { capabilities } : {}),
      ...(boundedNumber(raw.qualityScore, 0, 100) !== undefined
        ? { qualityScore: boundedNumber(raw.qualityScore, 0, 100) }
        : {}),
      ...(discoveredAt && Number.isFinite(Date.parse(discoveredAt)) ? { discoveredAt } : {}),
    });
  }
  return [...profiles.values()];
}

interface StoredModelBenchmark {
  samples: number;
  outputTokens: number;
  modelDurationMs: number;
  tokensPerSecond: number;
  joulesPer1kTokens: number | null;
  lastObservedAt: string;
}

interface RoutingNode {
  row: Row;
  models: string[];
  modelProfiles: WorkerModelProfile[];
  benchmarks: Map<string, StoredModelBenchmark>;
  runtimes: Set<AgentRuntime>;
  runtimeProfiles: Set<AgentRuntimeProfile>;
  metrics: WorkerMetrics;
  freeSlots: number;
}

interface RoutingOption {
  node: RoutingNode;
  model: string | null;
  profile: WorkerModelProfile | null;
  benchmark: StoredModelBenchmark | null;
  footprint: number;
  health: number;
  uncertain: boolean;
}

function clampInteger(value: number | undefined, min: number, max: number, fallback: number): number {
  if (!Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, Math.trunc(value as number)));
}

function normalizeResultDestination(value: unknown): ResultDestination {
  if (value === undefined || value === null || value === "history") return "history";
  if (value === "artifacts") return value;
  throw new Error("Неизвестное назначение результата");
}

function normalizeArtifactPath(value: unknown): string {
  if (value === undefined || value === null || value === "") return "";
  if (typeof value !== "string") throw new Error("Каталог артефактов должен быть строкой");
  const normalized = value.trim().normalize("NFC").replaceAll("\\", "/");
  if (!normalized) return "";
  if (normalized.length > 180 || normalized.startsWith("/") || /^[A-Za-z]:\//.test(normalized)) {
    throw new Error("Каталог артефактов должен быть относительным и не длиннее 180 символов");
  }
  const segments = normalized.split("/");
  if (segments.some((segment) =>
    !segment
    || segment === "."
    || segment === ".."
    || !/^[\p{L}\p{N}][\p{L}\p{N}._ -]*$/u.test(segment)
  )) {
    throw new Error("Каталог артефактов содержит недопустимый сегмент");
  }
  return segments.join("/");
}

function safeArtifactName(value: unknown, fallback = "artifact.txt"): string {
  if (typeof value !== "string") return fallback;
  const normalized = value.trim().normalize("NFC");
  if (!normalized || normalized.length > 160 || normalized.includes("/") || normalized.includes("\\")) return fallback;
  const safe = normalized
    .replace(/[^\p{L}\p{N}._ -]+/gu, "-")
    .replace(/^[. ]+|[. ]+$/g, "")
    .slice(0, 160);
  return safe && safe !== "." && safe !== ".." ? safe : fallback;
}

function safeArtifactSlug(value: unknown): string {
  return safeArtifactName(value, "stage")
    .replace(/\.[^.]+$/, "")
    .replace(/\s+/g, "-")
    .toLowerCase();
}

function normalizeProjectId(value: unknown): string {
  if (typeof value !== "string" || !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(value)) {
    throw new Error("Некорректный идентификатор проекта");
  }
  return value;
}

function requiredAgentText(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string") throw new Error(`${field} должен быть строкой`);
  const normalized = value.trim();
  if (!normalized) throw new Error(`${field} обязателен`);
  if (normalized.length > maxLength) throw new Error(`${field}: максимум ${maxLength} символов`);
  return normalized;
}

function normalizeAgentRuntime(value: unknown): AgentRuntime {
  if (value === undefined || value === null || value === "single") return "single";
  if (value === "langgraph") return value;
  throw new Error("Неизвестный runtime агента");
}

function normalizeAgentRuntimeConfig(value: unknown): AgentRuntimeConfig {
  if (value !== undefined && value !== null && (typeof value !== "object" || Array.isArray(value))) {
    throw new Error("Конфигурация runtime должна быть объектом");
  }
  const config = (value ?? {}) as Record<string, unknown>;
  const profile = config.profile ?? "tool_loop_v1";
  const maxIterations = config.maxIterations ?? 6;
  if (!Number.isInteger(maxIterations) || Number(maxIterations) < 1 || Number(maxIterations) > 12) {
    throw new Error("Лимит итераций runtime должен быть целым числом от 1 до 12");
  }
  if (profile === "tool_loop_v1") {
    return { profile, maxIterations: Number(maxIterations) };
  }
  if (profile !== "specialist_team_v1") throw new Error("Неизвестный профиль runtime агента");

  const maxHandoffs = config.maxHandoffs ?? 4;
  if (!Number.isInteger(maxHandoffs) || Number(maxHandoffs) < 1 || Number(maxHandoffs) > 8) {
    throw new Error("Лимит handoff должен быть целым числом от 1 до 8");
  }
  const stateSchema = config.stateSchema ?? "specialist_team_state_v1";
  if (stateSchema !== "specialist_team_state_v1") {
    throw new Error("Неизвестная state schema команды агентов");
  }
  if (!Array.isArray(config.specialistAgentIds)
    || config.specialistAgentIds.length < 2
    || config.specialistAgentIds.length > 8) {
    throw new Error("Команда должна содержать от 2 до 8 специалистов");
  }
  const specialistAgentIds = config.specialistAgentIds.map((value) => {
    if (typeof value !== "string") throw new Error("Идентификатор специалиста должен быть строкой");
    const id = value.trim();
    if (!id || id.length > 128 || /[\u0000-\u001f\u007f]/u.test(id)) {
      throw new Error("Некорректный идентификатор специалиста");
    }
    return id;
  });
  if (new Set(specialistAgentIds).size !== specialistAgentIds.length) {
    throw new Error("Специалисты команды не должны повторяться");
  }
  return {
    profile,
    maxIterations: Number(maxIterations),
    maxHandoffs: Number(maxHandoffs),
    stateSchema,
    specialistAgentIds,
  };
}

function normalizeAgentRuntimes(value: unknown): AgentRuntime[] {
  if (value === undefined || value === null) return ["single"];
  if (!Array.isArray(value)) throw new Error("agentRuntimes должен быть массивом");
  const runtimes = [...new Set(value.map((item) => normalizeAgentRuntime(item)))];
  if (runtimes.length === 0) throw new Error("Worker должен поддерживать хотя бы один runtime агента");
  return runtimes;
}

function normalizeAgentRuntimeProfiles(value: unknown): AgentRuntimeProfile[] {
  if (value === undefined || value === null) return ["tool_loop_v1"];
  if (!Array.isArray(value)) throw new Error("agentRuntimeProfiles должен быть массивом");
  if (value.length > 16) throw new Error("Worker объявил слишком много agent runtime profiles");
  const profiles = value.map((profile): AgentRuntimeProfile => {
    if (profile === "tool_loop_v1" || profile === "specialist_team_v1") return profile;
    throw new Error("Worker объявил неизвестный agent runtime profile");
  });
  return [...new Set(profiles)];
}

function normalizeEmbeddingModels(value: unknown): string[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new Error("embeddingModels должен быть массивом");
  if (value.length > 64) throw new Error("Worker не может объявить больше 64 embedding-моделей");
  const models = value.map((item) => {
    if (typeof item !== "string") throw new Error("Имя embedding-модели должно быть строкой");
    const model = item.trim();
    if (!model || model.length > 200 || /[\u0000-\u001f\u007f]/u.test(model)) {
      throw new Error("Имя embedding-модели должно содержать от 1 до 200 печатных символов");
    }
    return model;
  });
  return [...new Set(models)];
}

function normalizeAgentInput(input: CreateAgentInput): {
  name: string;
  role: string;
  systemPrompt: string;
  model: string;
  runtime: AgentRuntime;
  runtimeConfig: AgentRuntimeConfig;
} {
  const rawModel: unknown = input.model;
  const model = rawModel === null || rawModel === undefined || (typeof rawModel === "string" && rawModel.trim() === "")
    ? ""
    : requiredAgentText(rawModel, "Модель", 200);
  const runtime = normalizeAgentRuntime(input.runtime);
  const runtimeConfig = normalizeAgentRuntimeConfig(input.runtimeConfig);
  if (runtimeConfig.profile === "specialist_team_v1" && runtime !== "langgraph") {
    throw new Error("Профиль specialist_team_v1 требует runtime langgraph");
  }
  return {
    name: requiredAgentText(input.name, "Имя агента", 80),
    role: requiredAgentText(input.role, "Роль агента", 280),
    systemPrompt: requiredAgentText(input.systemPrompt, "Системный промпт", 20_000),
    model,
    runtime,
    runtimeConfig,
  };
}

interface NormalizedEvalRubricCriterion {
  id: string;
  label: string;
  description: string;
  weight: number;
}

interface NormalizedEvalExample {
  id: string;
  name: string;
  input: string;
  referenceOutput: string;
  requiredTerms: string[];
  forbiddenTerms: string[];
  knowledgeCollectionIds: string[];
}

function optionalText(value: unknown, field: string, maxLength: number): string {
  if (value === undefined || value === null) return "";
  if (typeof value !== "string") throw new Error(`${field} должен быть строкой`);
  const normalized = value.trim();
  if (normalized.length > maxLength) throw new Error(`${field}: максимум ${maxLength} символов`);
  return normalized;
}

function normalizeEvalRubric(value: unknown): NormalizedEvalRubricCriterion[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value) || value.length > 20) {
    throw new Error("Rubric должен содержать не больше 20 критериев");
  }
  const usedIds = new Set<string>();
  return value.map((candidate, index) => {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
      throw new Error(`Критерий ${index + 1} задан некорректно`);
    }
    const raw = candidate as Record<string, unknown>;
    const label = requiredAgentText(raw.label, `Название критерия ${index + 1}`, 120);
    const fallbackId = label
      .normalize("NFKD")
      .toLocaleLowerCase("ru")
      .replace(/[^a-z0-9а-яё]+/giu, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 48) || `criterion_${index + 1}`;
    const id = optionalText(raw.id, `ID критерия ${index + 1}`, 64) || fallbackId;
    if (!/^[a-z0-9а-яё][a-z0-9а-яё_-]{0,63}$/iu.test(id)) {
      throw new Error(`ID критерия ${index + 1} содержит недопустимые символы`);
    }
    const normalizedId = id.toLocaleLowerCase("ru");
    if (usedIds.has(normalizedId)) throw new Error(`ID критерия «${id}» повторяется`);
    usedIds.add(normalizedId);
    const weight = raw.weight === undefined ? 1 : Number(raw.weight);
    if (!Number.isFinite(weight) || weight <= 0 || weight > 100) {
      throw new Error(`Вес критерия «${label}» должен быть больше 0 и не больше 100`);
    }
    return {
      id,
      label,
      description: optionalText(raw.description, `Описание критерия ${index + 1}`, 1_000),
      weight: Math.round(weight * 1_000) / 1_000,
    };
  });
}

function normalizeEvalTerms(value: unknown, field: string): string[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value) || value.length > 50) throw new Error(`${field}: максимум 50 значений`);
  const terms = value.map((candidate, index) => {
    if (typeof candidate !== "string") throw new Error(`${field}, значение ${index + 1}: нужна строка`);
    const term = candidate.trim();
    if (!term || term.length > 300) throw new Error(`${field}, значение ${index + 1}: от 1 до 300 символов`);
    return term;
  });
  return [...new Map(terms.map((term) => [term.toLocaleLowerCase("ru"), term])).values()];
}

function normalizeEvalExamples(value: unknown): Array<Omit<NormalizedEvalExample, "knowledgeCollectionIds"> & {
  knowledgeCollectionIds: string[];
}> {
  if (!Array.isArray(value) || value.length < 1 || value.length > 200) {
    throw new Error("Golden dataset должен содержать от 1 до 200 примеров");
  }
  return value.map((candidate, index) => {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
      throw new Error(`Пример ${index + 1} задан некорректно`);
    }
    const raw = candidate as Record<string, unknown>;
    const input = requiredAgentText(raw.input, `Input примера ${index + 1}`, 100_000);
    const name = optionalText(raw.name, `Название примера ${index + 1}`, 120) || `Пример ${index + 1}`;
    return {
      id: randomUUID(),
      name,
      input,
      referenceOutput: optionalText(raw.referenceOutput, `Reference примера ${index + 1}`, 100_000),
      requiredTerms: normalizeEvalTerms(raw.requiredTerms, `Обязательные фразы примера ${index + 1}`),
      forbiddenTerms: normalizeEvalTerms(raw.forbiddenTerms, `Запрещённые фразы примера ${index + 1}`),
      knowledgeCollectionIds: normalizeKnowledgeCollectionIds(raw.knowledgeCollectionIds),
    };
  });
}

function normalizeEvalScore(value: unknown, field: string): number {
  const score = Number(value);
  if (!Number.isFinite(score) || score < 0 || score > 100) {
    throw new Error(`${field} должен быть числом от 0 до 100`);
  }
  return Math.round(score * 100) / 100;
}

function normalizedEvalText(value: string): string {
  return value.normalize("NFKC").toLocaleLowerCase("ru").replace(/\s+/g, " ").trim();
}

function parseJudgeOutput(value: string): Record<string, unknown> | null {
  const trimmed = value.trim();
  const candidates = [trimmed];
  const fenced = /```(?:json)?\s*([\s\S]*?)```/iu.exec(trimmed)?.[1]?.trim();
  if (fenced) candidates.push(fenced);
  const objectStart = trimmed.indexOf("{");
  const objectEnd = trimmed.lastIndexOf("}");
  if (objectStart >= 0 && objectEnd > objectStart) candidates.push(trimmed.slice(objectStart, objectEnd + 1));
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate) as unknown;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as Record<string, unknown>;
    } catch {
      // Try the next safe extraction. Raw model output remains available in the run trace.
    }
  }
  return null;
}

function normalizeCredentialScope(
  value: CreateCredentialInput["scope"] | undefined,
  fallback?: CredentialScope,
): CredentialScope {
  if (value === undefined && fallback) return fallback;
  if (!value || value.kind === undefined || value.kind === "project") {
    return {
      kind: "project",
      serverNamespaces: [],
      toolPatterns: [],
      risks: ["read", "write", "destructive", "unknown"],
      allowCatalog: true,
      expiresAt: null,
    };
  }
  if (value.kind !== "mcp") throw new Error("Неизвестный scope credentials");
  const namespaces = Array.isArray(value.serverNamespaces)
    ? [...new Set(value.serverNamespaces.map((item) => typeof item === "string" ? item.trim().toLowerCase() : ""))]
    : [];
  if (namespaces.length === 0 || namespaces.length > 50
    || namespaces.some((item) => !/^[a-z][a-z0-9_]{0,23}$/.test(item))) {
    throw new Error("MCP scope требует от 1 до 50 точных server namespaces");
  }
  const toolPatterns = Array.isArray(value.toolPatterns)
    ? [...new Set(value.toolPatterns.map((item) => typeof item === "string" ? item.trim() : ""))]
    : ["*"];
  if (toolPatterns.length === 0 || toolPatterns.length > 100
    || toolPatterns.some((item) => !/^[A-Za-z0-9_.*?-]{1,256}$/.test(item))) {
    throw new Error("MCP scope toolPatterns содержит недопустимый шаблон");
  }
  const risks: McpToolRisk[] = Array.isArray(value.risks)
    ? [...new Set(value.risks)]
    : ["read", "write", "destructive", "unknown"];
  if (risks.length === 0 || risks.some((risk) => !(["read", "write", "destructive", "unknown"] as const).includes(risk))) {
    throw new Error("MCP scope risks содержит недопустимый класс риска");
  }
  let expiresAt: string | null = null;
  if (value.expiresAt !== undefined && value.expiresAt !== null && value.expiresAt !== "") {
    if (typeof value.expiresAt !== "string" || !Number.isFinite(Date.parse(value.expiresAt))) {
      throw new Error("MCP scope expiresAt должен быть ISO timestamp");
    }
    expiresAt = new Date(value.expiresAt).toISOString();
    if (expiresAt <= nowIso()) throw new Error("MCP scope expiresAt должен быть в будущем");
  }
  return {
    kind: "mcp",
    serverNamespaces: namespaces.sort(),
    toolPatterns: toolPatterns.sort(),
    risks: risks as McpToolRisk[],
    allowCatalog: value.allowCatalog !== false,
    expiresAt,
  };
}

function normalizeCredentialInput(input: CreateCredentialInput, currentScope?: CredentialScope): {
  name: string;
  type: CreateCredentialInput["type"];
  data: Record<string, string>;
  scope: CredentialScope;
} {
  const name = requiredAgentText(input.name, "Название credentials", 100);
  if (input.type !== "http_header" && input.type !== "api_key") {
    throw new Error("Неизвестный тип credentials");
  }
  if (!input.data || typeof input.data !== "object" || Array.isArray(input.data)) {
    throw new Error("Данные credentials должны быть объектом");
  }
  const data: Record<string, string> = {};
  if (input.type === "http_header") {
    const headerName = requiredAgentText(input.data.headerName, "Имя HTTP-заголовка", 80);
    const headerValue = requiredAgentText(input.data.headerValue, "Значение HTTP-заголовка", 8_000);
    if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(headerName)) throw new Error("Некорректное имя HTTP-заголовка");
    if (/\r|\n/.test(headerValue)) throw new Error("Значение HTTP-заголовка содержит недопустимый перенос строки");
    if (isReservedMcpCredentialHeader(headerName)) {
      throw new Error("Этот HTTP-заголовок зарезервирован транспортом и не может быть credentials");
    }
    data.headerName = headerName;
    data.headerValue = headerValue;
  } else {
    data.apiKey = requiredAgentText(input.data.apiKey, "API key", 8_000);
  }
  return { name, type: input.type, data, scope: normalizeCredentialScope(input.scope, currentScope) };
}

export interface StoreOptions {
  seedDemo?: boolean;
  leaseTtlSeconds?: number;
  artifactsDir?: string;
  credentialsKey?: string;
  temporalProcesses?: boolean;
  telemetry?: CoordinatorTelemetry;
}

export class AgatStore {
  readonly db: DatabaseSync;
  private readonly leaseTtlSeconds: number;
  private readonly artifactsDir: string;
  private readonly credentialsKey: string;
  private readonly temporalProcesses: boolean;
  private readonly telemetry: CoordinatorTelemetry;

  constructor(dbPath: string, options: StoreOptions = {}) {
    if (dbPath !== ":memory:") {
      fs.mkdirSync(path.dirname(path.resolve(dbPath)), { recursive: true });
    }

    this.db = new DatabaseSync(dbPath);
    this.leaseTtlSeconds = options.leaseTtlSeconds ?? 180;
    this.artifactsDir = path.resolve(options.artifactsDir ?? "./data/artifacts");
    this.credentialsKey = options.credentialsKey ?? "agat-local-credentials-key";
    this.temporalProcesses = options.temporalProcesses ?? false;
    this.telemetry = options.telemetry ?? new CoordinatorTelemetry({
      enabled: false,
      serviceName: "agat-coordinator",
      exporterEndpoint: "",
    });
    this.configure();
    this.migrate();
    this.seedAgents();
    this.ensureAllPromptRegistries();
    if (options.seedDemo) this.seedDemoData();
    else this.removeDemoData();
  }

  close(): void {
    this.db.close();
  }

  maintenanceTick(): void {
    this.completeAutomaticWaitStages();
    this.cleanupExpiredMcpCalls();
    this.cleanupExpiredKnowledgeLeases();
    this.cleanupExpiredMemory();
    this.cleanupExpiredLeases();
  }

  private configure(): void {
    this.db.exec("PRAGMA foreign_keys = ON;");
    this.db.exec("PRAGMA busy_timeout = 5000;");
    try {
      this.db.exec("PRAGMA journal_mode = WAL;");
    } catch {
      // In-memory databases and some read-only filesystems do not support WAL.
    }
  }

  private migrate(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS nodes (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        platform TEXT NOT NULL,
        architecture TEXT NOT NULL DEFAULT '',
        endpoint TEXT NOT NULL DEFAULT '',
        models_json TEXT NOT NULL DEFAULT '[]',
        model_profiles_json TEXT NOT NULL DEFAULT '[]',
        labels_json TEXT NOT NULL DEFAULT '{}',
        agent_runtimes_json TEXT NOT NULL DEFAULT '["single"]',
        agent_runtime_profiles_json TEXT NOT NULL DEFAULT '["tool_loop_v1"]',
        embedding_models_json TEXT NOT NULL DEFAULT '[]',
        cpu_cores INTEGER NOT NULL DEFAULT 1,
        memory_mb INTEGER NOT NULL DEFAULT 0,
        vram_mb INTEGER NOT NULL DEFAULT 0,
        gpu TEXT NOT NULL DEFAULT '',
        max_concurrency INTEGER NOT NULL DEFAULT 1,
        token_hash TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'online',
        metrics_json TEXT NOT NULL DEFAULT '{}',
        last_seen TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS agents (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        system_prompt TEXT NOT NULL,
        model TEXT,
        runtime TEXT NOT NULL DEFAULT 'single',
        runtime_config_json TEXT NOT NULL DEFAULT '{"profile":"tool_loop_v1","maxIterations":6}',
        is_builtin INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        input TEXT NOT NULL,
        status TEXT NOT NULL,
        execution_mode TEXT NOT NULL DEFAULT 'sequential',
        priority INTEGER NOT NULL DEFAULT 50,
        approval_required INTEGER NOT NULL DEFAULT 1,
        result_destination TEXT NOT NULL DEFAULT 'history',
        artifact_path TEXT NOT NULL DEFAULT '',
        trace_id TEXT NOT NULL DEFAULT '',
        root_span_id TEXT NOT NULL DEFAULT '',
        replay_of_run_id TEXT REFERENCES runs(id),
        evaluation_group_id TEXT,
        variant_name TEXT NOT NULL DEFAULT '',
        knowledge_collection_ids_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT
      );

      CREATE TABLE IF NOT EXISTS stages (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        agent_id TEXT NOT NULL REFERENCES agents(id),
        position INTEGER NOT NULL,
        status TEXT NOT NULL,
        node_id TEXT REFERENCES nodes(id),
        lease_id TEXT UNIQUE,
        lease_expires_at TEXT,
        attempt INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        output TEXT,
        requires_approval INTEGER NOT NULL DEFAULT 0,
        process_node_id TEXT,
        process_token_id TEXT,
        stage_kind TEXT NOT NULL DEFAULT 'agent',
        stage_input TEXT,
        activity_json TEXT,
        idempotency_key TEXT,
        available_at TEXT,
        agent_snapshot_json TEXT,
        lease_traceparent TEXT,
        worker_snapshot_json TEXT,
        routing_json TEXT,
        metrics_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        UNIQUE(run_id, position)
      );

      CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
        stage_id TEXT REFERENCES stages(id) ON DELETE CASCADE,
        node_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
        level TEXT NOT NULL,
        type TEXT NOT NULL,
        message TEXT NOT NULL,
        data_json TEXT,
        project_id TEXT NOT NULL DEFAULT 'global',
        created_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        stage_id TEXT REFERENCES stages(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        media_type TEXT NOT NULL,
        relative_path TEXT NOT NULL UNIQUE,
        size_bytes INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS processes (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description TEXT NOT NULL DEFAULT '',
        is_template INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'draft',
        draft_graph_json TEXT NOT NULL,
        published_version INTEGER NOT NULL DEFAULT 0,
        has_unpublished_changes INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        published_at TEXT
      );

      CREATE TABLE IF NOT EXISTS process_versions (
        process_id TEXT NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        graph_json TEXT NOT NULL,
        published_at TEXT NOT NULL,
        PRIMARY KEY(process_id, version)
      );

      CREATE TABLE IF NOT EXISTS process_instances (
        id TEXT PRIMARY KEY,
        process_id TEXT NOT NULL REFERENCES processes(id),
        process_version INTEGER NOT NULL,
        run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
        graph_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        current_node_id TEXT,
        last_output TEXT,
        loop_counts_json TEXT NOT NULL DEFAULT '{}',
        transition_count INTEGER NOT NULL DEFAULT 0,
        runtime TEXT NOT NULL DEFAULT 'database',
        workflow_id TEXT,
        replay_of_instance_id TEXT REFERENCES process_instances(id),
        replay_mode TEXT NOT NULL DEFAULT 'live',
        terminal_status_after_compensation TEXT,
        compensation_error TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT
      );

      CREATE TABLE IF NOT EXISTS process_tokens (
        id TEXT PRIMARY KEY,
        instance_id TEXT NOT NULL REFERENCES process_instances(id) ON DELETE CASCADE,
        parent_token_id TEXT REFERENCES process_tokens(id) ON DELETE CASCADE,
        fork_node_id TEXT,
        branch_edge_id TEXT,
        current_node_id TEXT,
        last_output TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS process_join_arrivals (
        instance_id TEXT NOT NULL REFERENCES process_instances(id) ON DELETE CASCADE,
        join_node_id TEXT NOT NULL,
        token_id TEXT NOT NULL REFERENCES process_tokens(id) ON DELETE CASCADE,
        fork_node_id TEXT NOT NULL,
        branch_edge_id TEXT NOT NULL,
        output TEXT,
        arrived_at TEXT NOT NULL,
        PRIMARY KEY(instance_id, join_node_id, token_id)
      );

      CREATE TABLE IF NOT EXISTS process_signal_waits (
        id TEXT PRIMARY KEY,
        instance_id TEXT NOT NULL REFERENCES process_instances(id) ON DELETE CASCADE,
        stage_id TEXT NOT NULL UNIQUE REFERENCES stages(id) ON DELETE CASCADE,
        process_id TEXT NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        signal_name TEXT NOT NULL,
        correlation_key TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'waiting',
        payload TEXT,
        expires_at TEXT,
        created_at TEXT NOT NULL,
        completed_at TEXT
      );

      CREATE TABLE IF NOT EXISTS process_subprocess_links (
        id TEXT PRIMARY KEY,
        parent_instance_id TEXT NOT NULL REFERENCES process_instances(id) ON DELETE CASCADE,
        parent_stage_id TEXT NOT NULL UNIQUE REFERENCES stages(id) ON DELETE CASCADE,
        parent_token_id TEXT NOT NULL REFERENCES process_tokens(id) ON DELETE CASCADE,
        child_instance_id TEXT NOT NULL UNIQUE REFERENCES process_instances(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'running',
        created_at TEXT NOT NULL,
        completed_at TEXT
      );

      CREATE TABLE IF NOT EXISTS process_compensations (
        id TEXT PRIMARY KEY,
        instance_id TEXT NOT NULL REFERENCES process_instances(id) ON DELETE CASCADE,
        source_node_id TEXT NOT NULL,
        source_stage_id TEXT REFERENCES stages(id) ON DELETE SET NULL,
        compensation_stage_id TEXT UNIQUE REFERENCES stages(id) ON DELETE SET NULL,
        sequence INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'armed',
        idempotency_key TEXT NOT NULL,
        activity_json TEXT NOT NULL,
        error TEXT,
        created_at TEXT NOT NULL,
        completed_at TEXT
      );

      CREATE TABLE IF NOT EXISTS process_webhooks (
        id TEXT PRIMARY KEY,
        process_id TEXT NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        signal_name TEXT,
        default_input TEXT NOT NULL DEFAULT '',
        token_hash TEXT NOT NULL UNIQUE,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_used_at TEXT,
        UNIQUE(project_id, name)
      );

      CREATE TABLE IF NOT EXISTS process_webhook_receipts (
        webhook_id TEXT NOT NULL REFERENCES process_webhooks(id) ON DELETE CASCADE,
        idempotency_key TEXT NOT NULL,
        response_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(webhook_id, idempotency_key)
      );

      CREATE TABLE IF NOT EXISTS credentials (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        type TEXT NOT NULL,
        secret_blob TEXT NOT NULL,
        fields_json TEXT NOT NULL DEFAULT '[]',
        scope_json TEXT NOT NULL DEFAULT '{"kind":"project","serverNamespaces":[],"toolPatterns":[],"risks":["read","write","destructive","unknown"],"allowCatalog":true,"expiresAt":null}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS mcp_servers (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        namespace TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        credential_id TEXT REFERENCES credentials(id) ON DELETE SET NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        trust_annotations INTEGER NOT NULL DEFAULT 0,
        allow_insecure_http INTEGER NOT NULL DEFAULT 0,
        protocol_version TEXT NOT NULL DEFAULT '2026-07-28',
        default_policy TEXT NOT NULL DEFAULT 'deny',
        catalog_json TEXT NOT NULL DEFAULT '[]',
        catalog_ttl_ms INTEGER NOT NULL DEFAULT 300000,
        catalog_scope TEXT NOT NULL DEFAULT 'private',
        catalog_expires_at TEXT,
        last_sync_at TEXT,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(project_id, name),
        UNIQUE(project_id, namespace)
      );

      CREATE TABLE IF NOT EXISTS mcp_tool_policies (
        server_id TEXT NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
        tool_name TEXT NOT NULL,
        policy TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(server_id, tool_name)
      );

      CREATE TABLE IF NOT EXISTS mcp_tool_calls (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        stage_id TEXT NOT NULL,
        lease_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        client_call_id TEXT NOT NULL,
        server_id TEXT NOT NULL,
        server_name TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        public_name TEXT NOT NULL,
        risk TEXT NOT NULL,
        risk_tier TEXT NOT NULL DEFAULT 'high',
        legacy_policy TEXT NOT NULL DEFAULT 'approval',
        policy TEXT NOT NULL,
        required_approvals INTEGER NOT NULL DEFAULT 1,
        policy_version INTEGER NOT NULL DEFAULT 0,
        policy_sha256 TEXT NOT NULL DEFAULT '',
        policy_rule_id TEXT,
        policy_reason TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        arguments_blob TEXT NOT NULL,
        arguments_summary_json TEXT NOT NULL DEFAULT '{}',
        preview_diff_json TEXT NOT NULL DEFAULT '[]',
        result_blob TEXT,
        result_sha256 TEXT,
        result_bytes INTEGER,
        traceparent TEXT,
        error TEXT,
        decision_actor TEXT,
        decision_at TEXT,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE(lease_id, client_call_id)
      );

      CREATE TABLE IF NOT EXISTS mcp_tool_call_approvals (
        call_id TEXT NOT NULL REFERENCES mcp_tool_calls(id) ON DELETE CASCADE,
        actor_subject TEXT NOT NULL,
        actor_display TEXT NOT NULL,
        decision TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(call_id, actor_subject)
      );

      CREATE TABLE IF NOT EXISTS mcp_policy_versions (
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        document_json TEXT NOT NULL,
        actor_subject TEXT NOT NULL,
        actor_display TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(project_id, version)
      );

      CREATE TABLE IF NOT EXISTS a2a_endpoints (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        version TEXT NOT NULL DEFAULT '1.0.0',
        skill_id TEXT NOT NULL,
        skill_name TEXT NOT NULL,
        skill_description TEXT NOT NULL,
        tags_json TEXT NOT NULL DEFAULT '[]',
        examples_json TEXT NOT NULL DEFAULT '[]',
        input_modes_json TEXT NOT NULL DEFAULT '["text/plain"]',
        knowledge_collection_ids_json TEXT NOT NULL DEFAULT '[]',
        approval_required INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1,
        priority INTEGER NOT NULL DEFAULT 50,
        max_input_characters INTEGER NOT NULL DEFAULT 20000,
        max_active_tasks INTEGER NOT NULL DEFAULT 10,
        token_hash TEXT NOT NULL UNIQUE,
        token_suffix TEXT NOT NULL,
        token_rotated_at TEXT NOT NULL,
        created_by TEXT NOT NULL DEFAULT 'system',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(project_id, agent_id),
        UNIQUE(project_id, skill_id)
      );

      CREATE TABLE IF NOT EXISTS a2a_tasks (
        id TEXT PRIMARY KEY,
        endpoint_id TEXT NOT NULL REFERENCES a2a_endpoints(id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
        context_id TEXT NOT NULL,
        client_message_id TEXT NOT NULL,
        request_sha256 TEXT NOT NULL,
        message_blob TEXT NOT NULL,
        external_traceparent TEXT,
        protocol_version TEXT NOT NULL DEFAULT '1.0',
        adapter_version TEXT NOT NULL DEFAULT '1.0.0',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        canceled_at TEXT,
        UNIQUE(endpoint_id, client_message_id)
      );

      CREATE TABLE IF NOT EXISTS a2a_push_configs (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES a2a_tasks(id) ON DELETE CASCADE,
        endpoint_id TEXT NOT NULL REFERENCES a2a_endpoints(id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        callback_blob TEXT NOT NULL,
        callback_origin TEXT NOT NULL,
        auth_scheme TEXT,
        auth_suffix TEXT NOT NULL DEFAULT '',
        last_state TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(task_id, id)
      );

      CREATE TABLE IF NOT EXISTS a2a_push_deliveries (
        id TEXT PRIMARY KEY,
        config_id TEXT NOT NULL REFERENCES a2a_push_configs(id) ON DELETE CASCADE,
        task_id TEXT NOT NULL REFERENCES a2a_tasks(id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        event_kind TEXT NOT NULL,
        task_state TEXT NOT NULL,
        payload_blob TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT NOT NULL,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        delivered_at TEXT,
        UNIQUE(config_id, event_kind, task_state)
      );

      CREATE TABLE IF NOT EXISTS a2a_remotes (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        agent_card_url TEXT NOT NULL,
        interface_url TEXT NOT NULL,
        protocol_version TEXT NOT NULL DEFAULT '1.0',
        tenant TEXT,
        skill_id TEXT NOT NULL,
        skill_name TEXT NOT NULL,
        input_modes_json TEXT NOT NULL DEFAULT '["text/plain"]',
        output_modes_json TEXT NOT NULL DEFAULT '["text/plain"]',
        capabilities_json TEXT NOT NULL DEFAULT '{}',
        card_sha256 TEXT NOT NULL,
        auth_mode TEXT NOT NULL,
        auth_blob TEXT NOT NULL,
        auth_suffix TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        allow_file_artifacts INTEGER NOT NULL DEFAULT 0,
        max_response_bytes INTEGER NOT NULL DEFAULT 1048576,
        created_by TEXT NOT NULL DEFAULT 'system',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(project_id, agent_card_url, skill_id)
      );

      CREATE TABLE IF NOT EXISTS a2a_outbound_tasks (
        id TEXT PRIMARY KEY,
        remote_id TEXT NOT NULL REFERENCES a2a_remotes(id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        remote_task_id TEXT,
        context_id TEXT NOT NULL,
        client_message_id TEXT NOT NULL,
        state TEXT NOT NULL,
        request_sha256 TEXT NOT NULL,
        request_blob TEXT NOT NULL,
        response_blob TEXT NOT NULL,
        trace_id TEXT NOT NULL,
        actor_subject TEXT NOT NULL,
        actor_display TEXT NOT NULL,
        delegated INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(remote_id, client_message_id)
      );

      CREATE TABLE IF NOT EXISTS model_benchmarks (
        node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        model TEXT NOT NULL,
        samples INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        model_duration_ms INTEGER NOT NULL DEFAULT 0,
        ewma_tokens_per_second REAL NOT NULL DEFAULT 0,
        ewma_joules_per_1k_tokens REAL,
        last_observed_at TEXT NOT NULL,
        PRIMARY KEY(node_id, model)
      );

      CREATE TABLE IF NOT EXISTS knowledge_collections (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL COLLATE NOCASE,
        description TEXT NOT NULL DEFAULT '',
        embedding_model TEXT NOT NULL,
        chunk_size INTEGER NOT NULL DEFAULT 1200,
        chunk_overlap INTEGER NOT NULL DEFAULT 160,
        top_k INTEGER NOT NULL DEFAULT 6,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(project_id, name)
      );

      CREATE TABLE IF NOT EXISTS knowledge_documents (
        id TEXT PRIMARY KEY,
        collection_id TEXT NOT NULL REFERENCES knowledge_collections(id) ON DELETE CASCADE,
        name TEXT NOT NULL COLLATE NOCASE,
        source_uri TEXT NOT NULL DEFAULT '',
        media_type TEXT NOT NULL DEFAULT 'text/plain',
        content_sha256 TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        error TEXT,
        chunk_count INTEGER NOT NULL DEFAULT 0,
        embedded_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(collection_id, name)
      );

      CREATE TABLE IF NOT EXISTS knowledge_chunks (
        id TEXT PRIMARY KEY,
        collection_id TEXT NOT NULL REFERENCES knowledge_collections(id) ON DELETE CASCADE,
        document_id TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL,
        content TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        char_start INTEGER NOT NULL,
        char_end INTEGER NOT NULL,
        embedding_model TEXT,
        embedding_json TEXT,
        embedding_dimensions INTEGER,
        embedded_at TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(document_id, ordinal)
      );

      CREATE TABLE IF NOT EXISTS knowledge_embedding_jobs (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        collection_id TEXT NOT NULL REFERENCES knowledge_collections(id) ON DELETE CASCADE,
        document_id TEXT NOT NULL UNIQUE REFERENCES knowledge_documents(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending',
        node_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
        lease_id TEXT UNIQUE,
        lease_expires_at TEXT,
        failures INTEGER NOT NULL DEFAULT 0,
        max_failures INTEGER NOT NULL DEFAULT 3,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS knowledge_retrievals (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        stage_id TEXT NOT NULL REFERENCES stages(id) ON DELETE CASCADE,
        node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        query_sha256 TEXT NOT NULL,
        queries_json TEXT NOT NULL,
        hits_json TEXT NOT NULL,
        created_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS memory_entries (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        agent_id TEXT REFERENCES agents(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        expires_at TEXT,
        explicitly_saved INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS prompt_registry (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
        name TEXT NOT NULL COLLATE NOCASE,
        description TEXT NOT NULL DEFAULT '',
        active_version INTEGER NOT NULL DEFAULT 1,
        active_model TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(project_id, name),
        UNIQUE(project_id, agent_id)
      );

      CREATE TABLE IF NOT EXISTS prompt_versions (
        prompt_id TEXT NOT NULL REFERENCES prompt_registry(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        content TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        change_note TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT 'system',
        created_at TEXT NOT NULL,
        PRIMARY KEY(prompt_id, version),
        UNIQUE(prompt_id, content_sha256)
      );

      CREATE TABLE IF NOT EXISTS eval_datasets (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL COLLATE NOCASE,
        description TEXT NOT NULL DEFAULT '',
        current_version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(project_id, name)
      );

      CREATE TABLE IF NOT EXISTS eval_dataset_versions (
        dataset_id TEXT NOT NULL REFERENCES eval_datasets(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        change_note TEXT NOT NULL DEFAULT '',
        rubric_json TEXT NOT NULL DEFAULT '[]',
        knowledge_snapshot_json TEXT NOT NULL DEFAULT '{}',
        content_sha256 TEXT NOT NULL,
        created_by TEXT NOT NULL DEFAULT 'system',
        created_at TEXT NOT NULL,
        PRIMARY KEY(dataset_id, version)
      );

      CREATE TABLE IF NOT EXISTS eval_examples (
        id TEXT PRIMARY KEY,
        dataset_id TEXT NOT NULL,
        dataset_version INTEGER NOT NULL,
        position INTEGER NOT NULL,
        name TEXT NOT NULL,
        input TEXT NOT NULL,
        reference_output TEXT NOT NULL DEFAULT '',
        required_terms_json TEXT NOT NULL DEFAULT '[]',
        forbidden_terms_json TEXT NOT NULL DEFAULT '[]',
        knowledge_collection_ids_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        FOREIGN KEY(dataset_id, dataset_version)
          REFERENCES eval_dataset_versions(dataset_id, version) ON DELETE CASCADE,
        UNIQUE(dataset_id, dataset_version, position)
      );

      CREATE TABLE IF NOT EXISTS eval_experiments (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        dataset_id TEXT NOT NULL REFERENCES eval_datasets(id),
        dataset_version INTEGER NOT NULL,
        agent_id TEXT NOT NULL REFERENCES agents(id),
        prompt_id TEXT NOT NULL REFERENCES prompt_registry(id),
        prompt_version INTEGER NOT NULL,
        model TEXT,
        min_quality_score REAL NOT NULL DEFAULT 80,
        knowledge_snapshot_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'queued',
        created_by TEXT NOT NULL DEFAULT 'system',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        FOREIGN KEY(dataset_id, dataset_version)
          REFERENCES eval_dataset_versions(dataset_id, version),
        FOREIGN KEY(prompt_id, prompt_version)
          REFERENCES prompt_versions(prompt_id, version)
      );

      CREATE TABLE IF NOT EXISTS eval_experiment_items (
        id TEXT PRIMARY KEY,
        experiment_id TEXT NOT NULL REFERENCES eval_experiments(id) ON DELETE CASCADE,
        example_id TEXT NOT NULL REFERENCES eval_examples(id),
        run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
        judge_run_id TEXT UNIQUE REFERENCES runs(id) ON DELETE SET NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        deterministic_score REAL,
        deterministic_details_json TEXT NOT NULL DEFAULT '[]',
        judge_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(experiment_id, example_id)
      );

      CREATE TABLE IF NOT EXISTS eval_reviews (
        id TEXT PRIMARY KEY,
        item_id TEXT NOT NULL REFERENCES eval_experiment_items(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        reviewer TEXT NOT NULL,
        model TEXT,
        scores_json TEXT NOT NULL DEFAULT '{}',
        overall_score REAL NOT NULL,
        rationale TEXT NOT NULL DEFAULT '',
        raw_output_sha256 TEXT,
        run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL
      );

      CREATE INDEX IF NOT EXISTS idx_runs_status_created ON runs(status, created_at);
      CREATE INDEX IF NOT EXISTS idx_stages_status_position ON stages(status, position);
      CREATE INDEX IF NOT EXISTS idx_stages_node_status ON stages(node_id, status);
      CREATE INDEX IF NOT EXISTS idx_events_run_created ON events(run_id, created_at);
      CREATE INDEX IF NOT EXISTS idx_artifacts_run_created ON artifacts(run_id, created_at);
      CREATE INDEX IF NOT EXISTS idx_processes_updated ON processes(updated_at DESC);
      CREATE INDEX IF NOT EXISTS idx_process_instances_status_created ON process_instances(status, created_at DESC);
      CREATE INDEX IF NOT EXISTS idx_process_instances_process_created ON process_instances(process_id, created_at DESC);
      CREATE INDEX IF NOT EXISTS idx_process_tokens_instance_status ON process_tokens(instance_id, status);
      CREATE INDEX IF NOT EXISTS idx_process_signals_lookup ON process_signal_waits(project_id, process_id, signal_name, status);
      CREATE INDEX IF NOT EXISTS idx_process_compensations_instance ON process_compensations(instance_id, status, sequence DESC);
      CREATE INDEX IF NOT EXISTS idx_process_webhooks_project ON process_webhooks(project_id, process_id, enabled);
      CREATE INDEX IF NOT EXISTS idx_mcp_servers_project ON mcp_servers(project_id, enabled, updated_at DESC);
      CREATE INDEX IF NOT EXISTS idx_mcp_calls_project_created ON mcp_tool_calls(project_id, created_at DESC);
      CREATE INDEX IF NOT EXISTS idx_mcp_calls_lease_status ON mcp_tool_calls(lease_id, status);
      CREATE INDEX IF NOT EXISTS idx_mcp_calls_approval ON mcp_tool_calls(status, expires_at);
      CREATE INDEX IF NOT EXISTS idx_mcp_call_approvals_call ON mcp_tool_call_approvals(call_id, created_at);
      CREATE INDEX IF NOT EXISTS idx_mcp_policy_versions_project ON mcp_policy_versions(project_id, version DESC);
      CREATE INDEX IF NOT EXISTS idx_a2a_endpoints_project ON a2a_endpoints(project_id, enabled, updated_at DESC);
      CREATE INDEX IF NOT EXISTS idx_a2a_tasks_endpoint_updated ON a2a_tasks(endpoint_id, updated_at DESC, id DESC);
      CREATE INDEX IF NOT EXISTS idx_a2a_tasks_project_updated ON a2a_tasks(project_id, updated_at DESC);
      CREATE INDEX IF NOT EXISTS idx_a2a_tasks_context ON a2a_tasks(endpoint_id, context_id, updated_at DESC);
      CREATE INDEX IF NOT EXISTS idx_a2a_push_configs_task ON a2a_push_configs(task_id, created_at);
      CREATE INDEX IF NOT EXISTS idx_a2a_push_deliveries_due ON a2a_push_deliveries(status, next_attempt_at);
      CREATE INDEX IF NOT EXISTS idx_a2a_remotes_project ON a2a_remotes(project_id, enabled, updated_at DESC);
      CREATE INDEX IF NOT EXISTS idx_a2a_outbound_project ON a2a_outbound_tasks(project_id, updated_at DESC);
      CREATE INDEX IF NOT EXISTS idx_a2a_outbound_remote ON a2a_outbound_tasks(remote_id, updated_at DESC);
      CREATE INDEX IF NOT EXISTS idx_model_benchmarks_model ON model_benchmarks(model, last_observed_at DESC);
      CREATE INDEX IF NOT EXISTS idx_knowledge_collections_project ON knowledge_collections(project_id, updated_at DESC);
      CREATE INDEX IF NOT EXISTS idx_knowledge_documents_collection ON knowledge_documents(collection_id, created_at DESC);
      CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_collection ON knowledge_chunks(collection_id, embedded_at);
      CREATE INDEX IF NOT EXISTS idx_knowledge_jobs_status ON knowledge_embedding_jobs(status, created_at);
      CREATE INDEX IF NOT EXISTS idx_knowledge_retrievals_run ON knowledge_retrievals(run_id, created_at);
      CREATE INDEX IF NOT EXISTS idx_memory_project_expiry ON memory_entries(project_id, expires_at, created_at DESC);
      CREATE INDEX IF NOT EXISTS idx_prompts_project_updated ON prompt_registry(project_id, updated_at DESC);
      CREATE INDEX IF NOT EXISTS idx_prompt_versions_prompt ON prompt_versions(prompt_id, version DESC);
      CREATE INDEX IF NOT EXISTS idx_eval_datasets_project_updated ON eval_datasets(project_id, updated_at DESC);
      CREATE INDEX IF NOT EXISTS idx_eval_examples_version ON eval_examples(dataset_id, dataset_version, position);
      CREATE INDEX IF NOT EXISTS idx_eval_experiments_project_created ON eval_experiments(project_id, created_at DESC);
      CREATE INDEX IF NOT EXISTS idx_eval_items_experiment ON eval_experiment_items(experiment_id, created_at);
      CREATE INDEX IF NOT EXISTS idx_eval_reviews_item_created ON eval_reviews(item_id, created_at DESC);
      CREATE UNIQUE INDEX IF NOT EXISTS idx_eval_reviews_judge_run
        ON eval_reviews(run_id) WHERE run_id IS NOT NULL;

      INSERT OR IGNORE INTO settings(key, value, updated_at)
      VALUES ('scheduler_mode', 'sequential', datetime('now'));
      INSERT OR IGNORE INTO settings(key, value, updated_at)
      VALUES ('global_max_concurrency', '1', datetime('now'));
      INSERT OR IGNORE INTO settings(key, value, updated_at)
      VALUES ('model_router_policy', '${JSON.stringify(DEFAULT_MODEL_ROUTER_POLICY)}', datetime('now'));
      INSERT OR IGNORE INTO settings(key, value, updated_at)
      VALUES ('mcp_emergency_deny', '{"enabled":false,"reason":"","actor":null,"changedAt":null,"pendingCallsDenied":0}', datetime('now'));
    `);

    const nodeColumns = this.db.prepare("PRAGMA table_info(nodes)").all() as Row[];
    if (!nodeColumns.some((column) => column.name === "model_profiles_json")) {
      this.db.exec("ALTER TABLE nodes ADD COLUMN model_profiles_json TEXT NOT NULL DEFAULT '[]';");
    }
    if (!nodeColumns.some((column) => column.name === "vram_mb")) {
      this.db.exec("ALTER TABLE nodes ADD COLUMN vram_mb INTEGER NOT NULL DEFAULT 0;");
    }
    if (!nodeColumns.some((column) => column.name === "agent_runtimes_json")) {
      this.db.exec("ALTER TABLE nodes ADD COLUMN agent_runtimes_json TEXT NOT NULL DEFAULT '[\"single\"]';");
    }
    if (!nodeColumns.some((column) => column.name === "agent_runtime_profiles_json")) {
      this.db.exec("ALTER TABLE nodes ADD COLUMN agent_runtime_profiles_json TEXT NOT NULL DEFAULT '[\"tool_loop_v1\"]';");
    }
    if (!nodeColumns.some((column) => column.name === "embedding_models_json")) {
      this.db.exec("ALTER TABLE nodes ADD COLUMN embedding_models_json TEXT NOT NULL DEFAULT '[]';");
    }
    const agentColumns = this.db.prepare("PRAGMA table_info(agents)").all() as Row[];
    if (!agentColumns.some((column) => column.name === "is_builtin")) {
      this.db.exec("ALTER TABLE agents ADD COLUMN is_builtin INTEGER NOT NULL DEFAULT 0;");
    }
    if (!agentColumns.some((column) => column.name === "project_id")) {
      this.db.exec("ALTER TABLE agents ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default';");
    }
    if (!agentColumns.some((column) => column.name === "runtime")) {
      this.db.exec("ALTER TABLE agents ADD COLUMN runtime TEXT NOT NULL DEFAULT 'single';");
    }
    if (!agentColumns.some((column) => column.name === "runtime_config_json")) {
      this.db.exec("ALTER TABLE agents ADD COLUMN runtime_config_json TEXT NOT NULL DEFAULT '{\"profile\":\"tool_loop_v1\",\"maxIterations\":6}';");
    }
    const stageColumns = this.db.prepare("PRAGMA table_info(stages)").all() as Row[];
    if (!stageColumns.some((column) => column.name === "process_node_id")) {
      this.db.exec("ALTER TABLE stages ADD COLUMN process_node_id TEXT;");
    }
    if (!stageColumns.some((column) => column.name === "process_token_id")) {
      this.db.exec("ALTER TABLE stages ADD COLUMN process_token_id TEXT;");
    }
    if (!stageColumns.some((column) => column.name === "stage_kind")) {
      this.db.exec("ALTER TABLE stages ADD COLUMN stage_kind TEXT NOT NULL DEFAULT 'agent';");
    }
    if (!stageColumns.some((column) => column.name === "stage_input")) {
      this.db.exec("ALTER TABLE stages ADD COLUMN stage_input TEXT;");
    }
    if (!stageColumns.some((column) => column.name === "activity_json")) {
      this.db.exec("ALTER TABLE stages ADD COLUMN activity_json TEXT;");
    }
    if (!stageColumns.some((column) => column.name === "idempotency_key")) {
      this.db.exec("ALTER TABLE stages ADD COLUMN idempotency_key TEXT;");
    }
    if (!stageColumns.some((column) => column.name === "available_at")) {
      this.db.exec("ALTER TABLE stages ADD COLUMN available_at TEXT;");
    }
    if (!stageColumns.some((column) => column.name === "agent_snapshot_json")) {
      this.db.exec("ALTER TABLE stages ADD COLUMN agent_snapshot_json TEXT;");
    }
    if (!stageColumns.some((column) => column.name === "lease_traceparent")) {
      this.db.exec("ALTER TABLE stages ADD COLUMN lease_traceparent TEXT;");
    }
    if (!stageColumns.some((column) => column.name === "worker_snapshot_json")) {
      this.db.exec("ALTER TABLE stages ADD COLUMN worker_snapshot_json TEXT;");
    }
    if (!stageColumns.some((column) => column.name === "routing_json")) {
      this.db.exec("ALTER TABLE stages ADD COLUMN routing_json TEXT;");
    }
    if (!stageColumns.some((column) => column.name === "metrics_json")) {
      this.db.exec("ALTER TABLE stages ADD COLUMN metrics_json TEXT NOT NULL DEFAULT '{}';");
    }
    const eventColumns = this.db.prepare("PRAGMA table_info(events)").all() as Row[];
    if (!eventColumns.some((column) => column.name === "project_id")) {
      this.db.exec("ALTER TABLE events ADD COLUMN project_id TEXT NOT NULL DEFAULT 'global';");
      this.db.exec(`
        UPDATE events SET project_id = COALESCE(
          (SELECT project_id FROM runs WHERE runs.id = events.run_id),
          'global'
        )
      `);
    }
    const runColumns = this.db.prepare("PRAGMA table_info(runs)").all() as Row[];
    if (!runColumns.some((column) => column.name === "result_destination")) {
      this.db.exec("ALTER TABLE runs ADD COLUMN result_destination TEXT NOT NULL DEFAULT 'history';");
    }
    if (!runColumns.some((column) => column.name === "artifact_path")) {
      this.db.exec("ALTER TABLE runs ADD COLUMN artifact_path TEXT NOT NULL DEFAULT '';");
    }
    if (!runColumns.some((column) => column.name === "project_id")) {
      this.db.exec("ALTER TABLE runs ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default';");
    }
    if (!runColumns.some((column) => column.name === "trace_id")) {
      this.db.exec("ALTER TABLE runs ADD COLUMN trace_id TEXT NOT NULL DEFAULT '';");
    }
    if (!runColumns.some((column) => column.name === "root_span_id")) {
      this.db.exec("ALTER TABLE runs ADD COLUMN root_span_id TEXT NOT NULL DEFAULT '';");
    }
    if (!runColumns.some((column) => column.name === "replay_of_run_id")) {
      this.db.exec("ALTER TABLE runs ADD COLUMN replay_of_run_id TEXT;");
    }
    if (!runColumns.some((column) => column.name === "evaluation_group_id")) {
      this.db.exec("ALTER TABLE runs ADD COLUMN evaluation_group_id TEXT;");
    }
    if (!runColumns.some((column) => column.name === "variant_name")) {
      this.db.exec("ALTER TABLE runs ADD COLUMN variant_name TEXT NOT NULL DEFAULT '';");
    }
    if (!runColumns.some((column) => column.name === "knowledge_collection_ids_json")) {
      this.db.exec("ALTER TABLE runs ADD COLUMN knowledge_collection_ids_json TEXT NOT NULL DEFAULT '[]';");
    }
    const knowledgeDocumentColumns = this.db.prepare("PRAGMA table_info(knowledge_documents)").all() as Array<{ name: string }>;
    if (!knowledgeDocumentColumns.some((column) => column.name === "content")) {
      this.db.exec("ALTER TABLE knowledge_documents ADD COLUMN content TEXT NOT NULL DEFAULT '';");
    }
    const processColumns = this.db.prepare("PRAGMA table_info(processes)").all() as Row[];
    if (!processColumns.some((column) => column.name === "project_id")) {
      this.db.exec("ALTER TABLE processes ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default';");
    }
    if (!processColumns.some((column) => column.name === "is_template")) {
      this.db.exec("ALTER TABLE processes ADD COLUMN is_template INTEGER NOT NULL DEFAULT 0;");
    }
    const credentialColumns = this.db.prepare("PRAGMA table_info(credentials)").all() as Row[];
    if (!credentialColumns.some((column) => column.name === "project_id")) {
      this.db.exec("ALTER TABLE credentials ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default';");
    }
    if (!credentialColumns.some((column) => column.name === "scope_json")) {
      this.db.exec(`ALTER TABLE credentials ADD COLUMN scope_json TEXT NOT NULL DEFAULT '{"kind":"project","serverNamespaces":[],"toolPatterns":[],"risks":["read","write","destructive","unknown"],"allowCatalog":true,"expiresAt":null}';`);
    }
    const mcpCallColumns = this.db.prepare("PRAGMA table_info(mcp_tool_calls)").all() as Row[];
    if (!mcpCallColumns.some((column) => column.name === "risk_tier")) {
      this.db.exec("ALTER TABLE mcp_tool_calls ADD COLUMN risk_tier TEXT NOT NULL DEFAULT 'high';");
      this.db.exec("UPDATE mcp_tool_calls SET risk_tier = CASE risk WHEN 'read' THEN 'low' WHEN 'write' THEN 'elevated' WHEN 'destructive' THEN 'critical' ELSE 'high' END;");
    }
    if (!mcpCallColumns.some((column) => column.name === "legacy_policy")) {
      this.db.exec("ALTER TABLE mcp_tool_calls ADD COLUMN legacy_policy TEXT NOT NULL DEFAULT 'approval';");
      this.db.exec("UPDATE mcp_tool_calls SET legacy_policy = policy;");
    }
    if (!mcpCallColumns.some((column) => column.name === "required_approvals")) {
      this.db.exec("ALTER TABLE mcp_tool_calls ADD COLUMN required_approvals INTEGER NOT NULL DEFAULT 1;");
      this.db.exec("UPDATE mcp_tool_calls SET required_approvals = CASE WHEN risk = 'destructive' THEN 2 WHEN policy = 'approval' THEN 1 ELSE 0 END;");
    }
    if (!mcpCallColumns.some((column) => column.name === "policy_version")) {
      this.db.exec("ALTER TABLE mcp_tool_calls ADD COLUMN policy_version INTEGER NOT NULL DEFAULT 0;");
    }
    if (!mcpCallColumns.some((column) => column.name === "policy_sha256")) {
      this.db.exec("ALTER TABLE mcp_tool_calls ADD COLUMN policy_sha256 TEXT NOT NULL DEFAULT '';");
    }
    if (!mcpCallColumns.some((column) => column.name === "policy_rule_id")) {
      this.db.exec("ALTER TABLE mcp_tool_calls ADD COLUMN policy_rule_id TEXT;");
    }
    if (!mcpCallColumns.some((column) => column.name === "policy_reason")) {
      this.db.exec("ALTER TABLE mcp_tool_calls ADD COLUMN policy_reason TEXT NOT NULL DEFAULT '';");
    }
    if (!mcpCallColumns.some((column) => column.name === "preview_diff_json")) {
      this.db.exec("ALTER TABLE mcp_tool_calls ADD COLUMN preview_diff_json TEXT NOT NULL DEFAULT '[]';");
    }
    const a2aEndpointColumns = this.db.prepare("PRAGMA table_info(a2a_endpoints)").all() as Row[];
    if (!a2aEndpointColumns.some((column) => column.name === "output_modes_json")) {
      this.db.exec("ALTER TABLE a2a_endpoints ADD COLUMN output_modes_json TEXT NOT NULL DEFAULT '[\"text/plain\"]';");
    }
    if (!a2aEndpointColumns.some((column) => column.name === "streaming_enabled")) {
      this.db.exec("ALTER TABLE a2a_endpoints ADD COLUMN streaming_enabled INTEGER NOT NULL DEFAULT 0;");
    }
    if (!a2aEndpointColumns.some((column) => column.name === "push_notifications_enabled")) {
      this.db.exec("ALTER TABLE a2a_endpoints ADD COLUMN push_notifications_enabled INTEGER NOT NULL DEFAULT 0;");
    }
    if (!a2aEndpointColumns.some((column) => column.name === "file_artifacts_enabled")) {
      this.db.exec("ALTER TABLE a2a_endpoints ADD COLUMN file_artifacts_enabled INTEGER NOT NULL DEFAULT 0;");
    }
    if (!a2aEndpointColumns.some((column) => column.name === "max_file_bytes")) {
      this.db.exec("ALTER TABLE a2a_endpoints ADD COLUMN max_file_bytes INTEGER NOT NULL DEFAULT 512000;");
    }
    if (!a2aEndpointColumns.some((column) => column.name === "max_files")) {
      this.db.exec("ALTER TABLE a2a_endpoints ADD COLUMN max_files INTEGER NOT NULL DEFAULT 4;");
    }
    this.db.exec("UPDATE a2a_push_deliveries SET status = 'pending' WHERE status = 'delivering';");
    const processInstanceColumns = this.db.prepare("PRAGMA table_info(process_instances)").all() as Row[];
    if (!processInstanceColumns.some((column) => column.name === "runtime")) {
      this.db.exec("ALTER TABLE process_instances ADD COLUMN runtime TEXT NOT NULL DEFAULT 'database';");
    }
    if (!processInstanceColumns.some((column) => column.name === "workflow_id")) {
      this.db.exec("ALTER TABLE process_instances ADD COLUMN workflow_id TEXT;");
    }
    if (!processInstanceColumns.some((column) => column.name === "replay_of_instance_id")) {
      this.db.exec("ALTER TABLE process_instances ADD COLUMN replay_of_instance_id TEXT;");
    }
    if (!processInstanceColumns.some((column) => column.name === "replay_mode")) {
      this.db.exec("ALTER TABLE process_instances ADD COLUMN replay_mode TEXT NOT NULL DEFAULT 'live';");
    }
    if (!processInstanceColumns.some((column) => column.name === "terminal_status_after_compensation")) {
      this.db.exec("ALTER TABLE process_instances ADD COLUMN terminal_status_after_compensation TEXT;");
    }
    if (!processInstanceColumns.some((column) => column.name === "compensation_error")) {
      this.db.exec("ALTER TABLE process_instances ADD COLUMN compensation_error TEXT;");
    }
    const timestamp = nowIso();
    this.db.prepare("INSERT OR IGNORE INTO projects(id, name, created_at, updated_at) VALUES ('default', 'Основной проект', ?, ?)")
      .run(timestamp, timestamp);
    this.db.exec("UPDATE agents SET is_builtin = 1 WHERE id IN ('collector', 'analyst', 'editor');");
    this.db.exec("UPDATE agents SET project_id = '__system__' WHERE is_builtin = 1 OR id = '__agat_system__';");
    const runsWithoutTrace = this.db.prepare("SELECT id FROM runs WHERE trace_id = '' OR root_span_id = ''").all() as Row[];
    const updateRunTrace = this.db.prepare("UPDATE runs SET trace_id = ?, root_span_id = ? WHERE id = ?");
    for (const run of runsWithoutTrace) {
      updateRunTrace.run(
        randomUUID().replaceAll("-", ""),
        randomUUID().replaceAll("-", "").slice(0, 16),
        String(run.id),
      );
    }
    const stagesWithoutSnapshot = this.db.prepare(`
      SELECT s.id AS stage_id, s.created_at AS stage_created_at, a.*
      FROM stages s JOIN agents a ON a.id = s.agent_id
      WHERE s.agent_snapshot_json IS NULL OR s.agent_snapshot_json = ''
    `).all() as Row[];
    const updateStageSnapshot = this.db.prepare("UPDATE stages SET agent_snapshot_json = ? WHERE id = ?");
    for (const stage of stagesWithoutSnapshot) {
      const snapshot = agentSnapshot(
        stage,
        "migration_backfill",
        typeof stage.stage_created_at === "string" ? stage.stage_created_at : timestamp,
      );
      updateStageSnapshot.run(JSON.stringify(snapshot), String(stage.stage_id));
    }
    this.db.exec("CREATE INDEX IF NOT EXISTS idx_runs_evaluation_group ON runs(evaluation_group_id, created_at);");
    const instancesWithoutToken = this.db.prepare(`
      SELECT pi.id, pi.current_node_id, pi.last_output, pi.status, pi.created_at, pi.updated_at
      FROM process_instances pi
      WHERE NOT EXISTS (SELECT 1 FROM process_tokens pt WHERE pt.instance_id = pi.id)
    `).all() as Row[];
    const insertLegacyToken = this.db.prepare(`
      INSERT INTO process_tokens(
        id, instance_id, parent_token_id, fork_node_id, branch_edge_id,
        current_node_id, last_output, status, created_at, updated_at
      ) VALUES (?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?)
    `);
    for (const instance of instancesWithoutToken) {
      const tokenId = randomUUID();
      insertLegacyToken.run(
        String(tokenId),
        String(instance.id),
        instance.current_node_id == null ? null : String(instance.current_node_id),
        instance.last_output == null ? null : String(instance.last_output),
        ["completed", "failed", "cancelled"].includes(String(instance.status)) ? "completed" : "active",
        String(instance.created_at),
        String(instance.updated_at),
      );
      this.db.prepare("UPDATE stages SET process_token_id = ? WHERE run_id = (SELECT run_id FROM process_instances WHERE id = ?) AND process_token_id IS NULL")
        .run(tokenId, String(instance.id));
    }
    this.db.exec("PRAGMA user_version = 17;");
  }

  private seedAgents(): void {
    const timestamp = nowIso();
    const insert = this.db.prepare(`
      INSERT OR IGNORE INTO agents(id, name, role, system_prompt, model, is_builtin, created_at, updated_at)
      VALUES (?, ?, ?, ?, NULL, 1, ?, ?)
    `);

    const agents = [
      {
        id: "collector",
        name: "Сборщик",
        role: "Собирает факты и формирует проверяемый набор исходных данных",
        prompt:
          "Ты внутренний агент-сборщик. Выдели факты, источники, ограничения и пробелы. Не делай выводов без опоры на входные данные. Верни структурированный материал для следующего агента.",
      },
      {
        id: "analyst",
        name: "Аналитик",
        role: "Проверяет данные, находит закономерности и формирует выводы",
        prompt:
          "Ты внутренний агент-аналитик. Проверь факты из контекста, обозначь допущения, риски и противоречия. Сформируй краткие выводы и рекомендации для редактора.",
      },
      {
        id: "editor",
        name: "Редактор",
        role: "Собирает финальный результат в понятный внутренний документ",
        prompt:
          "Ты внутренний агент-редактор. Сформируй точный, компактный и полезный итог на русском языке. Не добавляй неподтверждённые факты, сохрани важные оговорки и следующие действия.",
      },
      {
        id: "__agat_system__",
        name: "Системный шаг",
        role: "Выполняет управляемые детерминированные операции процесса",
        prompt: "Системный служебный агент АГАТ. Не используется для вызова модели.",
      },
      {
        id: "__agat_eval_judge__",
        name: "Golden eval judge",
        role: "Оценивает ответы по зафиксированной rubric и возвращает только проверяемый JSON",
        prompt:
          "Ты внутренний model judge АГАТ. Оцени только переданные input, reference, candidate output и rubric. Не добавляй критерии. Верни только JSON-объект вида {\"scores\":{\"criterion_id\":0},\"overallScore\":0,\"rationale\":\"краткое объяснение\"}. Все оценки должны быть числами от 0 до 100. Не используй markdown.",
      },
    ];

    for (const agent of agents) {
      insert.run(agent.id, agent.name, agent.role, agent.prompt, timestamp, timestamp);
    }
    this.db.exec("UPDATE agents SET project_id = '__system__' WHERE is_builtin = 1 OR id IN ('__agat_system__', '__agat_eval_judge__');");
  }

  private ensureAllPromptRegistries(): void {
    const projects = this.db.prepare("SELECT id FROM projects ORDER BY id").all() as Row[];
    for (const project of projects) this.ensurePromptRegistry(String(project.id));
  }

  private ensurePromptRegistry(projectId: string): void {
    const project = normalizeProjectId(projectId);
    const agents = this.db.prepare(`
      SELECT * FROM agents
      WHERE id NOT IN ('__agat_system__', '__agat_eval_judge__')
        AND (is_builtin = 1 OR project_id = ?)
      ORDER BY is_builtin DESC, created_at, id
    `).all(project) as Row[];
    const timestamp = nowIso();
    const find = this.db.prepare("SELECT id FROM prompt_registry WHERE project_id = ? AND agent_id = ?");
    const insertPrompt = this.db.prepare(`
      INSERT INTO prompt_registry(
        id, project_id, agent_id, name, description, active_version, active_model, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
    `);
    const insertVersion = this.db.prepare(`
      INSERT OR IGNORE INTO prompt_versions(
        prompt_id, version, content, content_sha256, change_note, created_by, created_at
      ) VALUES (?, 1, ?, ?, 'Импорт активного prompt агента', 'migration', ?)
    `);
    for (const agent of agents) {
      const existing = find.get(project, String(agent.id)) as Row | undefined;
      if (existing) continue;
      const promptId = randomUUID();
      insertPrompt.run(
        promptId,
        project,
        String(agent.id),
        String(agent.name),
        `Prompt registry агента «${String(agent.name)}»`,
        typeof agent.model === "string" ? agent.model : null,
        timestamp,
        timestamp,
      );
      insertVersion.run(
        promptId,
        String(agent.system_prompt),
        sha256Text(String(agent.system_prompt)),
        timestamp,
      );
    }
  }

  private seedDemoData(): void {
    const nodeCount = Number((this.db.prepare("SELECT COUNT(*) AS count FROM nodes").get() as Row).count);
    const runCount = Number((this.db.prepare("SELECT COUNT(*) AS count FROM runs").get() as Row).count);
    if (nodeCount > 0 || runCount > 0) return;

    const timestamp = nowIso();
    const nodeInsert = this.db.prepare(`
      INSERT INTO nodes(
        id, name, platform, architecture, endpoint, models_json, labels_json,
        cpu_cores, memory_mb, gpu, max_concurrency, token_hash, status,
        metrics_json, last_seen, created_at, updated_at
      ) VALUES (?, ?, ?, ?, '', ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);

    const demoNodes = [
      ["demo-studio", "studio-mac", "macOS · M2 Max", "arm64", ["qwen3:8b"], 12, 64_000, "Apple 38-core", 1, "online", { cpuPercent: 34, memoryPercent: 42, gpuPercent: 58 }],
      ["demo-gpu", "gpu-box-01", "Ubuntu 24.04", "x64", ["llama3.3:70b"], 24, 128_000, "RTX 4090", 2, "online", { cpuPercent: 18, memoryPercent: 39, gpuPercent: 76 }],
      ["demo-thinkpad", "thinkpad-edge", "Windows 11", "x64", ["mistral-nemo"], 8, 32_000, "RTX 3060", 1, "sleeping", { cpuPercent: 8, memoryPercent: 26, gpuPercent: 0 }],
      ["demo-pixel", "pixel-9", "Android 16", "arm64", ["gemma3:1b"], 8, 12_000, "Tensor", 1, "offline", { cpuPercent: 0, memoryPercent: 0, gpuPercent: 0 }],
    ] as const;

    for (const node of demoNodes) {
      nodeInsert.run(
        node[0],
        node[1],
        node[2],
        node[3],
        JSON.stringify(node[4]),
        node[5],
        node[6],
        node[7],
        node[8],
        hashToken(`demo-${node[0]}`),
        node[9],
        JSON.stringify(node[10]),
        timestamp,
        timestamp,
        timestamp,
      );
    }

    const activeRun = this.createRun({
      name: "Сводка инцидентов",
      input: "Собрать внутреннюю сводку по инцидентам за последние сутки.",
      executionMode: "sequential",
      approvalRequired: false,
    });
    const activeStages = this.db
      .prepare("SELECT id, position FROM stages WHERE run_id = ? ORDER BY position")
      .all(activeRun.id) as Row[];
    const collector = activeStages[0];
    const analyst = activeStages[1];
    if (collector && analyst) {
      this.db
        .prepare("UPDATE stages SET status = 'completed', output = ?, completed_at = ?, updated_at = ? WHERE id = ?")
        .run("Собрано 18 событий из журналов наблюдаемости и базы инцидентов.", timestamp, timestamp, String(collector.id));
      this.db
        .prepare("UPDATE stages SET status = 'running', node_id = ?, lease_id = ?, lease_expires_at = ?, started_at = ?, updated_at = ? WHERE id = ?")
        .run("demo-studio", `demo-${randomUUID()}`, futureIso(600), timestamp, timestamp, String(analyst.id));
      this.db
        .prepare("UPDATE runs SET status = 'running', started_at = ?, updated_at = ? WHERE id = ?")
        .run(timestamp, timestamp, activeRun.id);
      this.addEvent(activeRun.id, String(analyst.id), "demo-studio", "info", "stage.started", "Аналитик обрабатывает сводку", null);
      this.addEvent(activeRun.id, String(analyst.id), "demo-studio", "debug", "model.progress", "Сопоставлено 12 из 18 событий", { progress: 0.67 });
      this.addEvent(activeRun.id, String(collector.id), "demo-studio", "info", "source.connected", "Подключены incidents-db, logs и email", null);
      this.addEvent(activeRun.id, String(collector.id), "demo-studio", "info", "source.records", "Найдено 1287 записей за выбранный период", { records: 1287 });
      this.addEvent(activeRun.id, String(collector.id), "demo-studio", "info", "stage.summary", "Сборщик сформировал проверяемый набор фактов", null);
      this.addEvent(activeRun.id, String(analyst.id), "demo-studio", "debug", "analysis.clustering", "Выполняется кластеризация повторяющихся причин", null);
      this.addEvent(activeRun.id, String(analyst.id), "demo-studio", "info", "analysis.progress", "Сформировано 6 предварительных выводов", { findings: 6 });
    }

    this.createRun({
      name: "Проверка релиза",
      input: "Проверить release notes, тесты и известные риски перед выкладкой.",
      executionMode: "sequential",
      approvalRequired: true,
      priority: 70,
    });

    const approvalRun = this.createRun({
      name: "Отчёт по продажам",
      input: "Собрать внутренний отчёт по продажам за неделю.",
      executionMode: "sequential",
      approvalRequired: true,
      priority: 40,
    });
    const approvalStages = this.db
      .prepare("SELECT id, position FROM stages WHERE run_id = ? ORDER BY position")
      .all(approvalRun.id) as Row[];
    if (approvalStages.length === 3) {
      for (const stage of approvalStages.slice(0, 2)) {
        this.db
          .prepare("UPDATE stages SET status = 'completed', output = ?, completed_at = ?, updated_at = ? WHERE id = ?")
          .run("Демонстрационный промежуточный результат.", timestamp, timestamp, String(stage.id));
      }
      const approvalStage = approvalStages[2]!;
      this.db
        .prepare("UPDATE stages SET status = 'waiting_approval', updated_at = ? WHERE id = ?")
        .run(timestamp, String(approvalStage.id));
      this.db
        .prepare("UPDATE runs SET status = 'waiting_approval', updated_at = ? WHERE id = ?")
        .run(timestamp, approvalRun.id);
      this.addEvent(approvalRun.id, String(approvalStage.id), null, "warn", "approval.requested", "Финальный этап ожидает подтверждения", {
        operation: "Сформировать и сохранить внутренний отчёт",
      });
    }
  }

  private removeDemoData(): void {
    const demoNodeIds = ["demo-studio", "demo-gpu", "demo-thinkpad", "demo-pixel"];
    const placeholders = demoNodeIds.map(() => "?").join(", ");
    const demoNodes = this.db
      .prepare(`SELECT id, created_at FROM nodes WHERE id IN (${placeholders})`)
      .all(...demoNodeIds) as Row[];
    if (demoNodes.length === 0) return;

    const fixtureRuns = [
      ["Сводка инцидентов", "Собрать внутреннюю сводку по инцидентам за последние сутки."],
      ["Проверка релиза", "Проверить release notes, тесты и известные риски перед выкладкой."],
      ["Отчёт по продажам", "Собрать внутренний отчёт по продажам за неделю."],
    ] as const;
    const createdTimes = demoNodes
      .map((node) => new Date(String(node.created_at)).getTime())
      .filter(Number.isFinite);

    this.transaction(() => {
      if (createdTimes.length > 0) {
        const candidates = this.db
          .prepare("SELECT id, name, input, created_at FROM runs")
          .all() as Row[];
        const deleteRun = this.db.prepare("DELETE FROM runs WHERE id = ?");
        for (const run of candidates) {
          const matchesFixture = fixtureRuns.some(([name, input]) => run.name === name && run.input === input);
          const createdAt = new Date(String(run.created_at)).getTime();
          const createdWithFixtures = createdTimes.some((nodeCreatedAt) => Math.abs(nodeCreatedAt - createdAt) <= 10_000);
          if (matchesFixture && createdWithFixtures) deleteRun.run(String(run.id));
        }
      }
      this.db.prepare(`DELETE FROM nodes WHERE id IN (${placeholders})`).run(...demoNodeIds);
    });
  }

  getSetting(key: string): string | null {
    const row = this.db.prepare("SELECT value FROM settings WHERE key = ?").get(key) as Row | undefined;
    return typeof row?.value === "string" ? row.value : null;
  }

  updateScheduler(mode: SchedulerMode, globalMaxConcurrency?: number): void {
    const timestamp = nowIso();
    const upsert = this.db.prepare(`
      INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)
      ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
    `);
    this.transaction(() => {
      upsert.run("scheduler_mode", mode, timestamp);
      if (globalMaxConcurrency !== undefined) {
        upsert.run(
          "global_max_concurrency",
          String(clampInteger(globalMaxConcurrency, 1, 128, 1)),
          timestamp,
        );
      }
    });
  }

  getModelRouterPolicy(): ModelRouterPolicy {
    return normalizeModelRouterPolicy(parseJson<unknown>(
      this.getSetting("model_router_policy"),
      DEFAULT_MODEL_ROUTER_POLICY,
    ));
  }

  updateModelRouterPolicy(input: Partial<ModelRouterPolicy>): ModelRouterPolicy {
    const policy = normalizeModelRouterPolicy({ ...this.getModelRouterPolicy(), ...input });
    const timestamp = nowIso();
    this.db.prepare(`
      INSERT INTO settings(key, value, updated_at) VALUES ('model_router_policy', ?, ?)
      ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
    `).run(JSON.stringify(policy), timestamp);
    this.addEvent(null, null, null, "info", "model_router.policy.updated", "Политика Model Router обновлена", {
      policy,
    });
    return policy;
  }

  listProjects(allowedIds?: Set<string>): Array<Record<string, unknown>> {
    const rows = this.db.prepare(`
      SELECT p.*,
        (SELECT COUNT(*) FROM processes WHERE project_id = p.id) AS process_count,
        (SELECT COUNT(*) FROM agents WHERE project_id = p.id AND is_builtin = 0) AS agent_count
      FROM projects p
      ORDER BY CASE p.id WHEN 'default' THEN 0 ELSE 1 END, p.name COLLATE NOCASE
    `).all() as Row[];
    return rows
      .filter((row) => !allowedIds || allowedIds.has(String(row.id)))
      .map((row) => ({
        id: row.id,
        name: row.name,
        processCount: Number(row.process_count),
        agentCount: Number(row.agent_count),
        createdAt: row.created_at,
        updatedAt: row.updated_at,
      }));
  }

  createProject(input: CreateProjectInput): Record<string, unknown> {
    const name = requiredAgentText(input.name, "Название проекта", 100);
    const requested = typeof input.id === "string" && input.id.trim()
      ? input.id.trim().toLowerCase()
      : name.toLocaleLowerCase("ru")
        .normalize("NFKD")
        .replace(/[^a-z0-9_-]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 64);
    const id = normalizeProjectId(requested || `project-${randomUUID().slice(0, 8)}`);
    if (id === "__system__") throw new Error("Этот идентификатор проекта зарезервирован");
    const timestamp = nowIso();
    try {
      this.db.prepare("INSERT INTO projects(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)")
        .run(id, name, timestamp, timestamp);
    } catch (error) {
      if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
        throw new Error("Проект с таким названием или ID уже существует");
      }
      throw error;
    }
    this.ensurePromptRegistry(id);
    this.addEvent(null, null, null, "info", "project.created", `Создан проект «${name}»`, { projectId: id });
    return this.listProjects().find((project) => project.id === id)!;
  }

  private requireProject(projectId: string): string {
    const normalized = normalizeProjectId(projectId);
    if (!this.db.prepare("SELECT id FROM projects WHERE id = ?").get(normalized)) {
      throw new Error("Проект не найден");
    }
    return normalized;
  }

  listCredentials(projectId = "default"): Array<Record<string, unknown>> {
    const project = normalizeProjectId(projectId);
    return (this.db
      .prepare("SELECT id, name, type, fields_json, scope_json, created_at, updated_at FROM credentials WHERE project_id = ? ORDER BY name COLLATE NOCASE")
      .all(project) as Row[]).map((row) => ({
      id: row.id,
      name: row.name,
      type: row.type,
      fields: parseJson<string[]>(row.fields_json, []),
      scope: normalizeCredentialScope(parseJson<CreateCredentialInput["scope"]>(row.scope_json, { kind: "project" })),
      createdAt: row.created_at,
      updatedAt: row.updated_at,
    }));
  }

  createCredential(input: CreateCredentialInput, projectId = "default"): Record<string, unknown> {
    const project = this.requireProject(projectId);
    const credential = normalizeCredentialInput(input);
    const id = randomUUID();
    const timestamp = nowIso();
    try {
      this.db.prepare(`
        INSERT INTO credentials(id, name, type, secret_blob, fields_json, scope_json, project_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        id,
        credential.name,
        credential.type,
        encryptCredential(credential.data, this.credentialsKey),
        JSON.stringify(Object.keys(credential.data)),
        JSON.stringify(credential.scope),
        project,
        timestamp,
        timestamp,
      );
    } catch (error) {
      if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
        throw new Error("Credentials с таким названием уже существуют");
      }
      throw error;
    }
    this.addEvent(null, null, null, "info", "credential.created", `Созданы credentials «${credential.name}»`, {
      credentialId: id,
      credentialType: credential.type,
      credentialScope: credential.scope.kind,
    });
    return this.listCredentials(project).find((item) => item.id === id)!;
  }

  updateCredential(id: string, input: CreateCredentialInput, projectId = "default"): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const existing = this.db.prepare("SELECT id, scope_json FROM credentials WHERE id = ? AND project_id = ?").get(id, project) as Row | undefined;
    if (!existing) return null;
    const currentScope = normalizeCredentialScope(parseJson<CreateCredentialInput["scope"]>(existing.scope_json, { kind: "project" }));
    const credential = normalizeCredentialInput(input, currentScope);
    const boundNamespaces = (this.db.prepare("SELECT namespace FROM mcp_servers WHERE credential_id = ? AND project_id = ?")
      .all(id, project) as Row[]).map((row) => String(row.namespace));
    if (credential.scope.kind === "mcp") {
      const invalidNamespace = boundNamespaces.find((namespace) => !credential.scope.serverNamespaces.includes(namespace));
      if (invalidNamespace) throw new Error(`Scope credentials не разрешает уже привязанный MCP namespace ${invalidNamespace}`);
    }
    const timestamp = nowIso();
    try {
      this.db.prepare(`
        UPDATE credentials
        SET name = ?, type = ?, secret_blob = ?, fields_json = ?, scope_json = ?, updated_at = ?
        WHERE id = ?
      `).run(
        credential.name,
        credential.type,
        encryptCredential(credential.data, this.credentialsKey),
        JSON.stringify(Object.keys(credential.data)),
        JSON.stringify(credential.scope),
        timestamp,
        id,
      );
    } catch (error) {
      if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
        throw new Error("Credentials с таким названием уже существуют");
      }
      throw error;
    }
    this.addEvent(null, null, null, "info", "credential.updated", `Обновлены credentials «${credential.name}»`, {
      credentialId: id,
      credentialType: credential.type,
      credentialScope: credential.scope.kind,
    });
    return this.listCredentials(project).find((item) => item.id === id)!;
  }

  deleteCredential(id: string, projectId = "default"): boolean {
    const project = this.requireProject(projectId);
    if (!this.db.prepare("SELECT id FROM credentials WHERE id = ? AND project_id = ?").get(id, project)) return false;
    const mcpReference = this.db.prepare("SELECT name FROM mcp_servers WHERE credential_id = ? AND project_id = ? LIMIT 1")
      .get(id, project) as Row | undefined;
    if (mcpReference) throw new Error(`Credentials используются MCP-сервером «${String(mcpReference.name)}»`);
    const used = this.db
      .prepare("SELECT id, name, draft_graph_json FROM processes WHERE project_id = ?")
      .all(project) as Row[];
    const references = used.filter((row) => parseJson<ProcessGraph>(row.draft_graph_json, { nodes: [], edges: [] })
      .nodes.some((node) => node.config.credentialId === id));
    if (references.length > 0) {
      throw new Error(`Credentials используются в процессе «${String(references[0]!.name)}»`);
    }
    const publishedReference = (this.db
      .prepare("SELECT pv.process_id, p.name, pv.graph_json FROM process_versions pv JOIN processes p ON p.id = pv.process_id WHERE p.project_id = ?")
      .all(project) as Row[]).find((row) => parseJson<ProcessGraph>(row.graph_json, { nodes: [], edges: [] })
      .nodes.some((node) => node.config.credentialId === id));
    if (publishedReference) {
      throw new Error(`Credentials используются в опубликованной версии процесса «${String(publishedReference.name)}»`);
    }
    const result = this.db.prepare("DELETE FROM credentials WHERE id = ?").run(id);
    if (Number(result.changes) > 0) {
      this.addEvent(null, null, null, "warn", "credential.deleted", "Credentials удалены", { credentialId: id, projectId: project });
      return true;
    }
    return false;
  }

  private credentialData(id: string, projectId?: string): {
    type: string;
    data: Record<string, string>;
    scope: CredentialScope;
  } | null {
    const row = projectId
      ? this.db.prepare("SELECT type, secret_blob, scope_json FROM credentials WHERE id = ? AND project_id = ?").get(id, normalizeProjectId(projectId)) as Row | undefined
      : this.db.prepare("SELECT type, secret_blob, scope_json FROM credentials WHERE id = ?").get(id) as Row | undefined;
    if (!row) return null;
    return {
      type: String(row.type),
      data: decryptCredential(String(row.secret_blob), this.credentialsKey),
      scope: normalizeCredentialScope(parseJson<CreateCredentialInput["scope"]>(row.scope_json, { kind: "project" })),
    };
  }

  private httpCredentialData(id: string, projectId: string) {
    const credential = this.credentialData(id, projectId);
    if (credential?.scope.kind === "mcp") {
      throw new Error("MCP-scoped credentials нельзя использовать в HTTP process step");
    }
    return credential;
  }

  private assertMcpCredentialBinding(credentialId: string, namespace: string, projectId: string): void {
    const credential = this.credentialData(credentialId, projectId);
    if (!credential) throw new Error("Credentials MCP-сервера не найдены");
    if (credential.scope.kind === "mcp" && !credential.scope.serverNamespaces.includes(namespace)) {
      throw new Error(`Scope credentials не разрешает MCP namespace ${namespace}`);
    }
  }

  private currentMcpPolicy(projectId: string): ActiveMcpPolicy {
    const project = this.requireProject(projectId);
    const row = this.db.prepare(`
      SELECT version, sha256, document_json, actor_display, created_at
      FROM mcp_policy_versions WHERE project_id = ? ORDER BY version DESC LIMIT 1
    `).get(project) as Row | undefined;
    if (!row) {
      const document = normalizeMcpPolicyDocument(DEFAULT_MCP_POLICY);
      return {
        version: 0,
        sha256: mcpPolicySha256(document),
        document,
        actor: "system-default",
        createdAt: "1970-01-01T00:00:00.000Z",
      };
    }
    const document = normalizeMcpPolicyDocument(parseJson<unknown>(row.document_json, DEFAULT_MCP_POLICY));
    const sha256 = mcpPolicySha256(document);
    if (sha256 !== row.sha256) throw new Error("Хэш активной MCP policy не совпадает с документом");
    return {
      version: Number(row.version),
      sha256,
      document,
      actor: String(row.actor_display),
      createdAt: String(row.created_at),
    };
  }

  getMcpEmergencyDenyState(): McpPolicySnapshot["emergencyDeny"] {
    const stored = parseJson<Record<string, unknown>>(this.getSetting("mcp_emergency_deny"), {});
    const executing = Number((this.db.prepare(
      "SELECT COUNT(*) AS count FROM mcp_tool_calls WHERE status = 'executing'",
    ).get() as Row).count);
    return {
      enabled: stored.enabled === true,
      reason: typeof stored.reason === "string" ? stored.reason : "",
      actor: typeof stored.actor === "string" ? stored.actor : null,
      changedAt: typeof stored.changedAt === "string" ? stored.changedAt : null,
      pendingCallsDenied: Number.isFinite(Number(stored.pendingCallsDenied)) ? Number(stored.pendingCallsDenied) : 0,
      executingCalls: executing,
    };
  }

  getMcpPolicySnapshot(projectId = "default"): McpPolicySnapshot {
    return {
      ...this.currentMcpPolicy(projectId),
      emergencyDeny: this.getMcpEmergencyDenyState(),
    };
  }

  private mcpPolicyDecision(input: {
    projectId: string;
    namespace: string;
    toolName: string;
    risk: McpToolRisk;
    legacyPolicy: McpToolPolicy;
    policy?: Pick<ActiveMcpPolicy, "version" | "sha256" | "document">;
    emergencyDeny?: boolean;
  }): McpPolicyDecision {
    const policy = input.policy ?? this.currentMcpPolicy(input.projectId);
    return evaluateMcpPolicy({
      document: policy.document,
      version: policy.version,
      sha256: policy.sha256,
      namespace: input.namespace,
      toolName: input.toolName,
      risk: input.risk,
      legacyPolicy: input.legacyPolicy,
      emergencyDeny: input.emergencyDeny ?? this.getMcpEmergencyDenyState().enabled,
    });
  }

  previewMcpPolicy(documentInput: unknown, projectId = "default"): McpPolicyPreview {
    const project = this.requireProject(projectId);
    const current = this.currentMcpPolicy(project);
    const document = normalizeMcpPolicyDocument(documentInput);
    const candidateSha256 = mcpPolicySha256(document);
    const candidate = { version: current.version + 1, sha256: candidateSha256, document };
    const changes: McpPolicyPreview["changes"] = [];
    let toolsEvaluated = 0;
    let newlyAllowed = 0;
    let newlyDenied = 0;
    let approvalsIncreased = 0;
    let approvalsDecreased = 0;

    for (const server of this.listMcpServers(project)) {
      for (const tool of server.tools as Array<McpCatalogTool & {
        risk: McpToolRisk;
        legacyPolicy: McpToolPolicy;
      }>) {
        toolsEvaluated += 1;
        const before = this.mcpPolicyDecision({
          projectId: project,
          namespace: String(server.namespace),
          toolName: tool.name,
          risk: tool.risk,
          legacyPolicy: tool.legacyPolicy,
          policy: current,
          emergencyDeny: false,
        });
        const after = this.mcpPolicyDecision({
          projectId: project,
          namespace: String(server.namespace),
          toolName: tool.name,
          risk: tool.risk,
          legacyPolicy: tool.legacyPolicy,
          policy: candidate,
          emergencyDeny: false,
        });
        const changed = before.tier !== after.tier
          || before.effect !== after.effect
          || before.approvals !== after.approvals
          || before.ruleId !== after.ruleId;
        if (!changed) continue;
        if (before.effect !== "allow" && after.effect === "allow") newlyAllowed += 1;
        if (before.effect !== "deny" && after.effect === "deny") newlyDenied += 1;
        if (after.approvals > before.approvals) approvalsIncreased += 1;
        if (after.approvals < before.approvals) approvalsDecreased += 1;
        changes.push({
          serverId: String(server.id),
          serverName: String(server.name),
          namespace: String(server.namespace),
          toolName: tool.name,
          publicName: tool.publicName,
          risk: tool.risk,
          before,
          after,
        });
      }
    }
    return {
      baseVersion: current.version,
      baseSha256: current.sha256,
      candidateSha256,
      document,
      changed: current.sha256 !== candidateSha256,
      summary: {
        toolsEvaluated,
        toolsChanged: changes.length,
        newlyAllowed,
        newlyDenied,
        approvalsIncreased,
        approvalsDecreased,
      },
      changes: changes.slice(0, 1_000),
    };
  }

  activateMcpPolicy(
    documentInput: unknown,
    baseSha256: string,
    projectId: string,
    actor: McpDecisionActor,
  ): McpPolicySnapshot {
    const project = this.requireProject(projectId);
    const preview = this.previewMcpPolicy(documentInput, project);
    if (!baseSha256 || baseSha256 !== preview.baseSha256) {
      throw new Error("MCP policy изменилась после preview; выполните preview повторно");
    }
    if (!preview.changed) return this.getMcpPolicySnapshot(project);
    const timestamp = nowIso();
    this.db.prepare(`
      INSERT INTO mcp_policy_versions(
        project_id, version, sha256, document_json, actor_subject, actor_display, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?)
    `).run(
      project,
      preview.baseVersion + 1,
      preview.candidateSha256,
      serializeMcpPolicy(preview.document),
      actor.subject.slice(0, 300),
      actor.display.slice(0, 200),
      timestamp,
    );
    this.addEvent(null, null, null, "warn", "mcp.policy.activated", `Активирована MCP policy v${preview.baseVersion + 1}`, {
      projectId: project,
      version: preview.baseVersion + 1,
      sha256: preview.candidateSha256,
      actor: actor.display.slice(0, 200),
      diff: preview.summary,
    });
    return this.getMcpPolicySnapshot(project);
  }

  setMcpEmergencyDeny(enabled: boolean, reasonValue: string, actor: McpDecisionActor): McpPolicySnapshot["emergencyDeny"] {
    const reason = typeof reasonValue === "string" ? reasonValue.trim().slice(0, 500) : "";
    if (!reason) throw new Error("Причина изменения emergency deny обязательна");
    const timestamp = nowIso();
    return this.transaction(() => {
      const pending = enabled
        ? this.db.prepare("SELECT id, run_id, stage_id, node_id, public_name FROM mcp_tool_calls WHERE status = 'waiting_approval'")
          .all() as Row[]
        : [];
      for (const call of pending) {
        this.db.prepare(`
          UPDATE mcp_tool_calls SET status = 'rejected', error = ?, completed_at = ?, updated_at = ?
          WHERE id = ? AND status = 'waiting_approval'
        `).run(`Emergency deny: ${reason}`, timestamp, timestamp, String(call.id));
        this.addEvent(
          String(call.run_id),
          String(call.stage_id),
          typeof call.node_id === "string" ? call.node_id : null,
          "error",
          "mcp.call.emergency_denied",
          `MCP-вызов ${String(call.public_name)} остановлен emergency deny`,
          { callId: call.id, tool: call.public_name, actor: actor.display.slice(0, 200), reason },
        );
      }
      const state = {
        enabled,
        reason,
        actor: actor.display.slice(0, 200),
        changedAt: timestamp,
        pendingCallsDenied: pending.length,
      };
      this.db.prepare(`
        INSERT INTO settings(key, value, updated_at) VALUES ('mcp_emergency_deny', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
      `).run(JSON.stringify(state), timestamp);
      this.addEvent(null, null, null, enabled ? "error" : "warn", enabled ? "mcp.emergency_deny.enabled" : "mcp.emergency_deny.disabled", enabled
        ? "Централизованный emergency deny для MCP включён"
        : "Централизованный emergency deny для MCP снят", {
        actor: actor.display.slice(0, 200),
        reason,
        pendingCallsDenied: pending.length,
      });
      return this.getMcpEmergencyDenyState();
    });
  }

  listMcpServers(projectId = "default"): Array<Record<string, unknown>> {
    const project = normalizeProjectId(projectId);
    const activePolicy = this.currentMcpPolicy(project);
    const emergencyDeny = this.getMcpEmergencyDenyState().enabled;
    const policies = this.db.prepare(`
      SELECT p.server_id, p.tool_name, p.policy
      FROM mcp_tool_policies p JOIN mcp_servers s ON s.id = p.server_id
      WHERE s.project_id = ?
    `).all(project) as Row[];
    const policyMap = new Map(policies.map((row) => [`${String(row.server_id)}\0${String(row.tool_name)}`, String(row.policy) as McpToolPolicy]));
    return (this.db.prepare(`
      SELECT s.*,
        (SELECT COUNT(*) FROM mcp_tool_calls c WHERE c.server_id = s.id) AS total_calls,
        (SELECT COUNT(*) FROM mcp_tool_calls c WHERE c.server_id = s.id AND c.status = 'waiting_approval') AS waiting_calls
      FROM mcp_servers s WHERE s.project_id = ?
      ORDER BY s.name COLLATE NOCASE
    `).all(project) as Row[]).map((row) => {
      const trustAnnotations = Number(row.trust_annotations) === 1;
      const defaultPolicy = String(row.default_policy) as McpServerConnection["defaultPolicy"];
      const tools = parseJson<McpCatalogTool[]>(row.catalog_json, []).map((tool) => {
        const risk = mcpToolRisk(tool.annotations, trustAnnotations);
        const override = policyMap.get(`${String(row.id)}\0${tool.name}`) ?? null;
        const legacyPolicy = effectiveMcpPolicy(defaultPolicy, override, risk);
        const decision = this.mcpPolicyDecision({
          projectId: project,
          namespace: String(row.namespace),
          toolName: tool.name,
          risk,
          legacyPolicy,
          policy: activePolicy,
          emergencyDeny,
        });
        return {
          ...tool,
          risk,
          legacyPolicy,
          policy: decision.effect,
          policyOverride: override,
          decision,
          riskTier: decision.tier,
          requiredApprovals: decision.approvals,
          policyVersion: decision.policyVersion,
          policySha256: decision.policySha256,
          policyReason: decision.reason,
        };
      });
      return {
        id: row.id,
        name: row.name,
        namespace: row.namespace,
        endpoint: row.endpoint,
        credentialId: row.credential_id,
        hasCredential: typeof row.credential_id === "string" && Boolean(row.credential_id),
        enabled: Number(row.enabled) === 1,
        trustAnnotations,
        allowInsecureHttp: Number(row.allow_insecure_http) === 1,
        protocolVersion: row.protocol_version,
        defaultPolicy,
        catalogTtlSeconds: Math.max(30, Math.trunc(Number(row.catalog_ttl_ms) / 1_000)),
        catalogScope: row.catalog_scope,
        catalogExpiresAt: row.catalog_expires_at,
        lastSyncAt: row.last_sync_at,
        lastError: row.last_error,
        tools,
        totalCalls: Number(row.total_calls),
        waitingCalls: Number(row.waiting_calls),
        createdAt: row.created_at,
        updatedAt: row.updated_at,
      };
    });
  }

  listRecentMcpToolCalls(projectId = "default", limit = 100): Array<Record<string, unknown>> {
    const project = normalizeProjectId(projectId);
    return (this.db.prepare(`
      SELECT id, run_id, stage_id, server_id, server_name, tool_name, public_name,
        risk, risk_tier, legacy_policy, policy, required_approvals, policy_version,
        policy_sha256, policy_rule_id, policy_reason, status, arguments_summary_json,
        preview_diff_json, result_sha256, result_bytes,
        error, decision_actor, decision_at, created_at, started_at, completed_at, updated_at
      FROM mcp_tool_calls WHERE project_id = ?
      ORDER BY created_at DESC LIMIT ?
    `).all(project, Math.max(1, Math.min(500, Math.trunc(limit)))) as Row[]).map((row) => {
      const approvals = this.db.prepare(`
        SELECT actor_display FROM mcp_tool_call_approvals
        WHERE call_id = ? AND decision = 'approve' ORDER BY created_at, actor_subject
      `).all(String(row.id)) as Row[];
      return {
      callId: row.id,
      runId: row.run_id,
      stageId: row.stage_id,
      serverId: row.server_id,
      serverName: row.server_name,
      toolName: row.tool_name,
      publicName: row.public_name,
      risk: row.risk,
      riskTier: row.risk_tier,
      legacyPolicy: row.legacy_policy,
      policy: row.policy,
      requiredApprovals: Number(row.required_approvals),
      approvalCount: approvals.length,
      approvers: approvals.map((approval) => String(approval.actor_display)),
      policyVersion: Number(row.policy_version),
      policySha256: row.policy_sha256,
      policyRuleId: row.policy_rule_id,
      policyReason: row.policy_reason,
      status: row.status,
      arguments: parseJson<Record<string, unknown>>(row.arguments_summary_json, {}),
      previewDiff: parseJson<Array<Record<string, unknown>>>(row.preview_diff_json, []),
      resultSha256: row.result_sha256,
      resultBytes: row.result_bytes === null ? null : Number(row.result_bytes),
      error: row.error,
      decisionActor: row.decision_actor,
      decisionAt: row.decision_at,
      createdAt: row.created_at,
      startedAt: row.started_at,
      completedAt: row.completed_at,
      updatedAt: row.updated_at,
    };
    });
  }

  createMcpServer(input: CreateMcpServerInput, projectId = "default"): Record<string, unknown> {
    const project = this.requireProject(projectId);
    const normalized = normalizeMcpServerInput(input);
    if (normalized.credentialId) this.assertMcpCredentialBinding(normalized.credentialId, normalized.namespace, project);
    const id = randomUUID();
    const timestamp = nowIso();
    try {
      this.db.prepare(`
        INSERT INTO mcp_servers(
          id, project_id, name, namespace, endpoint, credential_id, enabled,
          trust_annotations, allow_insecure_http, protocol_version, default_policy,
          catalog_ttl_ms, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        id,
        project,
        normalized.name,
        normalized.namespace,
        normalized.endpoint,
        normalized.credentialId,
        normalized.enabled ? 1 : 0,
        normalized.trustAnnotations ? 1 : 0,
        normalized.allowInsecureHttp ? 1 : 0,
        MCP_PROTOCOL_VERSION,
        normalized.defaultPolicy,
        normalized.catalogTtlSeconds * 1_000,
        timestamp,
        timestamp,
      );
    } catch (error) {
      if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
        throw new Error("MCP-сервер с таким названием или namespace уже существует в проекте");
      }
      throw error;
    }
    this.addEvent(null, null, null, "info", "mcp.server.created", `Добавлен MCP-сервер «${normalized.name}»`, {
      projectId: project,
      serverId: id,
      namespace: normalized.namespace,
      defaultPolicy: normalized.defaultPolicy,
    });
    return this.listMcpServers(project).find((server) => server.id === id)!;
  }

  updateMcpServer(id: string, input: UpdateMcpServerInput, projectId = "default"): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const current = this.getMcpServerConnection(id, project);
    if (!current) return null;
    const normalized = normalizeMcpServerPatch(input, current);
    if (normalized.credentialId) this.assertMcpCredentialBinding(normalized.credentialId, normalized.namespace, project);
    const resetCatalog = normalized.namespace !== current.namespace
      || normalized.endpoint !== current.endpoint
      || normalized.credentialId !== current.credentialId;
    const timestamp = nowIso();
    try {
      this.db.prepare(`
        UPDATE mcp_servers SET
          name = ?, namespace = ?, endpoint = ?, credential_id = ?, enabled = ?,
          trust_annotations = ?, allow_insecure_http = ?, default_policy = ?,
          catalog_ttl_ms = ?, catalog_json = CASE WHEN ? THEN '[]' ELSE catalog_json END,
          catalog_expires_at = CASE WHEN ? THEN NULL ELSE catalog_expires_at END,
          last_sync_at = CASE WHEN ? THEN NULL ELSE last_sync_at END,
          last_error = CASE WHEN ? THEN NULL ELSE last_error END,
          updated_at = ?
        WHERE id = ? AND project_id = ?
      `).run(
        normalized.name,
        normalized.namespace,
        normalized.endpoint,
        normalized.credentialId,
        normalized.enabled ? 1 : 0,
        normalized.trustAnnotations ? 1 : 0,
        normalized.allowInsecureHttp ? 1 : 0,
        normalized.defaultPolicy,
        normalized.catalogTtlSeconds * 1_000,
        resetCatalog ? 1 : 0,
        resetCatalog ? 1 : 0,
        resetCatalog ? 1 : 0,
        resetCatalog ? 1 : 0,
        timestamp,
        id,
        project,
      );
    } catch (error) {
      if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
        throw new Error("MCP-сервер с таким названием или namespace уже существует в проекте");
      }
      throw error;
    }
    this.addEvent(null, null, null, "info", "mcp.server.updated", `Обновлён MCP-сервер «${normalized.name}»`, {
      projectId: project,
      serverId: id,
      catalogReset: resetCatalog,
    });
    return this.listMcpServers(project).find((server) => server.id === id) ?? null;
  }

  deleteMcpServer(id: string, projectId = "default"): boolean {
    const project = this.requireProject(projectId);
    const active = Number((this.db.prepare(`
      SELECT COUNT(*) AS count FROM mcp_tool_calls
      WHERE server_id = ? AND status IN ('waiting_approval', 'executing')
    `).get(id) as Row).count);
    if (active > 0) throw new Error("Нельзя удалить MCP-сервер с активными вызовами");
    const result = this.db.prepare("DELETE FROM mcp_servers WHERE id = ? AND project_id = ?").run(id, project);
    if (Number(result.changes) === 0) return false;
    this.addEvent(null, null, null, "warn", "mcp.server.deleted", "MCP-сервер удалён", { projectId: project, serverId: id });
    return true;
  }

  setMcpToolPolicy(
    serverId: string,
    toolName: string,
    policy: McpToolPolicy,
    projectId = "default",
  ): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const server = this.getMcpServerConnection(serverId, project);
    if (!server) return null;
    if (!server.catalog.some((tool) => tool.name === toolName)) throw new Error("MCP-инструмент не найден в каталоге");
    if (!(["allow", "approval", "deny"] as const).includes(policy)) throw new Error("Неизвестная policy инструмента");
    const timestamp = nowIso();
    this.db.prepare(`
      INSERT INTO mcp_tool_policies(server_id, tool_name, policy, updated_at)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(server_id, tool_name) DO UPDATE SET policy = excluded.policy, updated_at = excluded.updated_at
    `).run(serverId, toolName, policy, timestamp);
    this.db.prepare("UPDATE mcp_servers SET updated_at = ? WHERE id = ?").run(timestamp, serverId);
    this.addEvent(null, null, null, "info", "mcp.policy.updated", `Policy ${server.namespace}.${toolName}: ${policy}`, {
      projectId: project,
      serverId,
      toolName,
      policy,
    });
    return this.listMcpServers(project).find((candidate) => candidate.id === serverId) ?? null;
  }

  getMcpServerConnection(id: string, projectId?: string): McpServerConnection | null {
    const row = projectId
      ? this.db.prepare("SELECT * FROM mcp_servers WHERE id = ? AND project_id = ?").get(id, normalizeProjectId(projectId)) as Row | undefined
      : this.db.prepare("SELECT * FROM mcp_servers WHERE id = ?").get(id) as Row | undefined;
    if (!row) return null;
    const project = String(row.project_id);
    const credentialId = typeof row.credential_id === "string" && row.credential_id ? row.credential_id : null;
    return {
      id: String(row.id),
      projectId: project,
      name: String(row.name),
      namespace: String(row.namespace),
      endpoint: String(row.endpoint),
      credentialId,
      credential: credentialId ? this.credentialData(credentialId, project) : null,
      enabled: Number(row.enabled) === 1,
      trustAnnotations: Number(row.trust_annotations) === 1,
      allowInsecureHttp: Number(row.allow_insecure_http) === 1,
      defaultPolicy: String(row.default_policy) as McpServerConnection["defaultPolicy"],
      catalogTtlSeconds: Math.max(30, Math.trunc(Number(row.catalog_ttl_ms) / 1_000)),
      catalog: parseJson<McpCatalogTool[]>(row.catalog_json, []),
    };
  }

  dueMcpServerIds(limit = 10): string[] {
    return (this.db.prepare(`
      SELECT id FROM mcp_servers
      WHERE enabled = 1
        AND (last_sync_at IS NULL OR catalog_expires_at IS NULL OR catalog_expires_at <= ?)
      ORDER BY COALESCE(last_sync_at, '') ASC LIMIT ?
    `).all(nowIso(), Math.max(1, Math.min(100, Math.trunc(limit)))) as Row[]).map((row) => String(row.id));
  }

  saveMcpCatalog(id: string, tools: McpCatalogTool[], ttlMs: number, cacheScope: string): void {
    const timestamp = nowIso();
    const boundedTtl = Math.max(30_000, Math.min(86_400_000, Math.trunc(ttlMs)));
    const expiresAt = new Date(Date.now() + boundedTtl).toISOString();
    const result = this.db.prepare(`
      UPDATE mcp_servers SET catalog_json = ?, catalog_ttl_ms = ?, catalog_scope = ?,
        catalog_expires_at = ?, last_sync_at = ?, last_error = NULL, updated_at = ?
      WHERE id = ?
    `).run(JSON.stringify(tools), boundedTtl, cacheScope === "private" ? "private" : "public", expiresAt, timestamp, timestamp, id);
    if (Number(result.changes) === 0) throw new Error("MCP-сервер не найден");
    const server = this.getMcpServerConnection(id);
    this.addEvent(null, null, null, "info", "mcp.catalog.synced", `Каталог MCP «${server?.name ?? id}» синхронизирован`, {
      projectId: server?.projectId ?? "global",
      serverId: id,
      tools: tools.length,
      ttlMs: boundedTtl,
      cacheScope,
    });
  }

  saveMcpCatalogError(id: string, message: string): void {
    const timestamp = nowIso();
    const safe = message.replace(/Bearer\s+[^\s,;]+/gi, "Bearer [redacted]").slice(0, 2_000);
    this.db.prepare("UPDATE mcp_servers SET last_error = ?, updated_at = ? WHERE id = ?").run(safe, timestamp, id);
    const server = this.getMcpServerConnection(id);
    this.addEvent(null, null, null, "warn", "mcp.catalog.failed", `Не удалось синхронизировать MCP «${server?.name ?? id}»`, {
      projectId: server?.projectId ?? "global",
      serverId: id,
      error: safe,
    });
  }

  private mcpLeaseTools(projectId: string): McpLeaseTool[] {
    const tools: McpLeaseTool[] = [];
    for (const serverDto of this.listMcpServers(projectId)) {
      if (serverDto.enabled !== true) continue;
      for (const tool of serverDto.tools as Array<McpCatalogTool & {
        risk: McpLeaseTool["risk"];
        policy: McpToolPolicy;
        decision: McpPolicyDecision;
      }>) {
        if (tool.policy === "deny") continue;
        tools.push({
          publicName: tool.publicName,
          serverId: String(serverDto.id),
          serverName: String(serverDto.name),
          name: tool.name,
          description: tool.description,
          inputSchema: tool.inputSchema,
          risk: tool.risk,
          policy: tool.policy,
          riskTier: tool.decision.tier,
          requiredApprovals: tool.decision.approvals,
          policyVersion: tool.decision.policyVersion,
          policySha256: tool.decision.policySha256,
        });
      }
    }
    return tools.sort((left, right) => left.publicName.localeCompare(right.publicName));
  }

  resolveMcpLeaseTool(nodeId: string, leaseId: string, publicName: string): ResolvedMcpLeaseTool | null {
    const stage = this.db.prepare(`
      SELECT s.id AS stage_id, s.run_id, s.lease_traceparent, r.project_id
      FROM stages s JOIN runs r ON r.id = s.run_id
      WHERE s.node_id = ? AND s.lease_id = ? AND s.status = 'running' AND s.lease_expires_at >= ?
    `).get(nodeId, leaseId, nowIso()) as Row | undefined;
    if (!stage) return null;
    const project = String(stage.project_id);
    for (const serverDto of this.listMcpServers(project)) {
      if (serverDto.enabled !== true) continue;
      const tool = (serverDto.tools as Array<McpCatalogTool & {
        risk: McpLeaseTool["risk"];
        policy: McpToolPolicy;
        decision: McpPolicyDecision;
      }>)
        .find((candidate) => candidate.publicName === publicName);
      if (!tool) continue;
      const server = this.getMcpServerConnection(String(serverDto.id), project);
      if (!server) return null;
      return {
        nodeId,
        leaseId,
        runId: String(stage.run_id),
        stageId: String(stage.stage_id),
        projectId: project,
        traceparent: typeof stage.lease_traceparent === "string" ? stage.lease_traceparent : null,
        server,
        tool,
        publicName,
        risk: tool.risk,
        policy: tool.policy,
        decision: tool.decision,
      };
    }
    return null;
  }

  createOrGetMcpToolCall(input: {
    resolved: ResolvedMcpLeaseTool;
    clientCallId: string;
    arguments: Record<string, unknown>;
    status: "waiting_approval" | "executing" | "rejected";
    approvalTtlSeconds: number;
    traceparent: string | null;
  }): StoredMcpCall {
    return this.transaction(() => {
      const existing = this.db.prepare("SELECT * FROM mcp_tool_calls WHERE lease_id = ? AND client_call_id = ?")
        .get(input.resolved.leaseId, input.clientCallId) as Row | undefined;
      if (existing) return { ...this.mcpCallResponse(existing, true), isNew: false };
      const callId = randomUUID();
      const timestamp = nowIso();
      const expiresAt = futureIso(input.approvalTtlSeconds);
      const decision = input.resolved.decision;
      const deniedError = input.status === "rejected" ? `Policy deny: ${decision.reason}` : null;
      this.db.prepare(`
        INSERT INTO mcp_tool_calls(
          id, project_id, run_id, stage_id, lease_id, node_id, client_call_id,
          server_id, server_name, tool_name, public_name, risk, risk_tier,
          legacy_policy, policy, required_approvals, policy_version, policy_sha256,
          policy_rule_id, policy_reason, status, arguments_blob, arguments_summary_json,
          preview_diff_json, traceparent, error, expires_at, created_at, started_at,
          completed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        callId,
        input.resolved.projectId,
        input.resolved.runId,
        input.resolved.stageId,
        input.resolved.leaseId,
        input.resolved.nodeId,
        input.clientCallId,
        input.resolved.server.id,
        input.resolved.server.name,
        input.resolved.tool.name,
        input.resolved.publicName,
        input.resolved.risk,
        decision.tier,
        decision.legacyPolicy,
        decision.effect,
        decision.approvals,
        decision.policyVersion,
        decision.policySha256,
        decision.ruleId,
        decision.reason,
        input.status,
        encryptJson(input.arguments, this.credentialsKey),
        JSON.stringify(redactMcpValue(input.arguments)),
        JSON.stringify(mcpArgumentsPreviewDiff(input.arguments)),
        input.traceparent,
        deniedError,
        expiresAt,
        timestamp,
        input.status === "executing" ? timestamp : null,
        input.status === "rejected" ? timestamp : null,
        timestamp,
      );
      const isApproval = input.status === "waiting_approval";
      const isDenied = input.status === "rejected";
      this.addEvent(
        input.resolved.runId,
        input.resolved.stageId,
        input.resolved.nodeId,
        isDenied ? "error" : isApproval ? "warn" : "info",
        isDenied ? "mcp.call.policy_denied" : isApproval ? "mcp.approval.requested" : "mcp.call.started",
        isDenied
          ? `MCP-инструмент ${input.resolved.publicName} запрещён policy`
          : isApproval
          ? `MCP-инструмент ${input.resolved.publicName} ожидает подтверждения`
          : `MCP-инструмент ${input.resolved.publicName} запущен`,
        {
          callId,
          serverId: input.resolved.server.id,
          tool: input.resolved.publicName,
          risk: input.resolved.risk,
          riskTier: decision.tier,
          policy: decision.effect,
          requiredApprovals: decision.approvals,
          policyVersion: decision.policyVersion,
          policySha256: decision.policySha256,
          policyRuleId: decision.ruleId,
          policyReason: decision.reason,
          arguments: redactMcpValue(input.arguments),
          previewDiff: mcpArgumentsPreviewDiff(input.arguments),
        },
      );
      return {
        callId,
        status: input.status,
        ...(deniedError ? { error: deniedError } : {}),
        requiredApprovals: decision.approvals,
        approvalCount: 0,
        approvers: [],
        isNew: true,
      };
    });
  }

  private mcpCallResponse(row: Row, includeResult: boolean): StoredMcpCall {
    const response: StoredMcpCall = {
      callId: String(row.id),
      status: String(row.status) as StoredMcpCall["status"],
      requiredApprovals: Number(row.required_approvals ?? 0),
    };
    const approvals = this.db.prepare(`
      SELECT actor_display FROM mcp_tool_call_approvals
      WHERE call_id = ? AND decision = 'approve' ORDER BY created_at, actor_subject
    `).all(String(row.id)) as Row[];
    response.approvalCount = approvals.length;
    response.approvers = approvals.map((approval) => String(approval.actor_display));
    if (includeResult && row.status === "completed" && typeof row.result_blob === "string") {
      response.result = decryptJson(row.result_blob, this.credentialsKey);
    }
    if (typeof row.error === "string" && row.error) response.error = row.error;
    return response;
  }

  getMcpToolCallForLease(nodeId: string, leaseId: string, callId: string): StoredMcpCall | null {
    const row = this.db.prepare(`
      SELECT * FROM mcp_tool_calls WHERE id = ? AND node_id = ? AND lease_id = ?
    `).get(callId, nodeId, leaseId) as Row | undefined;
    return row ? this.mcpCallResponse(row, true) : null;
  }

  approveMcpToolCall(
    callId: string,
    projectId: string,
    actorInput: McpDecisionActor | string,
    executionTtlSeconds: number,
  ): McpApprovalResult | null {
    const project = this.requireProject(projectId);
    const actor = typeof actorInput === "string"
      ? { subject: actorInput, display: actorInput }
      : actorInput;
    if (!actor.subject.trim() || !actor.display.trim()) throw new Error("Идентификатор approving actor обязателен");
    return this.transaction(() => {
      const row = this.db.prepare(`
        SELECT c.* FROM mcp_tool_calls c
        JOIN stages s ON s.id = c.stage_id AND s.lease_id = c.lease_id AND s.node_id = c.node_id
        WHERE c.id = ? AND c.project_id = ? AND c.status = 'waiting_approval'
          AND c.expires_at >= ? AND s.status = 'running' AND s.lease_expires_at >= ?
      `).get(callId, project, nowIso(), nowIso()) as Row | undefined;
      if (!row) return null;
      const resolved = this.resolveMcpLeaseTool(String(row.node_id), String(row.lease_id), String(row.public_name));
      if (!resolved) return null;
      const timestamp = nowIso();
      if (resolved.decision.effect === "deny") {
        this.db.prepare(`
          UPDATE mcp_tool_calls SET status = 'rejected', error = ?, completed_at = ?, updated_at = ?
          WHERE id = ? AND status = 'waiting_approval'
        `).run(`Policy deny: ${resolved.decision.reason}`, timestamp, timestamp, callId);
        this.addEvent(resolved.runId, resolved.stageId, resolved.nodeId, "error", "mcp.call.policy_denied", `MCP-вызов ${resolved.publicName} запрещён актуальной policy`, {
          callId,
          tool: resolved.publicName,
          actor: actor.display.slice(0, 200),
          policyReason: resolved.decision.reason,
        });
        const rejected = this.db.prepare("SELECT * FROM mcp_tool_calls WHERE id = ?").get(callId) as Row;
        return { call: this.mcpCallResponse(rejected, false), execution: null };
      }
      const requiredApprovals = Math.max(
        Number(row.required_approvals),
        resolved.decision.approvals,
        resolved.risk === "destructive" || resolved.decision.tier === "critical" ? 2 : 0,
      );
      const previous = this.db.prepare("SELECT decision FROM mcp_tool_call_approvals WHERE call_id = ? AND actor_subject = ?")
        .get(callId, actor.subject.slice(0, 300)) as Row | undefined;
      if (previous) throw new Error("Этот пользователь уже принял решение по MCP-вызову; нужен другой approver");
      this.db.prepare(`
        INSERT INTO mcp_tool_call_approvals(call_id, actor_subject, actor_display, decision, created_at)
        VALUES (?, ?, ?, 'approve', ?)
      `).run(callId, actor.subject.slice(0, 300), actor.display.slice(0, 200), timestamp);
      const approvalCount = Number((this.db.prepare(`
        SELECT COUNT(*) AS count FROM mcp_tool_call_approvals WHERE call_id = ? AND decision = 'approve'
      `).get(callId) as Row).count);
      this.db.prepare(`
        UPDATE mcp_tool_calls SET required_approvals = ?, risk_tier = ?, policy = ?,
          policy_version = ?, policy_sha256 = ?, policy_rule_id = ?, policy_reason = ?, updated_at = ?
        WHERE id = ? AND status = 'waiting_approval'
      `).run(
        requiredApprovals,
        resolved.decision.tier,
        resolved.decision.effect,
        resolved.decision.policyVersion,
        resolved.decision.policySha256,
        resolved.decision.ruleId,
        resolved.decision.reason,
        timestamp,
        callId,
      );
      this.addEvent(resolved.runId, resolved.stageId, resolved.nodeId, "info", "mcp.approval.recorded", `Зафиксировано решение ${approvalCount}/${requiredApprovals} для ${resolved.publicName}`, {
        callId,
        tool: resolved.publicName,
        risk: resolved.risk,
        riskTier: resolved.decision.tier,
        actor: actor.display.slice(0, 200),
        approvalCount,
        requiredApprovals,
      });
      if (approvalCount < requiredApprovals) {
        const pending = this.db.prepare("SELECT * FROM mcp_tool_calls WHERE id = ?").get(callId) as Row;
        return { call: this.mcpCallResponse(pending, false), execution: null };
      }
      const executionExpiresAt = futureIso(Math.max(1, Math.min(330, Math.trunc(executionTtlSeconds))));
      const claimed = this.db.prepare(`
        UPDATE mcp_tool_calls SET status = 'executing', decision_actor = ?, decision_at = ?,
          started_at = ?, expires_at = ?, updated_at = ? WHERE id = ? AND status = 'waiting_approval'
      `).run(actor.display.slice(0, 200), timestamp, timestamp, executionExpiresAt, timestamp, callId);
      if (Number(claimed.changes) !== 1) return null;
      this.addEvent(resolved.runId, resolved.stageId, resolved.nodeId, "info", "mcp.approval.approved", `Разрешён вызов ${resolved.publicName}`, {
        callId,
        tool: resolved.publicName,
        risk: resolved.risk,
        actor: actor.display.slice(0, 200),
        approvalCount,
        requiredApprovals,
      });
      const executing = this.db.prepare("SELECT * FROM mcp_tool_calls WHERE id = ?").get(callId) as Row;
      return {
        call: this.mcpCallResponse(executing, false),
        execution: {
          ...resolved,
          callId,
          arguments: (decryptJson(row.arguments_blob, this.credentialsKey) ?? {}) as Record<string, unknown>,
          traceparent: typeof row.traceparent === "string" ? row.traceparent : resolved.traceparent,
        },
      };
    });
  }

  rejectMcpToolCall(callId: string, projectId: string, actorInput: McpDecisionActor | string): boolean {
    const project = this.requireProject(projectId);
    const actor = typeof actorInput === "string"
      ? { subject: actorInput, display: actorInput }
      : actorInput;
    return this.transaction(() => {
      const row = this.db.prepare(`
        SELECT * FROM mcp_tool_calls WHERE id = ? AND project_id = ? AND status = 'waiting_approval'
      `).get(callId, project) as Row | undefined;
      if (!row) return false;
      const timestamp = nowIso();
      this.db.prepare(`
        INSERT INTO mcp_tool_call_approvals(call_id, actor_subject, actor_display, decision, created_at)
        VALUES (?, ?, ?, 'reject', ?)
        ON CONFLICT(call_id, actor_subject) DO UPDATE SET
          actor_display = excluded.actor_display, decision = 'reject', created_at = excluded.created_at
      `).run(callId, actor.subject.slice(0, 300), actor.display.slice(0, 200), timestamp);
      this.db.prepare(`
        UPDATE mcp_tool_calls SET status = 'rejected', error = 'Вызов отклонён оператором',
          decision_actor = ?, decision_at = ?, completed_at = ?, updated_at = ?
        WHERE id = ? AND status = 'waiting_approval'
      `).run(actor.display.slice(0, 200), timestamp, timestamp, timestamp, callId);
      this.addEvent(String(row.run_id), String(row.stage_id), String(row.node_id), "warn", "mcp.approval.rejected", `Отклонён вызов ${String(row.public_name)}`, {
        callId,
        tool: row.public_name,
        risk: row.risk,
        actor: actor.display.slice(0, 200),
      });
      return true;
    });
  }

  cancelMcpToolCallForLease(nodeId: string, leaseId: string, callId: string): StoredMcpCall | null {
    return this.transaction(() => {
      const row = this.db.prepare(`
        SELECT * FROM mcp_tool_calls WHERE id = ? AND node_id = ? AND lease_id = ?
      `).get(callId, nodeId, leaseId) as Row | undefined;
      if (!row) return null;
      if (row.status !== "waiting_approval") return this.mcpCallResponse(row, true);
      const timestamp = nowIso();
      const changed = this.db.prepare(`
        UPDATE mcp_tool_calls SET status = 'expired', error = 'Worker перестал ожидать approval',
          completed_at = ?, updated_at = ?
        WHERE id = ? AND status = 'waiting_approval'
      `).run(timestamp, timestamp, callId);
      if (Number(changed.changes) === 1) {
        this.addEvent(
          String(row.run_id),
          String(row.stage_id),
          nodeId,
          "warn",
          "mcp.call.cancelled",
          `Worker отменил ожидающий MCP-вызов ${String(row.public_name)}`,
          { callId, tool: row.public_name },
        );
      }
      const current = this.db.prepare("SELECT * FROM mcp_tool_calls WHERE id = ?").get(callId) as Row;
      return this.mcpCallResponse(current, true);
    });
  }

  completeMcpToolCall(callId: string, result: unknown): void {
    const serialized = JSON.stringify(result);
    const timestamp = nowIso();
    const row = this.db.prepare("SELECT run_id, stage_id, node_id, public_name FROM mcp_tool_calls WHERE id = ? AND status = 'executing'")
      .get(callId) as Row | undefined;
    if (!row) throw new Error("Активный MCP-вызов не найден");
    this.db.prepare(`
      UPDATE mcp_tool_calls SET status = 'completed', result_blob = ?, result_sha256 = ?,
        result_bytes = ?, completed_at = ?, updated_at = ? WHERE id = ? AND status = 'executing'
    `).run(
      encryptJson(result, this.credentialsKey),
      sha256Text(serialized),
      Buffer.byteLength(serialized),
      timestamp,
      timestamp,
      callId,
    );
    this.addEvent(String(row.run_id), String(row.stage_id), String(row.node_id), "info", "mcp.call.completed", `MCP-инструмент ${String(row.public_name)} завершён`, {
      callId,
      tool: row.public_name,
      resultSha256: sha256Text(serialized),
      resultBytes: Buffer.byteLength(serialized),
    });
  }

  failMcpToolCall(callId: string, message: string): void {
    const safe = message.replace(/Bearer\s+[^\s,;]+/gi, "Bearer [redacted]").slice(0, 4_000);
    const timestamp = nowIso();
    const row = this.db.prepare("SELECT run_id, stage_id, node_id, public_name FROM mcp_tool_calls WHERE id = ? AND status = 'executing'")
      .get(callId) as Row | undefined;
    if (!row) return;
    this.db.prepare(`
      UPDATE mcp_tool_calls SET status = 'failed', error = ?, completed_at = ?, updated_at = ?
      WHERE id = ? AND status = 'executing'
    `).run(safe, timestamp, timestamp, callId);
    this.addEvent(String(row.run_id), String(row.stage_id), String(row.node_id), "error", "mcp.call.failed", `MCP-инструмент ${String(row.public_name)} завершился ошибкой`, {
      callId,
      tool: row.public_name,
      error: safe,
    });
  }

  assertMcpToolCallExecutable(callId: string): void {
    const row = this.db.prepare("SELECT * FROM mcp_tool_calls WHERE id = ? AND status = 'executing'")
      .get(callId) as Row | undefined;
    if (!row) throw new Error("MCP-вызов больше не разрешён к выполнению");
    const resolved = this.resolveMcpLeaseTool(String(row.node_id), String(row.lease_id), String(row.public_name));
    const approvalCount = Number((this.db.prepare(`
      SELECT COUNT(*) AS count FROM mcp_tool_call_approvals WHERE call_id = ? AND decision = 'approve'
    `).get(callId) as Row).count);
    const requiredNow = resolved
      ? Math.max(
        resolved.decision.approvals,
        resolved.risk === "destructive" || resolved.decision.tier === "critical" ? 2 : 0,
      )
      : Number.POSITIVE_INFINITY;
    const reason = !resolved
      ? "MCP server/tool больше не доступен этой аренде"
      : resolved.decision.effect === "deny"
        ? resolved.decision.reason
        : approvalCount < requiredNow
          ? `Актуальная policy требует approvals ${approvalCount}/${requiredNow}`
          : null;
    if (!reason) return;
    const timestamp = nowIso();
    this.db.prepare(`
      UPDATE mcp_tool_calls SET status = 'rejected', error = ?, completed_at = ?, updated_at = ?
      WHERE id = ? AND status = 'executing'
    `).run(`Execution blocked: ${reason}`, timestamp, timestamp, callId);
    this.addEvent(String(row.run_id), String(row.stage_id), String(row.node_id), "error", "mcp.call.execution_blocked", `MCP-вызов ${String(row.public_name)} заблокирован перед upstream`, {
      callId,
      tool: row.public_name,
      reason,
      approvalCount,
      requiredApprovals: Number.isFinite(requiredNow) ? requiredNow : null,
    });
    throw new Error(`MCP execution blocked: ${reason}`);
  }

  private a2aEndpointConnection(row: Row): A2AEndpointConnection {
    return {
      id: String(row.id),
      projectId: String(row.project_id),
      agentId: String(row.agent_id),
      agentName: String(row.agent_name),
      agentRole: String(row.agent_role),
      name: String(row.name),
      description: String(row.description),
      version: String(row.version),
      skillId: String(row.skill_id),
      skillName: String(row.skill_name),
      skillDescription: String(row.skill_description),
      tags: parseJson<string[]>(row.tags_json, []),
      examples: parseJson<string[]>(row.examples_json, []),
      inputModes: parseJson<A2AEndpointConnection["inputModes"]>(row.input_modes_json, ["text/plain"]),
      outputModes: parseJson<string[]>(row.output_modes_json, ["text/plain"]),
      knowledgeCollectionIds: normalizeKnowledgeCollectionIds(parseJson<unknown>(row.knowledge_collection_ids_json, [])),
      approvalRequired: Number(row.approval_required) === 1,
      streamingEnabled: Number(row.streaming_enabled) === 1,
      pushNotificationsEnabled: Number(row.push_notifications_enabled) === 1,
      fileArtifactsEnabled: Number(row.file_artifacts_enabled) === 1,
      enabled: Number(row.enabled) === 1,
      priority: Number(row.priority),
      maxInputCharacters: Number(row.max_input_characters),
      maxActiveTasks: Number(row.max_active_tasks),
      maxFileBytes: Number(row.max_file_bytes),
      maxFiles: Number(row.max_files),
      tokenSuffix: String(row.token_suffix),
      tokenRotatedAt: String(row.token_rotated_at),
      createdAt: String(row.created_at),
      updatedAt: String(row.updated_at),
    };
  }

  getA2AEndpointConnection(
    id: string,
    projectId?: string,
    enabledOnly = false,
  ): A2AEndpointConnection | null {
    const conditions = ["e.id = ?"];
    const params: SqlScalar[] = [id];
    if (projectId !== undefined) {
      conditions.push("e.project_id = ?");
      params.push(normalizeProjectId(projectId));
    }
    if (enabledOnly) conditions.push("e.enabled = 1");
    const row = this.db.prepare(`
      SELECT e.*, a.name AS agent_name, a.role AS agent_role
      FROM a2a_endpoints e JOIN agents a ON a.id = e.agent_id
      WHERE ${conditions.join(" AND ")}
    `).get(...params) as Row | undefined;
    return row ? this.a2aEndpointConnection(row) : null;
  }

  authenticateA2AEndpoint(id: string, token: string): A2AEndpointConnection | null {
    if (!token || token.length > 512) return null;
    const row = this.db.prepare(`
      SELECT e.*, a.name AS agent_name, a.role AS agent_role
      FROM a2a_endpoints e JOIN agents a ON a.id = e.agent_id
      WHERE e.id = ? AND e.token_hash = ? AND e.enabled = 1
    `).get(id, hashToken(token)) as Row | undefined;
    return row ? this.a2aEndpointConnection(row) : null;
  }

  listA2AEndpoints(projectId = "default"): Array<Record<string, unknown>> {
    const project = normalizeProjectId(projectId);
    return (this.db.prepare(`
      SELECT e.*, a.name AS agent_name, a.role AS agent_role,
        (SELECT COUNT(*) FROM a2a_tasks t WHERE t.endpoint_id = e.id) AS total_tasks,
        (SELECT COUNT(*) FROM a2a_tasks t JOIN runs r ON r.id = t.run_id
          WHERE t.endpoint_id = e.id AND r.status IN ('queued', 'running', 'waiting_approval')) AS active_tasks,
        (SELECT MAX(t.updated_at) FROM a2a_tasks t WHERE t.endpoint_id = e.id) AS last_task_at
      FROM a2a_endpoints e JOIN agents a ON a.id = e.agent_id
      WHERE e.project_id = ?
      ORDER BY e.updated_at DESC, e.name COLLATE NOCASE
    `).all(project) as Row[]).map((row) => ({
      ...this.a2aEndpointConnection(row),
      totalTasks: Number(row.total_tasks),
      activeTasks: Number(row.active_tasks),
      lastTaskAt: typeof row.last_task_at === "string" ? row.last_task_at : null,
    }));
  }

  createA2AEndpoint(
    input: CreateA2AEndpointInput,
    projectId = "default",
    actor = "system",
  ): { endpoint: Record<string, unknown>; accessToken: string } {
    const project = this.requireProject(projectId);
    if (typeof input.agentId !== "string" || !input.agentId.trim()) throw new Error("Агент A2A endpoint обязателен");
    const agent = this.effectiveAgentRow(input.agentId.trim(), project);
    if (!agent || agent.id === "__agat_eval_judge__") throw new Error("Агент A2A endpoint не найден в проекте");
    const defaults: A2AEndpointConnection = {
      id: "",
      projectId: project,
      agentId: String(agent.id),
      agentName: String(agent.name),
      agentRole: String(agent.role),
      name: String(agent.name),
      description: String(agent.role),
      version: "1.0.0",
      skillId: "",
      skillName: String(agent.name),
      skillDescription: String(agent.role),
      tags: ["agat", "local-agent"],
      examples: [],
      inputModes: ["text/plain"],
      outputModes: ["text/plain"],
      knowledgeCollectionIds: [],
      approvalRequired: false,
      streamingEnabled: true,
      pushNotificationsEnabled: false,
      fileArtifactsEnabled: false,
      enabled: true,
      priority: 50,
      maxInputCharacters: 20_000,
      maxActiveTasks: 10,
      maxFileBytes: 512_000,
      maxFiles: 4,
      tokenSuffix: "",
      tokenRotatedAt: "",
      createdAt: "",
      updatedAt: "",
    };
    const normalized = normalizeA2AEndpointInput(input, defaults);
    const collectionIds = this.requireKnowledgeCollectionIds(project, normalized.knowledgeCollectionIds);
    const id = randomUUID();
    const accessToken = `agat_a2a_${createToken(32)}`;
    const timestamp = nowIso();
    try {
      this.db.prepare(`
        INSERT INTO a2a_endpoints(
          id, project_id, agent_id, name, description, version, skill_id,
          skill_name, skill_description, tags_json, examples_json, input_modes_json,
          output_modes_json, knowledge_collection_ids_json, approval_required,
          streaming_enabled, push_notifications_enabled, file_artifacts_enabled,
          enabled, priority, max_input_characters, max_active_tasks, max_file_bytes,
          max_files, token_hash, token_suffix,
          token_rotated_at, created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        id,
        project,
        normalized.agentId,
        normalized.name,
        normalized.description,
        normalized.version,
        normalized.skillId,
        normalized.skillName,
        normalized.skillDescription,
        JSON.stringify(normalized.tags),
        JSON.stringify(normalized.examples),
        JSON.stringify(normalized.inputModes),
        JSON.stringify(normalized.outputModes),
        JSON.stringify(collectionIds),
        normalized.approvalRequired ? 1 : 0,
        normalized.streamingEnabled ? 1 : 0,
        normalized.pushNotificationsEnabled ? 1 : 0,
        normalized.fileArtifactsEnabled ? 1 : 0,
        normalized.enabled ? 1 : 0,
        normalized.priority,
        normalized.maxInputCharacters,
        normalized.maxActiveTasks,
        normalized.maxFileBytes,
        normalized.maxFiles,
        hashToken(accessToken),
        accessToken.slice(-6),
        timestamp,
        actor.slice(0, 200),
        timestamp,
        timestamp,
      );
    } catch (error) {
      if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
        throw new Error("Для этого агента или skill ID уже существует A2A endpoint в проекте");
      }
      throw error;
    }
    this.addEvent(null, null, null, "info", "a2a.endpoint.created", `Опубликован A2A endpoint «${normalized.name}»`, {
      projectId: project,
      endpointId: id,
      agentId: normalized.agentId,
      protocolVersion: A2A_PROTOCOL_VERSION,
      inputModes: normalized.inputModes,
      outputModes: normalized.outputModes,
      capabilities: {
        streaming: normalized.streamingEnabled,
        pushNotifications: normalized.pushNotificationsEnabled,
        fileArtifacts: normalized.fileArtifactsEnabled,
      },
      knowledgeCollectionIds: collectionIds,
      actor: actor.slice(0, 200),
    });
    return {
      endpoint: this.listA2AEndpoints(project).find((endpoint) => endpoint.id === id)!,
      accessToken,
    };
  }

  updateA2AEndpoint(
    id: string,
    input: UpdateA2AEndpointInput,
    projectId = "default",
    actor = "system",
  ): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const current = this.getA2AEndpointConnection(id, project);
    if (!current) return null;
    if (input.agentId !== undefined && input.agentId !== current.agentId) {
      throw new Error("Привязку опубликованного endpoint к агенту менять нельзя; создайте новый endpoint");
    }
    const normalized = normalizeA2AEndpointInput({ ...input, agentId: current.agentId }, current);
    const collectionIds = this.requireKnowledgeCollectionIds(project, normalized.knowledgeCollectionIds);
    const timestamp = nowIso();
    try {
      this.db.prepare(`
        UPDATE a2a_endpoints SET
          name = ?, description = ?, version = ?, skill_id = ?, skill_name = ?,
          skill_description = ?, tags_json = ?, examples_json = ?, input_modes_json = ?,
          output_modes_json = ?, knowledge_collection_ids_json = ?, approval_required = ?,
          streaming_enabled = ?, push_notifications_enabled = ?, file_artifacts_enabled = ?,
          enabled = ?, priority = ?, max_input_characters = ?, max_active_tasks = ?,
          max_file_bytes = ?, max_files = ?, updated_at = ?
        WHERE id = ? AND project_id = ?
      `).run(
        normalized.name,
        normalized.description,
        normalized.version,
        normalized.skillId,
        normalized.skillName,
        normalized.skillDescription,
        JSON.stringify(normalized.tags),
        JSON.stringify(normalized.examples),
        JSON.stringify(normalized.inputModes),
        JSON.stringify(normalized.outputModes),
        JSON.stringify(collectionIds),
        normalized.approvalRequired ? 1 : 0,
        normalized.streamingEnabled ? 1 : 0,
        normalized.pushNotificationsEnabled ? 1 : 0,
        normalized.fileArtifactsEnabled ? 1 : 0,
        normalized.enabled ? 1 : 0,
        normalized.priority,
        normalized.maxInputCharacters,
        normalized.maxActiveTasks,
        normalized.maxFileBytes,
        normalized.maxFiles,
        timestamp,
        id,
        project,
      );
    } catch (error) {
      if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
        throw new Error("A2A endpoint с таким skill ID уже существует в проекте");
      }
      throw error;
    }
    this.addEvent(null, null, null, "info", "a2a.endpoint.updated", `Обновлён A2A endpoint «${normalized.name}»`, {
      projectId: project,
      endpointId: id,
      enabled: normalized.enabled,
      actor: actor.slice(0, 200),
    });
    return this.listA2AEndpoints(project).find((endpoint) => endpoint.id === id) ?? null;
  }

  rotateA2AEndpointToken(
    id: string,
    projectId = "default",
    actor = "system",
  ): { endpoint: Record<string, unknown>; accessToken: string } | null {
    const project = this.requireProject(projectId);
    const current = this.getA2AEndpointConnection(id, project);
    if (!current) return null;
    const accessToken = `agat_a2a_${createToken(32)}`;
    const timestamp = nowIso();
    this.db.prepare(`
      UPDATE a2a_endpoints SET token_hash = ?, token_suffix = ?, token_rotated_at = ?, updated_at = ?
      WHERE id = ? AND project_id = ?
    `).run(hashToken(accessToken), accessToken.slice(-6), timestamp, timestamp, id, project);
    this.addEvent(null, null, null, "warn", "a2a.endpoint.token_rotated", `Токен A2A endpoint «${current.name}» заменён`, {
      projectId: project,
      endpointId: id,
      actor: actor.slice(0, 200),
    });
    return {
      endpoint: this.listA2AEndpoints(project).find((endpoint) => endpoint.id === id)!,
      accessToken,
    };
  }

  deleteA2AEndpoint(id: string, projectId = "default", actor = "system"): boolean {
    const project = this.requireProject(projectId);
    const current = this.getA2AEndpointConnection(id, project);
    if (!current) return false;
    const active = Number((this.db.prepare(`
      SELECT COUNT(*) AS count FROM a2a_tasks t JOIN runs r ON r.id = t.run_id
      WHERE t.endpoint_id = ? AND r.status IN ('queued', 'running', 'waiting_approval')
    `).get(id) as Row).count);
    if (active > 0) throw new Error("Нельзя удалить A2A endpoint с активными tasks; сначала отключите или отмените их");
    this.db.prepare("DELETE FROM a2a_endpoints WHERE id = ? AND project_id = ?").run(id, project);
    this.addEvent(null, null, null, "warn", "a2a.endpoint.deleted", `A2A endpoint «${current.name}» удалён`, {
      projectId: project,
      endpointId: id,
      actor: actor.slice(0, 200),
    });
    return true;
  }

  createA2ATask(
    endpoint: A2AEndpointConnection,
    request: A2ANormalizedMessage,
    traceparent: string | null,
  ): Record<string, unknown> {
    const existing = this.db.prepare(`
      SELECT id, request_sha256 FROM a2a_tasks
      WHERE endpoint_id = ? AND client_message_id = ?
    `).get(endpoint.id, request.message.messageId) as Row | undefined;
    if (existing) {
      if (existing.request_sha256 !== request.requestSha256) {
        throw new Error("A2A messageId уже использован с другим содержимым");
      }
      return this.getA2ATask(endpoint.id, String(existing.id), request.historyLength, true)!;
    }
    const activeTasks = Number((this.db.prepare(`
      SELECT COUNT(*) AS count FROM a2a_tasks t JOIN runs r ON r.id = t.run_id
      WHERE t.endpoint_id = ? AND r.status IN ('queued', 'running', 'waiting_approval')
    `).get(endpoint.id) as Row).count);
    if (activeTasks >= endpoint.maxActiveTasks) {
      throw new Error(`Достигнут лимит активных A2A tasks: ${endpoint.maxActiveTasks}`);
    }
    const created = this.createRun({
      name: `A2A · ${endpoint.name} · ${request.message.messageId}`.slice(0, 120),
      input: request.input,
      executionMode: "sequential",
      priority: endpoint.priority,
      approvalRequired: endpoint.approvalRequired,
      agentIds: [endpoint.agentId],
      resultDestination: "history",
      knowledgeCollectionIds: endpoint.knowledgeCollectionIds,
    }, endpoint.projectId, traceparent);
    if (request.files.length) {
      const run = this.db.prepare("SELECT id, artifact_path FROM runs WHERE id = ?").get(created.id) as Row;
      try {
        for (const file of request.files) {
          const storageName = `${randomUUID().slice(0, 8)}-${safeArtifactName(file.filename)}`;
          this.persistArtifact(
            { id: null, run_id: created.id, artifact_path: run.artifact_path ?? "" },
            file.filename,
            "a2a_input",
            file.mediaType,
            file.bytes,
            path.posix.join("a2a-input", storageName),
          );
        }
      } catch (error) {
        const failedAt = nowIso();
        this.db.prepare("UPDATE runs SET status = 'failed', completed_at = ?, updated_at = ? WHERE id = ?")
          .run(failedAt, failedAt, created.id);
        throw error;
      }
    }
    const taskId = randomUUID();
    const timestamp = nowIso();
    this.db.prepare(`
      INSERT INTO a2a_tasks(
        id, endpoint_id, project_id, run_id, context_id, client_message_id,
        request_sha256, message_blob, external_traceparent, protocol_version,
        adapter_version, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      taskId,
      endpoint.id,
      endpoint.projectId,
      created.id,
      request.contextId,
      request.message.messageId,
      request.requestSha256,
      encryptJson(request.message, this.credentialsKey),
      traceparent,
      A2A_PROTOCOL_VERSION,
      A2A_ADAPTER_VERSION,
      timestamp,
      timestamp,
    );
    this.addEvent(created.id, null, null, "info", "a2a.task.accepted", "A2A task принят в локальный scheduler", {
      endpointId: endpoint.id,
      taskId,
      contextId: request.contextId,
      clientMessageId: request.message.messageId,
      protocolVersion: A2A_PROTOCOL_VERSION,
      externalTraceparent: traceparent,
      inputModes: request.message.parts.map((part) => part.mediaType ?? "unknown"),
      fileCount: request.files.length,
    });
    return this.getA2ATask(endpoint.id, taskId, request.historyLength, true)!;
  }

  private a2aTaskRow(endpointId: string, taskId: string): Row | null {
    const row = this.db.prepare(`
      SELECT t.*, e.name AS endpoint_name, r.status AS run_status,
        e.output_modes_json, e.file_artifacts_enabled, e.max_file_bytes, e.max_files,
        r.trace_id, r.root_span_id, r.updated_at AS run_updated_at,
        r.completed_at AS run_completed_at,
        (SELECT output FROM stages WHERE run_id = r.id ORDER BY position DESC LIMIT 1) AS final_output
      FROM a2a_tasks t
      JOIN a2a_endpoints e ON e.id = t.endpoint_id
      JOIN runs r ON r.id = t.run_id
      WHERE t.endpoint_id = ? AND t.id = ?
    `).get(endpointId, taskId) as Row | undefined;
    if (!row) return null;
    if (row.updated_at !== row.run_updated_at) {
      const runUpdatedAt = String(row.run_updated_at);
      this.db.prepare("UPDATE a2a_tasks SET updated_at = ? WHERE id = ?").run(runUpdatedAt, taskId);
      row.updated_at = runUpdatedAt;
    }
    return row;
  }

  private a2aStatusMessage(taskId: string, contextId: string, state: A2ATaskState): A2AMessage | null {
    let text = "";
    if (state === "TASK_STATE_AUTH_REQUIRED") text = "Task ожидает решения оператора АГАТ";
    else if (state === "TASK_STATE_FAILED") text = "Локальное выполнение task завершилось ошибкой";
    else if (state === "TASK_STATE_CANCELED") text = "Task отменён";
    if (!text) return null;
    return {
      messageId: `${taskId}-status-${state.toLowerCase()}`,
      contextId,
      taskId,
      role: "ROLE_AGENT",
      parts: [{ text, mediaType: "text/plain" }],
    };
  }

  private a2aTaskDto(row: Row, historyLength: number, includeArtifacts: boolean): Record<string, unknown> {
    const state = runStatusToA2AState(String(row.run_status));
    const contextId = String(row.context_id);
    const taskId = String(row.id);
    const statusMessage = this.a2aStatusMessage(taskId, contextId, state);
    const status = {
      state,
      ...(statusMessage ? { message: statusMessage } : {}),
      timestamp: String(row.run_updated_at),
    };
    const result: Record<string, unknown> = {
      id: taskId,
      contextId,
      status,
      metadata: {
        "io.agat.a2a": {
          protocolVersion: String(row.protocol_version),
          adapterVersion: String(row.adapter_version),
          traceId: String(row.trace_id),
          traceparent: `00-${String(row.trace_id)}-${String(row.root_span_id)}-01`,
        },
      },
    };
    if (includeArtifacts) {
      const artifacts: Array<Record<string, unknown>> = [];
      if (state === "TASK_STATE_COMPLETED") {
        const output = typeof row.final_output === "string" ? row.final_output : "";
        const outputModes = parseJson<string[]>(row.output_modes_json, ["text/plain"]);
        if (outputModes.includes("text/plain")) {
          artifacts.push({
            artifactId: `${taskId}-result`,
            name: "result.txt",
            description: "Финальный результат опубликованного агента АГАТ",
            parts: [{ text: output, mediaType: "text/plain" }],
            metadata: {
              sha256: sha256Text(output),
              bytes: Buffer.byteLength(output),
            },
          });
        }
        if (Number(row.file_artifacts_enabled) === 1) {
          const maxFiles = Math.max(1, Math.min(8, Number(row.max_files)));
          const maxFileBytes = Math.max(1_024, Math.min(2_000_000, Number(row.max_file_bytes)));
          const candidates = this.db.prepare(`
            SELECT * FROM artifacts
            WHERE run_id = ? AND kind = 'agent_artifact'
            ORDER BY created_at ASC, name ASC LIMIT ?
          `).all(String(row.run_id), maxFiles) as Row[];
          for (const candidate of candidates) {
            const mediaType = String(candidate.media_type).split(";", 1)[0]!.trim().toLowerCase();
            if (!outputModes.includes(mediaType) || Number(candidate.size_bytes) > maxFileBytes) continue;
            const download = this.getArtifactDownload(String(candidate.id), String(row.project_id));
            if (!download) continue;
            const bytes = fs.readFileSync(download.filePath);
            const part = mediaType.startsWith("text/")
              ? { text: bytes.toString("utf8"), filename: String(candidate.name), mediaType }
              : { raw: bytes.toString("base64"), filename: String(candidate.name), mediaType };
            artifacts.push({
              artifactId: String(candidate.id),
              name: String(candidate.name),
              description: "Bounded agent artifact АГАТ",
              parts: [part],
              metadata: {
                sha256: String(candidate.sha256),
                bytes: Number(candidate.size_bytes),
              },
            });
          }
        }
      }
      result.artifacts = artifacts;
    }
    if (historyLength > 0) {
      const message = decryptJson(row.message_blob, this.credentialsKey) as A2AMessage;
      result.history = [message].slice(-historyLength);
    }
    return result;
  }

  getA2ATask(
    endpointId: string,
    taskId: string,
    historyLength = 1,
    includeArtifacts = true,
  ): Record<string, unknown> | null {
    const row = this.a2aTaskRow(endpointId, taskId);
    return row ? this.a2aTaskDto(row, Math.max(0, Math.min(100, Math.trunc(historyLength))), includeArtifacts) : null;
  }

  listA2ATasks(endpointId: string, input: {
    contextId?: string;
    status?: A2ATaskState;
    pageSize?: number;
    pageToken?: string;
    historyLength?: number;
    statusTimestampAfter?: string;
    includeArtifacts?: boolean;
  }): Record<string, unknown> {
    const pageSize = Math.max(1, Math.min(100, Math.trunc(input.pageSize ?? 50)));
    const conditions = ["t.endpoint_id = ?"];
    const params: SqlScalar[] = [endpointId];
    if (input.contextId) {
      conditions.push("t.context_id = ?");
      params.push(input.contextId);
    }
    const statusMap: Partial<Record<A2ATaskState, string>> = {
      TASK_STATE_SUBMITTED: "queued",
      TASK_STATE_WORKING: "running",
      TASK_STATE_AUTH_REQUIRED: "waiting_approval",
      TASK_STATE_COMPLETED: "completed",
      TASK_STATE_FAILED: "failed",
      TASK_STATE_CANCELED: "cancelled",
    };
    if (input.status) {
      const runStatus = statusMap[input.status];
      if (!runStatus) throw new Error("Фильтр A2A status не поддерживается");
      conditions.push("r.status = ?");
      params.push(runStatus);
    }
    if (input.statusTimestampAfter) {
      conditions.push("r.updated_at >= ?");
      params.push(input.statusTimestampAfter);
    }
    if (input.pageToken) {
      let cursor: { updatedAt?: unknown; id?: unknown };
      try {
        cursor = JSON.parse(Buffer.from(input.pageToken, "base64url").toString("utf8")) as { updatedAt?: unknown; id?: unknown };
      } catch {
        throw new Error("Некорректный A2A pageToken");
      }
      if (typeof cursor.updatedAt !== "string" || typeof cursor.id !== "string") throw new Error("Некорректный A2A pageToken");
      conditions.push("(r.updated_at < ? OR (r.updated_at = ? AND t.id < ?))");
      params.push(cursor.updatedAt, cursor.updatedAt, cursor.id);
    }
    const where = conditions.join(" AND ");
    const rows = this.db.prepare(`
      SELECT t.id, r.updated_at AS run_updated_at
      FROM a2a_tasks t JOIN runs r ON r.id = t.run_id
      WHERE ${where}
      ORDER BY r.updated_at DESC, t.id DESC LIMIT ?
    `).all(...params, pageSize + 1) as Row[];
    const visibleRows = rows.slice(0, pageSize);
    const tasks = visibleRows.map((row) => this.getA2ATask(
      endpointId,
      String(row.id),
      input.historyLength ?? 0,
      input.includeArtifacts ?? false,
    )!);
    const last = visibleRows.at(-1);
    const nextPageToken = rows.length > pageSize && last
      ? Buffer.from(JSON.stringify({ updatedAt: last.run_updated_at, id: last.id })).toString("base64url")
      : "";
    const totalConditions = conditions.filter((condition) => !condition.startsWith("(r.updated_at <"));
    const totalParams = input.pageToken ? params.slice(0, -3) : params;
    const totalSize = Number((this.db.prepare(`
      SELECT COUNT(*) AS count FROM a2a_tasks t JOIN runs r ON r.id = t.run_id
      WHERE ${totalConditions.join(" AND ")}
    `).get(...totalParams) as Row).count);
    return { tasks, nextPageToken, pageSize, totalSize };
  }

  cancelA2ATask(endpointId: string, taskId: string): { kind: "not_found" | "not_cancelable" | "cancelled"; task?: Record<string, unknown> } {
    const row = this.a2aTaskRow(endpointId, taskId);
    if (!row) return { kind: "not_found" };
    if (row.run_status === "cancelled") return { kind: "cancelled", task: this.a2aTaskDto(row, 1, true) };
    if (!["queued", "running", "waiting_approval"].includes(String(row.run_status))) return { kind: "not_cancelable" };
    this.cancelRun(String(row.run_id), String(row.project_id));
    const timestamp = nowIso();
    this.db.prepare("UPDATE a2a_tasks SET canceled_at = ?, updated_at = ? WHERE id = ?")
      .run(timestamp, timestamp, taskId);
    const updated = this.a2aTaskRow(endpointId, taskId)!;
    return { kind: "cancelled", task: this.a2aTaskDto(updated, 1, true) };
  }

  listRecentA2ATasks(projectId = "default", limit = 100): Array<Record<string, unknown>> {
    const project = normalizeProjectId(projectId);
    return (this.db.prepare(`
      SELECT t.id, t.endpoint_id, t.run_id, t.context_id, t.client_message_id,
        t.external_traceparent, t.protocol_version, t.adapter_version,
        t.created_at, r.updated_at, r.status AS run_status, r.trace_id,
        e.name AS endpoint_name, e.agent_id
      FROM a2a_tasks t
      JOIN runs r ON r.id = t.run_id
      JOIN a2a_endpoints e ON e.id = t.endpoint_id
      WHERE t.project_id = ?
      ORDER BY r.updated_at DESC, t.id DESC LIMIT ?
    `).all(project, Math.max(1, Math.min(500, Math.trunc(limit)))) as Row[]).map((row) => ({
      id: row.id,
      endpointId: row.endpoint_id,
      endpointName: row.endpoint_name,
      agentId: row.agent_id,
      runId: row.run_id,
      contextId: row.context_id,
      clientMessageId: row.client_message_id,
      state: runStatusToA2AState(String(row.run_status)),
      traceId: row.trace_id,
      hasExternalTraceparent: typeof row.external_traceparent === "string" && Boolean(row.external_traceparent),
      protocolVersion: row.protocol_version,
      adapterVersion: row.adapter_version,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
    }));
  }

  private a2aPushConfigDto(row: Row): Record<string, unknown> {
    const config = decryptJson(row.callback_blob, this.credentialsKey) as A2ANormalizedPushConfig;
    return {
      id: row.id,
      taskId: row.task_id,
      url: config.url,
      ...(config.token ? { token: config.token } : {}),
      ...(config.authentication ? {
        authentication: {
          scheme: config.authentication.scheme,
          credentialsSuffix: row.auth_suffix,
        },
      } : {}),
      createdAt: row.created_at,
      updatedAt: row.updated_at,
    };
  }

  createA2APushConfig(
    endpointId: string,
    taskId: string,
    config: A2ANormalizedPushConfig,
  ): Record<string, unknown> {
    const task = this.db.prepare(`
      SELECT t.project_id, t.run_id, e.push_notifications_enabled
      FROM a2a_tasks t JOIN a2a_endpoints e ON e.id = t.endpoint_id
      WHERE t.id = ? AND t.endpoint_id = ?
    `).get(taskId, endpointId) as Row | undefined;
    if (!task) throw new Error("A2A task не найден");
    if (Number(task.push_notifications_enabled) !== 1) throw new Error("Push notifications выключены policy endpoint");
    const timestamp = nowIso();
    const origin = new URL(config.url).origin;
    const authSuffix = config.authentication?.credentials.slice(-6) ?? "";
    try {
      this.db.prepare(`
        INSERT INTO a2a_push_configs(
          id, task_id, endpoint_id, project_id, callback_blob, callback_origin,
          auth_scheme, auth_suffix, last_state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
      `).run(
        config.id,
        taskId,
        endpointId,
        String(task.project_id),
        encryptJson(config, this.credentialsKey),
        origin,
        config.authentication?.scheme ?? null,
        authSuffix,
        timestamp,
        timestamp,
      );
    } catch (error) {
      if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
        const existing = this.db.prepare("SELECT * FROM a2a_push_configs WHERE id = ? AND task_id = ?")
          .get(config.id, taskId) as Row | undefined;
        if (existing) return this.a2aPushConfigDto(existing);
      }
      throw error;
    }
    this.addEvent(String(task.run_id), null, null, "info", "a2a.push.configured", "Настроен A2A push callback", {
      endpointId,
      taskId,
      configId: config.id,
      callbackOrigin: origin,
      authScheme: config.authentication?.scheme ?? null,
    });
    const row = this.db.prepare("SELECT * FROM a2a_push_configs WHERE id = ?").get(config.id) as Row;
    return this.a2aPushConfigDto(row);
  }

  getA2APushConfig(endpointId: string, taskId: string, configId: string): Record<string, unknown> | null {
    const row = this.db.prepare(`
      SELECT * FROM a2a_push_configs WHERE endpoint_id = ? AND task_id = ? AND id = ?
    `).get(endpointId, taskId, configId) as Row | undefined;
    return row ? this.a2aPushConfigDto(row) : null;
  }

  listA2APushConfigs(endpointId: string, taskId: string): Record<string, unknown> {
    const exists = this.db.prepare("SELECT id FROM a2a_tasks WHERE endpoint_id = ? AND id = ?")
      .get(endpointId, taskId);
    if (!exists) throw new Error("A2A task не найден");
    const configs = (this.db.prepare(`
      SELECT * FROM a2a_push_configs WHERE endpoint_id = ? AND task_id = ? ORDER BY created_at, id
    `).all(endpointId, taskId) as Row[]).map((row) => this.a2aPushConfigDto(row));
    return { configs, nextPageToken: "" };
  }

  deleteA2APushConfig(endpointId: string, taskId: string, configId: string): boolean {
    const row = this.db.prepare(`
      SELECT pc.*, t.run_id FROM a2a_push_configs pc
      JOIN a2a_tasks t ON t.id = pc.task_id
      WHERE pc.endpoint_id = ? AND pc.task_id = ? AND pc.id = ?
    `).get(endpointId, taskId, configId) as Row | undefined;
    if (!row) return false;
    this.db.prepare("DELETE FROM a2a_push_configs WHERE id = ?").run(configId);
    this.addEvent(String(row.run_id), null, null, "info", "a2a.push.deleted", "Удалён A2A push callback", {
      endpointId,
      taskId,
      configId,
      callbackOrigin: row.callback_origin,
    });
    return true;
  }

  private collectA2APushUpdates(): void {
    const configs = this.db.prepare(`
      SELECT pc.*, t.context_id, t.run_id, r.status AS run_status, r.updated_at AS run_updated_at
      FROM a2a_push_configs pc
      JOIN a2a_tasks t ON t.id = pc.task_id
      JOIN runs r ON r.id = t.run_id
      JOIN a2a_endpoints e ON e.id = pc.endpoint_id
      WHERE pc.last_state <> r.status AND e.enabled = 1 AND e.push_notifications_enabled = 1
      ORDER BY r.updated_at, pc.created_at
    `).all() as Row[];
    const insert = this.db.prepare(`
      INSERT OR IGNORE INTO a2a_push_deliveries(
        id, config_id, task_id, project_id, event_kind, task_state,
        payload_blob, status, attempts, next_attempt_at, created_at, updated_at
      ) VALUES (?, ?, ?, ?, 'status', ?, ?, 'pending', 0, ?, ?, ?)
    `);
    const updateConfig = this.db.prepare("UPDATE a2a_push_configs SET last_state = ?, updated_at = ? WHERE id = ?");
    for (const config of configs) {
      const taskState = runStatusToA2AState(String(config.run_status));
      const timestamp = nowIso();
      const task = this.getA2ATask(String(config.endpoint_id), String(config.task_id), 0, false);
      const status = task && typeof task.status === "object" && task.status
        ? task.status as Record<string, unknown>
        : { state: taskState, timestamp: config.run_updated_at };
      const payload = {
        statusUpdate: {
          taskId: String(config.task_id),
          contextId: String(config.context_id),
          status,
        },
      };
      insert.run(
        randomUUID(),
        String(config.id),
        String(config.task_id),
        String(config.project_id),
        taskState,
        encryptJson(payload, this.credentialsKey),
        timestamp,
        timestamp,
        timestamp,
      );
      updateConfig.run(String(config.run_status), timestamp, String(config.id));
    }
  }

  claimA2APushDeliveries(limit = 10): Array<{
    deliveryId: string;
    taskId: string;
    url: string;
    authorization: string | null;
    payload: Record<string, unknown>;
    attempts: number;
  }> {
    this.collectA2APushUpdates();
    const timestamp = nowIso();
    const rows = this.db.prepare(`
      SELECT d.*, pc.callback_blob FROM a2a_push_deliveries d
      JOIN a2a_push_configs pc ON pc.id = d.config_id
      JOIN a2a_endpoints e ON e.id = pc.endpoint_id
      WHERE d.status = 'pending' AND d.next_attempt_at <= ?
        AND e.enabled = 1 AND e.push_notifications_enabled = 1
      ORDER BY d.next_attempt_at, d.created_at LIMIT ?
    `).all(timestamp, Math.max(1, Math.min(50, Math.trunc(limit)))) as Row[];
    const claim = this.db.prepare(`
      UPDATE a2a_push_deliveries SET status = 'delivering', updated_at = ?
      WHERE id = ? AND status = 'pending'
    `);
    const result: Array<{
      deliveryId: string;
      taskId: string;
      url: string;
      authorization: string | null;
      payload: Record<string, unknown>;
      attempts: number;
    }> = [];
    for (const row of rows) {
      if (Number(claim.run(timestamp, String(row.id)).changes) !== 1) continue;
      const config = decryptJson(row.callback_blob, this.credentialsKey) as A2ANormalizedPushConfig;
      result.push({
        deliveryId: String(row.id),
        taskId: String(row.task_id),
        url: config.url,
        authorization: config.authentication ? `${config.authentication.scheme} ${config.authentication.credentials}` : null,
        payload: decryptJson(row.payload_blob, this.credentialsKey) as Record<string, unknown>,
        attempts: Number(row.attempts),
      });
    }
    return result;
  }

  completeA2APushDelivery(deliveryId: string, error: string | null): void {
    const row = this.db.prepare(`
      SELECT d.*, t.run_id FROM a2a_push_deliveries d
      JOIN a2a_tasks t ON t.id = d.task_id WHERE d.id = ? AND d.status = 'delivering'
    `).get(deliveryId) as Row | undefined;
    if (!row) return;
    const timestamp = nowIso();
    if (!error) {
      this.db.prepare(`
        UPDATE a2a_push_deliveries SET status = 'delivered', delivered_at = ?, updated_at = ? WHERE id = ?
      `).run(timestamp, timestamp, deliveryId);
      this.addEvent(String(row.run_id), null, null, "info", "a2a.push.delivered", "A2A push update доставлен", {
        deliveryId,
        taskId: row.task_id,
        taskState: row.task_state,
      });
      return;
    }
    const attempts = Number(row.attempts) + 1;
    const terminal = attempts >= 5;
    const nextAttemptAt = new Date(Date.now() + Math.min(300, 2 ** attempts) * 1_000).toISOString();
    this.db.prepare(`
      UPDATE a2a_push_deliveries SET status = ?, attempts = ?, next_attempt_at = ?,
        last_error = ?, updated_at = ? WHERE id = ?
    `).run(terminal ? "failed" : "pending", attempts, nextAttemptAt, error.slice(0, 1_000), timestamp, deliveryId);
    if (terminal) {
      this.addEvent(String(row.run_id), null, null, "error", "a2a.push.failed", "A2A push update не доставлен после retries", {
        deliveryId,
        taskId: row.task_id,
        attempts,
      });
    }
  }

  private a2aRemoteConnection(row: Row): A2ARemoteConnection {
    const capabilities = parseJson<A2ARemoteConnection["capabilities"]>(row.capabilities_json, {
      streaming: false,
      pushNotifications: false,
    });
    const authMode = String(row.auth_mode) as A2ARemoteConnection["authMode"];
    let tokenEndpointOrigin: string | null = null;
    if (authMode === "oauth2_token_exchange") {
      const auth = decryptJson(row.auth_blob, this.credentialsKey) as A2ARemoteAuthMaterial;
      if (typeof auth.tokenUrl === "string") {
        try {
          tokenEndpointOrigin = new URL(auth.tokenUrl).origin;
        } catch {
          tokenEndpointOrigin = null;
        }
      }
    }
    return {
      id: String(row.id),
      projectId: String(row.project_id),
      name: String(row.name),
      description: String(row.description),
      agentCardUrl: String(row.agent_card_url),
      interfaceUrl: String(row.interface_url),
      protocolVersion: String(row.protocol_version),
      tenant: typeof row.tenant === "string" && row.tenant ? row.tenant : null,
      skillId: String(row.skill_id),
      skillName: String(row.skill_name),
      inputModes: parseJson<string[]>(row.input_modes_json, ["text/plain"]),
      outputModes: parseJson<string[]>(row.output_modes_json, ["text/plain"]),
      capabilities,
      authMode,
      authSuffix: String(row.auth_suffix),
      tokenEndpointOrigin,
      enabled: Number(row.enabled) === 1,
      allowFileArtifacts: Number(row.allow_file_artifacts) === 1,
      maxResponseBytes: Number(row.max_response_bytes),
      createdAt: String(row.created_at),
      updatedAt: String(row.updated_at),
    };
  }

  private normalizeA2ARemoteAuth(input: CreateA2ARemoteInput["auth"]): A2ARemoteAuthMaterial {
    if (!input || !["none", "bearer", "oauth2_token_exchange"].includes(input.mode)) {
      throw new Error("Неизвестный auth mode outbound A2A peer");
    }
    if (input.mode === "none") return { mode: "none" };
    if (input.mode === "bearer") {
      const bearerToken = requiredAgentText(input.bearerToken, "Bearer token outbound A2A peer", 8_000);
      if (/[\r\n]/.test(bearerToken)) throw new Error("Bearer token содержит управляющие символы");
      return { mode: "bearer", bearerToken };
    }
    const tokenUrl = requiredAgentText(input.tokenUrl, "OAuth token URL", 2_048);
    let parsed: URL;
    try {
      parsed = new URL(tokenUrl);
    } catch {
      throw new Error("OAuth token URL должен быть абсолютным HTTP(S) URL");
    }
    if (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && ["127.0.0.1", "::1", "[::1]", "localhost"].includes(parsed.hostname))) {
      throw new Error("OAuth token URL требует HTTPS; HTTP разрешён только для loopback");
    }
    if (parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error("OAuth token URL не должен содержать credentials, query или fragment");
    const scopes = Array.isArray(input.scopes)
      ? [...new Set(input.scopes.map((scope) => requiredAgentText(scope, "OAuth scope", 200)))]
      : [];
    if (scopes.length > 32 || scopes.some((scope) => /\s/.test(scope))) throw new Error("OAuth scopes содержат недопустимое значение");
    return {
      mode: "oauth2_token_exchange",
      tokenUrl: parsed.toString(),
      audience: input.audience ? requiredAgentText(input.audience, "OAuth audience", 500) : undefined,
      scopes,
      clientId: requiredAgentText(input.clientId, "OAuth client ID", 500),
      clientSecret: requiredAgentText(input.clientSecret, "OAuth client secret", 8_000),
    };
  }

  getA2ARemoteConnection(id: string, projectId?: string, enabledOnly = false): A2ARemoteConnection | null {
    const conditions = ["id = ?"];
    const params: SqlScalar[] = [id];
    if (projectId !== undefined) {
      conditions.push("project_id = ?");
      params.push(normalizeProjectId(projectId));
    }
    if (enabledOnly) conditions.push("enabled = 1");
    const row = this.db.prepare(`SELECT * FROM a2a_remotes WHERE ${conditions.join(" AND ")}`)
      .get(...params) as Row | undefined;
    return row ? this.a2aRemoteConnection(row) : null;
  }

  getA2ARemoteAuth(id: string, projectId: string): A2ARemoteAuthMaterial | null {
    const row = this.db.prepare("SELECT auth_blob FROM a2a_remotes WHERE id = ? AND project_id = ?")
      .get(id, normalizeProjectId(projectId)) as Row | undefined;
    return row ? decryptJson(row.auth_blob, this.credentialsKey) as A2ARemoteAuthMaterial : null;
  }

  listA2ARemotes(projectId = "default"): Array<Record<string, unknown>> {
    const project = normalizeProjectId(projectId);
    return (this.db.prepare(`
      SELECT r.*,
        (SELECT COUNT(*) FROM a2a_outbound_tasks t WHERE t.remote_id = r.id) AS total_tasks,
        (SELECT MAX(t.updated_at) FROM a2a_outbound_tasks t WHERE t.remote_id = r.id) AS last_task_at
      FROM a2a_remotes r WHERE r.project_id = ? ORDER BY r.updated_at DESC, r.name COLLATE NOCASE
    `).all(project) as Row[]).map((row) => ({
      ...this.a2aRemoteConnection(row),
      totalTasks: Number(row.total_tasks),
      lastTaskAt: typeof row.last_task_at === "string" ? row.last_task_at : null,
    }));
  }

  createA2ARemote(
    input: CreateA2ARemoteInput,
    discovered: {
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
      capabilities: A2ARemoteConnection["capabilities"];
      cardSha256: string;
    },
    projectId = "default",
    actor = "system",
  ): Record<string, unknown> {
    const project = this.requireProject(projectId);
    const auth = this.normalizeA2ARemoteAuth(input.auth);
    const id = randomUUID();
    const timestamp = nowIso();
    const name = input.name ? requiredAgentText(input.name, "Название outbound A2A peer", 120) : discovered.name;
    const authSecret = auth.mode === "bearer" ? auth.bearerToken! : auth.mode === "oauth2_token_exchange" ? auth.clientSecret! : "";
    try {
      this.db.prepare(`
        INSERT INTO a2a_remotes(
          id, project_id, name, description, agent_card_url, interface_url,
          protocol_version, tenant, skill_id, skill_name, input_modes_json,
          output_modes_json, capabilities_json, card_sha256, auth_mode, auth_blob,
          auth_suffix, enabled, allow_file_artifacts, max_response_bytes,
          created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        id,
        project,
        name,
        discovered.description,
        discovered.agentCardUrl,
        discovered.interfaceUrl,
        discovered.protocolVersion,
        discovered.tenant,
        discovered.skillId,
        discovered.skillName,
        JSON.stringify(discovered.inputModes),
        JSON.stringify(discovered.outputModes),
        JSON.stringify(discovered.capabilities),
        discovered.cardSha256,
        auth.mode,
        encryptJson(auth, this.credentialsKey),
        authSecret.slice(-6),
        input.enabled === false ? 0 : 1,
        input.allowFileArtifacts === true ? 1 : 0,
        Math.max(65_536, Math.min(4_194_304, Math.trunc(input.maxResponseBytes ?? 1_048_576))),
        actor.slice(0, 200),
        timestamp,
        timestamp,
      );
    } catch (error) {
      if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
        throw new Error("Outbound A2A peer с этим Agent Card и skill уже существует в проекте");
      }
      throw error;
    }
    this.addEvent(null, null, null, "info", "a2a.remote.created", `Добавлен outbound A2A peer «${name}»`, {
      projectId: project,
      remoteId: id,
      agentCardOrigin: new URL(discovered.agentCardUrl).origin,
      interfaceOrigin: new URL(discovered.interfaceUrl).origin,
      skillId: discovered.skillId,
      authMode: auth.mode,
      tokenEndpointOrigin: auth.mode === "oauth2_token_exchange" ? new URL(auth.tokenUrl!).origin : null,
      oauthAudience: auth.mode === "oauth2_token_exchange" ? auth.audience ?? null : null,
      oauthScopes: auth.mode === "oauth2_token_exchange" ? auth.scopes ?? [] : [],
      actor: actor.slice(0, 200),
    });
    return this.listA2ARemotes(project).find((remote) => remote.id === id)!;
  }

  updateA2ARemote(
    id: string,
    input: UpdateA2ARemoteInput,
    projectId = "default",
    actor = "system",
  ): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const current = this.getA2ARemoteConnection(id, project);
    if (!current) return null;
    const currentAuth = this.getA2ARemoteAuth(id, project)!;
    const auth = input.auth ? this.normalizeA2ARemoteAuth(input.auth) : currentAuth;
    const authSecret = auth.mode === "bearer" ? auth.bearerToken! : auth.mode === "oauth2_token_exchange" ? auth.clientSecret! : "";
    const timestamp = nowIso();
    this.db.prepare(`
      UPDATE a2a_remotes SET name = ?, auth_mode = ?, auth_blob = ?, auth_suffix = ?,
        enabled = ?, allow_file_artifacts = ?, max_response_bytes = ?, updated_at = ?
      WHERE id = ? AND project_id = ?
    `).run(
      input.name ? requiredAgentText(input.name, "Название outbound A2A peer", 120) : current.name,
      auth.mode,
      encryptJson(auth, this.credentialsKey),
      authSecret.slice(-6),
      (input.enabled ?? current.enabled) ? 1 : 0,
      (input.allowFileArtifacts ?? current.allowFileArtifacts) ? 1 : 0,
      Math.max(65_536, Math.min(4_194_304, Math.trunc(input.maxResponseBytes ?? current.maxResponseBytes))),
      timestamp,
      id,
      project,
    );
    this.addEvent(null, null, null, "info", "a2a.remote.updated", `Обновлён outbound A2A peer «${current.name}»`, {
      projectId: project,
      remoteId: id,
      authMode: auth.mode,
      tokenEndpointOrigin: auth.mode === "oauth2_token_exchange" ? new URL(auth.tokenUrl!).origin : null,
      oauthAudience: auth.mode === "oauth2_token_exchange" ? auth.audience ?? null : null,
      oauthScopes: auth.mode === "oauth2_token_exchange" ? auth.scopes ?? [] : [],
      actor: actor.slice(0, 200),
    });
    return this.listA2ARemotes(project).find((remote) => remote.id === id) ?? null;
  }

  deleteA2ARemote(id: string, projectId = "default", actor = "system"): boolean {
    const project = this.requireProject(projectId);
    const remote = this.getA2ARemoteConnection(id, project);
    if (!remote) return false;
    this.db.prepare("DELETE FROM a2a_remotes WHERE id = ? AND project_id = ?").run(id, project);
    this.addEvent(null, null, null, "warn", "a2a.remote.deleted", `Удалён outbound A2A peer «${remote.name}»`, {
      projectId: project,
      remoteId: id,
      actor: actor.slice(0, 200),
    });
    return true;
  }

  recordA2AOutboundTask(
    remote: A2ARemoteConnection,
    request: Record<string, unknown>,
    response: Record<string, unknown>,
    actor: { subject: string; display: string },
    delegated: boolean,
  ): Record<string, unknown> {
    const message = request.message && typeof request.message === "object" && !Array.isArray(request.message)
      ? request.message as Record<string, unknown>
      : {};
    const messageId = typeof message.messageId === "string" ? message.messageId : "";
    if (!messageId) throw new Error("Outbound A2A messageId обязателен");
    const task = response.task && typeof response.task === "object" && !Array.isArray(response.task)
      ? response.task as Record<string, unknown>
      : null;
    const status = task?.status && typeof task.status === "object" && !Array.isArray(task.status)
      ? task.status as Record<string, unknown>
      : {};
    const state = typeof status.state === "string" ? status.state : task ? "TASK_STATE_UNSPECIFIED" : "TASK_STATE_COMPLETED";
    const remoteTaskId = task && typeof task.id === "string" ? task.id : null;
    const contextId = task && typeof task.contextId === "string"
      ? task.contextId
      : typeof message.contextId === "string" ? message.contextId : randomUUID();
    const requestSha256 = createHash("sha256").update(JSON.stringify(request)).digest("hex");
    const existing = this.db.prepare("SELECT * FROM a2a_outbound_tasks WHERE remote_id = ? AND client_message_id = ?")
      .get(remote.id, messageId) as Row | undefined;
    if (existing) {
      if (existing.request_sha256 !== requestSha256) throw new Error("Outbound A2A messageId уже использован с другим содержимым");
      return this.a2aOutboundTaskDto(existing);
    }
    const id = randomUUID();
    const timestamp = nowIso();
    const traceId = randomUUID().replaceAll("-", "");
    this.db.prepare(`
      INSERT INTO a2a_outbound_tasks(
        id, remote_id, project_id, remote_task_id, context_id, client_message_id,
        state, request_sha256, request_blob, response_blob, trace_id,
        actor_subject, actor_display, delegated, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      id,
      remote.id,
      remote.projectId,
      remoteTaskId,
      contextId,
      messageId,
      state,
      requestSha256,
      encryptJson(request, this.credentialsKey),
      encryptJson(response, this.credentialsKey),
      traceId,
      actor.subject.slice(0, 500),
      actor.display.slice(0, 200),
      delegated ? 1 : 0,
      timestamp,
      timestamp,
    );
    this.addEvent(null, null, null, "info", "a2a.outbound.recorded", `Outbound task отправлен peer «${remote.name}»`, {
      projectId: remote.projectId,
      remoteId: remote.id,
      outboundTaskId: id,
      remoteTaskId,
      state,
      delegated,
      actor: actor.display.slice(0, 200),
    });
    const row = this.db.prepare("SELECT * FROM a2a_outbound_tasks WHERE id = ?").get(id) as Row;
    return this.a2aOutboundTaskDto(row);
  }

  updateA2AOutboundTask(
    remoteId: string,
    remoteTaskId: string,
    response: Record<string, unknown>,
    projectId: string,
  ): Record<string, unknown> | null {
    const project = normalizeProjectId(projectId);
    const row = this.db.prepare(`
      SELECT * FROM a2a_outbound_tasks WHERE remote_id = ? AND remote_task_id = ? AND project_id = ?
    `).get(remoteId, remoteTaskId, project) as Row | undefined;
    if (!row) return null;
    const status = response.status && typeof response.status === "object" && !Array.isArray(response.status)
      ? response.status as Record<string, unknown>
      : {};
    const state = typeof status.state === "string" ? status.state : String(row.state);
    const timestamp = nowIso();
    this.db.prepare("UPDATE a2a_outbound_tasks SET state = ?, response_blob = ?, updated_at = ? WHERE id = ?")
      .run(state, encryptJson(response, this.credentialsKey), timestamp, String(row.id));
    const updated = this.db.prepare("SELECT * FROM a2a_outbound_tasks WHERE id = ?").get(String(row.id)) as Row;
    return this.a2aOutboundTaskDto(updated);
  }

  private a2aOutboundTaskDto(row: Row): Record<string, unknown> {
    return {
      id: row.id,
      remoteId: row.remote_id,
      remoteTaskId: row.remote_task_id,
      contextId: row.context_id,
      clientMessageId: row.client_message_id,
      state: row.state,
      traceId: row.trace_id,
      delegated: Number(row.delegated) === 1,
      actor: row.actor_display,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
    };
  }

  getA2AOutboundTask(id: string, projectId = "default"): Record<string, unknown> | null {
    const row = this.db.prepare(`
      SELECT t.*, r.name AS remote_name FROM a2a_outbound_tasks t
      JOIN a2a_remotes r ON r.id = t.remote_id
      WHERE t.id = ? AND t.project_id = ?
    `).get(id, normalizeProjectId(projectId)) as Row | undefined;
    return row ? { ...this.a2aOutboundTaskDto(row), remoteName: row.remote_name } : null;
  }

  listRecentA2AOutboundTasks(projectId = "default", limit = 100): Array<Record<string, unknown>> {
    const project = normalizeProjectId(projectId);
    return (this.db.prepare(`
      SELECT t.*, r.name AS remote_name FROM a2a_outbound_tasks t
      JOIN a2a_remotes r ON r.id = t.remote_id
      WHERE t.project_id = ? ORDER BY t.updated_at DESC LIMIT ?
    `).all(project, Math.max(1, Math.min(500, Math.trunc(limit)))) as Row[]).map((row) => ({
      ...this.a2aOutboundTaskDto(row),
      remoteName: row.remote_name,
    }));
  }

  getA2ASnapshot(projectId = "default"): Record<string, unknown> {
    const endpoints = this.listA2AEndpoints(projectId);
    const tasks = this.listRecentA2ATasks(projectId, 100);
    const remotes = this.listA2ARemotes(projectId);
    const outboundTasks = this.listRecentA2AOutboundTasks(projectId, 100);
    return {
      protocolVersion: A2A_PROTOCOL_VERSION,
      adapterVersion: A2A_ADAPTER_VERSION,
      endpoints,
      tasks,
      remotes,
      outboundTasks,
      counts: {
        endpoints: endpoints.length,
        enabledEndpoints: endpoints.filter((endpoint) => endpoint.enabled === true).length,
        tasks: tasks.length,
        activeTasks: tasks.filter((task) => ["TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_AUTH_REQUIRED"].includes(String(task.state))).length,
        completedTasks: tasks.filter((task) => task.state === "TASK_STATE_COMPLETED").length,
        failedTasks: tasks.filter((task) => task.state === "TASK_STATE_FAILED").length,
        remotes: remotes.length,
        enabledRemotes: remotes.filter((remote) => remote.enabled === true).length,
        outboundTasks: outboundTasks.length,
      },
    };
  }

  listAgents(projectId = "default"): Array<Record<string, unknown>> {
    const project = normalizeProjectId(projectId);
    this.ensurePromptRegistry(project);
    const rows = this.db
      .prepare(`
        SELECT
          a.id,
          a.name,
          a.role,
          a.system_prompt,
          a.model,
          CASE WHEN pr.id IS NOT NULL THEN pv.content ELSE a.system_prompt END AS effective_system_prompt,
          CASE WHEN pr.id IS NOT NULL THEN pr.active_model ELSE a.model END AS effective_model,
          pr.id AS registry_prompt_id,
          pr.active_version AS registry_prompt_version,
          a.runtime,
          a.runtime_config_json,
          a.is_builtin,
          a.created_at,
          a.updated_at,
          COUNT(DISTINCT r.id) AS total_runs,
          COUNT(DISTINCT CASE
            WHEN r.status IN ('queued', 'running', 'waiting_approval') THEN r.id
          END) AS active_runs,
          MAX(r.updated_at) AS last_run_at
        FROM agents a
        LEFT JOIN prompt_registry pr
          ON pr.project_id = ? AND pr.agent_id = a.id
        LEFT JOIN prompt_versions pv
          ON pv.prompt_id = pr.id AND pv.version = pr.active_version
        LEFT JOIN stages s ON s.agent_id = a.id
        LEFT JOIN runs r ON r.id = s.run_id AND r.project_id = ?
        WHERE a.id NOT IN ('__agat_system__', '__agat_eval_judge__')
          AND (a.is_builtin = 1 OR a.project_id = ?)
        GROUP BY a.id
        ORDER BY
          a.is_builtin DESC,
          CASE a.id WHEN 'collector' THEN 0 WHEN 'analyst' THEN 1 WHEN 'editor' THEN 2 ELSE 3 END,
          a.created_at ASC,
          a.name COLLATE NOCASE ASC
      `)
      .all(project, project, project) as Row[];

    return rows.map((row) => ({
      id: row.id,
      name: row.name,
      role: row.role,
      systemPrompt: row.effective_system_prompt,
      model: row.effective_model,
      registryPromptId: row.registry_prompt_id,
      registryPromptVersion: row.registry_prompt_version === null ? null : Number(row.registry_prompt_version),
      runtime: normalizeAgentRuntime(row.runtime),
      runtimeConfig: normalizeAgentRuntimeConfig(
        parseJson<Record<string, unknown>>(row.runtime_config_json, {}),
      ),
      isBuiltIn: Number(row.is_builtin) === 1,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
      totalRuns: Number(row.total_runs),
      activeRuns: Number(row.active_runs),
      lastRunAt: row.last_run_at,
    }));
  }

  createAgent(input: CreateAgentInput, projectId = "default"): Record<string, unknown> {
    const project = this.requireProject(projectId);
    const agent = normalizeAgentInput(input);
    this.validateSpecialistTeamConfig(agent.runtimeConfig, project);
    const duplicate = this.db
      .prepare("SELECT id FROM agents WHERE name = ? COLLATE NOCASE AND (project_id = ? OR is_builtin = 1)")
      .get(agent.name, project) as Row | undefined;
    if (duplicate) throw new Error("Агент с таким именем уже существует");

    const id = randomUUID();
    const timestamp = nowIso();
    this.db
      .prepare(`
        INSERT INTO agents(
          id, name, role, system_prompt, model, runtime, runtime_config_json,
          is_builtin, project_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
      `)
      .run(
        id,
        agent.name,
        agent.role,
        agent.systemPrompt,
        agent.model || null,
        agent.runtime,
        JSON.stringify(agent.runtimeConfig),
        project,
        timestamp,
        timestamp,
      );
    this.ensurePromptRegistry(project);
    this.addEvent(null, null, null, "info", "agent.created", `Создан агент «${agent.name}»`, {
      agentId: id,
      model: agent.model || null,
      runtime: agent.runtime,
      runtimeConfig: agent.runtimeConfig,
    });
    return this.listAgents(project).find((item) => item.id === id)!;
  }

  updateAgent(agentId: string, input: CreateAgentInput, projectId = "default"): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    this.ensurePromptRegistry(project);
    const existing = this.db.prepare(`
      SELECT a.id, a.runtime, a.runtime_config_json,
        COALESCE(pv.content, a.system_prompt) AS effective_system_prompt,
        CASE WHEN pr.id IS NOT NULL THEN pr.active_model ELSE a.model END AS effective_model
      FROM agents a
      LEFT JOIN prompt_registry pr ON pr.project_id = ? AND pr.agent_id = a.id
      LEFT JOIN prompt_versions pv ON pv.prompt_id = pr.id AND pv.version = pr.active_version
      WHERE a.id = ? AND a.project_id = ? AND a.is_builtin = 0
    `).get(project, agentId, project) as Row | undefined;
    if (!existing) return null;

    const agent = normalizeAgentInput({
      ...input,
      runtime: input.runtime ?? normalizeAgentRuntime(existing.runtime),
      runtimeConfig: input.runtimeConfig ?? parseJson<Record<string, unknown>>(
        existing.runtime_config_json,
        {},
      ),
    });
    this.validateSpecialistTeamConfig(agent.runtimeConfig, project, agentId);
    const duplicate = this.db
      .prepare("SELECT id FROM agents WHERE name = ? COLLATE NOCASE AND id <> ? AND (project_id = ? OR is_builtin = 1)")
      .get(agent.name, agentId, project) as Row | undefined;
    if (duplicate) throw new Error("Агент с таким именем уже существует");
    const currentModel = typeof existing.effective_model === "string" ? existing.effective_model : "";
    if (agent.systemPrompt !== String(existing.effective_system_prompt) || agent.model !== currentModel) {
      throw new Error("Prompt или модель меняются через Golden eval → Prompt registry после прошедшего quality gate");
    }

    const timestamp = nowIso();
    this.db
      .prepare(`
        UPDATE agents
        SET name = ?, role = ?, runtime = ?, runtime_config_json = ?, updated_at = ?
        WHERE id = ?
      `)
      .run(
        agent.name,
        agent.role,
        agent.runtime,
        JSON.stringify(agent.runtimeConfig),
        timestamp,
        agentId,
      );
    this.addEvent(null, null, null, "info", "agent.updated", `Обновлён агент «${agent.name}»`, {
      agentId,
      model: agent.model || null,
      runtime: agent.runtime,
      runtimeConfig: agent.runtimeConfig,
    });
    return this.listAgents(project).find((item) => item.id === agentId)!;
  }

  listPromptRegistry(projectId = "default"): Array<Record<string, unknown>> {
    const project = this.requireProject(projectId);
    this.ensurePromptRegistry(project);
    return (this.db.prepare(`
      SELECT pr.*, a.name AS agent_name, a.is_builtin
      FROM prompt_registry pr
      LEFT JOIN agents a ON a.id = pr.agent_id
      WHERE pr.project_id = ?
      ORDER BY pr.updated_at DESC, pr.name COLLATE NOCASE
    `).all(project) as Row[]).map((row) => this.promptDto(row));
  }

  createPrompt(input: CreatePromptInput, projectId = "default", actor = "system"): Record<string, unknown> {
    const project = this.requireProject(projectId);
    this.ensurePromptRegistry(project);
    const name = requiredAgentText(input.name, "Название prompt", 120);
    const description = optionalText(input.description, "Описание prompt", 1_000);
    const content = requiredAgentText(input.content, "Prompt", 20_000);
    const agentId = input.agentId === undefined || input.agentId === null || input.agentId === ""
      ? null
      : String(input.agentId);
    let model: string | null = null;
    if (agentId) {
      const agent = this.effectiveAgentRow(agentId, project);
      if (!agent || ["__agat_system__", "__agat_eval_judge__"].includes(agentId)) {
        throw new Error("Агент prompt registry не найден");
      }
      const bound = this.db.prepare("SELECT id FROM prompt_registry WHERE project_id = ? AND agent_id = ?")
        .get(project, agentId) as Row | undefined;
      if (bound) throw new Error("У агента уже есть prompt registry");
      model = typeof agent.model === "string" ? agent.model : null;
    }
    const id = randomUUID();
    const timestamp = nowIso();
    try {
      this.transaction(() => {
        this.db.prepare(`
          INSERT INTO prompt_registry(
            id, project_id, agent_id, name, description, active_version, active_model, created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        `).run(id, project, agentId, name, description, model, timestamp, timestamp);
        this.db.prepare(`
          INSERT INTO prompt_versions(
            prompt_id, version, content, content_sha256, change_note, created_by, created_at
          ) VALUES (?, 1, ?, ?, 'Первая версия', ?, ?)
        `).run(id, content, sha256Text(content), actor.slice(0, 200), timestamp);
      });
    } catch (error) {
      if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
        throw new Error("Prompt с таким названием уже существует");
      }
      throw error;
    }
    this.addEvent(null, null, null, "info", "prompt.created", `Создан prompt registry «${name}»`, {
      projectId: project,
      promptId: id,
      agentId,
      contentSha256: sha256Text(content),
      actor: actor.slice(0, 200),
    });
    return this.listPromptRegistry(project).find((prompt) => prompt.id === id)!;
  }

  createPromptVersion(
    promptId: string,
    input: CreatePromptVersionInput,
    projectId = "default",
    actor = "system",
  ): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const prompt = this.db.prepare("SELECT * FROM prompt_registry WHERE id = ? AND project_id = ?")
      .get(promptId, project) as Row | undefined;
    if (!prompt) return null;
    const content = requiredAgentText(input.content, "Prompt", 20_000);
    const changeNote = optionalText(input.changeNote, "Описание изменения", 1_000);
    const hash = sha256Text(content);
    const duplicate = this.db.prepare("SELECT version FROM prompt_versions WHERE prompt_id = ? AND content_sha256 = ?")
      .get(promptId, hash) as Row | undefined;
    if (duplicate) throw new Error(`Такая версия prompt уже существует: v${Number(duplicate.version)}`);
    const version = Number((this.db.prepare("SELECT COALESCE(MAX(version), 0) + 1 AS version FROM prompt_versions WHERE prompt_id = ?")
      .get(promptId) as Row).version);
    const timestamp = nowIso();
    this.db.prepare(`
      INSERT INTO prompt_versions(
        prompt_id, version, content, content_sha256, change_note, created_by, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?)
    `).run(promptId, version, content, hash, changeNote, actor.slice(0, 200), timestamp);
    this.db.prepare("UPDATE prompt_registry SET updated_at = ? WHERE id = ?").run(timestamp, promptId);
    this.addEvent(null, null, null, "info", "prompt.version.created", `Создана версия v${version} prompt «${String(prompt.name)}»`, {
      projectId: project,
      promptId,
      version,
      contentSha256: hash,
      actor: actor.slice(0, 200),
    });
    return this.listPromptRegistry(project).find((candidate) => candidate.id === promptId)!;
  }

  private promptDto(row: Row): Record<string, unknown> {
    const versions = (this.db.prepare(`
      SELECT * FROM prompt_versions WHERE prompt_id = ? ORDER BY version DESC
    `).all(String(row.id)) as Row[]).map((version) => ({
      version: Number(version.version),
      content: String(version.content),
      contentSha256: String(version.content_sha256),
      changeNote: String(version.change_note),
      createdBy: String(version.created_by),
      createdAt: String(version.created_at),
      active: Number(version.version) === Number(row.active_version),
    }));
    return {
      id: String(row.id),
      name: String(row.name),
      description: String(row.description),
      agentId: typeof row.agent_id === "string" ? row.agent_id : null,
      agentName: typeof row.agent_name === "string" ? row.agent_name : null,
      agentBuiltIn: Number(row.is_builtin) === 1,
      activeVersion: Number(row.active_version),
      activeModel: typeof row.active_model === "string" ? row.active_model : null,
      versions,
      createdAt: String(row.created_at),
      updatedAt: String(row.updated_at),
    };
  }

  createEvalDataset(
    input: CreateEvalDatasetInput,
    projectId = "default",
    actor = "system",
  ): Record<string, unknown> {
    const project = this.requireProject(projectId);
    const name = requiredAgentText(input.name, "Название golden dataset", 120);
    const description = optionalText(input.description, "Описание golden dataset", 2_000);
    const id = randomUUID();
    const timestamp = nowIso();
    const normalized = this.normalizeEvalDatasetVersion(project, description, input.changeNote, input.rubric, input.examples);
    try {
      this.transaction(() => {
        this.db.prepare(`
          INSERT INTO eval_datasets(id, project_id, name, description, current_version, created_at, updated_at)
          VALUES (?, ?, ?, ?, 1, ?, ?)
        `).run(id, project, name, description, timestamp, timestamp);
        this.insertEvalDatasetVersion(id, 1, normalized, actor, timestamp);
      });
    } catch (error) {
      if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
        throw new Error("Golden dataset с таким названием уже существует");
      }
      throw error;
    }
    this.addEvent(null, null, null, "info", "eval.dataset.created", `Создан golden dataset «${name}»`, {
      projectId: project,
      datasetId: id,
      version: 1,
      examples: normalized.examples.length,
      contentSha256: normalized.contentSha256,
      actor: actor.slice(0, 200),
    });
    return this.listEvalDatasets(project).find((dataset) => dataset.id === id)!;
  }

  createEvalDatasetVersion(
    datasetId: string,
    input: CreateEvalDatasetVersionInput,
    projectId = "default",
    actor = "system",
  ): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const dataset = this.db.prepare("SELECT * FROM eval_datasets WHERE id = ? AND project_id = ?")
      .get(datasetId, project) as Row | undefined;
    if (!dataset) return null;
    const description = input.description === undefined
      ? String(dataset.description)
      : optionalText(input.description, "Описание golden dataset", 2_000);
    const normalized = this.normalizeEvalDatasetVersion(project, description, input.changeNote, input.rubric, input.examples);
    const duplicate = this.db.prepare("SELECT version FROM eval_dataset_versions WHERE dataset_id = ? AND content_sha256 = ?")
      .get(datasetId, normalized.contentSha256) as Row | undefined;
    if (duplicate) throw new Error(`Такое содержимое dataset уже зафиксировано в v${Number(duplicate.version)}`);
    const version = Number(dataset.current_version) + 1;
    const timestamp = nowIso();
    this.transaction(() => {
      this.insertEvalDatasetVersion(datasetId, version, normalized, actor, timestamp);
      this.db.prepare(`
        UPDATE eval_datasets SET description = ?, current_version = ?, updated_at = ? WHERE id = ?
      `).run(description, version, timestamp, datasetId);
    });
    this.addEvent(null, null, null, "info", "eval.dataset.version.created", `Создана версия v${version} golden dataset «${String(dataset.name)}»`, {
      projectId: project,
      datasetId,
      version,
      examples: normalized.examples.length,
      contentSha256: normalized.contentSha256,
      actor: actor.slice(0, 200),
    });
    return this.listEvalDatasets(project).find((candidate) => candidate.id === datasetId)!;
  }

  listEvalDatasets(projectId = "default"): Array<Record<string, unknown>> {
    const project = this.requireProject(projectId);
    return (this.db.prepare(`
      SELECT * FROM eval_datasets WHERE project_id = ? ORDER BY updated_at DESC, name COLLATE NOCASE
    `).all(project) as Row[]).map((row) => this.evalDatasetDto(row));
  }

  private normalizeEvalDatasetVersion(
    projectId: string,
    description: string,
    rawChangeNote: unknown,
    rawRubric: unknown,
    rawExamples: unknown,
  ): {
    description: string;
    changeNote: string;
    rubric: NormalizedEvalRubricCriterion[];
    examples: NormalizedEvalExample[];
    knowledgeSnapshot: Record<string, unknown>;
    contentSha256: string;
  } {
    const rubric = normalizeEvalRubric(rawRubric);
    const examples = normalizeEvalExamples(rawExamples).map((example) => ({
      ...example,
      knowledgeCollectionIds: this.requireKnowledgeCollectionIds(projectId, example.knowledgeCollectionIds),
    }));
    const collectionIds = [...new Set(examples.flatMap((example) => example.knowledgeCollectionIds))].sort();
    const knowledgeSnapshot = this.evalKnowledgeSnapshot(projectId, collectionIds);
    const canonical = {
      description,
      rubric,
      examples: examples.map(({ id: _id, ...example }) => example),
      knowledgeFingerprint: knowledgeSnapshot.fingerprint,
    };
    return {
      description,
      changeNote: optionalText(rawChangeNote, "Описание версии dataset", 1_000),
      rubric,
      examples,
      knowledgeSnapshot,
      contentSha256: sha256Text(JSON.stringify(canonical)),
    };
  }

  private insertEvalDatasetVersion(
    datasetId: string,
    version: number,
    normalized: {
      description: string;
      changeNote: string;
      rubric: NormalizedEvalRubricCriterion[];
      examples: NormalizedEvalExample[];
      knowledgeSnapshot: Record<string, unknown>;
      contentSha256: string;
    },
    actor: string,
    timestamp: string,
  ): void {
    this.db.prepare(`
      INSERT INTO eval_dataset_versions(
        dataset_id, version, description, change_note, rubric_json,
        knowledge_snapshot_json, content_sha256, created_by, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      datasetId,
      version,
      normalized.description,
      normalized.changeNote,
      JSON.stringify(normalized.rubric),
      JSON.stringify(normalized.knowledgeSnapshot),
      normalized.contentSha256,
      actor.slice(0, 200),
      timestamp,
    );
    const insert = this.db.prepare(`
      INSERT INTO eval_examples(
        id, dataset_id, dataset_version, position, name, input, reference_output,
        required_terms_json, forbidden_terms_json, knowledge_collection_ids_json, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    normalized.examples.forEach((example, position) => insert.run(
      example.id,
      datasetId,
      version,
      position,
      example.name,
      example.input,
      example.referenceOutput,
      JSON.stringify(example.requiredTerms),
      JSON.stringify(example.forbiddenTerms),
      JSON.stringify(example.knowledgeCollectionIds),
      timestamp,
    ));
  }

  private evalDatasetDto(row: Row): Record<string, unknown> {
    const versions = (this.db.prepare(`
      SELECT * FROM eval_dataset_versions WHERE dataset_id = ? ORDER BY version DESC
    `).all(String(row.id)) as Row[]).map((version) => {
      const examples = (this.db.prepare(`
        SELECT * FROM eval_examples
        WHERE dataset_id = ? AND dataset_version = ? ORDER BY position
      `).all(String(row.id), Number(version.version)) as Row[]).map((example) => ({
        id: String(example.id),
        position: Number(example.position),
        name: String(example.name),
        input: String(example.input),
        referenceOutput: String(example.reference_output),
        requiredTerms: parseJson<string[]>(example.required_terms_json, []),
        forbiddenTerms: parseJson<string[]>(example.forbidden_terms_json, []),
        knowledgeCollectionIds: normalizeKnowledgeCollectionIds(
          parseJson<unknown>(example.knowledge_collection_ids_json, []),
        ),
      }));
      return {
        version: Number(version.version),
        description: String(version.description),
        changeNote: String(version.change_note),
        rubric: parseJson<NormalizedEvalRubricCriterion[]>(version.rubric_json, []),
        knowledgeSnapshot: parseJson<Record<string, unknown>>(version.knowledge_snapshot_json, {}),
        contentSha256: String(version.content_sha256),
        createdBy: String(version.created_by),
        createdAt: String(version.created_at),
        examples,
        active: Number(version.version) === Number(row.current_version),
      };
    });
    return {
      id: String(row.id),
      name: String(row.name),
      description: String(row.description),
      currentVersion: Number(row.current_version),
      versions,
      createdAt: String(row.created_at),
      updatedAt: String(row.updated_at),
    };
  }

  private evalKnowledgeSnapshot(projectId: string, rawCollectionIds: unknown): Record<string, unknown> {
    const collectionIds = normalizeKnowledgeCollectionIds(rawCollectionIds).sort();
    const collections = collectionIds.map((collectionId) => {
      const collection = this.db.prepare(`
        SELECT id, name, embedding_model, chunk_size, chunk_overlap, top_k
        FROM knowledge_collections WHERE id = ? AND project_id = ?
      `).get(collectionId, projectId) as Row | undefined;
      if (!collection) return null;
      const documents = (this.db.prepare(`
        SELECT id, name, content_sha256, status, chunk_count, embedded_count
        FROM knowledge_documents WHERE collection_id = ? ORDER BY id
      `).all(collectionId) as Row[]).map((document) => ({
        id: String(document.id),
        name: String(document.name),
        contentSha256: String(document.content_sha256),
        status: String(document.status),
        chunkCount: Number(document.chunk_count),
        embeddedCount: Number(document.embedded_count),
        chunkHashes: (this.db.prepare(`
          SELECT content_sha256 FROM knowledge_chunks WHERE document_id = ? ORDER BY ordinal
        `).all(String(document.id)) as Row[]).map((chunk) => String(chunk.content_sha256)),
      }));
      return {
        id: String(collection.id),
        name: String(collection.name),
        embeddingModel: String(collection.embedding_model),
        chunkSize: Number(collection.chunk_size),
        chunkOverlap: Number(collection.chunk_overlap),
        topK: Number(collection.top_k),
        documents,
      };
    });
    const base = {
      schemaVersion: 1,
      collectionIds,
      missingCollectionIds: collectionIds.filter((_id, index) => collections[index] === null),
      collections: collections.filter((collection) => collection !== null),
    };
    return { ...base, fingerprint: sha256Text(JSON.stringify(base)) };
  }

  createEvalExperiment(
    input: CreateEvalExperimentInput,
    projectId = "default",
    actor = "system",
  ): Record<string, unknown> {
    const project = this.requireProject(projectId);
    this.ensurePromptRegistry(project);
    const name = requiredAgentText(input.name, "Название experiment", 120);
    const dataset = this.db.prepare("SELECT * FROM eval_datasets WHERE id = ? AND project_id = ?")
      .get(input.datasetId, project) as Row | undefined;
    if (!dataset) throw new Error("Golden dataset не найден");
    const datasetVersion = input.datasetVersion === undefined
      ? Number(dataset.current_version)
      : Number(input.datasetVersion);
    if (!Number.isInteger(datasetVersion) || datasetVersion < 1) throw new Error("Некорректная версия golden dataset");
    const version = this.db.prepare(`
      SELECT * FROM eval_dataset_versions WHERE dataset_id = ? AND version = ?
    `).get(input.datasetId, datasetVersion) as Row | undefined;
    if (!version) throw new Error("Версия golden dataset не найдена");
    const prompt = this.db.prepare(`
      SELECT pr.*, pv.content, pv.content_sha256
      FROM prompt_registry pr
      JOIN prompt_versions pv ON pv.prompt_id = pr.id AND pv.version = ?
      WHERE pr.id = ? AND pr.project_id = ?
    `).get(Number(input.promptVersion), input.promptId, project) as Row | undefined;
    if (!prompt) throw new Error("Версия prompt registry не найдена");
    const agent = this.effectiveAgentRow(String(input.agentId), project);
    if (!agent || ["__agat_system__", "__agat_eval_judge__"].includes(String(input.agentId))) {
      throw new Error("Агент experiment не найден");
    }
    const promptVersion = Number(input.promptVersion);
    if (!Number.isInteger(promptVersion) || promptVersion < 1) throw new Error("Некорректная версия prompt");
    let model: string | null;
    if (input.model === undefined) model = typeof agent.model === "string" ? agent.model : null;
    else if (input.model === null || input.model === "") model = null;
    else model = requiredAgentText(input.model, "Модель experiment", 200);
    const minQualityScore = input.minQualityScore === undefined
      ? 80
      : normalizeEvalScore(input.minQualityScore, "Quality threshold");
    const expectedKnowledge = parseJson<Record<string, unknown>>(version.knowledge_snapshot_json, {});
    const currentKnowledge = this.evalKnowledgeSnapshot(project, expectedKnowledge.collectionIds ?? []);
    if (expectedKnowledge.fingerprint !== currentKnowledge.fingerprint) {
      throw new Error("Knowledge snapshot golden dataset изменился; создайте новую версию dataset перед запуском eval");
    }
    const examples = this.db.prepare(`
      SELECT * FROM eval_examples WHERE dataset_id = ? AND dataset_version = ? ORDER BY position
    `).all(input.datasetId, datasetVersion) as Row[];
    if (examples.length === 0) throw new Error("Golden dataset не содержит примеров");

    const experimentId = randomUUID();
    const timestamp = nowIso();
    const startedRunIds: string[] = [];
    try {
      this.transaction(() => {
        this.db.prepare(`
          INSERT INTO eval_experiments(
            id, project_id, name, dataset_id, dataset_version, agent_id,
            prompt_id, prompt_version, model, min_quality_score,
            knowledge_snapshot_json, status, created_by, created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
        `).run(
          experimentId,
          project,
          name,
          input.datasetId,
          datasetVersion,
          String(input.agentId),
          input.promptId,
          promptVersion,
          model,
          minQualityScore,
          JSON.stringify(expectedKnowledge),
          actor.slice(0, 200),
          timestamp,
          timestamp,
        );
        const insertRun = this.db.prepare(`
          INSERT INTO runs(
            id, name, input, status, execution_mode, priority, approval_required,
            result_destination, artifact_path, project_id, trace_id, root_span_id,
            replay_of_run_id, evaluation_group_id, variant_name,
            knowledge_collection_ids_json, created_at, updated_at
          ) VALUES (?, ?, ?, 'queued', 'sequential', 50, 0, 'history', '', ?, ?, ?, NULL, ?, ?, ?, ?, ?)
        `);
        const insertStage = this.db.prepare(`
          INSERT INTO stages(
            id, run_id, agent_id, position, status, requires_approval,
            agent_snapshot_json, created_at, updated_at
          ) VALUES (?, ?, ?, 0, 'queued', 0, ?, ?, ?)
        `);
        const insertItem = this.db.prepare(`
          INSERT INTO eval_experiment_items(
            id, experiment_id, example_id, run_id, status, created_at, updated_at
          ) VALUES (?, ?, ?, ?, 'queued', ?, ?)
        `);
        for (const example of examples) {
          const runId = randomUUID();
          const trace = this.telemetry.startRun({ runId, projectId: project, evaluationGroupId: experimentId });
          startedRunIds.push(runId);
          const collectionIds = this.requireKnowledgeCollectionIds(
            project,
            parseJson<unknown>(example.knowledge_collection_ids_json, []),
          );
          const snapshot = this.captureAgentSnapshot({
            ...agent,
            system_prompt: String(prompt.content),
            model,
            registry_prompt_id: input.promptId,
            registry_prompt_version: promptVersion,
          }, "evaluation", project, timestamp, model);
          insertRun.run(
            runId,
            `${name} · ${String(example.name)}`.slice(0, 120),
            String(example.input),
            project,
            trace.traceId,
            trace.spanId,
            experimentId,
            String(example.name).slice(0, 60),
            JSON.stringify(collectionIds),
            timestamp,
            timestamp,
          );
          insertStage.run(randomUUID(), runId, String(input.agentId), JSON.stringify(snapshot), timestamp, timestamp);
          insertItem.run(randomUUID(), experimentId, String(example.id), runId, timestamp, timestamp);
          this.addEvent(runId, null, null, "info", "eval.candidate.queued", "Golden eval пример добавлен в batch", {
            experimentId,
            datasetId: input.datasetId,
            datasetVersion,
            exampleId: example.id,
            promptId: input.promptId,
            promptVersion,
            promptSha256: prompt.content_sha256,
            model,
          });
        }
      });
    } catch (error) {
      for (const runId of startedRunIds) {
        this.telemetry.endRun(runId, { status: "failed", errorType: error instanceof Error ? error.name : "Error" });
      }
      throw error;
    }
    this.addEvent(null, null, null, "info", "eval.experiment.created", `Запущен golden experiment «${name}»`, {
      projectId: project,
      experimentId,
      datasetId: input.datasetId,
      datasetVersion,
      examples: examples.length,
      promptId: input.promptId,
      promptVersion,
      model,
      minQualityScore,
      actor: actor.slice(0, 200),
    });
    return this.getEvalExperiment(experimentId, project)!;
  }

  getEvalSnapshot(projectId = "default"): Record<string, unknown> {
    const project = this.requireProject(projectId);
    this.ensurePromptRegistry(project);
    const rows = this.db.prepare(`
      SELECT id FROM eval_experiments WHERE project_id = ? ORDER BY created_at DESC
    `).all(project) as Row[];
    for (const row of rows) this.refreshEvalExperiment(String(row.id), project);
    const experiments = (this.db.prepare(`
      SELECT * FROM eval_experiments WHERE project_id = ? ORDER BY created_at DESC LIMIT 100
    `).all(project) as Row[]).map((row) => this.evalExperimentDto(row, false));
    const prompts = this.listPromptRegistry(project);
    const datasets = this.listEvalDatasets(project);
    return {
      generatedAt: nowIso(),
      prompts,
      datasets,
      experiments,
      counts: {
        prompts: prompts.length,
        promptVersions: prompts.reduce((count, prompt) => count + ((prompt.versions as unknown[])?.length ?? 0), 0),
        datasets: datasets.length,
        datasetVersions: datasets.reduce((count, dataset) => count + ((dataset.versions as unknown[])?.length ?? 0), 0),
        experiments: experiments.length,
        passingExperiments: experiments.filter((experiment) =>
          (experiment.gates as Record<string, unknown> | undefined)?.overall === "pass",
        ).length,
      },
    };
  }

  getEvalExperiment(experimentId: string, projectId = "default"): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const owned = this.db.prepare("SELECT id FROM eval_experiments WHERE id = ? AND project_id = ?")
      .get(experimentId, project) as Row | undefined;
    if (!owned) return null;
    this.refreshEvalExperiment(experimentId, project);
    const row = this.db.prepare("SELECT * FROM eval_experiments WHERE id = ? AND project_id = ?")
      .get(experimentId, project) as Row;
    return this.evalExperimentDto(row, true);
  }

  reviewEvalItem(
    itemId: string,
    input: HumanEvalReviewInput,
    projectId = "default",
    actor = "system",
  ): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const item = this.db.prepare(`
      SELECT i.*, e.project_id, e.dataset_id, e.dataset_version
      FROM eval_experiment_items i
      JOIN eval_experiments e ON e.id = i.experiment_id
      WHERE i.id = ? AND e.project_id = ?
    `).get(itemId, project) as Row | undefined;
    if (!item) return null;
    const rubricRow = this.db.prepare(`
      SELECT rubric_json FROM eval_dataset_versions WHERE dataset_id = ? AND version = ?
    `).get(String(item.dataset_id), Number(item.dataset_version)) as Row;
    const rubric = parseJson<NormalizedEvalRubricCriterion[]>(rubricRow.rubric_json, []);
    const rationale = requiredAgentText(input.rationale, "Комментарий оценки", 4_000);
    const scores: Record<string, number> = {};
    let overallScore: number;
    if (rubric.length > 0) {
      if (!input.scores || typeof input.scores !== "object" || Array.isArray(input.scores)) {
        throw new Error("Для human rubric нужны оценки всех критериев");
      }
      let weighted = 0;
      let totalWeight = 0;
      for (const criterion of rubric) {
        if (!Object.hasOwn(input.scores, criterion.id)) throw new Error(`Нет оценки критерия «${criterion.label}»`);
        const score = normalizeEvalScore(input.scores[criterion.id], `Оценка «${criterion.label}»`);
        scores[criterion.id] = score;
        weighted += score * criterion.weight;
        totalWeight += criterion.weight;
      }
      overallScore = Math.round(weighted / totalWeight * 100) / 100;
    } else {
      overallScore = normalizeEvalScore(input.overallScore, "Итоговая оценка");
    }
    const timestamp = nowIso();
    this.db.prepare(`
      INSERT INTO eval_reviews(
        id, item_id, kind, reviewer, model, scores_json, overall_score,
        rationale, raw_output_sha256, run_id, created_at
      ) VALUES (?, ?, 'human', ?, NULL, ?, ?, ?, NULL, NULL, ?)
    `).run(
      randomUUID(),
      itemId,
      actor.slice(0, 200),
      JSON.stringify(scores),
      overallScore,
      rationale,
      timestamp,
    );
    this.addEvent(String(item.run_id), null, null, "info", "eval.human_review.created", "Сохранена human rubric оценка", {
      experimentId: item.experiment_id,
      itemId,
      reviewer: actor.slice(0, 200),
      overallScore,
      scores,
    });
    return this.getEvalExperiment(String(item.experiment_id), project);
  }

  judgeEvalExperiment(
    experimentId: string,
    input: JudgeEvalExperimentInput,
    projectId = "default",
    actor = "system",
  ): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const model = requiredAgentText(input.model, "Модель judge", 200);
    const experiment = this.db.prepare("SELECT * FROM eval_experiments WHERE id = ? AND project_id = ?")
      .get(experimentId, project) as Row | undefined;
    if (!experiment) return null;
    this.refreshEvalExperiment(experimentId, project);
    const rubricRow = this.db.prepare(`
      SELECT rubric_json FROM eval_dataset_versions WHERE dataset_id = ? AND version = ?
    `).get(String(experiment.dataset_id), Number(experiment.dataset_version)) as Row;
    const storedRubric = parseJson<NormalizedEvalRubricCriterion[]>(rubricRow.rubric_json, []);
    const rubric = storedRubric.length > 0 ? storedRubric : [{
      id: "overall_correctness",
      label: "Общая корректность",
      description: "Насколько ответ корректен, релевантен input и согласуется с reference",
      weight: 1,
    }];
    const judgeAgent = this.db.prepare("SELECT * FROM agents WHERE id = '__agat_eval_judge__'").get() as Row;
    const items = this.db.prepare(`
      SELECT i.*, ex.name AS example_name, ex.input AS example_input,
        ex.reference_output, r.status AS candidate_status,
        (SELECT output FROM stages WHERE run_id = r.id ORDER BY position DESC LIMIT 1) AS candidate_output,
        jr.status AS judge_status
      FROM eval_experiment_items i
      JOIN eval_examples ex ON ex.id = i.example_id
      JOIN runs r ON r.id = i.run_id
      LEFT JOIN runs jr ON jr.id = i.judge_run_id
      WHERE i.experiment_id = ? ORDER BY ex.position
    `).all(experimentId) as Row[];
    if (items.some((item) => item.candidate_status !== "completed")) {
      throw new Error("Model judge можно запустить после завершения всех candidate runs");
    }
    const timestamp = nowIso();
    const startedRunIds: string[] = [];
    try {
      this.transaction(() => {
        const insertRun = this.db.prepare(`
          INSERT INTO runs(
            id, name, input, status, execution_mode, priority, approval_required,
            result_destination, artifact_path, project_id, trace_id, root_span_id,
            knowledge_collection_ids_json, variant_name, created_at, updated_at
          ) VALUES (?, ?, ?, 'queued', 'sequential', 40, 0, 'history', '', ?, ?, ?, '[]', 'model judge', ?, ?)
        `);
        const insertStage = this.db.prepare(`
          INSERT INTO stages(
            id, run_id, agent_id, position, status, requires_approval,
            agent_snapshot_json, created_at, updated_at
          ) VALUES (?, ?, '__agat_eval_judge__', 0, 'queued', 0, ?, ?, ?)
        `);
        for (const item of items) {
          const existingReview = this.db.prepare(`
            SELECT id FROM eval_reviews WHERE item_id = ? AND kind = 'model_judge' LIMIT 1
          `).get(String(item.id)) as Row | undefined;
          const reusableRun = typeof item.judge_run_id === "string"
            && ["queued", "running", "waiting_approval"].includes(String(item.judge_status));
          if (existingReview || reusableRun) continue;
          const output = String(item.candidate_output ?? "");
          const payload = {
            schemaVersion: 1,
            example: String(item.example_name),
            input: String(item.example_input).slice(0, 12_000),
            referenceOutput: String(item.reference_output).slice(0, 24_000),
            candidateOutput: output.slice(0, 60_000),
            hashes: {
              inputSha256: sha256Text(String(item.example_input)),
              referenceSha256: sha256Text(String(item.reference_output)),
              candidateSha256: sha256Text(output),
            },
            truncated: {
              input: String(item.example_input).length > 12_000,
              referenceOutput: String(item.reference_output).length > 24_000,
              candidateOutput: output.length > 60_000,
            },
            rubric,
          };
          const judgeRunId = randomUUID();
          const trace = this.telemetry.startRun({ runId: judgeRunId, projectId: project });
          startedRunIds.push(judgeRunId);
          const snapshot = agentSnapshot(judgeAgent, "model_judge", timestamp, model);
          insertRun.run(
            judgeRunId,
            `Judge · ${String(experiment.name)} · ${String(item.example_name)}`.slice(0, 120),
            JSON.stringify(payload),
            project,
            trace.traceId,
            trace.spanId,
            timestamp,
            timestamp,
          );
          insertStage.run(randomUUID(), judgeRunId, JSON.stringify(snapshot), timestamp, timestamp);
          this.db.prepare(`
            UPDATE eval_experiment_items
            SET judge_run_id = ?, judge_error = NULL, updated_at = ? WHERE id = ?
          `).run(judgeRunId, timestamp, String(item.id));
          this.addEvent(judgeRunId, null, null, "info", "eval.judge.queued", "Model judge добавлен в локальную очередь", {
            experimentId,
            itemId: item.id,
            candidateRunId: item.run_id,
            model,
            actor: actor.slice(0, 200),
          });
        }
        this.db.prepare("UPDATE eval_experiments SET updated_at = ? WHERE id = ?")
          .run(timestamp, experimentId);
      });
    } catch (error) {
      for (const runId of startedRunIds) {
        this.telemetry.endRun(runId, { status: "failed", errorType: error instanceof Error ? error.name : "Error" });
      }
      throw error;
    }
    this.addEvent(null, null, null, "info", "eval.judge.batch.created", `Model judge запущен для experiment «${String(experiment.name)}»`, {
      projectId: project,
      experimentId,
      model,
      actor: actor.slice(0, 200),
    });
    return this.getEvalExperiment(experimentId, project);
  }

  promotePrompt(
    promptId: string,
    input: PromotePromptInput,
    projectId = "default",
    actor = "system",
  ): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const prompt = this.db.prepare("SELECT * FROM prompt_registry WHERE id = ? AND project_id = ?")
      .get(promptId, project) as Row | undefined;
    if (!prompt) return null;
    const version = Number(input.version);
    if (!Number.isInteger(version) || version < 1) throw new Error("Некорректная версия prompt");
    const promptVersion = this.db.prepare("SELECT * FROM prompt_versions WHERE prompt_id = ? AND version = ?")
      .get(promptId, version) as Row | undefined;
    if (!promptVersion) throw new Error("Версия prompt не найдена");
    const experimentId = requiredAgentText(input.experimentId, "Experiment quality gate", 64);
    this.refreshEvalExperiment(experimentId, project);
    const experiment = this.db.prepare("SELECT * FROM eval_experiments WHERE id = ? AND project_id = ?")
      .get(experimentId, project) as Row | undefined;
    if (!experiment) throw new Error("Experiment quality gate не найден");
    let targetModel: string | null;
    if (input.model === undefined) targetModel = typeof experiment.model === "string" ? experiment.model : null;
    else if (input.model === null || input.model === "") targetModel = null;
    else targetModel = requiredAgentText(input.model, "Модель promotion", 200);
    if (String(experiment.prompt_id) !== promptId || Number(experiment.prompt_version) !== version) {
      throw new Error("Experiment выполнен для другой версии prompt");
    }
    if ((typeof experiment.model === "string" ? experiment.model : null) !== targetModel) {
      throw new Error("Experiment выполнен для другой модели");
    }
    if (typeof prompt.agent_id === "string" && String(experiment.agent_id) !== prompt.agent_id) {
      throw new Error("Experiment выполнен другим агентом");
    }
    const summary = this.evalExperimentDto(experiment, false);
    const gates = summary.gates as Record<string, unknown>;
    if (summary.status !== "completed" || gates.overall !== "pass") {
      throw new Error("Prompt/model нельзя активировать: quality gate experiment не пройден");
    }
    const timestamp = nowIso();
    this.transaction(() => {
      this.db.prepare(`
        UPDATE prompt_registry SET active_version = ?, active_model = ?, updated_at = ? WHERE id = ?
      `).run(version, targetModel, timestamp, promptId);
      if (typeof prompt.agent_id === "string") {
        this.db.prepare(`
          UPDATE agents SET system_prompt = ?, model = ?, updated_at = ?
          WHERE id = ? AND project_id = ? AND is_builtin = 0
        `).run(String(promptVersion.content), targetModel, timestamp, prompt.agent_id, project);
      }
    });
    this.addEvent(null, null, null, "info", "prompt.promoted", `Активирована v${version} prompt «${String(prompt.name)}»`, {
      projectId: project,
      promptId,
      version,
      model: targetModel,
      experimentId,
      qualityScore: summary.qualityScore,
      actor: actor.slice(0, 200),
    });
    return this.listPromptRegistry(project).find((candidate) => candidate.id === promptId)!;
  }

  private refreshEvalExperiment(experimentId: string, projectId: string): void {
    const experiment = this.db.prepare("SELECT * FROM eval_experiments WHERE id = ? AND project_id = ?")
      .get(experimentId, projectId) as Row | undefined;
    if (!experiment) return;
    const rubricRow = this.db.prepare(`
      SELECT rubric_json FROM eval_dataset_versions WHERE dataset_id = ? AND version = ?
    `).get(String(experiment.dataset_id), Number(experiment.dataset_version)) as Row;
    const rubric = parseJson<NormalizedEvalRubricCriterion[]>(rubricRow.rubric_json, []);
    const items = this.db.prepare(`
      SELECT i.*, r.status AS candidate_status,
        (SELECT output FROM stages WHERE run_id = r.id ORDER BY position DESC LIMIT 1) AS candidate_output,
        jr.status AS judge_status,
        (SELECT output FROM stages WHERE run_id = jr.id ORDER BY position DESC LIMIT 1) AS judge_output,
        (SELECT agent_snapshot_json FROM stages WHERE run_id = jr.id ORDER BY position DESC LIMIT 1) AS judge_snapshot_json
      FROM eval_experiment_items i
      JOIN runs r ON r.id = i.run_id
      LEFT JOIN runs jr ON jr.id = i.judge_run_id
      WHERE i.experiment_id = ?
    `).all(experimentId) as Row[];
    const timestamp = nowIso();
    for (const item of items) {
      const candidateStatus = String(item.candidate_status);
      let deterministicScore: number | null = null;
      let deterministicDetails: Array<Record<string, unknown>> = [];
      if (candidateStatus === "completed") {
        const example = this.db.prepare("SELECT * FROM eval_examples WHERE id = ?")
          .get(String(item.example_id)) as Row;
        const output = normalizedEvalText(String(item.candidate_output ?? ""));
        const requiredTerms = parseJson<string[]>(example.required_terms_json, []);
        const forbiddenTerms = parseJson<string[]>(example.forbidden_terms_json, []);
        deterministicDetails = [
          ...requiredTerms.map((term) => ({
            kind: "required_term",
            term,
            pass: output.includes(normalizedEvalText(term)),
          })),
          ...forbiddenTerms.map((term) => ({
            kind: "forbidden_term",
            term,
            pass: !output.includes(normalizedEvalText(term)),
          })),
        ];
        if (deterministicDetails.length > 0) {
          deterministicScore = Math.round(
            deterministicDetails.filter((check) => check.pass === true).length / deterministicDetails.length * 10_000,
          ) / 100;
        }
      }
      this.db.prepare(`
        UPDATE eval_experiment_items
        SET status = ?, deterministic_score = ?, deterministic_details_json = ?, updated_at = ?
        WHERE id = ?
      `).run(
        candidateStatus,
        deterministicScore,
        JSON.stringify(deterministicDetails),
        timestamp,
        String(item.id),
      );

      if (typeof item.judge_run_id !== "string") continue;
      const existingReview = this.db.prepare(`
        SELECT id FROM eval_reviews WHERE run_id = ? AND kind = 'model_judge'
      `).get(item.judge_run_id) as Row | undefined;
      if (existingReview) continue;
      if (["failed", "cancelled"].includes(String(item.judge_status))) {
        this.db.prepare("UPDATE eval_experiment_items SET judge_error = ?, updated_at = ? WHERE id = ?")
          .run(`Judge run завершён со статусом ${String(item.judge_status)}`, timestamp, String(item.id));
        continue;
      }
      if (item.judge_status !== "completed" || typeof item.judge_output !== "string") continue;
      const parsed = parseJudgeOutput(item.judge_output);
      try {
        if (!parsed) throw new Error("Model judge не вернул JSON-объект");
        const rawScores = parsed.scores;
        const scores: Record<string, number> = {};
        let overallScore: number;
        if (rubric.length > 0) {
          if (!rawScores || typeof rawScores !== "object" || Array.isArray(rawScores)) {
            throw new Error("Model judge не вернул scores по rubric");
          }
          let weighted = 0;
          let totalWeight = 0;
          for (const criterion of rubric) {
            if (!Object.hasOwn(rawScores, criterion.id)) {
              throw new Error(`Model judge пропустил критерий ${criterion.id}`);
            }
            const score = normalizeEvalScore(
              (rawScores as Record<string, unknown>)[criterion.id],
              `Judge score ${criterion.id}`,
            );
            scores[criterion.id] = score;
            weighted += score * criterion.weight;
            totalWeight += criterion.weight;
          }
          overallScore = Math.round(weighted / totalWeight * 100) / 100;
        } else {
          overallScore = normalizeEvalScore(parsed.overallScore, "Judge overallScore");
          if (rawScores && typeof rawScores === "object" && !Array.isArray(rawScores)) {
            for (const [key, value] of Object.entries(rawScores).slice(0, 20)) {
              scores[key.slice(0, 64)] = normalizeEvalScore(value, `Judge score ${key}`);
            }
          }
        }
        const snapshot = parseAgentSnapshot(item.judge_snapshot_json);
        const rationale = optionalText(parsed.rationale, "Judge rationale", 4_000);
        this.db.prepare(`
          INSERT INTO eval_reviews(
            id, item_id, kind, reviewer, model, scores_json, overall_score,
            rationale, raw_output_sha256, run_id, created_at
          ) VALUES (?, ?, 'model_judge', ?, ?, ?, ?, ?, ?, ?, ?)
        `).run(
          randomUUID(),
          String(item.id),
          `model-judge:${snapshot?.model ?? "unknown"}`.slice(0, 200),
          snapshot?.model ?? null,
          JSON.stringify(scores),
          overallScore,
          rationale,
          sha256Text(item.judge_output),
          item.judge_run_id,
          timestamp,
        );
        this.db.prepare("UPDATE eval_experiment_items SET judge_error = NULL, updated_at = ? WHERE id = ?")
          .run(timestamp, String(item.id));
        this.addEvent(String(item.judge_run_id), null, null, "info", "eval.judge.review.created", "Model judge score сохранён в audit trail", {
          experimentId,
          itemId: item.id,
          overallScore,
          scores,
          rawOutputSha256: sha256Text(item.judge_output),
        });
      } catch (error) {
        this.db.prepare("UPDATE eval_experiment_items SET judge_error = ?, updated_at = ? WHERE id = ?")
          .run((error instanceof Error ? error.message : "Некорректный ответ model judge").slice(0, 1_000), timestamp, String(item.id));
      }
    }

    const current = this.evalExperimentState(experiment, false);
    const completion = (current.gates as Record<string, unknown>).completion;
    const quality = (current.gates as Record<string, unknown>).quality;
    let status: string;
    if (completion === "fail") status = "failed";
    else if (completion === "pending") {
      status = Number((current.counts as Record<string, number>).running ?? 0) > 0 ? "running" : "queued";
    } else if (quality === "pending") status = "scoring";
    else status = "completed";
    const timing = this.db.prepare(`
      SELECT MIN(r.started_at) AS started_at
      FROM eval_experiment_items i JOIN runs r ON r.id = i.run_id
      WHERE i.experiment_id = ?
    `).get(experimentId) as Row;
    const terminal = status === "completed" || status === "failed";
    this.db.prepare(`
      UPDATE eval_experiments
      SET status = ?, started_at = COALESCE(started_at, ?),
        completed_at = CASE WHEN ? = 1 THEN COALESCE(completed_at, ?) ELSE NULL END,
        updated_at = ?
      WHERE id = ?
    `).run(
      status,
      typeof timing.started_at === "string" ? timing.started_at : null,
      terminal ? 1 : 0,
      timestamp,
      timestamp,
      experimentId,
    );
  }

  private evalExperimentState(experiment: Row, detailed: boolean): Record<string, unknown> {
    const rows = this.db.prepare(`
      SELECT i.*, ex.position, ex.name AS example_name, ex.input AS example_input,
        ex.reference_output, ex.required_terms_json, ex.forbidden_terms_json,
        ex.knowledge_collection_ids_json,
        r.status AS candidate_status, r.started_at AS run_started_at, r.completed_at AS run_completed_at,
        (SELECT output FROM stages WHERE run_id = r.id ORDER BY position DESC LIMIT 1) AS candidate_output,
        (SELECT metrics_json FROM stages WHERE run_id = r.id ORDER BY position DESC LIMIT 1) AS candidate_metrics,
        jr.status AS judge_status
      FROM eval_experiment_items i
      JOIN eval_examples ex ON ex.id = i.example_id
      JOIN runs r ON r.id = i.run_id
      LEFT JOIN runs jr ON jr.id = i.judge_run_id
      WHERE i.experiment_id = ? ORDER BY ex.position
    `).all(String(experiment.id)) as Row[];
    const items = rows.map((row) => {
      const reviews = (this.db.prepare(`
        SELECT * FROM eval_reviews WHERE item_id = ? ORDER BY created_at DESC, rowid DESC
      `).all(String(row.id)) as Row[]).map((review) => ({
        id: String(review.id),
        kind: String(review.kind),
        reviewer: String(review.reviewer),
        model: typeof review.model === "string" ? review.model : null,
        scores: parseJson<Record<string, number>>(review.scores_json, {}),
        overallScore: Number(review.overall_score),
        rationale: String(review.rationale),
        rawOutputSha256: typeof review.raw_output_sha256 === "string" ? review.raw_output_sha256 : null,
        runId: typeof review.run_id === "string" ? review.run_id : null,
        createdAt: String(review.created_at),
      }));
      const human = reviews.find((review) => review.kind === "human");
      const judge = reviews.find((review) => review.kind === "model_judge");
      const deterministicScore = row.deterministic_score === null ? null : Number(row.deterministic_score);
      const effective = human ?? judge ?? (deterministicScore === null ? null : {
        kind: "deterministic",
        overallScore: deterministicScore,
      });
      const output = String(row.candidate_output ?? "");
      const metrics = normalizeExecutionMetrics(parseJson<Record<string, unknown>>(row.candidate_metrics, {}));
      return {
        id: String(row.id),
        exampleId: String(row.example_id),
        position: Number(row.position),
        name: String(row.example_name),
        input: detailed ? String(row.example_input) : undefined,
        referenceOutput: detailed ? String(row.reference_output) : undefined,
        requiredTerms: parseJson<string[]>(row.required_terms_json, []),
        forbiddenTerms: parseJson<string[]>(row.forbidden_terms_json, []),
        knowledgeCollectionIds: normalizeKnowledgeCollectionIds(
          parseJson<unknown>(row.knowledge_collection_ids_json, []),
        ),
        runId: String(row.run_id),
        runStatus: String(row.candidate_status),
        runStartedAt: typeof row.run_started_at === "string" ? row.run_started_at : null,
        runCompletedAt: typeof row.run_completed_at === "string" ? row.run_completed_at : null,
        output: detailed ? output.slice(0, 100_000) : undefined,
        outputCharacters: output.length,
        outputTruncated: detailed && output.length > 100_000,
        outputSha256: output ? sha256Text(output) : null,
        metrics,
        deterministicScore,
        deterministicDetails: parseJson<Array<Record<string, unknown>>>(row.deterministic_details_json, []),
        judgeRunId: typeof row.judge_run_id === "string" ? row.judge_run_id : null,
        judgeStatus: typeof row.judge_status === "string" ? row.judge_status : null,
        judgeError: typeof row.judge_error === "string" ? row.judge_error : null,
        humanScore: human?.overallScore ?? null,
        judgeScore: judge?.overallScore ?? null,
        qualityScore: effective?.overallScore ?? null,
        qualitySource: effective?.kind ?? null,
        reviews: detailed ? reviews : undefined,
      };
    });
    const statuses = items.map((item) => item.runStatus);
    const completion = statuses.some((status) => status === "failed" || status === "cancelled")
      ? "fail"
      : statuses.length > 0 && statuses.every((status) => status === "completed")
        ? "pass"
        : "pending";
    const qualityScores = items
      .map((item) => item.qualityScore)
      .filter((score): score is number => typeof score === "number");
    const qualityScore = qualityScores.length === items.length && items.length > 0
      ? Math.round(qualityScores.reduce((sum, score) => sum + score, 0) / qualityScores.length * 100) / 100
      : null;
    const quality = qualityScore === null
      ? "pending"
      : qualityScore >= Number(experiment.min_quality_score) ? "pass" : "fail";
    const expectedKnowledge = parseJson<Record<string, unknown>>(experiment.knowledge_snapshot_json, {});
    const currentKnowledge = this.evalKnowledgeSnapshot(String(experiment.project_id), expectedKnowledge.collectionIds ?? []);
    const knowledge = expectedKnowledge.fingerprint === currentKnowledge.fingerprint ? "pass" : "fail";
    const overall = [completion, quality, knowledge].includes("fail")
      ? "fail"
      : [completion, quality, knowledge].every((gate) => gate === "pass") ? "pass" : "pending";
    return {
      items,
      qualityScore,
      gates: { completion, quality, knowledge, overall },
      knowledgeDrift: knowledge === "fail",
      currentKnowledgeFingerprint: currentKnowledge.fingerprint,
      counts: {
        total: items.length,
        queued: statuses.filter((status) => status === "queued" || status === "pending").length,
        running: statuses.filter((status) => status === "running" || status === "waiting_approval").length,
        completed: statuses.filter((status) => status === "completed").length,
        failed: statuses.filter((status) => status === "failed" || status === "cancelled").length,
        scored: qualityScores.length,
        humanReviewed: items.filter((item) => item.humanScore !== null).length,
        judgeReviewed: items.filter((item) => item.judgeScore !== null).length,
      },
    };
  }

  private evalExperimentDto(row: Row, detailed: boolean): Record<string, unknown> {
    const dataset = this.db.prepare("SELECT name FROM eval_datasets WHERE id = ?").get(String(row.dataset_id)) as Row;
    const prompt = this.db.prepare("SELECT name FROM prompt_registry WHERE id = ?").get(String(row.prompt_id)) as Row;
    const agent = this.db.prepare("SELECT name FROM agents WHERE id = ?").get(String(row.agent_id)) as Row;
    const state = this.evalExperimentState(row, detailed);
    return {
      id: String(row.id),
      name: String(row.name),
      status: String(row.status),
      datasetId: String(row.dataset_id),
      datasetName: String(dataset.name),
      datasetVersion: Number(row.dataset_version),
      agentId: String(row.agent_id),
      agentName: String(agent.name),
      promptId: String(row.prompt_id),
      promptName: String(prompt.name),
      promptVersion: Number(row.prompt_version),
      model: typeof row.model === "string" ? row.model : null,
      minQualityScore: Number(row.min_quality_score),
      knowledgeSnapshot: parseJson<Record<string, unknown>>(row.knowledge_snapshot_json, {}),
      createdBy: String(row.created_by),
      createdAt: String(row.created_at),
      updatedAt: String(row.updated_at),
      startedAt: typeof row.started_at === "string" ? row.started_at : null,
      completedAt: typeof row.completed_at === "string" ? row.completed_at : null,
      ...state,
    };
  }

  knowledgeOverview(projectId = "default"): Record<string, unknown> {
    const project = this.requireProject(projectId);
    this.cleanupExpiredKnowledgeLeases();
    this.cleanupExpiredMemory();
    const rows = this.db.prepare(`
      SELECT c.*,
        (SELECT COUNT(*) FROM knowledge_documents d WHERE d.collection_id = c.id) AS document_count,
        (SELECT COUNT(*) FROM knowledge_documents d WHERE d.collection_id = c.id AND d.status = 'ready') AS ready_documents,
        (SELECT COUNT(*) FROM knowledge_chunks ch WHERE ch.collection_id = c.id) AS chunk_count,
        (SELECT COUNT(*) FROM knowledge_chunks ch WHERE ch.collection_id = c.id AND ch.embedding_json IS NOT NULL) AS embedded_chunks,
        (SELECT COUNT(*) FROM knowledge_embedding_jobs j WHERE j.collection_id = c.id AND j.status IN ('pending', 'running')) AS pending_jobs
      FROM knowledge_collections c
      WHERE c.project_id = ?
      ORDER BY c.updated_at DESC, c.name COLLATE NOCASE ASC
    `).all(project) as Row[];
    const freshEmbeddingWorkers = (this.db.prepare(`
      SELECT embedding_models_json, last_seen FROM nodes WHERE status = 'online'
    `).all() as Row[]).filter((row) => {
      const age = Date.now() - new Date(String(row.last_seen)).getTime();
      return Number.isFinite(age) && age <= 90_000;
    });
    const collections = rows.map((row) => ({
      id: String(row.id),
      name: String(row.name),
      description: String(row.description),
      embeddingModel: String(row.embedding_model),
      chunkSize: Number(row.chunk_size),
      chunkOverlap: Number(row.chunk_overlap),
      topK: Number(row.top_k),
      documentCount: Number(row.document_count),
      readyDocuments: Number(row.ready_documents),
      chunkCount: Number(row.chunk_count),
      embeddedChunks: Number(row.embedded_chunks),
      pendingJobs: Number(row.pending_jobs),
      embeddingWorkers: freshEmbeddingWorkers.filter((node) =>
        normalizeEmbeddingModels(parseJson<unknown>(node.embedding_models_json, [])).includes(String(row.embedding_model))
      ).length,
      createdAt: String(row.created_at),
      updatedAt: String(row.updated_at),
    }));
    const activeMemory = Number((this.db.prepare(`
      SELECT COUNT(*) AS count FROM memory_entries
      WHERE project_id = ? AND (expires_at IS NULL OR expires_at > ?)
    `).get(project, nowIso()) as Row).count);
    return {
      collections,
      counts: {
        collections: collections.length,
        documents: collections.reduce((sum, collection) => sum + collection.documentCount, 0),
        readyDocuments: collections.reduce((sum, collection) => sum + collection.readyDocuments, 0),
        chunks: collections.reduce((sum, collection) => sum + collection.chunkCount, 0),
        embeddedChunks: collections.reduce((sum, collection) => sum + collection.embeddedChunks, 0),
        pendingJobs: collections.reduce((sum, collection) => sum + collection.pendingJobs, 0),
        activeMemory,
      },
    };
  }

  getKnowledgeSnapshot(projectId = "default"): Record<string, unknown> {
    const project = this.requireProject(projectId);
    const overview = this.knowledgeOverview(project);
    const documents = (this.db.prepare(`
      SELECT d.*, c.name AS collection_name, c.embedding_model
      FROM knowledge_documents d
      JOIN knowledge_collections c ON c.id = d.collection_id
      WHERE c.project_id = ?
      ORDER BY d.created_at DESC
      LIMIT 500
    `).all(project) as Row[]).map((row) => this.knowledgeDocumentDto(row));
    const memory = (this.db.prepare(`
      SELECT m.*, a.name AS agent_name
      FROM memory_entries m
      LEFT JOIN agents a ON a.id = m.agent_id
      WHERE m.project_id = ? AND (m.expires_at IS NULL OR m.expires_at > ?)
      ORDER BY m.created_at DESC
      LIMIT 500
    `).all(project, nowIso()) as Row[]).map((row) => this.memoryDto(row));
    return { ...overview, documents, memory };
  }

  createKnowledgeCollection(
    input: CreateKnowledgeCollectionInput,
    projectId = "default",
  ): Record<string, unknown> {
    const project = this.requireProject(projectId);
    const collection = normalizeKnowledgeCollectionInput(input);
    const id = randomUUID();
    const timestamp = nowIso();
    try {
      this.db.prepare(`
        INSERT INTO knowledge_collections(
          id, project_id, name, description, embedding_model,
          chunk_size, chunk_overlap, top_k, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        id,
        project,
        collection.name,
        collection.description,
        collection.embeddingModel,
        collection.chunkSize,
        collection.chunkOverlap,
        collection.topK,
        timestamp,
        timestamp,
      );
    } catch (error) {
      if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
        throw new Error("Коллекция с таким названием уже существует в проекте");
      }
      throw error;
    }
    this.addEvent(null, null, null, "info", "knowledge.collection.created", `Создана коллекция «${collection.name}»`, {
      projectId: project,
      collectionId: id,
      embeddingModel: collection.embeddingModel,
    });
    return (this.knowledgeOverview(project).collections as Array<Record<string, unknown>>)
      .find((candidate) => candidate.id === id)!;
  }

  deleteKnowledgeCollection(collectionId: string, projectId = "default"): boolean {
    const project = this.requireProject(projectId);
    const row = this.db.prepare("SELECT name FROM knowledge_collections WHERE id = ? AND project_id = ?")
      .get(collectionId, project) as Row | undefined;
    if (!row) return false;
    if (this.knowledgeCollectionUsedByActiveRun(project, collectionId)) {
      throw new Error("Нельзя удалить collection, пока её использует активный запуск");
    }
    const result = this.db.prepare("DELETE FROM knowledge_collections WHERE id = ? AND project_id = ?")
      .run(collectionId, project);
    if (result.changes === 1) {
      this.addEvent(null, null, null, "warn", "knowledge.collection.deleted", `Удалена коллекция «${String(row.name)}»`, {
        projectId: project,
        collectionId,
      });
    }
    return result.changes === 1;
  }

  ingestKnowledgeDocument(
    collectionId: string,
    input: IngestKnowledgeDocumentInput,
    projectId = "default",
  ): Record<string, unknown> {
    const project = this.requireProject(projectId);
    const collection = this.db.prepare(`
      SELECT * FROM knowledge_collections WHERE id = ? AND project_id = ?
    `).get(collectionId, project) as Row | undefined;
    if (!collection) throw new Error("Knowledge collection не найдена");
    const document = normalizeKnowledgeDocumentInput(input);
    const chunks = chunkKnowledgeText(document.content, Number(collection.chunk_size), Number(collection.chunk_overlap));
    if (chunks.length === 0) throw new Error("Документ не содержит индексируемого текста");
    const documentId = randomUUID();
    const timestamp = nowIso();
    try {
      this.transaction(() => {
        this.db.prepare(`
          INSERT INTO knowledge_documents(
            id, collection_id, name, source_uri, media_type, content_sha256,
            content, status, chunk_count, embedded_count, created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?)
        `).run(
          documentId,
          collectionId,
          document.name,
          document.sourceUri,
          document.mediaType,
          sha256Text(document.content),
          document.content,
          chunks.length,
          timestamp,
          timestamp,
        );
        const insertChunk = this.db.prepare(`
          INSERT INTO knowledge_chunks(
            id, collection_id, document_id, ordinal, content, content_sha256,
            char_start, char_end, created_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        `);
        for (const chunk of chunks) {
          insertChunk.run(
            randomUUID(),
            collectionId,
            documentId,
            chunk.ordinal,
            chunk.content,
            sha256Text(chunk.content),
            chunk.charStart,
            chunk.charEnd,
            timestamp,
          );
        }
        this.db.prepare(`
          INSERT INTO knowledge_embedding_jobs(
            id, project_id, collection_id, document_id, status, created_at, updated_at
          ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
        `).run(randomUUID(), project, collectionId, documentId, timestamp, timestamp);
      });
    } catch (error) {
      if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
        throw new Error("Документ с таким названием уже существует в коллекции");
      }
      throw error;
    }
    this.addEvent(null, null, null, "info", "knowledge.document.queued", `Документ «${document.name}» поставлен на локальную индексацию`, {
      projectId: project,
      collectionId,
      documentId,
      chunks: chunks.length,
      contentSha256: sha256Text(document.content),
    });
    const row = this.db.prepare(`
      SELECT d.*, c.name AS collection_name, c.embedding_model
      FROM knowledge_documents d JOIN knowledge_collections c ON c.id = d.collection_id
      WHERE d.id = ?
    `).get(documentId) as Row;
    return this.knowledgeDocumentDto(row);
  }

  deleteKnowledgeDocument(documentId: string, projectId = "default"): boolean {
    const project = this.requireProject(projectId);
    const row = this.db.prepare(`
      SELECT d.name, d.collection_id FROM knowledge_documents d
      JOIN knowledge_collections c ON c.id = d.collection_id
      WHERE d.id = ? AND c.project_id = ?
    `).get(documentId, project) as Row | undefined;
    if (!row) return false;
    if (this.knowledgeCollectionUsedByActiveRun(project, String(row.collection_id))) {
      throw new Error("Нельзя удалить document, пока его collection использует активный запуск");
    }
    const result = this.db.prepare("DELETE FROM knowledge_documents WHERE id = ?").run(documentId);
    if (result.changes === 1) {
      this.addEvent(null, null, null, "warn", "knowledge.document.deleted", `Удалён документ «${String(row.name)}»`, {
        projectId: project,
        collectionId: row.collection_id,
        documentId,
      });
    }
    return result.changes === 1;
  }

  saveMemory(input: SaveMemoryInput, projectId = "default"): Record<string, unknown> {
    const project = this.requireProject(projectId);
    const memory = normalizeMemoryInput(input);
    if (memory.agentId && !this.knownAgentIds(project).has(memory.agentId)) {
      throw new Error("Агент для memory entry не найден в проекте");
    }
    const id = randomUUID();
    const timestamp = nowIso();
    const expiresAt = memory.ttlSeconds === null
      ? null
      : new Date(Date.now() + memory.ttlSeconds * 1_000).toISOString();
    this.db.prepare(`
      INSERT INTO memory_entries(
        id, project_id, agent_id, kind, content, content_sha256,
        expires_at, explicitly_saved, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
    `).run(
      id,
      project,
      memory.agentId,
      memory.kind,
      memory.content,
      sha256Text(memory.content),
      expiresAt,
      timestamp,
      timestamp,
    );
    this.addEvent(null, null, null, "info", "memory.saved", `${memory.kind === "episodic" ? "Episodic" : "Working"} memory сохранена явно`, {
      projectId: project,
      memoryId: id,
      kind: memory.kind,
      agentId: memory.agentId,
      expiresAt,
      contentSha256: sha256Text(memory.content),
    });
    const row = this.db.prepare(`
      SELECT m.*, a.name AS agent_name FROM memory_entries m
      LEFT JOIN agents a ON a.id = m.agent_id WHERE m.id = ?
    `).get(id) as Row;
    return this.memoryDto(row);
  }

  deleteMemory(memoryId: string, projectId = "default"): boolean {
    const project = this.requireProject(projectId);
    const row = this.db.prepare("SELECT kind FROM memory_entries WHERE id = ? AND project_id = ?")
      .get(memoryId, project) as Row | undefined;
    if (!row) return false;
    const result = this.db.prepare("DELETE FROM memory_entries WHERE id = ? AND project_id = ?")
      .run(memoryId, project);
    if (result.changes === 1) {
      this.addEvent(null, null, null, "warn", "memory.deleted", "Memory entry удалена по явному запросу", {
        projectId: project,
        memoryId,
        kind: row.kind,
      });
    }
    return result.changes === 1;
  }

  exportKnowledge(projectId = "default"): Record<string, unknown> {
    const project = this.requireProject(projectId);
    const projectRow = this.db.prepare("SELECT id, name FROM projects WHERE id = ?").get(project) as Row;
    const collections = this.db.prepare(`
      SELECT * FROM knowledge_collections WHERE project_id = ? ORDER BY created_at
    `).all(project) as Row[];
    return {
      schemaVersion: 1,
      exportedAt: nowIso(),
      project: { id: String(projectRow.id), name: String(projectRow.name) },
      collections: collections.map((collection) => ({
        id: String(collection.id),
        name: String(collection.name),
        description: String(collection.description),
        embeddingModel: String(collection.embedding_model),
        chunkSize: Number(collection.chunk_size),
        chunkOverlap: Number(collection.chunk_overlap),
        topK: Number(collection.top_k),
        documents: (this.db.prepare(`
          SELECT * FROM knowledge_documents WHERE collection_id = ? ORDER BY created_at
        `).all(String(collection.id)) as Row[]).map((document) => ({
          id: String(document.id),
          name: String(document.name),
          sourceUri: String(document.source_uri) || null,
          mediaType: String(document.media_type),
          contentSha256: String(document.content_sha256),
          content: String(document.content),
          status: String(document.status),
          chunks: (this.db.prepare(`
            SELECT id, ordinal, content, content_sha256, char_start, char_end,
              embedding_model, embedding_dimensions, embedded_at
            FROM knowledge_chunks WHERE document_id = ? ORDER BY ordinal
          `).all(String(document.id)) as Row[]).map((chunk) => ({
            id: String(chunk.id),
            ordinal: Number(chunk.ordinal),
            content: String(chunk.content),
            contentSha256: String(chunk.content_sha256),
            charStart: Number(chunk.char_start),
            charEnd: Number(chunk.char_end),
            embeddingModel: typeof chunk.embedding_model === "string" ? chunk.embedding_model : null,
            embeddingDimensions: chunk.embedding_dimensions === null ? null : Number(chunk.embedding_dimensions),
            embeddedAt: typeof chunk.embedded_at === "string" ? chunk.embedded_at : null,
          })),
        })),
      })),
      memory: (this.db.prepare(`
        SELECT m.*, a.name AS agent_name FROM memory_entries m
        LEFT JOIN agents a ON a.id = m.agent_id
        WHERE m.project_id = ? ORDER BY m.created_at
      `).all(project) as Row[]).map((row) => this.memoryDto(row)),
      retrievals: (this.db.prepare(`
        SELECT id, run_id, stage_id, node_id, query_sha256, queries_json, hits_json, created_at
        FROM knowledge_retrievals WHERE project_id = ? ORDER BY created_at DESC LIMIT 1000
      `).all(project) as Row[]).map((row) => ({
        id: String(row.id),
        runId: String(row.run_id),
        stageId: String(row.stage_id),
        nodeId: String(row.node_id),
        querySha256: String(row.query_sha256),
        queries: parseJson<unknown[]>(row.queries_json, []),
        hits: parseJson<unknown[]>(row.hits_json, []),
        createdAt: String(row.created_at),
      })),
    };
  }

  leaseKnowledgeEmbedding(nodeId: string): KnowledgeEmbeddingLease | null {
    this.cleanupExpiredKnowledgeLeases();
    return this.transaction(() => {
      const node = this.db.prepare("SELECT * FROM nodes WHERE id = ?").get(nodeId) as Row | undefined;
      if (!node) throw new Error("Узел не найден");
      const embeddingModels = normalizeEmbeddingModels(parseJson<unknown>(node.embedding_models_json, []));
      if (embeddingModels.length === 0) return null;
      const used = Number((this.db.prepare(`
        SELECT
          (SELECT COUNT(*) FROM stages WHERE node_id = ? AND status = 'running')
          + (SELECT COUNT(*) FROM knowledge_embedding_jobs WHERE node_id = ? AND status = 'running') AS count
      `).get(nodeId, nodeId) as Row).count);
      if (used >= Number(node.max_concurrency)) return null;
      const schedulerMode = (this.getSetting("scheduler_mode") ?? "sequential") as SchedulerMode;
      const globalActive = Number((this.db.prepare(`
        SELECT
          (SELECT COUNT(*) FROM stages WHERE status = 'running')
          + (SELECT COUNT(*) FROM knowledge_embedding_jobs WHERE status = 'running') AS count
      `).get() as Row).count);
      const globalMax = Number(this.getSetting("global_max_concurrency") ?? "1");
      if (schedulerMode === "sequential" && globalActive >= globalMax) return null;
      if (schedulerMode === "auto") {
        const metrics = parseJson<WorkerMetrics>(node.metrics_json, {});
        if ((metrics.cpuPercent ?? 0) >= 90 || (metrics.memoryPercent ?? 0) >= 92) return null;
        if (metrics.onBattery && (metrics.batteryPercent ?? 100) < 30) return null;
      }
      const jobs = this.db.prepare(`
        SELECT j.*, c.name AS collection_name, c.embedding_model,
          d.name AS document_name
        FROM knowledge_embedding_jobs j
        JOIN knowledge_collections c ON c.id = j.collection_id
        JOIN knowledge_documents d ON d.id = j.document_id
        WHERE j.status = 'pending' AND j.failures < j.max_failures
        ORDER BY j.created_at ASC
        LIMIT 100
      `).all() as Row[];
      const job = jobs.find((candidate) => embeddingModels.includes(String(candidate.embedding_model)));
      if (!job) return null;
      const chunks = this.db.prepare(`
        SELECT id, ordinal, content FROM knowledge_chunks
        WHERE document_id = ? AND embedding_json IS NULL
        ORDER BY ordinal LIMIT ?
      `).all(String(job.document_id), KNOWLEDGE_EMBEDDING_BATCH_SIZE) as Row[];
      if (chunks.length === 0) {
        const timestamp = nowIso();
        this.db.prepare("UPDATE knowledge_embedding_jobs SET status = 'completed', updated_at = ? WHERE id = ?")
          .run(timestamp, String(job.id));
        this.db.prepare("UPDATE knowledge_documents SET status = 'ready', embedded_count = chunk_count, updated_at = ? WHERE id = ?")
          .run(timestamp, String(job.document_id));
        return null;
      }
      const leaseId = randomUUID();
      const timestamp = nowIso();
      const expiresAt = futureIso(this.leaseTtlSeconds);
      const updated = this.db.prepare(`
        UPDATE knowledge_embedding_jobs
        SET status = 'running', node_id = ?, lease_id = ?, lease_expires_at = ?, updated_at = ?
        WHERE id = ? AND status = 'pending'
      `).run(nodeId, leaseId, expiresAt, timestamp, String(job.id));
      if (updated.changes !== 1) return null;
      this.db.prepare("UPDATE knowledge_documents SET status = 'indexing', error = NULL, updated_at = ? WHERE id = ?")
        .run(timestamp, String(job.document_id));
      this.addEvent(null, null, nodeId, "info", "knowledge.embedding.started", `Локальный узел начал индексацию «${String(job.document_name)}»`, {
        projectId: String(job.project_id),
        collectionId: String(job.collection_id),
        documentId: String(job.document_id),
        embeddingModel: String(job.embedding_model),
        chunks: chunks.length,
      });
      return {
        leaseId,
        expiresAt,
        projectId: String(job.project_id),
        collection: {
          id: String(job.collection_id),
          name: String(job.collection_name),
          embeddingModel: String(job.embedding_model),
        },
        document: { id: String(job.document_id), name: String(job.document_name) },
        chunks: chunks.map((chunk) => ({
          id: String(chunk.id),
          ordinal: Number(chunk.ordinal),
          content: String(chunk.content),
        })),
      };
    });
  }

  renewKnowledgeEmbeddingLease(nodeId: string, leaseId: string): boolean {
    const result = this.db.prepare(`
      UPDATE knowledge_embedding_jobs SET lease_expires_at = ?, updated_at = ?
      WHERE node_id = ? AND lease_id = ? AND status = 'running'
    `).run(futureIso(this.leaseTtlSeconds), nowIso(), nodeId, leaseId);
    return result.changes === 1;
  }

  completeKnowledgeEmbedding(
    nodeId: string,
    leaseId: string,
    results: KnowledgeEmbeddingResult[],
  ): { completed: boolean; remainingChunks: number } {
    if (!Array.isArray(results) || results.length < 1 || results.length > KNOWLEDGE_EMBEDDING_BATCH_SIZE) {
      throw new Error(`Нужно вернуть от 1 до ${KNOWLEDGE_EMBEDDING_BATCH_SIZE} embeddings`);
    }
    return this.transaction(() => {
      const job = this.db.prepare(`
        SELECT j.*, c.embedding_model, d.name AS document_name
        FROM knowledge_embedding_jobs j
        JOIN knowledge_collections c ON c.id = j.collection_id
        JOIN knowledge_documents d ON d.id = j.document_id
        WHERE j.node_id = ? AND j.lease_id = ? AND j.status = 'running'
      `).get(nodeId, leaseId) as Row | undefined;
      if (!job) throw new Error("Активная embedding-аренда не найдена");
      const expected = this.db.prepare(`
        SELECT id FROM knowledge_chunks
        WHERE document_id = ? AND embedding_json IS NULL
        ORDER BY ordinal LIMIT ?
      `).all(String(job.document_id), KNOWLEDGE_EMBEDDING_BATCH_SIZE) as Row[];
      if (results.length !== expected.length) throw new Error("Worker вернул неполный embedding batch");
      const byId = new Map(results.map((result) => [result.chunkId, normalizeEmbeddingVector(result.embedding)]));
      if (byId.size !== results.length || expected.some((chunk) => !byId.has(String(chunk.id)))) {
        throw new Error("Embedding batch содержит неизвестные или повторяющиеся chunks");
      }
      const existingDimensions = this.db.prepare(`
        SELECT embedding_dimensions FROM knowledge_chunks
        WHERE collection_id = ? AND embedding_dimensions IS NOT NULL LIMIT 1
      `).get(String(job.collection_id)) as Row | undefined;
      let dimensions = existingDimensions ? Number(existingDimensions.embedding_dimensions) : null;
      const timestamp = nowIso();
      const update = this.db.prepare(`
        UPDATE knowledge_chunks SET embedding_model = ?, embedding_json = ?,
          embedding_dimensions = ?, embedded_at = ?
        WHERE id = ? AND document_id = ? AND embedding_json IS NULL
      `);
      for (const chunk of expected) {
        const vector = byId.get(String(chunk.id))!;
        dimensions ??= vector.length;
        if (vector.length !== dimensions) throw new Error("Размерность embeddings в коллекции должна совпадать");
        const updated = update.run(
          String(job.embedding_model),
          JSON.stringify(vector),
          dimensions,
          timestamp,
          String(chunk.id),
          String(job.document_id),
        );
        if (updated.changes !== 1) throw new Error("Chunk уже был проиндексирован другой арендой");
      }
      const remaining = Number((this.db.prepare(`
        SELECT COUNT(*) AS count FROM knowledge_chunks WHERE document_id = ? AND embedding_json IS NULL
      `).get(String(job.document_id)) as Row).count);
      const embedded = Number((this.db.prepare(`
        SELECT COUNT(*) AS count FROM knowledge_chunks WHERE document_id = ? AND embedding_json IS NOT NULL
      `).get(String(job.document_id)) as Row).count);
      this.db.prepare(`
        UPDATE knowledge_documents SET status = ?, embedded_count = ?, error = NULL, updated_at = ? WHERE id = ?
      `).run(remaining === 0 ? "ready" : "indexing", embedded, timestamp, String(job.document_id));
      this.db.prepare(`
        UPDATE knowledge_embedding_jobs SET status = ?, node_id = NULL, lease_id = NULL,
          lease_expires_at = NULL, last_error = NULL, updated_at = ? WHERE id = ?
      `).run(remaining === 0 ? "completed" : "pending", timestamp, String(job.id));
      this.addEvent(null, null, nodeId, "info", remaining === 0 ? "knowledge.document.ready" : "knowledge.embedding.batch.completed", remaining === 0
        ? `Документ «${String(job.document_name)}» готов для retrieval`
        : `Embedding batch документа «${String(job.document_name)}» завершён`, {
        projectId: String(job.project_id),
        collectionId: String(job.collection_id),
        documentId: String(job.document_id),
        embeddedChunks: embedded,
        remainingChunks: remaining,
        dimensions,
      });
      return { completed: remaining === 0, remainingChunks: remaining };
    });
  }

  failKnowledgeEmbedding(nodeId: string, leaseId: string, errorMessage: string): { retrying: boolean } {
    return this.transaction(() => {
      const job = this.db.prepare(`
        SELECT j.*, d.name AS document_name FROM knowledge_embedding_jobs j
        JOIN knowledge_documents d ON d.id = j.document_id
        WHERE j.node_id = ? AND j.lease_id = ? AND j.status = 'running'
      `).get(nodeId, leaseId) as Row | undefined;
      if (!job) throw new Error("Активная embedding-аренда не найдена");
      const failures = Number(job.failures) + 1;
      const retrying = failures < Number(job.max_failures);
      const timestamp = nowIso();
      this.db.prepare(`
        UPDATE knowledge_embedding_jobs SET status = ?, node_id = NULL, lease_id = NULL,
          lease_expires_at = NULL, failures = ?, last_error = ?, updated_at = ? WHERE id = ?
      `).run(retrying ? "pending" : "failed", failures, errorMessage.slice(0, 4_000), timestamp, String(job.id));
      this.db.prepare(`
        UPDATE knowledge_documents SET status = ?, error = ?, updated_at = ? WHERE id = ?
      `).run(retrying ? "pending" : "failed", errorMessage.slice(0, 4_000), timestamp, String(job.document_id));
      this.addEvent(null, null, nodeId, retrying ? "warn" : "error", retrying ? "knowledge.embedding.retrying" : "knowledge.embedding.failed", `Индексация «${String(job.document_name)}» завершилась ошибкой`, {
        projectId: String(job.project_id),
        collectionId: String(job.collection_id),
        documentId: String(job.document_id),
        failures,
        maxFailures: Number(job.max_failures),
        error: errorMessage.slice(0, 1_000),
      });
      return { retrying };
    });
  }

  searchKnowledge(
    nodeId: string,
    leaseId: string,
    request: KnowledgeSearchRequest,
  ): { hits: KnowledgeSearchHit[] } {
    const stage = this.db.prepare(`
      SELECT s.id, s.run_id, s.agent_id, r.project_id, r.knowledge_collection_ids_json
      FROM stages s JOIN runs r ON r.id = s.run_id
      WHERE s.node_id = ? AND s.lease_id = ? AND s.status = 'running'
    `).get(nodeId, leaseId) as Row | undefined;
    if (!stage) throw new Error("Активная stage-аренда не найдена");
    if (!Array.isArray(request.queries) || request.queries.length < 1 || request.queries.length > 8) {
      throw new Error("Retrieval должен содержать от 1 до 8 embedding queries");
    }
    const project = String(stage.project_id);
    const allowedIds = new Set(normalizeKnowledgeCollectionIds(
      parseJson<unknown>(stage.knowledge_collection_ids_json, []),
    ));
    const bestByChunk = new Map<string, Omit<KnowledgeSearchHit, "marker">>();
    const storedQueries: Array<Record<string, unknown>> = [];
    for (const rawQuery of request.queries) {
      if (!rawQuery || typeof rawQuery !== "object") throw new Error("Некорректный retrieval query");
      const embeddingModel = typeof rawQuery.embeddingModel === "string" ? rawQuery.embeddingModel.trim() : "";
      if (!embeddingModel || embeddingModel.length > 200) throw new Error("Embedding-модель retrieval обязательна");
      const collectionIds = normalizeKnowledgeCollectionIds(rawQuery.collectionIds);
      if (collectionIds.length === 0 || collectionIds.some((id) => !allowedIds.has(id))) {
        throw new Error("Retrieval запросил коллекцию вне snapshot запуска");
      }
      const vector = normalizeEmbeddingVector(rawQuery.vector);
      const topK = clampInteger(rawQuery.topK, 1, 20, 6);
      const placeholders = collectionIds.map(() => "?").join(",");
      const candidates = this.db.prepare(`
        SELECT ch.*, d.name AS document_name, d.source_uri, d.content_sha256 AS document_sha256,
          c.name AS collection_name, c.embedding_model
        FROM knowledge_chunks ch
        JOIN knowledge_documents d ON d.id = ch.document_id
        JOIN knowledge_collections c ON c.id = ch.collection_id
        WHERE c.project_id = ? AND c.id IN (${placeholders})
          AND c.embedding_model = ? AND ch.embedding_model = ? AND ch.embedding_json IS NOT NULL
        ORDER BY ch.embedded_at DESC
        LIMIT ?
      `).all(project, ...collectionIds, embeddingModel, embeddingModel, KNOWLEDGE_MAX_SEARCH_CANDIDATES) as Row[];
      const ranked = candidates.flatMap((candidate): Array<Omit<KnowledgeSearchHit, "marker">> => {
        const stored = parseJson<unknown>(candidate.embedding_json, null);
        let storedVector: number[];
        try {
          storedVector = normalizeEmbeddingVector(stored);
        } catch {
          return [];
        }
        const score = cosineSimilarity(vector, storedVector);
        if (score === null) return [];
        return [{
          score: Math.round(score * 1_000_000) / 1_000_000,
          content: String(candidate.content),
          provenance: {
            collectionId: String(candidate.collection_id),
            collectionName: String(candidate.collection_name),
            documentId: String(candidate.document_id),
            documentName: String(candidate.document_name),
            sourceUri: typeof candidate.source_uri === "string" && candidate.source_uri ? candidate.source_uri : null,
            documentSha256: String(candidate.document_sha256),
            chunkId: String(candidate.id),
            chunkOrdinal: Number(candidate.ordinal),
            charStart: Number(candidate.char_start),
            charEnd: Number(candidate.char_end),
            chunkSha256: String(candidate.content_sha256),
          },
        }];
      }).sort((left, right) => right.score - left.score).slice(0, topK);
      for (const hit of ranked) {
        const current = bestByChunk.get(hit.provenance.chunkId);
        if (!current || hit.score > current.score) bestByChunk.set(hit.provenance.chunkId, hit);
      }
      storedQueries.push({
        embeddingModel,
        collectionIds,
        topK,
        dimensions: vector.length,
        vectorSha256: sha256Text(JSON.stringify(vector)),
      });
    }
    const hits = [...bestByChunk.values()]
      .sort((left, right) => right.score - left.score)
      .slice(0, 20)
      .map((hit, index): KnowledgeSearchHit => ({ marker: `K${index + 1}`, ...hit }));
    const retrievalId = randomUUID();
    const storedHits = hits.map((hit) => ({
      marker: hit.marker,
      score: hit.score,
      excerpt: hit.content.slice(0, 280),
      provenance: hit.provenance,
    }));
    const querySha256 = sha256Text(JSON.stringify(storedQueries));
    this.db.prepare(`
      INSERT INTO knowledge_retrievals(
        id, project_id, run_id, stage_id, node_id, query_sha256,
        queries_json, hits_json, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      retrievalId,
      project,
      String(stage.run_id),
      String(stage.id),
      nodeId,
      querySha256,
      JSON.stringify(storedQueries),
      JSON.stringify(storedHits),
      nowIso(),
    );
    this.addEvent(String(stage.run_id), String(stage.id), nodeId, "info", "knowledge.retrieved", `Local RAG выбрал ${hits.length} фрагмент(а) с provenance`, {
      kind: "knowledge",
      retrievalId,
      querySha256,
      queries: storedQueries,
      hits: storedHits,
    });
    return { hits };
  }

  private requireKnowledgeCollectionIds(projectId: string, value: unknown): string[] {
    const ids = normalizeKnowledgeCollectionIds(value);
    if (ids.length === 0) return ids;
    const placeholders = ids.map(() => "?").join(",");
    const rows = this.db.prepare(`
      SELECT id FROM knowledge_collections WHERE project_id = ? AND id IN (${placeholders})
    `).all(projectId, ...ids) as Row[];
    if (rows.length !== ids.length) throw new Error("Запуск содержит неизвестную knowledge collection");
    return ids;
  }

  private knowledgeCollectionUsedByActiveRun(projectId: string, collectionId: string): boolean {
    const rows = this.db.prepare(`
      SELECT knowledge_collection_ids_json FROM runs
      WHERE project_id = ? AND status IN ('queued', 'running', 'waiting_approval', 'waiting_external', 'compensating')
    `).all(projectId) as Row[];
    return rows.some((row) => normalizeKnowledgeCollectionIds(
      parseJson<unknown>(row.knowledge_collection_ids_json, []),
    ).includes(collectionId));
  }

  private leaseKnowledgeContext(
    projectId: string,
    agentId: string,
    rawCollectionIds: unknown,
  ): LeasePayload["knowledge"] {
    const requestedIds = normalizeKnowledgeCollectionIds(rawCollectionIds);
    const collections = requestedIds.length === 0 ? [] : this.db.prepare(`
      SELECT id, embedding_model, top_k FROM knowledge_collections
      WHERE project_id = ? AND id IN (${requestedIds.map(() => "?").join(",")})
      ORDER BY created_at
    `).all(projectId, ...requestedIds) as Row[];
    const groupsByModel = new Map<string, { embeddingModel: string; collectionIds: string[]; topK: number }>();
    for (const collection of collections) {
      const model = String(collection.embedding_model);
      const group = groupsByModel.get(model) ?? { embeddingModel: model, collectionIds: [], topK: 1 };
      group.collectionIds.push(String(collection.id));
      group.topK = Math.min(20, group.topK + Number(collection.top_k) - (group.collectionIds.length === 1 ? 1 : 0));
      groupsByModel.set(model, group);
    }
    this.cleanupExpiredMemory();
    const memoryRows = this.db.prepare(`
      SELECT * FROM memory_entries
      WHERE project_id = ? AND (agent_id IS NULL OR agent_id = ?)
        AND (expires_at IS NULL OR expires_at > ?)
      ORDER BY CASE kind WHEN 'episodic' THEN 0 ELSE 1 END, created_at DESC
      LIMIT 40
    `).all(projectId, agentId, nowIso()) as Row[];
    let memoryCharacters = 0;
    const memory = memoryRows.flatMap((row) => {
      const content = String(row.content);
      if (memoryCharacters + content.length > 20_000) return [];
      memoryCharacters += content.length;
      return [{
        id: String(row.id),
        kind: String(row.kind) as "working" | "episodic",
        content,
        agentId: typeof row.agent_id === "string" ? row.agent_id : null,
        expiresAt: typeof row.expires_at === "string" ? row.expires_at : null,
        createdAt: String(row.created_at),
      }];
    });
    return { groups: [...groupsByModel.values()], memory };
  }

  private cleanupExpiredKnowledgeLeases(): void {
    const expired = this.db.prepare(`
      SELECT * FROM knowledge_embedding_jobs
      WHERE status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
    `).all(nowIso()) as Row[];
    if (expired.length === 0) return;
    const timestamp = nowIso();
    for (const job of expired) {
      const failures = Number(job.failures) + 1;
      const retrying = failures < Number(job.max_failures);
      this.db.prepare(`
        UPDATE knowledge_embedding_jobs SET status = ?, node_id = NULL, lease_id = NULL,
          lease_expires_at = NULL, failures = ?, last_error = 'Embedding lease expired', updated_at = ?
        WHERE id = ? AND status = 'running'
      `).run(retrying ? "pending" : "failed", failures, timestamp, String(job.id));
      this.db.prepare(`
        UPDATE knowledge_documents SET status = ?, error = 'Embedding lease expired', updated_at = ? WHERE id = ?
      `).run(retrying ? "pending" : "failed", timestamp, String(job.document_id));
    }
  }

  private cleanupExpiredMemory(): void {
    this.db.prepare("DELETE FROM memory_entries WHERE expires_at IS NOT NULL AND expires_at <= ?").run(nowIso());
  }

  private knowledgeDocumentDto(row: Row): Record<string, unknown> {
    return {
      id: String(row.id),
      collectionId: String(row.collection_id),
      collectionName: typeof row.collection_name === "string" ? row.collection_name : null,
      embeddingModel: typeof row.embedding_model === "string" ? row.embedding_model : null,
      name: String(row.name),
      sourceUri: typeof row.source_uri === "string" && row.source_uri ? row.source_uri : null,
      mediaType: String(row.media_type),
      contentSha256: String(row.content_sha256),
      status: String(row.status),
      error: typeof row.error === "string" ? row.error : null,
      chunkCount: Number(row.chunk_count),
      embeddedCount: Number(row.embedded_count),
      createdAt: String(row.created_at),
      updatedAt: String(row.updated_at),
    };
  }

  private memoryDto(row: Row): Record<string, unknown> {
    return {
      id: String(row.id),
      kind: String(row.kind),
      content: String(row.content),
      contentSha256: String(row.content_sha256),
      agentId: typeof row.agent_id === "string" ? row.agent_id : null,
      agentName: typeof row.agent_name === "string" ? row.agent_name : null,
      expiresAt: typeof row.expires_at === "string" ? row.expires_at : null,
      explicitlySaved: Number(row.explicitly_saved) === 1,
      createdAt: String(row.created_at),
      updatedAt: String(row.updated_at),
    };
  }

  listProcesses(projectId = "default"): Array<Record<string, unknown>> {
    const project = normalizeProjectId(projectId);
    const rows = this.db
      .prepare(`
        SELECT
          p.*,
          (SELECT COUNT(*) FROM process_instances pi WHERE pi.process_id = p.id) AS total_instances,
          (SELECT COUNT(*) FROM process_instances pi
            WHERE pi.process_id = p.id AND pi.status IN ('queued', 'running', 'waiting_approval', 'waiting_external', 'compensating')) AS active_instances,
          (SELECT MAX(pi.created_at) FROM process_instances pi WHERE pi.process_id = p.id) AS last_started_at
        FROM processes p
        WHERE p.project_id = ?
        ORDER BY p.updated_at DESC, p.name COLLATE NOCASE ASC
      `)
      .all(project) as Row[];
    return rows.map((row) => this.processDto(row));
  }

  getProcess(processId: string, projectId = "default"): Record<string, unknown> | null {
    const project = normalizeProjectId(projectId);
    const row = this.db
      .prepare(`
        SELECT
          p.*,
          (SELECT COUNT(*) FROM process_instances pi WHERE pi.process_id = p.id) AS total_instances,
          (SELECT COUNT(*) FROM process_instances pi
            WHERE pi.process_id = p.id AND pi.status IN ('queued', 'running', 'waiting_approval', 'waiting_external', 'compensating')) AS active_instances,
          (SELECT MAX(pi.created_at) FROM process_instances pi WHERE pi.process_id = p.id) AS last_started_at
        FROM processes p
        WHERE p.id = ? AND p.project_id = ?
      `)
      .get(processId, project) as Row | undefined;
    return row ? this.processDto(row) : null;
  }

  createProcess(input: CreateProcessInput, projectId = "default"): Record<string, unknown> {
    const project = this.requireProject(projectId);
    const identity = normalizeProcessIdentity(input.name, input.description);
    const duplicate = this.db
      .prepare("SELECT id FROM processes WHERE name = ? COLLATE NOCASE AND project_id = ?")
      .get(identity.name, project) as Row | undefined;
    if (duplicate) throw new Error("Процесс с таким названием уже существует");
    let sourceGraph = input.graph;
    if (!sourceGraph && input.templateId) {
      const template = this.db.prepare(`
        SELECT * FROM processes WHERE id = ? AND project_id = ? AND is_template = 1
      `).get(input.templateId, project) as Row | undefined;
      if (!template) throw new Error("Шаблон процесса не найден");
      if (Number(template.published_version) > 0) {
        const version = this.db.prepare(`
          SELECT graph_json FROM process_versions WHERE process_id = ? AND version = ?
        `).get(input.templateId, Number(template.published_version)) as Row | undefined;
        sourceGraph = parseJson<ProcessGraph>(version?.graph_json, { nodes: [], edges: [] });
      } else {
        sourceGraph = parseJson<ProcessGraph>(template.draft_graph_json, { nodes: [], edges: [] });
      }
    }
    const graph = normalizeProcessGraph(sourceGraph ?? defaultProcessGraph(), this.knownAgentIds(project));
    const processId = randomUUID();
    const timestamp = nowIso();
    this.db
      .prepare(`
        INSERT INTO processes(
          id, name, description, is_template, status, draft_graph_json, published_version,
          has_unpublished_changes, project_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'draft', ?, 0, 1, ?, ?, ?)
      `)
      .run(processId, identity.name, identity.description, input.isTemplate === true ? 1 : 0, JSON.stringify(graph), project, timestamp, timestamp);
    this.addEvent(null, null, null, "info", "process.created", `Создан процесс «${identity.name}»`, {
      processId,
      templateId: input.templateId ?? null,
      isTemplate: input.isTemplate === true,
    });
    return this.getProcess(processId, project)!;
  }

  updateProcess(processId: string, input: UpdateProcessInput, projectId = "default"): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const existing = this.db.prepare("SELECT id, is_template FROM processes WHERE id = ? AND project_id = ?").get(processId, project) as Row | undefined;
    if (!existing) return null;
    const identity = normalizeProcessIdentity(input.name, input.description);
    const duplicate = this.db
      .prepare("SELECT id FROM processes WHERE name = ? COLLATE NOCASE AND id <> ? AND project_id = ?")
      .get(identity.name, processId, project) as Row | undefined;
    if (duplicate) throw new Error("Процесс с таким названием уже существует");
    const graph = normalizeProcessDraftGraph(input.graph, this.knownAgentIds(project));
    const timestamp = nowIso();
    this.db
      .prepare(`
        UPDATE processes
        SET name = ?, description = ?, is_template = ?, draft_graph_json = ?,
            has_unpublished_changes = 1, updated_at = ?
        WHERE id = ?
      `)
      .run(
        identity.name,
        identity.description,
        input.isTemplate === undefined ? Number(existing.is_template) : input.isTemplate === true ? 1 : 0,
        JSON.stringify(graph),
        timestamp,
        processId,
      );
    this.addEvent(null, null, null, "info", "process.updated", `Сохранён черновик процесса «${identity.name}»`, {
      processId,
      nodeCount: graph.nodes.length,
      edgeCount: graph.edges.length,
    });
    return this.getProcess(processId, project)!;
  }

  publishProcess(processId: string, projectId = "default"): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    return this.transaction(() => {
      const process = this.db.prepare("SELECT * FROM processes WHERE id = ? AND project_id = ?").get(processId, project) as Row | undefined;
      if (!process) return null;
      if (Number(process.published_version) > 0 && Number(process.has_unpublished_changes) === 0) {
        return this.getProcess(processId, project)!;
      }
      let graph = normalizeProcessGraph(process.draft_graph_json
        ? parseJson<ProcessGraph>(process.draft_graph_json, defaultProcessGraph())
        : defaultProcessGraph(), this.knownAgentIds(project));
      const knownCredentials = new Set(this.listCredentials(project)
        .filter((credential) => (credential.scope as CredentialScope).kind === "project")
        .map((credential) => String(credential.id)));
      const missingCredential = graph.nodes.find((node) => node.config.credentialId && !knownCredentials.has(node.config.credentialId));
      if (missingCredential) throw new Error(`Шаг ${missingCredential.name}: credentials не найдены`);
      const missingCompensationCredential = graph.nodes.find((node) =>
        node.config.compensation?.credentialId && !knownCredentials.has(node.config.compensation.credentialId)
      );
      if (missingCompensationCredential) throw new Error(`Шаг ${missingCompensationCredential.name}: compensation credentials не найдены`);
      graph = this.pinSubprocessVersions(processId, graph, project);
      const version = Number(process.published_version) + 1;
      const timestamp = nowIso();
      this.db
        .prepare(`
          INSERT INTO process_versions(process_id, version, name, description, graph_json, published_at)
          VALUES (?, ?, ?, ?, ?, ?)
        `)
        .run(processId, version, String(process.name), String(process.description), JSON.stringify(graph), timestamp);
      this.db
        .prepare(`
          UPDATE processes
          SET status = 'published', published_version = ?, has_unpublished_changes = 0,
              published_at = ?, updated_at = ?
          WHERE id = ?
        `)
        .run(version, timestamp, timestamp, processId);
      this.addEvent(null, null, null, "info", "process.published", `Опубликован процесс «${String(process.name)}» v${version}`, {
        processId,
        version,
      });
      return this.getProcess(processId, project)!;
    });
  }

  getProcessVersion(
    processId: string,
    version: number | "draft",
    projectId = "default",
  ): Record<string, unknown> | null {
    const project = normalizeProjectId(projectId);
    const process = this.db.prepare("SELECT * FROM processes WHERE id = ? AND project_id = ?")
      .get(processId, project) as Row | undefined;
    if (!process) return null;
    if (version === "draft") {
      return {
        processId,
        version,
        name: process.name,
        description: process.description,
        graph: parseJson<ProcessGraph>(process.draft_graph_json, { nodes: [], edges: [] }),
        publishedAt: null,
      };
    }
    const row = this.db.prepare(`
      SELECT * FROM process_versions WHERE process_id = ? AND version = ?
    `).get(processId, version) as Row | undefined;
    if (!row) return null;
    return {
      processId,
      version,
      name: row.name,
      description: row.description,
      graph: parseJson<ProcessGraph>(row.graph_json, { nodes: [], edges: [] }),
      publishedAt: row.published_at,
    };
  }

  diffProcessVersions(
    processId: string,
    from: number | "draft",
    to: number | "draft",
    projectId = "default",
  ): ProcessVersionDiff | null {
    const before = this.getProcessVersion(processId, from, projectId);
    const after = this.getProcessVersion(processId, to, projectId);
    if (!before || !after) return null;
    return diffProcessDocuments({
      processId,
      from,
      to,
      before: {
        name: String(before.name),
        description: String(before.description),
        graph: before.graph as ProcessGraph,
      },
      after: {
        name: String(after.name),
        description: String(after.description),
        graph: after.graph as ProcessGraph,
      },
    });
  }

  exportProcessBpmn(
    processId: string,
    version: number | "draft",
    projectId = "default",
  ): string | null {
    const document = this.getProcessVersion(processId, version, projectId);
    if (!document) return null;
    return serializeProcessBpmn({
      processId,
      version,
      name: String(document.name),
      graph: document.graph as ProcessGraph,
    });
  }

  importProcessBpmn(
    xml: string,
    overrides: { name?: string; description?: string; isTemplate?: boolean } = {},
    projectId = "default",
  ): Record<string, unknown> {
    const parsed = parseProcessBpmn(xml);
    return this.createProcess({
      name: overrides.name?.trim() || parsed.name,
      description: overrides.description ?? "Импортировано из BPMN 2.0",
      graph: parsed.graph,
      isTemplate: overrides.isTemplate === true,
    }, projectId);
  }

  listProcessWebhooks(processId: string, projectId = "default"): Array<Record<string, unknown>> {
    const project = normalizeProjectId(projectId);
    return (this.db.prepare(`
      SELECT id, process_id, name, kind, signal_name, default_input, enabled,
             created_at, updated_at, last_used_at
      FROM process_webhooks
      WHERE process_id = ? AND project_id = ?
      ORDER BY created_at DESC
    `).all(processId, project) as Row[]).map((row) => ({
      id: row.id,
      processId: row.process_id,
      name: row.name,
      kind: row.kind,
      signalName: row.signal_name,
      defaultInput: row.default_input,
      enabled: Number(row.enabled) === 1,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
      lastUsedAt: row.last_used_at,
    }));
  }

  createProcessWebhook(
    processId: string,
    input: CreateProcessWebhookInput,
    projectId = "default",
  ): Record<string, unknown> {
    const project = this.requireProject(projectId);
    const process = this.db.prepare(`SELECT id, published_version FROM processes WHERE id = ? AND project_id = ?`)
      .get(processId, project) as Row | undefined;
    if (!process) throw new Error("Процесс не найден");
    if (Number(process.published_version) < 1) throw new Error("Для webhook сначала опубликуйте процесс");
    const name = requiredAgentText(input.name, "Название webhook", 100);
    if (input.kind !== "start" && input.kind !== "signal") throw new Error("Неизвестный тип webhook");
    const signalName = input.kind === "signal" ? requiredAgentText(input.signalName, "Signal name", 128) : "";
    if (signalName && !/^[A-Za-z][A-Za-z0-9_.-]{0,127}$/.test(signalName)) throw new Error("Signal name задан некорректно");
    if (input.kind === "signal") {
      const version = this.db.prepare(`
        SELECT graph_json FROM process_versions WHERE process_id = ? AND version = ?
      `).get(processId, Number(process.published_version)) as Row | undefined;
      const graph = parseJson<ProcessGraph>(version?.graph_json, { nodes: [], edges: [] });
      if (!graph.nodes.some((node) => node.type === "signal" && node.config.signalName === signalName)) {
        throw new Error(`В опубликованной версии нет signal node «${signalName}»`);
      }
    }
    const defaultInput = optionalText(input.defaultInput, "Webhook input", 100_000);
    const token = createToken();
    const id = randomUUID();
    const timestamp = nowIso();
    this.db.prepare(`
      INSERT INTO process_webhooks(
        id, process_id, project_id, name, kind, signal_name, default_input,
        token_hash, enabled, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
    `).run(id, processId, project, name, input.kind, signalName || null, defaultInput, hashToken(token), timestamp, timestamp);
    this.addEvent(null, null, null, "info", "process.webhook.created", `Создан webhook «${name}»`, {
      processId,
      webhookId: id,
      kind: input.kind,
      signalName: signalName || null,
    });
    return { ...this.listProcessWebhooks(processId, project).find((item) => item.id === id)!, token };
  }

  rotateProcessWebhook(webhookId: string, projectId = "default"): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const row = this.db.prepare(`SELECT * FROM process_webhooks WHERE id = ? AND project_id = ?`)
      .get(webhookId, project) as Row | undefined;
    if (!row) return null;
    const token = createToken();
    const timestamp = nowIso();
    this.db.prepare(`UPDATE process_webhooks SET token_hash = ?, updated_at = ? WHERE id = ?`)
      .run(hashToken(token), timestamp, webhookId);
    this.addEvent(null, null, null, "warn", "process.webhook.rotated", `Ротирован webhook «${String(row.name)}»`, {
      processId: row.process_id,
      webhookId,
    });
    return { ...this.listProcessWebhooks(String(row.process_id), project).find((item) => item.id === webhookId)!, token };
  }

  deleteProcessWebhook(webhookId: string, projectId = "default"): boolean {
    const project = this.requireProject(projectId);
    const row = this.db.prepare(`SELECT process_id, name FROM process_webhooks WHERE id = ? AND project_id = ?`)
      .get(webhookId, project) as Row | undefined;
    if (!row) return false;
    this.db.prepare(`DELETE FROM process_webhooks WHERE id = ?`).run(webhookId);
    this.addEvent(null, null, null, "warn", "process.webhook.deleted", `Удалён webhook «${String(row.name)}»`, {
      processId: row.process_id,
      webhookId,
    });
    return true;
  }

  invokeProcessWebhook(
    webhookId: string,
    token: string,
    input: DeliverProcessWebhookInput,
    idempotencyKey: string | null,
  ): Record<string, unknown> | null {
    if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("Webhook payload должен быть объектом");
    if (input.instanceId !== undefined && (
      typeof input.instanceId !== "string"
      || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(input.instanceId)
    )) {
      throw new Error("instanceId задан некорректно");
    }
    if (input.correlationKey !== undefined && (
      typeof input.correlationKey !== "string"
      || input.correlationKey.length > 1_000
      || /[\u0000-\u001f\u007f]/u.test(input.correlationKey)
    )) {
      throw new Error("correlationKey задан некорректно");
    }
    return this.transaction(() => {
      const webhook = this.db.prepare(`SELECT * FROM process_webhooks WHERE id = ? AND enabled = 1`)
        .get(webhookId) as Row | undefined;
      if (!webhook || !tokensEqual(hashToken(token), String(webhook.token_hash))) return null;
      const normalizedKey = idempotencyKey?.trim() ?? "";
      if (normalizedKey.length > 200 || /[\u0000-\u001f\u007f]/u.test(normalizedKey)) {
        throw new Error("Idempotency-Key задан некорректно");
      }
      if (webhook.kind === "start" && !normalizedKey) throw new Error("Start webhook требует Idempotency-Key");
      if (normalizedKey) {
        const receipt = this.db.prepare(`
          SELECT response_json FROM process_webhook_receipts WHERE webhook_id = ? AND idempotency_key = ?
        `).get(webhookId, normalizedKey) as Row | undefined;
        if (receipt) return { ...parseJson<Record<string, unknown>>(receipt.response_json, {}), duplicate: true };
      }

      let result: Record<string, unknown>;
      if (webhook.kind === "start") {
        const rawInput = input.input ?? webhook.default_input;
        const processInput = typeof rawInput === "string" ? rawInput : JSON.stringify(rawInput);
        if (!processInput.trim()) throw new Error("Webhook process input обязателен");
        const instance = this.startProcessLocked(
          String(webhook.process_id),
          { input: processInput, priority: clampInteger(input.priority, 0, 100, 50) },
          String(webhook.project_id),
          { knowledgeCollectionIds: [] },
        );
        if (!instance) throw new Error("Процесс webhook не найден");
        result = {
          kind: "start",
          processId: webhook.process_id,
          projectId: webhook.project_id,
          instance,
          duplicate: false,
        };
      } else {
        const delivered = this.deliverProcessSignalLocked({
          processId: String(webhook.process_id),
          projectId: String(webhook.project_id),
          signalName: String(webhook.signal_name),
          instanceId: input.instanceId,
          correlationKey: input.correlationKey,
          payload: input.payload ?? input.input ?? null,
        });
        result = {
          kind: "signal",
          processId: webhook.process_id,
          projectId: webhook.project_id,
          ...delivered,
          duplicate: false,
        };
      }
      const timestamp = nowIso();
      this.db.prepare(`UPDATE process_webhooks SET last_used_at = ?, updated_at = ? WHERE id = ?`)
        .run(timestamp, timestamp, webhookId);
      if (normalizedKey) {
        this.db.prepare(`
          INSERT INTO process_webhook_receipts(webhook_id, idempotency_key, response_json, created_at)
          VALUES (?, ?, ?, ?)
        `).run(webhookId, normalizedKey, JSON.stringify(result), timestamp);
      }
      return result;
    });
  }

  deliverProcessSignal(
    processId: string,
    signalName: string,
    input: { instanceId?: string; correlationKey?: string; payload?: unknown },
    projectId = "default",
  ): { delivered: boolean; instanceIds: string[] } | null {
    const project = this.requireProject(projectId);
    const process = this.db.prepare("SELECT id FROM processes WHERE id = ? AND project_id = ?")
      .get(processId, project) as Row | undefined;
    if (!process) return null;
    if (!/^[A-Za-z][A-Za-z0-9_.-]{0,127}$/.test(signalName)) throw new Error("Signal name задан некорректно");
    if (input.instanceId !== undefined && (
      typeof input.instanceId !== "string"
      || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(input.instanceId)
    )) {
      throw new Error("instanceId задан некорректно");
    }
    if (input.correlationKey !== undefined && (
      typeof input.correlationKey !== "string"
      || input.correlationKey.length > 1_000
      || /[\u0000-\u001f\u007f]/u.test(input.correlationKey)
    )) {
      throw new Error("correlationKey задан некорректно");
    }
    return this.transaction(() => this.deliverProcessSignalLocked({
      processId,
      projectId: project,
      signalName,
      instanceId: input.instanceId,
      correlationKey: input.correlationKey,
      payload: input.payload ?? null,
    }));
  }

  recordProcessScheduleEvent(
    processId: string,
    projectId: string,
    type: string,
    message: string,
    data: Record<string, unknown>,
  ): void {
    const project = this.requireProject(projectId);
    const process = this.db.prepare("SELECT id FROM processes WHERE id = ? AND project_id = ?")
      .get(processId, project) as Row | undefined;
    if (!process) throw new Error("Процесс не найден");
    this.addEvent(null, null, null, "info", type, message, {
      ...data,
      processId,
      projectId: project,
    });
  }

  startProcess(processId: string, input: StartProcessInput, projectId = "default"): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const processInput = typeof input.input === "string" ? input.input.trim() : "";
    if (!processInput) throw new Error("Входные данные процесса обязательны");
    if (processInput.length > 100_000) throw new Error("Входные данные процесса: максимум 100000 символов");
    const knowledgeCollectionIds = this.requireKnowledgeCollectionIds(project, input.knowledgeCollectionIds);
    return this.transaction(() => this.startProcessLocked(
      processId,
      { ...input, input: processInput },
      project,
      { knowledgeCollectionIds },
    ));
  }

  private startProcessLocked(
    processId: string,
    input: StartProcessInput,
    project: string,
    options: {
      knowledgeCollectionIds: string[];
      version?: number;
      runtime?: "database" | "temporal" | "embedded";
      replayOfInstanceId?: string | null;
      replayMode?: "safe" | "live";
    },
  ): Record<string, unknown> | null {
      if (typeof input.input !== "string") throw new Error("Входные данные процесса должны быть строкой");
      const processInput = input.input.trim();
      if (!processInput) throw new Error("Входные данные процесса обязательны");
      if (processInput.length > 100_000) throw new Error("Входные данные процесса: максимум 100000 символов");
      const knowledgeCollectionIds = options.knowledgeCollectionIds;
      const process = this.db.prepare("SELECT * FROM processes WHERE id = ? AND project_id = ?").get(processId, project) as Row | undefined;
      if (!process) return null;
      const version = options.version ?? Number(process.published_version);
      if (version < 1) throw new Error("Сначала опубликуйте процесс");
      const versionRow = this.db
        .prepare("SELECT * FROM process_versions WHERE process_id = ? AND version = ?")
        .get(processId, version) as Row | undefined;
      if (!versionRow) throw new Error("Опубликованная версия процесса не найдена");
      const graph = parseJson<ProcessGraph>(versionRow.graph_json, { nodes: [], edges: [] });
      const runId = randomUUID();
      const instanceId = randomUUID();
      const timestamp = nowIso();
      const priority = clampInteger(input.priority, 0, 100, 50);
      const resultDestination = normalizeResultDestination(input.resultDestination);
      const artifactPath = resultDestination === "artifacts" ? normalizeArtifactPath(input.artifactPath) : "";
      const hasApproval = graph.nodes.some((node) =>
        node.type === "approval" || (node.type === "agent" && node.config.approvalRequired === true)
      );

      this.db
        .prepare(`
          INSERT INTO runs(
            id, name, input, status, execution_mode, priority, approval_required,
            result_destination, artifact_path, created_at, updated_at
            , project_id, knowledge_collection_ids_json
          ) VALUES (?, ?, ?, 'queued', 'sequential', ?, ?, ?, ?, ?, ?, ?, ?)
        `)
        .run(
          runId,
          String(versionRow.name),
          processInput,
          priority,
          hasApproval ? 1 : 0,
          resultDestination,
          artifactPath,
          timestamp,
          timestamp,
          project,
          JSON.stringify(knowledgeCollectionIds),
        );
      this.db
        .prepare(`
          INSERT INTO process_instances(
            id, process_id, process_version, run_id, graph_json, status,
            loop_counts_json, transition_count, runtime, workflow_id,
            replay_of_instance_id, replay_mode,
            created_at, updated_at, started_at
          ) VALUES (?, ?, ?, ?, ?, 'queued', '{}', 0, ?, ?, ?, ?, ?, ?, ?)
        `)
        .run(
          instanceId,
          processId,
          version,
          runId,
          JSON.stringify(graph),
          options.runtime ?? (this.temporalProcesses ? "temporal" : "database"),
          (options.runtime ?? (this.temporalProcesses ? "temporal" : "database")) === "temporal"
            ? `agat-process-${instanceId}`
            : null,
          options.replayOfInstanceId ?? null,
          options.replayMode ?? "live",
          timestamp,
          timestamp,
          timestamp,
        );
      this.addEvent(runId, null, null, "info", "run.created", "Экземпляр процесса добавлен в очередь", {
        processId,
        processInstanceId: instanceId,
        processVersion: version,
        priority,
        resultDestination,
        artifactPath: artifactPath || null,
        knowledgeCollectionIds,
        replayOfInstanceId: options.replayOfInstanceId ?? null,
        replayMode: options.replayMode ?? "live",
      });
      this.addEvent(runId, null, null, "info", "process.instance.started", `Запущен процесс v${version}`, {
        processId,
        processInstanceId: instanceId,
        processVersion: version,
      });
      const requestedStartNodeId = typeof input.startNodeId === "string" ? input.startNodeId.trim() : "";
      const start = requestedStartNodeId
        ? graph.nodes.find((node) => node.id === requestedStartNodeId)
        : graph.nodes.find((node) => node.type === "start");
      if (!start) {
        this.failProcessInstance(instanceId, runId, requestedStartNodeId
          ? "Выбранный стартовый шаг отсутствует в опубликованной версии"
          : "В опубликованной версии отсутствует старт");
      } else {
        if (requestedStartNodeId) {
          this.addEvent(runId, null, null, "info", "process.instance.resumed_from_node", `Запуск продолжен с шага «${start.name}»`, {
            processInstanceId: instanceId,
            processNodeId: start.id,
          });
        }
        const tokenId = randomUUID();
        this.db.prepare(`
          INSERT INTO process_tokens(
            id, instance_id, parent_token_id, fork_node_id, branch_edge_id,
            current_node_id, last_output, status, created_at, updated_at
          ) VALUES (?, ?, NULL, NULL, NULL, ?, ?, 'active', ?, ?)
        `).run(tokenId, instanceId, start.id, requestedStartNodeId ? processInput : null, timestamp, timestamp);
        this.advanceProcessToken(instanceId, tokenId, start.id, requestedStartNodeId ? processInput : null);
      }
      return this.getProcessInstance(instanceId, project)!;
  }

  replayProcessInstance(
    instanceId: string,
    input: ReplayProcessInstanceInput,
    projectId = "default",
  ): Record<string, unknown> | null {
    const project = this.requireProject(projectId);
    const mode = input.mode ?? "safe";
    if (mode !== "safe" && mode !== "live") throw new Error("Неизвестный режим replay процесса");
    return this.transaction(() => {
      const source = this.db.prepare(`
        SELECT pi.*, r.input AS run_input, r.priority, r.result_destination, r.artifact_path,
               r.knowledge_collection_ids_json
        FROM process_instances pi
        JOIN runs r ON r.id = pi.run_id
        JOIN processes p ON p.id = pi.process_id
        WHERE pi.id = ? AND p.project_id = ?
      `).get(instanceId, project) as Row | undefined;
      if (!source) return null;
      if (!["completed", "failed", "cancelled"].includes(String(source.status))) {
        throw new Error("Replay доступен только для завершённого экземпляра");
      }
      const replay = this.startProcessLocked(
        String(source.process_id),
        {
          input: String(source.run_input),
          priority: clampInteger(input.priority, 0, 100, Number(source.priority)),
          resultDestination: normalizeResultDestination(source.result_destination),
          artifactPath: String(source.artifact_path ?? ""),
          knowledgeCollectionIds: parseJson<string[]>(source.knowledge_collection_ids_json, []),
        },
        project,
        {
          knowledgeCollectionIds: this.requireKnowledgeCollectionIds(
            project,
            parseJson<string[]>(source.knowledge_collection_ids_json, []),
          ),
          version: Number(source.process_version),
          runtime: this.temporalProcesses ? "temporal" : "database",
          replayOfInstanceId: instanceId,
          replayMode: mode,
        },
      );
      if (replay) {
        this.addEvent(String(replay.runId), null, null, "info", "process.instance.replayed", `Replay экземпляра ${instanceId}`, {
          sourceInstanceId: instanceId,
          replayInstanceId: replay.id,
          processVersion: source.process_version,
          mode,
        });
      }
      return replay;
    });
  }

  testProcessNode(input: TestProcessNodeInput, projectId = "default"): TestProcessNodeResult {
    const project = this.requireProject(projectId);
    const testInput = typeof input.input === "string" ? input.input.trim() : "";
    if (!testInput) throw new Error("Тестовые входные данные обязательны");
    if (testInput.length > 100_000) throw new Error("Тестовые входные данные: максимум 100000 символов");
    const node = normalizeProcessDraftGraph({ nodes: [input.node], edges: [] }, this.knownAgentIds(project)).nodes[0];
    if (!node) throw new Error("Шаг для тестирования не найден");

    if (node.type === "agent") {
      if (!node.config.agentId) throw new Error("Для тестового шага выберите агента");
      const created = this.createRun({
        name: `Тест шага · ${node.name || "Агент"}`,
        input: testInput,
        executionMode: "sequential",
        priority: 50,
        approvalRequired: false,
        agentIds: [node.config.agentId],
        resultDestination: "history",
      }, project);
      const stage = this.db.prepare("SELECT id FROM stages WHERE run_id = ? ORDER BY position LIMIT 1").get(created.id) as Row;
      this.db.prepare("UPDATE stages SET process_node_id = ? WHERE id = ?").run(node.id, String(stage.id));
      this.addEvent(created.id, String(stage.id), null, "info", "process.node.test.created", `Создан тест шага «${node.name}»`, {
        processNodeId: node.id,
        agentId: node.config.agentId,
      });
      return { kind: "queued", runId: created.id, status: created.status, output: "", branch: null };
    }

    if (node.type === "http") {
      if (node.config.credentialId && !this.httpCredentialData(node.config.credentialId, project)) {
        throw new Error("Credentials HTTP-шага не найдены");
      }
      const context = { input: testInput, lastOutput: testInput, loopCounts: {} };
      const created = this.createRun({
        name: `Тест шага · ${node.name || "HTTP"}`,
        input: testInput,
        executionMode: "sequential",
        priority: 50,
        approvalRequired: false,
        agentIds: ["__agat_system__"],
        resultDestination: "history",
      }, project);
      const activity = {
        credentialId: node.config.credentialId ?? "",
        request: {
          method: node.config.method ?? "GET",
          url: renderProcessTemplate(node.config.url ?? "", context),
          headers: Object.fromEntries(Object.entries(node.config.headers ?? {}).map(([name, value]) => [
            name,
            renderProcessTemplate(value, context),
          ])),
          body: ["GET", "DELETE"].includes(node.config.method ?? "GET")
            ? null
            : renderProcessTemplate(node.config.body ?? "", context),
          timeoutSeconds: node.config.timeoutSeconds ?? 30,
        },
      };
      const stage = this.db.prepare("SELECT id FROM stages WHERE run_id = ? ORDER BY position LIMIT 1").get(created.id) as Row;
      this.db.prepare(`
        UPDATE stages SET process_node_id = ?, stage_kind = 'http', stage_input = ?, activity_json = ? WHERE id = ?
      `).run(node.id, testInput, JSON.stringify(activity), String(stage.id));
      this.addEvent(created.id, String(stage.id), null, "info", "process.node.test.created", `Создан тест HTTP-шага «${node.name}»`, {
        processNodeId: node.id,
        method: activity.request.method,
        urlHost: this.safeUrlHost(activity.request.url),
      });
      return { kind: "queued", runId: created.id, status: created.status, output: "", branch: null };
    }

    if (node.type === "transform") {
      return {
        kind: "evaluated",
        runId: null,
        status: "completed",
        output: renderProcessTemplate(node.config.template ?? "", {
          input: testInput,
          lastOutput: testInput,
          loopCounts: {},
        }),
        branch: "default",
      };
    }

    if (node.type === "artifact") {
      const content = renderProcessTemplate(node.config.artifactContent ?? "", {
        input: testInput,
        lastOutput: testInput,
        loopCounts: {},
      });
      return {
        kind: "evaluated",
        runId: null,
        status: "completed",
        output: `Предпросмотр ${node.config.artifactName || "artifact.txt"}\n\n${content}`,
        branch: "default",
      };
    }

    if (node.type === "wait" || node.type === "approval") {
      return {
        kind: "evaluated",
        runId: null,
        status: "completed",
        output: node.type === "wait"
          ? `Ожидание настроено на ${node.config.waitSeconds ?? 60} сек.`
          : (node.config.approvalMessage || "Потребуется решение оператора"),
        branch: "default",
      };
    }

    if (node.type === "condition" || node.type === "loop") {
      const matched = evaluateProcessCondition(node.config.condition!, testInput);
      const branch: ProcessBranch = node.type === "condition"
        ? (matched ? "true" : "false")
        : (matched ? "repeat" : "exit");
      return {
        kind: "evaluated",
        runId: null,
        status: "completed",
        output: matched ? "Условие выполнено" : "Условие не выполнено",
        branch,
      };
    }

    return {
      kind: "passthrough",
      runId: null,
      status: "completed",
      output: testInput,
      branch: node.type === "start" ? "default" : null,
    };
  }

  listProcessInstances(limit = 100, projectId = "default"): Array<Record<string, unknown>> {
    const project = normalizeProjectId(projectId);
    const rows = this.db
      .prepare(`
        SELECT pi.*, p.name AS process_name, r.name AS run_name, r.input AS run_input
        FROM process_instances pi
        JOIN processes p ON p.id = pi.process_id
        JOIN runs r ON r.id = pi.run_id
        WHERE p.project_id = ?
        ORDER BY
          CASE pi.status
            WHEN 'running' THEN 0
            WHEN 'compensating' THEN 1
            WHEN 'queued' THEN 2
            WHEN 'waiting_approval' THEN 3
            WHEN 'waiting_external' THEN 4
            ELSE 5
          END,
          pi.created_at DESC
        LIMIT ?
      `)
      .all(project, clampInteger(limit, 1, 500, 100)) as Row[];
    return rows.map((row) => this.processInstanceDto(row));
  }

  getProcessInstance(instanceId: string, projectId = "default"): Record<string, unknown> | null {
    const project = normalizeProjectId(projectId);
    const row = this.db
      .prepare(`
        SELECT pi.*, p.name AS process_name, r.name AS run_name, r.input AS run_input
        FROM process_instances pi
        JOIN processes p ON p.id = pi.process_id
        JOIN runs r ON r.id = pi.run_id
        WHERE pi.id = ? AND p.project_id = ?
      `)
      .get(instanceId, project) as Row | undefined;
    return row ? this.processInstanceDto(row) : null;
  }

  listActiveDurableProcesses(): DurableProcessStart[] {
    return (this.db.prepare(`
      SELECT pi.id, pi.process_id, p.project_id, r.priority
      FROM process_instances pi
      JOIN processes p ON p.id = pi.process_id
      JOIN runs r ON r.id = pi.run_id
      WHERE pi.runtime = 'temporal'
        AND pi.status IN ('queued', 'running', 'waiting_approval', 'waiting_external', 'compensating')
      ORDER BY pi.created_at ASC
    `).all() as Row[]).map((row) => ({
      instanceId: String(row.id),
      processId: String(row.process_id),
      projectId: String(row.project_id),
      priority: Number(row.priority),
    }));
  }

  processRuntimeOwnerInstance(instanceId: string): string | null {
    const exists = this.db.prepare("SELECT id FROM process_instances WHERE id = ?").get(instanceId) as Row | undefined;
    if (!exists) return null;
    let current = instanceId;
    const seen = new Set<string>();
    while (!seen.has(current)) {
      seen.add(current);
      const link = this.db.prepare(`
        SELECT parent_instance_id FROM process_subprocess_links WHERE child_instance_id = ?
      `).get(current) as Row | undefined;
      if (!link || typeof link.parent_instance_id !== "string") return current;
      current = link.parent_instance_id;
    }
    throw new Error("Обнаружен цикл runtime ownership subprocess");
  }

  temporalProcessTick(instanceId: string, projectId: string): DurableProcessState | null {
    const project = this.requireProject(projectId);
    return this.transaction(() => {
      const owned = this.db.prepare(`
        SELECT pi.id FROM process_instances pi
        JOIN processes p ON p.id = pi.process_id
        WHERE pi.id = ? AND p.project_id = ? AND pi.runtime = 'temporal'
      `).get(instanceId, project) as Row | undefined;
      if (!owned) return null;
      this.completeDueWaitStages(instanceId);
      this.expireProcessSignals(instanceId);
      return this.durableProcessState(instanceId, project);
    });
  }

  cancelProcessInstance(instanceId: string, projectId = "default"): boolean {
    const project = this.requireProject(projectId);
    return this.transaction(() => {
      const instance = this.db.prepare(`
        SELECT pi.* FROM process_instances pi
        JOIN processes p ON p.id = pi.process_id
        WHERE pi.id = ? AND p.project_id = ?
      `).get(instanceId, project) as Row | undefined;
      if (!instance) return false;
      if (!["queued", "running", "waiting_approval", "waiting_external", "compensating"].includes(String(instance.status))) return true;
      if (instance.status === "compensating") return true;
      const activeLeases = this.db.prepare(`
        SELECT lease_id FROM stages WHERE run_id = ? AND status = 'running' AND lease_id IS NOT NULL
      `).all(String(instance.run_id)) as Row[];
      this.triggerProcessFailure(instanceId, String(instance.run_id), "Экземпляр процесса остановлен оператором", "cancelled");
      for (const lease of activeLeases) {
        if (typeof lease.lease_id === "string") this.telemetry.endStage(lease.lease_id, { status: "cancelled" });
      }
      return true;
    });
  }

  cancelRun(runId: string, projectId = "default"): boolean {
    const project = this.requireProject(projectId);
    const processInstance = this.db.prepare(`
      SELECT pi.id FROM process_instances pi
      JOIN runs r ON r.id = pi.run_id
      WHERE pi.run_id = ? AND r.project_id = ?
    `).get(runId, project) as Row | undefined;
    if (processInstance) return this.cancelProcessInstance(String(processInstance.id), project);

    return this.transaction(() => {
      const run = this.db.prepare("SELECT id, status FROM runs WHERE id = ? AND project_id = ?")
        .get(runId, project) as Row | undefined;
      if (!run) return false;
      if (!["queued", "running", "waiting_approval"].includes(String(run.status))) return true;
      const timestamp = nowIso();
      const activeLeases = this.db.prepare(`
        SELECT lease_id FROM stages WHERE run_id = ? AND status = 'running' AND lease_id IS NOT NULL
      `).all(runId) as Row[];
      this.db.prepare(`
        UPDATE stages
        SET status = 'cancelled', node_id = NULL, lease_id = NULL, lease_expires_at = NULL,
            completed_at = COALESCE(completed_at, ?), updated_at = ?
        WHERE run_id = ? AND status IN ('pending', 'queued', 'running', 'waiting_approval')
      `).run(timestamp, timestamp, runId);
      this.db.prepare(`
        UPDATE mcp_tool_calls
        SET status = 'expired', error = 'Run отменён до approval', completed_at = ?, updated_at = ?
        WHERE run_id = ? AND status = 'waiting_approval'
      `).run(timestamp, timestamp, runId);
      this.db.prepare(`
        UPDATE runs SET status = 'cancelled', completed_at = ?, updated_at = ?
        WHERE id = ? AND project_id = ?
      `).run(timestamp, timestamp, runId, project);
      this.addEvent(runId, null, null, "warn", "run.cancelled", "Запуск остановлен", {
        source: "external_or_operator",
      });
      for (const lease of activeLeases) {
        if (typeof lease.lease_id === "string") this.telemetry.endStage(lease.lease_id, { status: "cancelled" });
      }
      this.telemetry.endRun(runId, { status: "cancelled" });
      return true;
    });
  }

  createRun(
    input: CreateRunInput,
    projectId = "default",
    traceparent: string | null = null,
  ): { id: string; status: string } {
    const project = this.requireProject(projectId);
    const name = input.name.trim();
    const runInput = input.input.trim();
    if (!name || !runInput) throw new Error("Название и входные данные обязательны");
    if (name.length > 120) throw new Error("Название запуска: максимум 120 символов");
    if (runInput.length > 100_000) throw new Error("Входные данные запуска: максимум 100000 символов");

    const requestedAgentIds = input.agentIds?.length ? input.agentIds : ["collector", "analyst", "editor"];
    const knownAgents = this.knownAgentIds(project);
    if (requestedAgentIds.some((id) => !knownAgents.has(id))) {
      throw new Error("Цепочка содержит неизвестного агента");
    }
    const selectedAgentRows = new Map(this.effectiveAgentRows(project)
      .map((row) => [String(row.id), row]));

    const runId = randomUUID();
    const timestamp = nowIso();
    const mode = input.executionMode ?? "sequential";
    const priority = clampInteger(input.priority, 0, 100, 50);
    const approvalRequired = input.approvalRequired ?? true;
    const resultDestination = normalizeResultDestination(input.resultDestination);
    const artifactPath = resultDestination === "artifacts" ? normalizeArtifactPath(input.artifactPath) : "";
    const knowledgeCollectionIds = this.requireKnowledgeCollectionIds(project, input.knowledgeCollectionIds);
    const initiallyAwaitingApproval = approvalRequired && requestedAgentIds.length === 1;
    const initialRunStatus = initiallyAwaitingApproval ? "waiting_approval" : "queued";
    const runTrace = this.telemetry.startRun({ runId, projectId: project, traceparent });

    try {
      this.transaction(() => {
        this.db
          .prepare(`
            INSERT INTO runs(
              id, name, input, status, execution_mode, priority, approval_required,
              result_destination, artifact_path, project_id, trace_id, root_span_id,
              knowledge_collection_ids_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          `)
          .run(
            runId,
            name,
            runInput,
            initialRunStatus,
            mode,
            priority,
            approvalRequired ? 1 : 0,
            resultDestination,
            artifactPath,
            project,
            runTrace.traceId,
            runTrace.spanId,
            JSON.stringify(knowledgeCollectionIds),
            timestamp,
            timestamp,
          );

        const insertStage = this.db.prepare(`
          INSERT INTO stages(
            id, run_id, agent_id, position, status, requires_approval,
            agent_snapshot_json, created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        `);
        requestedAgentIds.forEach((agentId, index) => {
          const isLast = index === requestedAgentIds.length - 1;
          const agentRow = selectedAgentRows.get(agentId);
          if (!agentRow) throw new Error("Не удалось зафиксировать конфигурацию агента");
          insertStage.run(
            randomUUID(),
            runId,
            agentId,
            index,
            index === 0 ? (initiallyAwaitingApproval ? "waiting_approval" : "queued") : "pending",
            approvalRequired && isLast ? 1 : 0,
            JSON.stringify(this.captureAgentSnapshot(agentRow, "run_creation", project, timestamp)),
            timestamp,
            timestamp,
          );
        });

        this.addEvent(runId, null, null, "info", "run.created", "Запуск добавлен в очередь", {
          executionMode: mode,
          priority,
          resultDestination,
          artifactPath: artifactPath || null,
          traceId: runTrace.traceId,
          externalTraceparent: traceparent,
          knowledgeCollectionIds,
        });
        if (initiallyAwaitingApproval) {
          const firstStage = this.db
            .prepare("SELECT id FROM stages WHERE run_id = ? AND position = 0")
            .get(runId) as Row;
          this.addEvent(runId, String(firstStage.id), null, "warn", "approval.requested", "Единственный этап ожидает подтверждения", {
            position: 0,
          });
        }
      });
    } catch (error) {
      this.telemetry.endRun(runId, { status: "failed", errorType: error instanceof Error ? error.name : "Error" });
      throw error;
    }

    return { id: runId, status: initialRunStatus };
  }

  registerNode(registration: WorkerRegistration): { id: string; token: string } {
    const name = registration.name.trim();
    if (!name) throw new Error("Имя узла обязательно");

    const token = createToken();
    const tokenHash = hashToken(token);
    const existing = this.db.prepare("SELECT id FROM nodes WHERE name = ?").get(name) as Row | undefined;
    const id = existing ? String(existing.id) : randomUUID();
    const timestamp = nowIso();
    const maxConcurrency = clampInteger(registration.maxConcurrency, 1, 32, 1);
    const models = [...new Set(registration.models.map((model) => model.trim()).filter(Boolean))];
    const modelProfiles = normalizeModelProfiles(registration.modelProfiles, models);
    const agentRuntimes = normalizeAgentRuntimes(registration.agentRuntimes);
    const agentRuntimeProfiles = normalizeAgentRuntimeProfiles(registration.agentRuntimeProfiles);
    const embeddingModels = normalizeEmbeddingModels(registration.embeddingModels);

    this.db
      .prepare(`
        INSERT INTO nodes(
          id, name, platform, architecture, endpoint, models_json, model_profiles_json,
          labels_json, agent_runtimes_json, agent_runtime_profiles_json, embedding_models_json,
          cpu_cores, memory_mb, vram_mb, gpu,
          max_concurrency, token_hash, status,
          metrics_json, last_seen, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'online', '{}', ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
          platform = excluded.platform,
          architecture = excluded.architecture,
          endpoint = excluded.endpoint,
          models_json = excluded.models_json,
          model_profiles_json = excluded.model_profiles_json,
          labels_json = excluded.labels_json,
          agent_runtimes_json = excluded.agent_runtimes_json,
          agent_runtime_profiles_json = excluded.agent_runtime_profiles_json,
          embedding_models_json = excluded.embedding_models_json,
          cpu_cores = excluded.cpu_cores,
          memory_mb = excluded.memory_mb,
          vram_mb = excluded.vram_mb,
          gpu = excluded.gpu,
          max_concurrency = excluded.max_concurrency,
          token_hash = excluded.token_hash,
          status = 'online',
          last_seen = excluded.last_seen,
          updated_at = excluded.updated_at
      `)
      .run(
        id,
        name,
        registration.platform,
        registration.architecture ?? "",
        registration.endpoint ?? "",
        JSON.stringify(models),
        JSON.stringify(modelProfiles),
        JSON.stringify(registration.labels ?? {}),
        JSON.stringify(agentRuntimes),
        JSON.stringify(agentRuntimeProfiles),
        JSON.stringify(embeddingModels),
        clampInteger(registration.cpuCores, 1, 512, 1),
        clampInteger(registration.memoryMb, 0, 16_777_216, 0),
        clampInteger(registration.vramMb, 0, 16_777_216, 0),
        registration.gpu ?? "",
        maxConcurrency,
        tokenHash,
        timestamp,
        timestamp,
        timestamp,
      );
    this.pruneNodeBenchmarks(id, models);

    this.addEvent(null, null, id, "info", "node.registered", `Узел ${name} зарегистрирован`, {
      models,
      modelProfiles: modelProfiles.length,
      maxConcurrency,
      agentRuntimes,
      agentRuntimeProfiles,
      embeddingModels,
    });
    return { id, token };
  }

  authenticateNode(token: string): Row | null {
    const row = this.db.prepare("SELECT * FROM nodes WHERE token_hash = ?").get(hashToken(token)) as Row | undefined;
    return row ?? null;
  }

  heartbeatNode(nodeId: string, metrics: WorkerMetrics, capabilities?: WorkerCapabilities): void {
    const timestamp = nowIso();
    if (capabilities) {
      const models = [...new Set(capabilities.models.map((model) => model.trim()).filter(Boolean))];
      const current = this.db.prepare(`
        SELECT model_profiles_json, vram_mb, embedding_models_json, agent_runtime_profiles_json
        FROM nodes WHERE id = ?
      `).get(nodeId) as Row | undefined;
      const modelProfiles = capabilities.modelProfiles === undefined
        ? normalizeModelProfiles(parseJson<unknown>(current?.model_profiles_json, []), models)
        : normalizeModelProfiles(capabilities.modelProfiles, models);
      const vramMb = capabilities.vramMb === undefined
        ? Number(current?.vram_mb ?? 0)
        : clampInteger(capabilities.vramMb, 0, 16_777_216, 0);
      const agentRuntimes = normalizeAgentRuntimes(capabilities.agentRuntimes);
      const agentRuntimeProfiles = capabilities.agentRuntimeProfiles === undefined
        ? normalizeAgentRuntimeProfiles(parseJson<unknown>(current?.agent_runtime_profiles_json, ["tool_loop_v1"]))
        : normalizeAgentRuntimeProfiles(capabilities.agentRuntimeProfiles);
      const embeddingModels = capabilities.embeddingModels === undefined
        ? normalizeEmbeddingModels(parseJson<unknown>(current?.embedding_models_json, []))
        : normalizeEmbeddingModels(capabilities.embeddingModels);
      this.db
        .prepare(`
          UPDATE nodes SET
            status = 'online',
            metrics_json = ?,
            endpoint = ?,
            models_json = ?,
            model_profiles_json = ?,
            labels_json = ?,
            agent_runtimes_json = ?,
            agent_runtime_profiles_json = ?,
            embedding_models_json = ?,
            vram_mb = ?,
            max_concurrency = ?,
            last_seen = ?,
            updated_at = ?
          WHERE id = ?
        `)
        .run(
          JSON.stringify(metrics ?? {}),
          capabilities.endpoint ?? "",
          JSON.stringify(models),
          JSON.stringify(modelProfiles),
          JSON.stringify(capabilities.labels ?? {}),
          JSON.stringify(agentRuntimes),
          JSON.stringify(agentRuntimeProfiles),
          JSON.stringify(embeddingModels),
          vramMb,
          clampInteger(capabilities.maxConcurrency, 1, 32, 1),
          timestamp,
          timestamp,
          nodeId,
        );
      this.pruneNodeBenchmarks(nodeId, models);
      return;
    }
    this.db
      .prepare("UPDATE nodes SET status = 'online', metrics_json = ?, last_seen = ?, updated_at = ? WHERE id = ?")
      .run(JSON.stringify(metrics ?? {}), timestamp, timestamp, nodeId);
  }

  private availableRoutingNodes(): RoutingNode[] {
    const rows = this.db.prepare(`
      SELECT n.*,
        (SELECT COUNT(*) FROM stages s WHERE s.node_id = n.id AND s.status = 'running')
        + (SELECT COUNT(*) FROM knowledge_embedding_jobs j WHERE j.node_id = n.id AND j.status = 'running') AS used_concurrency
      FROM nodes n WHERE n.status = 'online'
    `).all() as Row[];
    return rows.flatMap((row): RoutingNode[] => {
      const ageMs = Date.now() - new Date(String(row.last_seen)).getTime();
      const freeSlots = Number(row.max_concurrency) - Number(row.used_concurrency);
      if (!Number.isFinite(ageMs) || ageMs > 90_000 || freeSlots <= 0 || String(row.id).startsWith("demo-")) return [];
      const models = parseJson<string[]>(row.models_json, []);
      return [{
        row,
        models,
        modelProfiles: normalizeModelProfiles(parseJson<unknown>(row.model_profiles_json, []), models),
        benchmarks: this.modelBenchmarks(String(row.id)),
        runtimes: new Set(normalizeAgentRuntimes(parseJson<unknown>(row.agent_runtimes_json, ["single"]))),
        runtimeProfiles: new Set(normalizeAgentRuntimeProfiles(
          parseJson<unknown>(row.agent_runtime_profiles_json, ["tool_loop_v1"]),
        )),
        metrics: parseJson<WorkerMetrics>(row.metrics_json, {}),
        freeSlots,
      }];
    });
  }

  private routeAgentStage(
    candidate: Row,
    snapshot: AgentExecutionSnapshot,
    pollingNodeId: string,
    policy: ModelRouterPolicy,
    nodes: RoutingNode[],
    requiresTools: boolean,
  ): ModelRoutingDecision | null {
    const options: RoutingOption[] = [];
    for (const node of nodes) {
      if (!node.runtimes.has(snapshot.runtime)) continue;
      if (snapshot.runtime === "langgraph" && !node.runtimeProfiles.has(snapshot.runtimeConfig.profile)) continue;
      const pinnedModels = pinnedAgentModels(snapshot);
      if (node.models.length > 0 && pinnedModels.some((model) => !node.models.includes(model))) continue;
      const temperature = node.metrics.temperatureC;
      if (policy.maxTemperatureC > 0 && temperature !== undefined && temperature > policy.maxTemperatureC) continue;
      if (node.metrics.onBattery
        && node.metrics.batteryPercent !== undefined
        && node.metrics.batteryPercent < policy.minBatteryPercent) continue;

      const modelNames = snapshot.model
        ? (node.models.length === 0 || node.models.includes(snapshot.model) ? [snapshot.model] : [])
        : (node.models.length > 0 ? node.models : [null]);
      for (const model of modelNames) {
        const profile = model === null
          ? null
          : node.modelProfiles.find((candidateProfile) => candidateProfile.name === model) ?? null;
        if (!profile && !policy.allowUnknownProfiles) continue;
        const capabilities = profile?.capabilities ?? [];
        const toolsKnown = capabilities.length > 0;
        const supportsTools = capabilities.some((capability) =>
          ["tools", "tool", "tool-calling", "function-calling", "function_calling"].includes(capability));
        if (requiresTools && ((toolsKnown && !supportsTools) || (!toolsKnown && !policy.allowUnknownProfiles))) continue;
        if (policy.minContextTokens > 0) {
          if (profile?.contextWindow === undefined && !policy.allowUnknownProfiles) continue;
          if (profile?.contextWindow !== undefined && profile.contextWindow < policy.minContextTokens) continue;
        }
        if (policy.minQualityScore > 0) {
          if (profile?.qualityScore === undefined && !policy.allowUnknownProfiles) continue;
          if (profile?.qualityScore !== undefined && profile.qualityScore < policy.minQualityScore) continue;
        }
        const storedBenchmark = model === null ? null : node.benchmarks.get(model) ?? null;
        const benchmark = storedBenchmark
          && Date.now() - new Date(storedBenchmark.lastObservedAt).getTime() <= 30 * 86_400_000
          ? storedBenchmark
          : null;
        const footprint = profile?.parameterCount ?? profile?.sizeBytes ?? Number.POSITIVE_INFINITY;
        const health = node.freeSlots * 100
          - (node.metrics.cpuPercent ?? 50)
          - (node.metrics.memoryPercent ?? 50)
          - Math.max(0, (node.metrics.temperatureC ?? 60) - 60) * 2;
        options.push({
          node,
          model,
          profile,
          benchmark,
          footprint,
          health,
          uncertain: !profile
            || (requiresTools && !toolsKnown)
            || (policy.minContextTokens > 0 && profile.contextWindow === undefined)
            || (policy.minQualityScore > 0 && profile.qualityScore === undefined),
        });
      }
    }
    if (options.length === 0) return null;

    const descendingOptional = (left: number | undefined, right: number | undefined): number =>
      (right ?? Number.NEGATIVE_INFINITY) - (left ?? Number.NEGATIVE_INFINITY);
    const ascendingOptional = (left: number | null | undefined, right: number | null | undefined): number =>
      (left ?? Number.POSITIVE_INFINITY) - (right ?? Number.POSITIVE_INFINITY);
    const compare = (left: RoutingOption, right: RoutingOption): number => {
      let difference = 0;
      if (policy.strategy === "performance") {
        difference = descendingOptional(left.benchmark?.tokensPerSecond, right.benchmark?.tokensPerSecond)
          || descendingOptional(left.profile?.qualityScore, right.profile?.qualityScore)
          || right.health - left.health
          || left.footprint - right.footprint;
      } else if (policy.strategy === "efficiency") {
        difference = ascendingOptional(left.benchmark?.joulesPer1kTokens, right.benchmark?.joulesPer1kTokens)
          || left.footprint - right.footprint
          || right.health - left.health
          || descendingOptional(left.benchmark?.tokensPerSecond, right.benchmark?.tokensPerSecond);
      } else {
        difference = left.footprint - right.footprint
          || descendingOptional(left.benchmark?.tokensPerSecond, right.benchmark?.tokensPerSecond)
          || descendingOptional(left.profile?.qualityScore, right.profile?.qualityScore)
          || right.health - left.health;
      }
      return difference
        || String(left.model ?? "").localeCompare(String(right.model ?? ""))
        || String(left.node.row.name).localeCompare(String(right.node.row.name))
        || String(left.node.row.id).localeCompare(String(right.node.row.id));
    };
    options.sort(compare);

    const previous = Number(candidate.attempt) > 0
      ? parseJson<ModelRoutingDecision | null>(candidate.routing_json, null)
      : null;
    const alternatives = previous
      ? options.filter((option) =>
        String(option.node.row.id) !== previous.nodeId || option.model !== previous.selectedModel)
      : options;
    const selected = alternatives[0] ?? options[0];
    if (!selected || String(selected.node.row.id) !== pollingNodeId) return null;

    const reasons = [
      snapshot.model
        ? `Соблюдён model pin: ${snapshot.model}`
        : policy.strategy === "performance"
          ? "Выбрана максимальная наблюдаемая производительность"
          : policy.strategy === "efficiency"
            ? "Выбрана минимальная наблюдаемая энергоёмкость и footprint"
            : "Выбрана минимальная модель, прошедшая ограничения policy",
      `На узле свободно ${selected.node.freeSlots} из ${Number(selected.node.row.max_concurrency)} слотов`,
    ];
    if (selected.benchmark) {
      reasons.push(`EWMA ${selected.benchmark.tokensPerSecond.toFixed(2)} ток/с по ${selected.benchmark.samples} запуск(ам)`);
    } else {
      reasons.push("Пассивный benchmark ещё не накоплен");
    }
    if (selected.uncertain) reasons.push("Часть capabilities неизвестна; разрешён compatibility fallback");
    if (previous && alternatives.length > 0) reasons.push("После неуспешной попытки выбрана следующая альтернатива");

    return {
      schemaVersion: 1,
      selectedAt: nowIso(),
      strategy: policy.strategy,
      requestedModel: snapshot.model,
      selectedModel: selected.model,
      nodeId: String(selected.node.row.id),
      nodeName: String(selected.node.row.name),
      alternativesConsidered: options.length,
      fallbackFrom: previous && alternatives.length > 0
        ? { nodeId: previous.nodeId, model: previous.selectedModel }
        : null,
      reasons,
      signals: {
        freeSlots: selected.node.freeSlots,
        cpuPercent: selected.node.metrics.cpuPercent ?? null,
        memoryPercent: selected.node.metrics.memoryPercent ?? null,
        temperatureC: selected.node.metrics.temperatureC ?? null,
        batteryPercent: selected.node.metrics.batteryPercent ?? null,
        onBattery: selected.node.metrics.onBattery ?? false,
        contextWindow: selected.profile?.contextWindow ?? null,
        qualityScore: selected.profile?.qualityScore ?? null,
        sizeBytes: selected.profile?.sizeBytes ?? null,
        parameterCount: selected.profile?.parameterCount ?? null,
        tokensPerSecond: selected.benchmark?.tokensPerSecond ?? null,
        joulesPer1kTokens: selected.benchmark?.joulesPer1kTokens ?? null,
        benchmarkSamples: selected.benchmark?.samples ?? 0,
        profileKnown: selected.profile !== null,
      },
    };
  }

  leaseNext(nodeId: string, workerVersion = "unknown"): LeasePayload | null {
    let startedLeaseId: string | null = null;
    try {
      return this.transaction(() => {
      this.completeAutomaticWaitStages();
      this.cleanupExpiredLeases();
      const node = this.db.prepare("SELECT * FROM nodes WHERE id = ?").get(nodeId) as Row | undefined;
      if (!node) throw new Error("Узел не найден");

      const nodeActive = Number(
        (this.db.prepare(`
          SELECT
            (SELECT COUNT(*) FROM stages WHERE node_id = ? AND status = 'running')
            + (SELECT COUNT(*) FROM knowledge_embedding_jobs WHERE node_id = ? AND status = 'running') AS count
        `).get(nodeId, nodeId) as Row).count,
      );
      if (nodeActive >= Number(node.max_concurrency)) return null;

      const schedulerMode = (this.getSetting("scheduler_mode") ?? "sequential") as SchedulerMode;
      const globalActive = Number(
        (this.db.prepare(`
          SELECT
            (SELECT COUNT(*) FROM stages WHERE status = 'running')
            + (SELECT COUNT(*) FROM knowledge_embedding_jobs WHERE status = 'running') AS count
        `).get() as Row).count,
      );
      const globalMax = Number(this.getSetting("global_max_concurrency") ?? "1");
      if (schedulerMode === "sequential" && globalActive >= globalMax) return null;

      if (schedulerMode === "auto") {
        const metrics = parseJson<WorkerMetrics>(node.metrics_json, {});
        if ((metrics.cpuPercent ?? 0) >= 90 || (metrics.memoryPercent ?? 0) >= 92) return null;
        if (metrics.onBattery && (metrics.batteryPercent ?? 100) < 30) return null;
      }

      const models = new Set(parseJson<string[]>(node.models_json, []));
      const agentRuntimes = new Set(
        normalizeAgentRuntimes(parseJson<unknown>(node.agent_runtimes_json, ["single"])),
      );
      const agentRuntimeProfiles = new Set(
        normalizeAgentRuntimeProfiles(parseJson<unknown>(node.agent_runtime_profiles_json, ["tool_loop_v1"])),
      );
      const candidates = this.db
        .prepare(`
          SELECT
            s.id AS stage_id,
            s.position,
            s.attempt,
            s.max_attempts,
            s.process_node_id,
            s.stage_kind,
            s.stage_input,
            s.activity_json,
            s.agent_snapshot_json,
            s.routing_json,
            a.id AS agent_id,
            a.name AS agent_name,
            a.role AS agent_role,
            a.system_prompt,
            a.model,
            a.runtime AS agent_runtime,
            a.runtime_config_json,
            r.id AS run_id,
            r.name AS run_name,
            r.input AS run_input,
            r.project_id,
            r.result_destination,
            r.artifact_path
            ,r.trace_id
            ,r.root_span_id
            ,r.knowledge_collection_ids_json
          FROM stages s
          JOIN runs r ON r.id = s.run_id
          JOIN agents a ON a.id = s.agent_id
          WHERE s.status = 'queued'
            AND s.stage_kind IN ('agent', 'http', 'compensation')
            AND (s.available_at IS NULL OR s.available_at <= ?)
            AND r.status IN ('queued', 'running', 'compensating')
            AND (s.process_node_id IS NOT NULL OR NOT EXISTS (
              SELECT 1 FROM stages previous
              WHERE previous.run_id = s.run_id
                AND previous.position < s.position
                AND previous.status <> 'completed'
            ))
          ORDER BY r.priority DESC, r.created_at ASC, s.position ASC
          LIMIT 100
        `)
        .all(nowIso()) as Row[];

      const modelRouterPolicy = this.getModelRouterPolicy();
      const routingNodes = modelRouterPolicy.enabled ? this.availableRoutingNodes() : [];
      let selectedRouting: ModelRoutingDecision | null = null;
      let selectedMcpTools: McpLeaseTool[] = [];
      const candidate = candidates.find((row) => {
        if (row.stage_kind !== "agent") return true;
        const snapshot = parseAgentSnapshot(row.agent_snapshot_json)
          ?? agentSnapshot({
            id: row.agent_id,
            name: row.agent_name,
            role: row.agent_role,
            system_prompt: row.system_prompt,
            model: row.model,
            runtime: row.agent_runtime,
            runtime_config_json: row.runtime_config_json,
          }, "migration_backfill");
        const requestedModel = snapshot.model;
        const requestedRuntime = snapshot.runtime;
        const requiredModels = pinnedAgentModels(snapshot);
        const modelCompatible = models.size === 0 || requiredModels.every((model) => models.has(model));
        const profileCompatible = requestedRuntime !== "langgraph"
          || agentRuntimeProfiles.has(snapshot.runtimeConfig.profile);
        if (!modelCompatible || !agentRuntimes.has(requestedRuntime) || !profileCompatible) return false;
        selectedMcpTools = this.mcpLeaseTools(String(row.project_id));
        if (!modelRouterPolicy.enabled) return true;
        const routing = this.routeAgentStage(
          row,
          snapshot,
          nodeId,
          modelRouterPolicy,
          routingNodes,
          selectedMcpTools.length > 0,
        );
        if (!routing) return false;
        selectedRouting = routing;
        return true;
      });
      if (!candidate) return null;
      const routingDecision = selectedRouting as ModelRoutingDecision | null;
      const candidateSnapshot = parseAgentSnapshot(candidate.agent_snapshot_json)
        ?? agentSnapshot({
          id: candidate.agent_id,
          name: candidate.agent_name,
          role: candidate.agent_role,
          system_prompt: candidate.system_prompt,
          model: candidate.model,
          runtime: candidate.agent_runtime,
          runtime_config_json: candidate.runtime_config_json,
        }, "migration_backfill");
      if (candidate.stage_kind !== "agent") selectedMcpTools = [];
      else if (selectedMcpTools.length === 0) selectedMcpTools = this.mcpLeaseTools(String(candidate.project_id));
      const selectedModel = routingDecision?.selectedModel ?? candidateSnapshot.model;
      const knowledge = this.leaseKnowledgeContext(
        String(candidate.project_id),
        candidateSnapshot.id,
        parseJson<unknown>(candidate.knowledge_collection_ids_json, []),
      );

      const leaseId = randomUUID();
      const timestamp = nowIso();
      const expiresAt = futureIso(this.leaseTtlSeconds);
      const result = this.db
        .prepare(`
          UPDATE stages
          SET status = 'running', node_id = ?, lease_id = ?, lease_expires_at = ?,
              attempt = attempt + 1, started_at = COALESCE(started_at, ?), updated_at = ?
          WHERE id = ? AND status = 'queued'
        `)
        .run(nodeId, leaseId, expiresAt, timestamp, timestamp, String(candidate.stage_id));
      if (result.changes !== 1) return null;

      const activeStatus = candidate.stage_kind === "compensation" ? "compensating" : "running";
      this.db.prepare("UPDATE runs SET status = ?, started_at = COALESCE(started_at, ?), updated_at = ? WHERE id = ?")
        .run(activeStatus, timestamp, timestamp, String(candidate.run_id));
      this.db.prepare(`
        UPDATE process_instances
        SET status = ?, started_at = COALESCE(started_at, ?), updated_at = ?
        WHERE run_id = ?
      `).run(activeStatus, timestamp, timestamp, String(candidate.run_id));
      this.addEvent(
        String(candidate.run_id),
        String(candidate.stage_id),
        nodeId,
        "info",
        "stage.started",
        `${candidateSnapshot.name} запущен на узле ${String(node.name)}`,
        {
          leaseId,
          runtime: candidateSnapshot.runtime,
          runtimeProfile: candidateSnapshot.runtimeConfig.profile,
          specialistDefinitionVersions: candidateSnapshot.specialists.map((specialist) => ({
            id: specialist.id,
            definitionVersion: specialist.definitionVersion,
          })),
          requestedModel: candidateSnapshot.model,
          selectedModel,
          routing: routingDecision,
        },
      );
      if (routingDecision) {
        this.addEvent(
          String(candidate.run_id),
          String(candidate.stage_id),
          nodeId,
          "info",
          "routing.selected",
          `Model Router выбрал ${selectedModel ?? "fallback worker model"} на ${String(node.name)}`,
          routingDecision as unknown as Record<string, unknown>,
        );
      }

      const context = this.db
        .prepare(`
          SELECT a.name AS fallback_agent_name, s.agent_snapshot_json, s.output
          FROM stages s
          JOIN agents a ON a.id = s.agent_id
          WHERE s.run_id = ? AND s.position < ? AND s.status = 'completed'
          ORDER BY s.position
        `)
        .all(String(candidate.run_id), Number(candidate.position)) as Row[];
      const contextSnapshot = context.map((row) => ({
        agentName: parseAgentSnapshot(row.agent_snapshot_json)?.name ?? String(row.fallback_agent_name),
        output: String(row.output ?? ""),
      }));
      const rawActivity = parseJson<Record<string, unknown>>(candidate.activity_json, {});
      let activity: LeasePayload["activity"];
      if (candidate.stage_kind === "http" || candidate.stage_kind === "compensation") {
        const request = rawActivity.request && typeof rawActivity.request === "object"
          ? rawActivity.request as Record<string, unknown>
          : {};
        const headers = request.headers && typeof request.headers === "object" && !Array.isArray(request.headers)
          ? Object.fromEntries(Object.entries(request.headers).map(([key, value]) => [key, String(value)]))
          : {};
        const credentialId = typeof rawActivity.credentialId === "string" ? rawActivity.credentialId : "";
        const idempotencyHeader = typeof rawActivity.idempotencyHeader === "string"
          && /^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,80}$/.test(rawActivity.idempotencyHeader)
          ? rawActivity.idempotencyHeader
          : "";
        if (credentialId) {
          const credential = this.httpCredentialData(credentialId, String(candidate.project_id));
          if (!credential) throw new Error("Credentials HTTP-шага не найдены");
          if (credential.type === "http_header") {
            headers[credential.data.headerName!] = credential.data.headerValue!;
          } else if (credential.type === "api_key") {
            headers.Authorization = `Bearer ${credential.data.apiKey!}`;
          }
        }
        if (idempotencyHeader && typeof candidate.idempotency_key === "string") {
          // Credentials may be rotated after publication; they must never erase the persisted retry key.
          headers[idempotencyHeader] = candidate.idempotency_key;
        }
        activity = {
          kind: "http",
          request: {
            method: String(request.method ?? "GET") as HttpMethod,
            url: String(request.url ?? ""),
            headers,
            body: typeof request.body === "string" ? request.body : null,
            timeoutSeconds: clampInteger(Number(request.timeoutSeconds), 1, 120, 30),
            maxResponseBytes: 2_000_000,
          },
        };
      }
      this.addEvent(
        String(candidate.run_id),
        String(candidate.stage_id),
        nodeId,
        "info",
        "trace.input",
        "Подготовлены входные данные этапа",
        {
          kind: "input",
          attempt: Number(candidate.attempt) + 1,
          position: Number(candidate.position),
          run: {
            name: String(candidate.run_name),
            input: typeof candidate.stage_input === "string" ? candidate.stage_input : String(candidate.run_input),
          },
          agent: {
            id: candidateSnapshot.id,
            name: candidateSnapshot.name,
            role: candidateSnapshot.role,
            systemPrompt: candidateSnapshot.systemPrompt,
            requestedModel: candidateSnapshot.model,
            selectedModel,
            runtime: candidateSnapshot.runtime,
            runtimeConfig: candidateSnapshot.runtimeConfig,
            specialists: candidateSnapshot.specialists.map((specialist) => ({
              id: specialist.id,
              name: specialist.name,
              model: specialist.model,
              promptVersion: specialist.promptVersion,
              definitionVersion: specialist.definitionVersion,
            })),
            promptVersion: candidateSnapshot.promptVersion,
            definitionVersion: candidateSnapshot.definitionVersion,
          },
          context: contextSnapshot,
          knowledge: {
            collectionIds: knowledge.groups.flatMap((group) => group.collectionIds),
            memoryIds: knowledge.memory.map((entry) => entry.id),
          },
        },
      );

      const stageTrace = this.telemetry.startStage({
        leaseId,
        runId: String(candidate.run_id),
        stageId: String(candidate.stage_id),
        traceId: String(candidate.trace_id),
        parentSpanId: String(candidate.root_span_id),
        agentName: candidateSnapshot.name,
        agentId: candidateSnapshot.id,
        model: selectedModel,
        runtime: candidateSnapshot.runtime,
        runtimeProfile: candidateSnapshot.runtimeConfig.profile,
        specialistCount: candidateSnapshot.specialists.length,
        attempt: Number(candidate.attempt) + 1,
        nodeId,
      });
      startedLeaseId = leaseId;
      this.db.prepare(`
        UPDATE stages SET lease_traceparent = ?, worker_snapshot_json = ?, routing_json = ? WHERE id = ?
      `).run(
        stageTrace.traceparent,
        JSON.stringify({
          schemaVersion: 2,
          capturedAt: timestamp,
          nodeId,
          nodeName: String(node.name),
          workerVersion: workerVersion.slice(0, 80),
          models: [...models],
          agentRuntimes: [...agentRuntimes],
          agentRuntimeProfiles: [...agentRuntimeProfiles],
          embeddingModels: normalizeEmbeddingModels(parseJson<unknown>(node.embedding_models_json, [])),
          labels: parseJson<Record<string, string>>(node.labels_json, {}),
          memoryMb: Number(node.memory_mb),
          vramMb: Number(node.vram_mb),
          modelProfiles: normalizeModelProfiles(parseJson<unknown>(node.model_profiles_json, []), [...models]),
          routing: routingDecision,
        }),
        routingDecision ? JSON.stringify(routingDecision) : null,
        String(candidate.stage_id),
      );

      return {
        leaseId,
        expiresAt,
        traceContext: {
          traceId: stageTrace.traceId,
          traceparent: stageTrace.traceparent,
        },
        run: {
          id: String(candidate.run_id),
          name: String(candidate.run_name),
          input: typeof candidate.stage_input === "string" ? candidate.stage_input : String(candidate.run_input),
          resultDestination: normalizeResultDestination(candidate.result_destination),
          artifactPath: typeof candidate.artifact_path === "string" && candidate.artifact_path
            ? candidate.artifact_path
            : null,
        },
        stage: {
          id: String(candidate.stage_id),
          position: Number(candidate.position),
          attempt: Number(candidate.attempt) + 1,
          processNodeId: typeof candidate.process_node_id === "string" ? candidate.process_node_id : null,
        },
        agent: {
          id: candidateSnapshot.id,
          name: candidateSnapshot.name,
          role: candidateSnapshot.role,
          systemPrompt: candidateSnapshot.systemPrompt,
          model: selectedModel,
          runtime: candidateSnapshot.runtime,
          runtimeConfig: candidateSnapshot.runtimeConfig,
          specialists: candidateSnapshot.specialists,
          promptVersion: candidateSnapshot.promptVersion,
          definitionVersion: candidateSnapshot.definitionVersion,
        },
        context: contextSnapshot,
        routing: routingDecision,
        mcpTools: selectedMcpTools,
        knowledge,
        ...(activity ? { activity } : {}),
      };
      });
    } catch (error) {
      if (startedLeaseId) {
        this.telemetry.endStage(startedLeaseId, {
          status: "failed",
          errorType: error instanceof Error ? error.name : "LeaseDispatchError",
        });
      }
      throw error;
    }
  }

  renewLease(nodeId: string, leaseId: string): boolean {
    const result = this.db
      .prepare(`
        UPDATE stages SET lease_expires_at = ?, updated_at = ?
        WHERE node_id = ? AND lease_id = ? AND status = 'running'
      `)
      .run(futureIso(this.leaseTtlSeconds), nowIso(), nodeId, leaseId);
    return result.changes === 1;
  }

  appendLeaseEvent(
    nodeId: string,
    leaseId: string,
    level: string,
    message: string,
    data: Record<string, unknown> | null,
  ): EventRecord {
    const stage = this.db
      .prepare("SELECT id, run_id FROM stages WHERE node_id = ? AND lease_id = ? AND status = 'running'")
      .get(nodeId, leaseId) as Row | undefined;
    if (!stage) throw new Error("Активная аренда не найдена");
    return this.addEvent(
      String(stage.run_id),
      String(stage.id),
      nodeId,
      level,
      "worker.log",
      message.slice(0, 4_000),
      data,
    );
  }

  completeLease(
    nodeId: string,
    leaseId: string,
    output: string,
    workerArtifacts: WorkerArtifactInput[] = [],
    executionMetrics: WorkerExecutionMetrics = {},
  ): { runCompleted: boolean; processInstanceId: string | null } {
    const metrics = normalizeExecutionMetrics(executionMetrics);
    let completedRunId: string | null = null;
    const result = this.transaction(() => {
      const stage = this.db
        .prepare(`
          SELECT s.*, r.result_destination, r.artifact_path, a.name AS agent_name
          FROM stages s
          JOIN runs r ON r.id = s.run_id
          JOIN agents a ON a.id = s.agent_id
          WHERE s.node_id = ? AND s.lease_id = ? AND s.status = 'running'
        `)
        .get(nodeId, leaseId) as Row | undefined;
      if (!stage) throw new Error("Активная аренда не найдена");
      if (output.length > 900_000) throw new Error("Результат этапа превышает лимит 900000 символов");
      const executingMcpCalls = Number((this.db.prepare(`
        SELECT COUNT(*) AS count FROM mcp_tool_calls
        WHERE lease_id = ? AND status = 'executing'
      `).get(leaseId) as Row).count);
      if (executingMcpCalls > 0) {
        throw new Error("Нельзя завершить аренду, пока MCP-вызов ещё выполняется");
      }
      this.expireWaitingMcpCallsForLease(leaseId, "Lease завершён до получения approval");
      completedRunId = String(stage.run_id);
      const snapshot = parseAgentSnapshot(stage.agent_snapshot_json);
      if (snapshot) stage.agent_name = snapshot.name;
      const timestamp = nowIso();

      this.db
        .prepare(`
          UPDATE stages
          SET status = 'completed', output = ?, lease_expires_at = NULL,
              metrics_json = ?, completed_at = ?, updated_at = ?
          WHERE id = ?
        `)
        .run(output, JSON.stringify(metrics), timestamp, timestamp, String(stage.id));
      const routing = parseJson<ModelRoutingDecision | null>(stage.routing_json, null);
      const benchmarkModel = metrics.model ?? routing?.selectedModel ?? null;
      if (benchmarkModel) this.recordModelBenchmark(nodeId, benchmarkModel, metrics, timestamp);
      let storedArtifacts = 0;
      const artifactDestination = normalizeResultDestination(stage.result_destination) === "artifacts";
      const a2aFileOutput = Boolean(this.db.prepare(`
        SELECT t.id FROM a2a_tasks t JOIN a2a_endpoints e ON e.id = t.endpoint_id
        WHERE t.run_id = ? AND e.file_artifacts_enabled = 1 LIMIT 1
      `).get(String(stage.run_id)));
      if (artifactDestination) {
        this.persistStageOutputArtifact(stage, output);
        storedArtifacts += 1;
      }
      if (artifactDestination || a2aFileOutput) {
        storedArtifacts += this.persistWorkerArtifacts(stage, workerArtifacts);
      }
      this.addEvent(String(stage.run_id), String(stage.id), nodeId, "info", "stage.completed", "Этап завершён", {
        kind: "output",
        characters: output.length,
        storedArtifacts,
        metrics,
      });

      if (stage.stage_kind === "compensation") {
        const compensation = this.db.prepare(`
          SELECT instance_id FROM process_compensations WHERE compensation_stage_id = ?
        `).get(String(stage.id)) as Row | undefined;
        this.completeCompensation(stage, output);
        return {
          runCompleted: false,
          processInstanceId: compensation ? String(compensation.instance_id) : null,
        };
      }

      const processInstance = this.db
        .prepare("SELECT * FROM process_instances WHERE run_id = ?")
        .get(String(stage.run_id)) as Row | undefined;
      if (processInstance) {
        const graph = parseJson<ProcessGraph>(processInstance.graph_json, { nodes: [], edges: [] });
        const processNodeId = typeof stage.process_node_id === "string" ? stage.process_node_id : "";
        const processNode = graph.nodes.find((node) => node.id === processNodeId);
        this.db
          .prepare("UPDATE process_instances SET last_output = ?, updated_at = ? WHERE id = ?")
          .run(output, timestamp, String(processInstance.id));
        if (!processNode || !["agent", "http"].includes(processNode.type)) {
          this.failProcessInstance(
            String(processInstance.id),
            String(stage.run_id),
            "Не удалось сопоставить завершённый lease с шагом процесса",
          );
          return { runCompleted: false, processInstanceId: String(processInstance.id) };
        }
        if (processNode.type === "http") {
          this.armProcessCompensation(processInstance, stage, processNode, output);
        }
        const nextEdge = outgoingEdge(graph, processNode.id, "default");
        if (!nextEdge) {
          this.failProcessInstance(
            String(processInstance.id),
            String(stage.run_id),
            `У шага «${processNode.name}» отсутствует следующий переход`,
          );
          return { runCompleted: false, processInstanceId: String(processInstance.id) };
        }
        const tokenId = typeof stage.process_token_id === "string" ? stage.process_token_id : "";
        if (!tokenId) {
          this.failProcessInstance(String(processInstance.id), String(stage.run_id), "У process stage отсутствует execution token");
          return { runCompleted: false, processInstanceId: String(processInstance.id) };
        }
        const processCompleted = this.advanceProcessToken(String(processInstance.id), tokenId, nextEdge.target, output);
        this.syncProcessAggregateStatus(String(processInstance.id));
        if (processCompleted && normalizeResultDestination(stage.result_destination) === "artifacts") {
          this.persistFinalResultArtifact(stage, output);
        }
        const parentLink = this.db.prepare(`
          SELECT parent_instance_id FROM process_subprocess_links WHERE child_instance_id = ?
        `).get(String(processInstance.id)) as Row | undefined;
        return {
          runCompleted: processCompleted,
          processInstanceId: parentLink ? String(parentLink.parent_instance_id) : String(processInstance.id),
        };
      }

      const next = this.db
        .prepare("SELECT * FROM stages WHERE run_id = ? AND position > ? ORDER BY position LIMIT 1")
        .get(String(stage.run_id), Number(stage.position)) as Row | undefined;
      if (!next) {
        this.db
          .prepare("UPDATE runs SET status = 'completed', completed_at = ?, updated_at = ? WHERE id = ?")
          .run(timestamp, timestamp, String(stage.run_id));
        if (normalizeResultDestination(stage.result_destination) === "artifacts") {
          this.persistFinalResultArtifact(stage, output);
        }
        this.addEvent(String(stage.run_id), null, nodeId, "info", "run.completed", "Цепочка агентов завершена", null);
        return { runCompleted: true, processInstanceId: null };
      }

      if (Number(next.requires_approval) === 1) {
        this.db
          .prepare("UPDATE stages SET status = 'waiting_approval', updated_at = ? WHERE id = ?")
          .run(timestamp, String(next.id));
        this.db
          .prepare("UPDATE runs SET status = 'waiting_approval', updated_at = ? WHERE id = ?")
          .run(timestamp, String(stage.run_id));
        this.addEvent(
          String(stage.run_id),
          String(next.id),
          null,
          "warn",
          "approval.requested",
          "Следующий этап ожидает подтверждения",
          { position: next.position },
        );
      } else {
        this.db.prepare("UPDATE stages SET status = 'queued', updated_at = ? WHERE id = ?").run(timestamp, String(next.id));
        this.db.prepare("UPDATE runs SET status = 'running', updated_at = ? WHERE id = ?").run(timestamp, String(stage.run_id));
      }
      return { runCompleted: false, processInstanceId: null };
    });
    const spanAttributes: Record<string, string | number | boolean> = {
      "agat.output.characters": output.length,
    };
    if (metrics.durationMs !== undefined) spanAttributes["agat.stage.duration_ms"] = metrics.durationMs;
    if (metrics.modelCalls !== undefined) spanAttributes["agat.model.calls"] = metrics.modelCalls;
    if (metrics.toolCalls !== undefined) spanAttributes["agat.tool.calls"] = metrics.toolCalls;
    if (metrics.inputTokens !== undefined) spanAttributes["gen_ai.usage.input_tokens"] = metrics.inputTokens;
    if (metrics.outputTokens !== undefined) spanAttributes["gen_ai.usage.output_tokens"] = metrics.outputTokens;
    if (metrics.model) spanAttributes["gen_ai.response.model"] = metrics.model;
    this.telemetry.endStage(leaseId, { status: "completed", attributes: spanAttributes });
    if (completedRunId) {
      const terminal = this.db.prepare("SELECT status FROM runs WHERE id = ?").get(completedRunId) as Row | undefined;
      if (terminal?.status === "completed") this.telemetry.endRun(completedRunId, { status: "completed" });
      else if (terminal?.status === "failed") this.telemetry.endRun(completedRunId, { status: "failed", errorType: "ProcessFailed" });
      else if (terminal?.status === "cancelled") this.telemetry.endRun(completedRunId, { status: "cancelled" });
    }
    return result;
  }

  failLease(nodeId: string, leaseId: string, errorMessage: string): { retrying: boolean; processInstanceId: string | null } {
    let failedRunId: string | null = null;
    const result = this.transaction(() => {
      const stage = this.db
        .prepare("SELECT * FROM stages WHERE node_id = ? AND lease_id = ? AND status = 'running'")
        .get(nodeId, leaseId) as Row | undefined;
      if (!stage) throw new Error("Активная аренда не найдена");
      failedRunId = String(stage.run_id);
      const processInstance = this.db.prepare("SELECT id FROM process_instances WHERE run_id = ?")
        .get(String(stage.run_id)) as Row | undefined;
      this.expireWaitingMcpCallsForLease(leaseId, "Worker завершил lease с ошибкой");
      const sideEffectGuard = this.leaseHasUncertainMcpSideEffects(leaseId);
      const retrying = Number(stage.attempt) < Number(stage.max_attempts) && !sideEffectGuard;
      const timestamp = nowIso();

      this.db
        .prepare(`
          UPDATE stages
          SET status = ?, node_id = NULL, lease_id = NULL, lease_expires_at = NULL, updated_at = ?
          WHERE id = ?
        `)
        .run(retrying ? "queued" : "failed", timestamp, String(stage.id));
      if (retrying) {
        const retryStatus = stage.stage_kind === "compensation" ? "compensating" : "queued";
        this.db.prepare("UPDATE runs SET status = ?, updated_at = ? WHERE id = ?")
          .run(retryStatus, timestamp, String(stage.run_id));
        this.db.prepare("UPDATE process_instances SET status = ?, updated_at = ? WHERE run_id = ?")
          .run(retryStatus, timestamp, String(stage.run_id));
      } else {
        if (stage.stage_kind === "compensation" && processInstance) {
          this.db.prepare(`
            UPDATE process_compensations
            SET status = 'failed', error = ?, completed_at = ?
            WHERE compensation_stage_id = ?
          `).run(errorMessage.slice(0, 4_000), timestamp, String(stage.id));
          this.db.prepare(`
            UPDATE process_instances SET compensation_error = ?, status = 'compensating', updated_at = ? WHERE id = ?
          `).run(errorMessage.slice(0, 4_000), timestamp, String(processInstance.id));
          this.db.prepare(`UPDATE runs SET status = 'compensating', updated_at = ? WHERE id = ?`)
            .run(timestamp, String(stage.run_id));
          this.queueNextCompensation(String(processInstance.id), String(stage.run_id));
        } else if (processInstance) {
          this.triggerProcessFailure(String(processInstance.id), String(stage.run_id), errorMessage, "failed");
        } else {
          this.db.prepare("UPDATE runs SET status = 'failed', completed_at = ?, updated_at = ? WHERE id = ?")
            .run(timestamp, timestamp, String(stage.run_id));
        }
      }
      this.addEvent(
        String(stage.run_id),
        String(stage.id),
        nodeId,
        "error",
        retrying ? "stage.retrying" : "stage.failed",
        errorMessage.slice(0, 4_000),
        { attempt: stage.attempt, maxAttempts: stage.max_attempts, sideEffectGuard },
      );
      const parentLink = processInstance ? this.db.prepare(`
        SELECT parent_instance_id FROM process_subprocess_links WHERE child_instance_id = ?
      `).get(String(processInstance.id)) as Row | undefined : undefined;
      return {
        retrying,
        processInstanceId: parentLink
          ? String(parentLink.parent_instance_id)
          : processInstance ? String(processInstance.id) : null,
      };
    });
    this.telemetry.endStage(leaseId, {
      status: "failed",
      errorType: errorMessage.split(":", 1)[0]?.slice(0, 120) || "WorkerError",
    });
    if (!result.retrying && failedRunId) {
      const run = this.db.prepare(`SELECT status FROM runs WHERE id = ?`).get(failedRunId) as Row | undefined;
      if (run?.status === "failed" || run?.status === "cancelled") {
        this.telemetry.endRun(failedRunId, {
          status: String(run.status) as "failed" | "cancelled",
          errorType: errorMessage.split(":", 1)[0]?.slice(0, 120) || "WorkerError",
        });
      }
    }
    return result;
  }

  decideApproval(stageId: string, approved: boolean, projectId = "default"): string | null {
    const project = this.requireProject(projectId);
    return this.transaction(() => {
      const stage = this.db
        .prepare(`
          SELECT s.* FROM stages s JOIN runs r ON r.id = s.run_id
          WHERE s.id = ? AND s.status = 'waiting_approval' AND r.project_id = ?
        `)
        .get(stageId, project) as Row | undefined;
      if (!stage) throw new Error("Запрос подтверждения не найден");
      const processInstance = this.db.prepare("SELECT * FROM process_instances WHERE run_id = ?")
        .get(String(stage.run_id)) as Row | undefined;
      const timestamp = nowIso();
      if (approved) {
        if (stage.stage_kind === "approval") {
          this.db.prepare(`
            UPDATE stages
            SET status = 'completed', output = ?, started_at = COALESCE(started_at, ?), completed_at = ?, updated_at = ?
            WHERE id = ?
          `).run(stage.stage_input ?? "", timestamp, timestamp, timestamp, String(stage.id));
          this.db.prepare("UPDATE runs SET status = 'running', updated_at = ? WHERE id = ?").run(timestamp, String(stage.run_id));
          this.addEvent(String(stage.run_id), stageId, null, "info", "approval.approved", "Действие разрешено оператором", {
            processNodeId: stage.process_node_id,
            stageKind: "approval",
          });
          if (!processInstance || typeof stage.process_node_id !== "string") {
            return processInstance ? String(processInstance.id) : null;
          }
          const graph = parseJson<ProcessGraph>(processInstance.graph_json, { nodes: [], edges: [] });
          const edge = outgoingEdge(graph, stage.process_node_id, "default");
          if (!edge) {
            this.failProcessInstance(String(processInstance.id), String(stage.run_id), "После подтверждения отсутствует переход");
            return String(processInstance.id);
          }
          const tokenId = typeof stage.process_token_id === "string" ? stage.process_token_id : "";
          if (!tokenId) {
            this.failProcessInstance(String(processInstance.id), String(stage.run_id), "У approval stage отсутствует execution token");
            return String(processInstance.id);
          }
          this.advanceProcessToken(
            String(processInstance.id),
            tokenId,
            edge.target,
            typeof stage.stage_input === "string" ? stage.stage_input : null,
          );
          this.syncProcessAggregateStatus(String(processInstance.id));
          return String(processInstance.id);
        }
        this.db.prepare("UPDATE stages SET status = 'queued', updated_at = ? WHERE id = ?").run(timestamp, String(stage.id));
        this.db.prepare("UPDATE runs SET status = 'running', updated_at = ? WHERE id = ?").run(timestamp, String(stage.run_id));
        this.db
          .prepare("UPDATE process_instances SET status = 'queued', updated_at = ? WHERE run_id = ?")
          .run(timestamp, String(stage.run_id));
        this.addEvent(String(stage.run_id), stageId, null, "info", "approval.approved", "Действие разрешено оператором", null);
        if (processInstance) this.syncProcessAggregateStatus(String(processInstance.id));
      } else {
        this.db.prepare("UPDATE stages SET status = 'cancelled', updated_at = ? WHERE id = ?").run(timestamp, String(stage.id));
        this.addEvent(String(stage.run_id), stageId, null, "warn", "approval.rejected", "Действие отклонено оператором", null);
        if (processInstance) {
          this.triggerProcessFailure(String(processInstance.id), String(stage.run_id), "Действие отклонено оператором", "cancelled");
        } else {
          this.db.prepare("UPDATE runs SET status = 'cancelled', completed_at = ?, updated_at = ? WHERE id = ?")
            .run(timestamp, timestamp, String(stage.run_id));
          this.telemetry.endRun(String(stage.run_id), { status: "cancelled" });
        }
      }
      return processInstance ? String(processInstance.id) : null;
    });
  }

  getOverview(projectId = "default"): Record<string, unknown> {
    const project = this.requireProject(projectId);
    this.completeAutomaticWaitStages();
    this.cleanupExpiredMcpCalls();
    this.cleanupExpiredLeases();
    const schedulerMode = this.getSetting("scheduler_mode") ?? "sequential";
    const globalMaxConcurrency = Number(this.getSetting("global_max_concurrency") ?? "1");
    const modelRouterPolicy = this.getModelRouterPolicy();
    const nodes = (this.db
      .prepare(`
        SELECT n.*,
          (SELECT COUNT(*) FROM stages s WHERE s.node_id = n.id AND s.status = 'running')
          + (SELECT COUNT(*) FROM knowledge_embedding_jobs j WHERE j.node_id = n.id AND j.status = 'running') AS used_concurrency
        FROM nodes n
        ORDER BY CASE n.status WHEN 'online' THEN 0 WHEN 'sleeping' THEN 1 ELSE 2 END, n.rowid
      `)
      .all() as Row[]).map((row) => this.nodeDto(row));

    const runRows = this.db
      .prepare(`
        SELECT * FROM runs
        WHERE project_id = ?
        ORDER BY
          CASE status
            WHEN 'running' THEN 0
            WHEN 'compensating' THEN 1
            WHEN 'queued' THEN 2
            WHEN 'waiting_approval' THEN 3
            WHEN 'waiting_external' THEN 4
            ELSE 5
          END,
          created_at DESC
        LIMIT 100
      `)
      .all(project) as Row[];
    const runs = runRows.map((run) => this.runDto(run));
    const events = (this.db.prepare(`
      SELECT e.* FROM events e
      LEFT JOIN runs r ON r.id = e.run_id
      WHERE e.project_id IN ('global', ?)
      ORDER BY e.id DESC LIMIT 80
    `).all(project) as Row[])
      .reverse()
      .map((row) => this.eventDto(row));
    const approvals = this.db
      .prepare(`
        SELECT s.id AS stage_id, s.run_id, s.stage_kind, s.activity_json,
          r.name AS run_name, a.name AS agent_name, r.input
        FROM stages s
        JOIN runs r ON r.id = s.run_id
        JOIN agents a ON a.id = s.agent_id
        WHERE s.status = 'waiting_approval' AND r.project_id = ?
        ORDER BY r.updated_at ASC
      `)
      .all(project) as Row[];
    const mcpApprovals = this.db.prepare(`
      SELECT id, run_id, stage_id, server_name, public_name, risk, risk_tier,
        policy, required_approvals, policy_version, policy_sha256, policy_rule_id,
        policy_reason, arguments_summary_json, preview_diff_json, created_at
      FROM mcp_tool_calls
      WHERE project_id = ? AND status = 'waiting_approval' AND expires_at >= ?
      ORDER BY created_at ASC
    `).all(project, nowIso()) as Row[];
    const agents = this.listAgents(project);
    const credentials = this.listCredentials(project);
    const processes = this.listProcesses(project);
    const processInstances = this.listProcessInstances(100, project);
    const knowledge = this.knowledgeOverview(project);
    const models = [
      ...new Set([
        ...nodes.flatMap((node) => node.models as string[]),
        ...agents.flatMap((agent) => typeof agent.model === "string" ? [agent.model] : []),
      ]),
    ];
    const onlineModels = [
      ...new Set(
        nodes
          .filter((node) => node.status === "online")
          .flatMap((node) => node.models as string[]),
      ),
    ];
    const runCounts = this.db
      .prepare(`
        SELECT
          COUNT(*) AS total_runs,
          SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued_runs,
          SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS active_runs,
          SUM(CASE WHEN status = 'waiting_approval' THEN 1 ELSE 0 END) AS waiting_approvals,
          SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_runs,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_runs
        FROM runs
        WHERE project_id = ?
      `)
      .get(project) as Row;
    const activeLeases = Number((this.db.prepare(`
      SELECT
        (SELECT COUNT(*) FROM stages WHERE status = 'running')
        + (SELECT COUNT(*) FROM knowledge_embedding_jobs WHERE status = 'running') AS count
    `).get() as Row).count);
    const onlineNodes = nodes.filter((node) => node.status === "online");
    const onlineProfiles = onlineNodes.flatMap((node) => node.modelProfiles as Array<Record<string, unknown>>);
    const benchmarkTimestamps = onlineProfiles
      .map((profile) => (profile.benchmark as Record<string, unknown> | null)?.lastObservedAt)
      .filter((value): value is string => typeof value === "string");
    const agentsById = new Map(agents.map((agent) => [String(agent.id), agent]));
    const readyAgents = agents.filter((agent) => {
      const runtimeConfig = normalizeAgentRuntimeConfig(agent.runtimeConfig);
      const specialistModels = runtimeConfig.profile === "specialist_team_v1"
        ? runtimeConfig.specialistAgentIds.map((id) => agentsById.get(id)?.model)
        : [];
      const requestedModels = [agent.model, ...specialistModels]
        .filter((model): model is string => typeof model === "string" && model.length > 0);
      const requestedRuntime = normalizeAgentRuntime(agent.runtime);
      return onlineNodes.some((node) => {
        const advertisedModels = node.models as string[];
        const advertisedRuntimes = normalizeAgentRuntimes(node.agentRuntimes);
        const advertisedProfiles = normalizeAgentRuntimeProfiles(node.agentRuntimeProfiles);
        const modelCompatible = advertisedModels.length === 0
          || requestedModels.every((model) => advertisedModels.includes(model));
        const profileCompatible = requestedRuntime !== "langgraph"
          || advertisedProfiles.includes(runtimeConfig.profile);
        return modelCompatible && advertisedRuntimes.includes(requestedRuntime) && profileCompatible;
      });
    }).length;

    return {
      generatedAt: nowIso(),
      project: this.listProjects().find((candidate) => candidate.id === project) ?? null,
      scheduler: {
        mode: schedulerMode,
        globalMaxConcurrency,
        activeLeases,
      },
      modelRouter: {
        policy: modelRouterPolicy,
        profiledModels: onlineProfiles.filter((profile) => profile.profileKnown === true).length,
        benchmarkedModels: onlineProfiles.filter((profile) => profile.benchmark !== null).length,
        lastBenchmarkAt: benchmarkTimestamps.sort().at(-1) ?? null,
      },
      counts: {
        queued: Number(runCounts.queued_runs),
        activeRuns: Number(runCounts.active_runs),
        waitingApprovals: Number(runCounts.waiting_approvals) + mcpApprovals.length,
        completedRuns: Number(runCounts.completed_runs),
        failedRuns: Number(runCounts.failed_runs),
        totalRuns: Number(runCounts.total_runs),
        agents: agents.length,
        readyAgents,
        nodes: nodes.length,
        onlineNodes: onlineNodes.length,
        models: onlineModels.length,
        totalModels: models.length,
        processes: processes.length,
        publishedProcesses: processes.filter((process) => Number(process.publishedVersion) > 0).length,
        activeProcessInstances: processInstances.filter((instance) =>
          ["queued", "running", "waiting_approval"].includes(String(instance.status)),
        ).length,
      },
      agents,
      credentials,
      processes,
      processInstances,
      runs,
      nodes,
      models,
      approvals: [
        ...approvals.map((row) => ({
        kind: "stage",
        stageId: row.stage_id,
        runId: row.run_id,
        runName: row.run_name,
        agentName: row.stage_kind === "approval" ? "Ручное подтверждение" : row.agent_name,
        summary: row.stage_kind === "approval"
          ? String(parseJson<Record<string, unknown>>(row.activity_json, {}).message ?? row.input).slice(0, 180)
          : String(row.input).slice(0, 180),
      })),
        ...mcpApprovals.map((row) => {
          const approvers = (this.db.prepare(`
            SELECT actor_display FROM mcp_tool_call_approvals
            WHERE call_id = ? AND decision = 'approve' ORDER BY created_at, actor_subject
          `).all(String(row.id)) as Row[]).map((approval) => String(approval.actor_display));
          return {
          kind: "mcp_tool",
          callId: row.id,
          stageId: row.stage_id,
          runId: row.run_id,
          runName: `MCP · ${String(row.server_name)}`,
          agentName: String(row.public_name),
          summary: `Риск: ${String(row.risk_tier)} · approvals ${approvers.length}/${Number(row.required_approvals)} · ${JSON.stringify(parseJson<Record<string, unknown>>(row.arguments_summary_json, {})).slice(0, 300)}`,
          toolName: row.public_name,
          risk: row.risk,
          riskTier: row.risk_tier,
          policy: row.policy,
          requiredApprovals: Number(row.required_approvals),
          approvalCount: approvers.length,
          approvers,
          policyVersion: Number(row.policy_version),
          policySha256: row.policy_sha256,
          policyRuleId: row.policy_rule_id,
          policyReason: row.policy_reason,
          arguments: parseJson<Record<string, unknown>>(row.arguments_summary_json, {}),
          previewDiff: parseJson<Array<Record<string, unknown>>>(row.preview_diff_json, []),
          createdAt: row.created_at,
        };
        }),
      ],
      mcp: {
        servers: this.listMcpServers(project),
        recentCalls: this.listRecentMcpToolCalls(project, 100),
        policy: this.getMcpPolicySnapshot(project),
      },
      knowledge,
      events,
    };
  }

  getRun(runId: string, projectId = "default"): Record<string, unknown> | null {
    const run = this.db.prepare("SELECT * FROM runs WHERE id = ? AND project_id = ?").get(runId, normalizeProjectId(projectId)) as Row | undefined;
    return run ? this.runDto(run) : null;
  }

  getRunTrace(runId: string, projectId = "default"): Record<string, unknown> | null {
    const project = normalizeProjectId(projectId);
    const run = this.db.prepare("SELECT * FROM runs WHERE id = ? AND project_id = ?").get(runId, project) as Row | undefined;
    if (!run) return null;
    const eventRows = this.db
      .prepare("SELECT * FROM events WHERE run_id = ? ORDER BY id ASC LIMIT 10001")
      .all(runId) as Row[];
    const truncated = eventRows.length > 10_000;
    const events = eventRows.slice(0, 10_000).map((row) => this.eventDto(row));
    const artifacts = this.listRunArtifacts(runId);
    const goldenEvaluation = this.goldenEvaluationForRun(runId, project);
    return {
      run: this.runDto(run),
      events,
      artifacts,
      manifest: this.executionManifest(run),
      comparison: typeof run.evaluation_group_id === "string" && !goldenEvaluation
        ? this.evaluationComparison(run.evaluation_group_id, project)
        : null,
      goldenEvaluation,
      truncated,
      tracePolicy: {
        rawReasoningStored: false,
        observableProgressStored: true,
        exactInputsStored: true,
        exactOutputsStored: true,
        toolArguments: "redacted",
      },
    };
  }

  private goldenEvaluationForRun(runId: string, projectId: string): Record<string, unknown> | null {
    const link = this.db.prepare(`
      SELECT i.id AS item_id, i.experiment_id, i.run_id, i.judge_run_id
      FROM eval_experiment_items i
      JOIN eval_experiments e ON e.id = i.experiment_id
      WHERE e.project_id = ? AND (i.run_id = ? OR i.judge_run_id = ?)
      LIMIT 1
    `).get(projectId, runId, runId) as Row | undefined;
    if (!link) return null;
    this.refreshEvalExperiment(String(link.experiment_id), projectId);
    const experiment = this.db.prepare("SELECT * FROM eval_experiments WHERE id = ?")
      .get(String(link.experiment_id)) as Row;
    const state = this.evalExperimentState(experiment, false);
    const item = (state.items as Array<Record<string, unknown>>)
      .find((candidate) => candidate.id === link.item_id) ?? null;
    return {
      experimentId: String(link.experiment_id),
      experimentName: String(experiment.name),
      itemId: String(link.item_id),
      runKind: link.judge_run_id === runId ? "model_judge" : "candidate",
      datasetId: String(experiment.dataset_id),
      datasetVersion: Number(experiment.dataset_version),
      promptId: String(experiment.prompt_id),
      promptVersion: Number(experiment.prompt_version),
      minQualityScore: Number(experiment.min_quality_score),
      qualityScore: item?.qualityScore ?? null,
      qualitySource: item?.qualitySource ?? null,
      gates: state.gates,
    };
  }

  replayRun(
    runId: string,
    input: ReplayRunInput,
    projectId = "default",
  ): { evaluationGroupId: string; runs: Array<{ id: string; status: string; variantName: string }> } {
    const project = this.requireProject(projectId);
    const source = this.db.prepare("SELECT * FROM runs WHERE id = ? AND project_id = ?").get(runId, project) as Row | undefined;
    if (!source) throw new Error("Запуск не найден");
    if (!["completed", "failed", "cancelled"].includes(String(source.status))) {
      throw new Error("Replay доступен только для завершённого запуска");
    }
    const processInstance = this.db.prepare("SELECT id FROM process_instances WHERE run_id = ?").get(runId) as Row | undefined;
    if (processInstance) {
      throw new Error("Replay процесса отключён: он может повторить HTTP или другой внешний side effect");
    }
    const sourceStages = this.db.prepare(`
      SELECT s.*, a.name AS fallback_name, a.role AS fallback_role,
        a.system_prompt, a.model, a.runtime, a.runtime_config_json
      FROM stages s JOIN agents a ON a.id = s.agent_id
      WHERE s.run_id = ? ORDER BY s.position
    `).all(runId) as Row[];
    if (sourceStages.length === 0 || sourceStages.some((stage) => stage.stage_kind !== "agent")) {
      throw new Error("Replay разрешён только для запусков, состоящих из агентных этапов");
    }
    const snapshots = sourceStages.map((stage) => {
      const snapshot = parseAgentSnapshot(stage.agent_snapshot_json);
      if (!snapshot) throw new Error("Execution manifest запуска неполон; безопасный replay невозможен");
      return snapshot;
    });
    const knownAgentIds = new Set(snapshots.map((snapshot) => snapshot.id));
    const rawVariants = input.variants ?? [{ name: "Повтор" }];
    if (!Array.isArray(rawVariants) || rawVariants.length < 1 || rawVariants.length > 2) {
      throw new Error("За один replay можно создать одну или две версии");
    }
    const variants = rawVariants.map((variant, index) => {
      if (!variant || typeof variant !== "object") throw new Error("Некорректная версия replay");
      const name = typeof variant.name === "string" ? variant.name.trim() : "";
      if (!name || name.length > 60) throw new Error(`Название версии ${index + 1}: от 1 до 60 символов`);
      const overrides: Record<string, string | null> = {};
      if (variant.modelOverrides !== undefined) {
        if (!variant.modelOverrides || typeof variant.modelOverrides !== "object" || Array.isArray(variant.modelOverrides)) {
          throw new Error(`modelOverrides версии «${name}» должен быть объектом`);
        }
        for (const [agentId, rawModel] of Object.entries(variant.modelOverrides)) {
          if (!knownAgentIds.has(agentId)) throw new Error(`Версия «${name}» содержит неизвестного агента ${agentId}`);
          if (rawModel === null) overrides[agentId] = null;
          else if (typeof rawModel === "string" && rawModel.trim() && rawModel.trim().length <= 200) {
            overrides[agentId] = rawModel.trim();
          } else {
            throw new Error(`Некорректная модель агента ${agentId} в версии «${name}»`);
          }
        }
      }
      return { name, modelOverrides: overrides };
    });
    if (new Set(variants.map((variant) => variant.name.toLocaleLowerCase("ru"))).size !== variants.length) {
      throw new Error("Названия версий replay должны различаться");
    }

    const evaluationGroupId = typeof source.evaluation_group_id === "string"
      ? source.evaluation_group_id
      : randomUUID();
    const timestamp = nowIso();
    const created: Array<{ id: string; status: string; variantName: string }> = [];
    const startedRunIds: string[] = [];
    try {
      this.transaction(() => {
        this.db.prepare("UPDATE runs SET evaluation_group_id = ?, updated_at = ? WHERE id = ?")
          .run(evaluationGroupId, timestamp, runId);
        if (typeof source.replay_of_run_id === "string") {
          this.db.prepare("UPDATE runs SET evaluation_group_id = ?, updated_at = ? WHERE id = ? AND project_id = ?")
            .run(evaluationGroupId, timestamp, source.replay_of_run_id, project);
        }
        const insertRun = this.db.prepare(`
          INSERT INTO runs(
            id, name, input, status, execution_mode, priority, approval_required,
            result_destination, artifact_path, project_id, trace_id, root_span_id,
            replay_of_run_id, evaluation_group_id, variant_name, knowledge_collection_ids_json,
            created_at, updated_at
          ) VALUES (?, ?, ?, 'queued', ?, ?, 0, 'history', '', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        `);
        const insertStage = this.db.prepare(`
          INSERT INTO stages(
            id, run_id, agent_id, position, status, requires_approval,
            agent_snapshot_json, created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
        `);
        for (const variant of variants) {
          const replayId = randomUUID();
          const runName = `${String(source.name)} · ${variant.name}`.slice(0, 120);
          const runTrace = this.telemetry.startRun({
            runId: replayId,
            projectId: project,
            replayOfRunId: runId,
            evaluationGroupId,
          });
          startedRunIds.push(replayId);
          insertRun.run(
            replayId,
            runName,
            String(source.input),
            String(source.execution_mode),
            Number(source.priority),
            project,
            runTrace.traceId,
            runTrace.spanId,
            runId,
            evaluationGroupId,
            variant.name,
            typeof source.knowledge_collection_ids_json === "string" ? source.knowledge_collection_ids_json : "[]",
            timestamp,
            timestamp,
          );
          snapshots.forEach((snapshot, index) => {
            const replaySnapshot = agentSnapshot({
              id: snapshot.id,
              name: snapshot.name,
              role: snapshot.role,
              system_prompt: snapshot.systemPrompt,
              model: snapshot.model,
              runtime: snapshot.runtime,
              runtime_config_json: JSON.stringify(snapshot.runtimeConfig),
              registry_prompt_id: snapshot.registryPromptId,
              registry_prompt_version: snapshot.registryPromptVersion,
            }, "replay", timestamp, Object.hasOwn(variant.modelOverrides, snapshot.id)
              ? variant.modelOverrides[snapshot.id]
              : undefined, snapshot.specialists);
            insertStage.run(
              randomUUID(),
              replayId,
              snapshot.id,
              index,
              index === 0 ? "queued" : "pending",
              JSON.stringify(replaySnapshot),
              timestamp,
              timestamp,
            );
          });
          this.addEvent(replayId, null, null, "info", "run.replayed", "Создан воспроизводимый replay запуска", {
            sourceRunId: runId,
            evaluationGroupId,
            variantName: variant.name,
            modelOverrides: variant.modelOverrides,
            traceId: runTrace.traceId,
          });
          created.push({ id: replayId, status: "queued", variantName: variant.name });
        }
      });
    } catch (error) {
      for (const replayId of startedRunIds) {
        this.telemetry.endRun(replayId, { status: "failed", errorType: error instanceof Error ? error.name : "Error" });
      }
      throw error;
    }
    this.addEvent(runId, null, null, "info", "evaluation.replay.created", "Создана группа сравнения replay", {
      evaluationGroupId,
      candidateRunIds: created.map((run) => run.id),
    });
    return { evaluationGroupId, runs: created };
  }

  private executionManifest(run: Row): Record<string, unknown> {
    const stages = (this.db.prepare(`
      SELECT s.*, a.name AS fallback_name, a.role AS fallback_role,
        a.system_prompt, a.model, a.runtime, a.runtime_config_json
      FROM stages s JOIN agents a ON a.id = s.agent_id
      WHERE s.run_id = ? ORDER BY s.position
    `).all(String(run.id)) as Row[]).map((stage) => {
      const snapshot = parseAgentSnapshot(stage.agent_snapshot_json)
        ?? agentSnapshot({
          id: stage.agent_id,
          name: stage.fallback_name,
          role: stage.fallback_role,
          system_prompt: stage.system_prompt,
          model: stage.model,
          runtime: stage.runtime,
          runtime_config_json: stage.runtime_config_json,
        }, "migration_backfill", String(stage.created_at));
      const worker = parseJson<Record<string, unknown> | null>(stage.worker_snapshot_json, null);
      const metrics = normalizeExecutionMetrics(parseJson<Record<string, unknown>>(stage.metrics_json, {}));
      const routing = parseJson<ModelRoutingDecision | null>(stage.routing_json, null);
      return {
        stageId: stage.id,
        position: Number(stage.position),
        kind: stage.stage_kind,
        processNodeId: typeof stage.process_node_id === "string" ? stage.process_node_id : null,
        stageInputSha256: typeof stage.stage_input === "string" ? sha256Text(stage.stage_input) : null,
        agent: snapshot,
        worker,
        routing,
        metrics,
      };
    });
    const base = {
      schemaVersion: 3,
      runId: String(run.id),
      traceId: String(run.trace_id),
      sourceRunId: typeof run.replay_of_run_id === "string" ? run.replay_of_run_id : null,
      inputSha256: sha256Text(String(run.input)),
      executionMode: String(run.execution_mode),
      knowledgeCollectionIds: normalizeKnowledgeCollectionIds(
        parseJson<unknown>(run.knowledge_collection_ids_json, []),
      ),
      createdAt: String(run.created_at),
      stages,
      guarantees: {
        promptAndAgentConfigImmutable: true,
        secretsIncluded: false,
        externalToolResultsDeterministic: false,
        processSideEffectsReplayable: false,
      },
    };
    return { ...base, manifestSha256: sha256Text(JSON.stringify(base)) };
  }

  private evaluationComparison(evaluationGroupId: string, projectId: string): Record<string, unknown> {
    const rows = this.db.prepare(`
      SELECT * FROM runs WHERE evaluation_group_id = ? AND project_id = ? ORDER BY created_at ASC
    `).all(evaluationGroupId, projectId) as Row[];
    const summaries = rows.map((row) => {
      const stages = this.db.prepare(`
        SELECT metrics_json, output FROM stages WHERE run_id = ? ORDER BY position
      `).all(String(row.id)) as Row[];
      const totals = stages.reduce((result, stage) => {
        const metrics = normalizeExecutionMetrics(parseJson<Record<string, unknown>>(stage.metrics_json, {}));
        result.durationMs += metrics.durationMs ?? 0;
        result.modelCalls += metrics.modelCalls ?? 0;
        result.inputTokens += metrics.inputTokens ?? 0;
        result.outputTokens += metrics.outputTokens ?? 0;
        result.toolCalls += metrics.toolCalls ?? 0;
        result.hasTokens ||= metrics.inputTokens !== undefined || metrics.outputTokens !== undefined;
        return result;
      }, { durationMs: 0, modelCalls: 0, inputTokens: 0, outputTokens: 0, toolCalls: 0, hasTokens: false });
      const finalOutput = [...stages].reverse().find((stage) => typeof stage.output === "string" && stage.output)?.output;
      const wallDurationMs = typeof row.started_at === "string" && typeof row.completed_at === "string"
        ? Math.max(0, new Date(row.completed_at).getTime() - new Date(row.started_at).getTime())
        : null;
      return {
        runId: String(row.id),
        sourceRunId: typeof row.replay_of_run_id === "string" ? row.replay_of_run_id : null,
        variantName: typeof row.variant_name === "string" && row.variant_name ? row.variant_name : "Оригинал",
        status: String(row.status),
        wallDurationMs,
        stageDurationMs: totals.durationMs || null,
        modelCalls: totals.modelCalls,
        inputTokens: totals.hasTokens ? totals.inputTokens : null,
        outputTokens: totals.hasTokens ? totals.outputTokens : null,
        toolCalls: totals.toolCalls,
        outputCharacters: typeof finalOutput === "string" ? finalOutput.length : 0,
        outputSha256: typeof finalOutput === "string" ? sha256Text(finalOutput) : null,
      };
    });
    const baseline = summaries.find((summary) => summary.sourceRunId === null) ?? summaries[0] ?? null;
    const baselineLatencyMs = baseline?.stageDurationMs ?? baseline?.wallDurationMs ?? null;
    return {
      evaluationGroupId,
      baselineRunId: baseline?.runId ?? null,
      runs: summaries.map((summary) => ({
        ...summary,
        gates: summary.runId === baseline?.runId ? null : {
          completion: summary.status === "completed" ? "pass" : summary.status === "failed" ? "fail" : "pending",
          latency: baselineLatencyMs !== null && (summary.stageDurationMs ?? summary.wallDurationMs) !== null
            ? ((summary.stageDurationMs ?? summary.wallDurationMs)! <= baselineLatencyMs * 1.25 ? "pass" : "fail")
            : "pending",
          tokenBudget: baseline?.outputTokens !== null && baseline?.outputTokens !== undefined
            && summary.outputTokens !== null
            ? (summary.outputTokens <= Math.max(1, baseline.outputTokens) * 1.25 ? "pass" : "fail")
            : "pending",
          quality: "not_evaluated",
        },
      })),
      gatePolicy: {
        latencyMetric: "stage_duration_then_wall",
        latencyRatioMax: 1.25,
        outputTokenRatioMax: 1.25,
        quality: "manual_or_golden_dataset_required",
      },
    };
  }

  listRunArtifacts(runId: string): Array<Record<string, unknown>> {
    return (this.db
      .prepare("SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at ASC, name ASC")
      .all(runId) as Row[]).map((row) => this.artifactDto(row));
  }

  getArtifactDownload(artifactId: string, projectId = "default"): { artifact: Record<string, unknown>; filePath: string } | null {
    const row = this.db.prepare(`
      SELECT a.* FROM artifacts a JOIN runs r ON r.id = a.run_id
      WHERE a.id = ? AND r.project_id = ?
    `).get(artifactId, normalizeProjectId(projectId)) as Row | undefined;
    if (!row) return null;
    const relativePath = String(row.relative_path);
    const segments = relativePath.split("/");
    if (segments.some((segment) => !segment || segment === "." || segment === "..")) {
      throw new Error("В метаданных артефакта сохранён небезопасный путь");
    }
    const candidate = path.resolve(this.artifactsDir, ...segments);
    const rootPrefix = `${path.resolve(this.artifactsDir)}${path.sep}`;
    if (!candidate.startsWith(rootPrefix)) throw new Error("Путь артефакта вышел за пределы хранилища");
    if (!fs.existsSync(this.artifactsDir) || fs.lstatSync(this.artifactsDir).isSymbolicLink()) {
      throw new Error("Хранилище артефактов недоступно");
    }
    let current = this.artifactsDir;
    for (const segment of segments) {
      current = path.join(current, segment);
      if (!fs.existsSync(current)) throw new Error("Файл артефакта не найден");
      const status = fs.lstatSync(current);
      if (status.isSymbolicLink()) throw new Error("Путь артефакта содержит символическую ссылку");
    }
    if (!fs.lstatSync(candidate).isFile()) throw new Error("Артефакт не является обычным файлом");
    return { artifact: this.artifactDto(row), filePath: candidate };
  }

  listEvents(afterId = 0, limit = 200, projectId = "default"): EventRecord[] {
    return (this.db
      .prepare(`
        SELECT e.* FROM events e LEFT JOIN runs r ON r.id = e.run_id
        WHERE e.id > ? AND e.project_id IN ('global', ?)
        ORDER BY e.id ASC LIMIT ?
      `)
      .all(afterId, normalizeProjectId(projectId), clampInteger(limit, 1, 1_000, 200)) as Row[]).map((row) => this.eventDto(row));
  }

  recordPlatformEvent(
    type: string,
    message: string,
    data: Record<string, unknown> | null = null,
    level = "info",
  ): EventRecord {
    return this.addEvent(null, null, null, level, type, message, data);
  }

  markWorkerPoolOffline(poolId: string): number {
    const rows = this.db.prepare("SELECT id, labels_json FROM nodes").all() as Row[];
    const matchingIds = rows
      .filter((row) => parseJson<Record<string, string>>(row.labels_json, {}).pool === poolId)
      .map((row) => String(row.id));
    if (matchingIds.length === 0) return 0;
    const timestamp = nowIso();
    const update = this.db.prepare("UPDATE nodes SET status = 'offline', updated_at = ? WHERE id = ?");
    for (const id of matchingIds) update.run(timestamp, id);
    return matchingIds.length;
  }

  deleteWorkerPoolNodes(poolId: string): number {
    const matchingIds = this.managedWorkerNodeIds((labels) => labels.pool === poolId);
    if (matchingIds.length === 0) return 0;
    const remove = this.db.prepare("DELETE FROM nodes WHERE id = ?");
    for (const id of matchingIds) remove.run(id);
    return matchingIds.length;
  }

  reconcileWorkerPoolNodes(activePoolIds: Set<string>): number {
    const staleIds = this.managedWorkerNodeIds((labels) => {
      const poolId = labels.pool;
      return typeof poolId === "string" && !activePoolIds.has(poolId);
    });
    if (staleIds.length === 0) return 0;
    const remove = this.db.prepare("DELETE FROM nodes WHERE id = ?");
    for (const id of staleIds) remove.run(id);
    return staleIds.length;
  }

  private managedWorkerNodeIds(predicate: (labels: Record<string, string>) => boolean): string[] {
    const rows = this.db.prepare("SELECT id, labels_json FROM nodes").all() as Row[];
    return rows.flatMap((row) => {
      const labels = parseJson<Record<string, string>>(row.labels_json, {});
      return labels.managed === "local-launcher" && predicate(labels) ? [String(row.id)] : [];
    });
  }

  private expireProcessSignals(instanceId?: string): void {
    const parameters: SqlScalar[] = [nowIso()];
    const instanceFilter = instanceId ? "AND psw.instance_id = ?" : "";
    if (instanceId) parameters.push(instanceId);
    const expired = this.db.prepare(`
      SELECT psw.*, s.run_id FROM process_signal_waits psw
      JOIN stages s ON s.id = psw.stage_id
      WHERE psw.status = 'waiting' AND psw.expires_at IS NOT NULL AND psw.expires_at <= ?
        ${instanceFilter}
      ORDER BY psw.expires_at
    `).all(...parameters) as Row[];
    for (const wait of expired) {
      const timestamp = nowIso();
      this.db.prepare(`UPDATE process_signal_waits SET status = 'expired', completed_at = ? WHERE id = ? AND status = 'waiting'`)
        .run(timestamp, String(wait.id));
      this.db.prepare(`UPDATE stages SET status = 'failed', completed_at = ?, updated_at = ? WHERE id = ? AND status = 'waiting_external'`)
        .run(timestamp, timestamp, String(wait.stage_id));
      this.triggerProcessFailure(
        String(wait.instance_id),
        String(wait.run_id),
        `Истёк срок ожидания signal «${String(wait.signal_name)}»`,
        "failed",
      );
    }
  }

  private completeAutomaticWaitStages(): void {
    this.completeDueWaitStages(undefined, this.temporalProcesses ? "non-temporal" : undefined);
    this.expireProcessSignals();
  }

  private completeDueWaitStages(instanceId?: string, runtime?: "database" | "temporal" | "embedded" | "non-temporal"): void {
    const filters: string[] = [];
    const parameters: SqlScalar[] = [nowIso()];
    if (instanceId) {
      filters.push("pi.id = ?");
      parameters.push(instanceId);
    }
    if (runtime) {
      if (runtime === "non-temporal") filters.push("pi.runtime <> 'temporal'");
      else {
        filters.push("pi.runtime = ?");
        parameters.push(runtime);
      }
    }
    const due = this.db.prepare(`
      SELECT s.*, pi.id AS instance_id, pi.graph_json
      FROM stages s
      JOIN process_instances pi ON pi.run_id = s.run_id
      JOIN runs r ON r.id = s.run_id
      WHERE s.stage_kind = 'wait' AND s.status = 'queued' AND s.available_at <= ?
        AND r.status IN ('queued', 'running')
        ${filters.length ? `AND ${filters.join(" AND ")}` : ""}
      ORDER BY s.available_at ASC
      LIMIT 100
    `).all(...parameters) as Row[];
    for (const stage of due) {
      const timestamp = nowIso();
      const result = this.db.prepare(`
        UPDATE stages
        SET status = 'completed', output = ?, started_at = COALESCE(started_at, ?), completed_at = ?, updated_at = ?
        WHERE id = ? AND status = 'queued'
      `).run(stage.stage_input ?? "", timestamp, timestamp, timestamp, String(stage.id));
      if (Number(result.changes) !== 1) continue;
      this.db.prepare("UPDATE runs SET status = 'running', updated_at = ? WHERE id = ?").run(timestamp, String(stage.run_id));
      this.addEvent(String(stage.run_id), String(stage.id), null, "info", "process.wait.completed", "Ожидание завершено", {
        processNodeId: stage.process_node_id,
        availableAt: stage.available_at,
      });
      if (typeof stage.process_node_id !== "string") continue;
      const graph = parseJson<ProcessGraph>(stage.graph_json, { nodes: [], edges: [] });
      const edge = outgoingEdge(graph, stage.process_node_id, "default");
      if (!edge) {
        this.failProcessInstance(String(stage.instance_id), String(stage.run_id), "После ожидания отсутствует переход");
        continue;
      }
      if (typeof stage.process_token_id !== "string") {
        this.failProcessInstance(String(stage.instance_id), String(stage.run_id), "У wait stage отсутствует execution token");
        continue;
      }
      this.advanceProcessToken(String(stage.instance_id), stage.process_token_id, edge.target, typeof stage.stage_input === "string" ? stage.stage_input : null);
      this.syncProcessAggregateStatus(String(stage.instance_id));
    }
  }

  private durableProcessState(instanceId: string, projectId: string): DurableProcessState | null {
    const row = this.db.prepare(`
      SELECT pi.*, p.project_id
      FROM process_instances pi
      JOIN processes p ON p.id = pi.process_id
      WHERE pi.id = ? AND p.project_id = ?
    `).get(instanceId, projectId) as Row | undefined;
    if (!row) return null;
    const graph = parseJson<ProcessGraph>(row.graph_json, { nodes: [], edges: [] });
    const currentNodeId = typeof row.current_node_id === "string" ? row.current_node_id : null;
    const currentNode = currentNodeId ? graph.nodes.find((node) => node.id === currentNodeId) ?? null : null;
    const waitingStage = this.db.prepare(`
      SELECT available_at FROM stages
      WHERE run_id = ? AND process_node_id = ?
        AND ((stage_kind = 'wait' AND status = 'queued') OR (stage_kind = 'signal' AND status = 'waiting_external'))
      ORDER BY position DESC LIMIT 1
    `).get(String(row.run_id), currentNodeId ?? "") as Row | undefined;
    const activeTokens = this.db.prepare(`
      SELECT current_node_id FROM process_tokens
      WHERE instance_id = ? AND status IN ('active', 'forked', 'waiting_join') AND current_node_id IS NOT NULL
      ORDER BY created_at
    `).all(instanceId) as Row[];
    const pendingSignals = this.db.prepare(`
      SELECT signal_name FROM process_signal_waits
      WHERE instance_id = ? AND status = 'waiting'
      ORDER BY created_at
    `).all(instanceId) as Row[];
    return {
      instanceId: String(row.id),
      status: String(row.status) as DurableProcessState["status"],
      currentNodeId,
      currentNodeType: currentNode?.type ?? null,
      waitUntil: typeof waitingStage?.available_at === "string" ? waitingStage.available_at : null,
      transitionCount: Number(row.transition_count),
      activeNodeIds: [...new Set(activeTokens.map((token) => String(token.current_node_id)))],
      pendingSignalNames: [...new Set(pendingSignals.map((signal) => String(signal.signal_name)))],
      updatedAt: String(row.updated_at),
    };
  }

  private cleanupExpiredLeases(): void {
    const expired = this.db
      .prepare(`
        SELECT id, run_id, node_id, lease_id, attempt, max_attempts, stage_kind
        FROM stages WHERE status = 'running' AND lease_expires_at < ?
      `)
      .all(nowIso()) as Row[];
    if (expired.length === 0) return;

    const timestamp = nowIso();
    for (const stage of expired) {
      const sideEffectGuard = typeof stage.lease_id === "string"
        ? this.leaseHasUncertainMcpSideEffects(stage.lease_id)
        : false;
      const retrying = Number(stage.attempt) < Number(stage.max_attempts) && !sideEffectGuard;
      const processInstance = this.db.prepare("SELECT id FROM process_instances WHERE run_id = ?")
        .get(String(stage.run_id)) as Row | undefined;
      if (typeof stage.lease_id === "string") {
        this.telemetry.endStage(stage.lease_id, { status: "failed", errorType: "LeaseExpired" });
      }
      if (retrying) {
        this.db.prepare(`
          UPDATE stages
          SET status = 'queued', node_id = NULL, lease_id = NULL, lease_expires_at = NULL, updated_at = ?
          WHERE id = ?
        `).run(timestamp, String(stage.id));
        const retryStatus = stage.stage_kind === "compensation" ? "compensating" : "queued";
        this.db.prepare("UPDATE runs SET status = ?, updated_at = ? WHERE id = ?")
          .run(retryStatus, timestamp, String(stage.run_id));
        this.db.prepare("UPDATE process_instances SET status = ?, updated_at = ? WHERE run_id = ?")
          .run(retryStatus, timestamp, String(stage.run_id));
      } else {
        this.db.prepare(`
          UPDATE stages
          SET status = 'failed', node_id = NULL, lease_id = NULL, lease_expires_at = NULL,
              completed_at = ?, updated_at = ?
          WHERE id = ?
        `).run(timestamp, timestamp, String(stage.id));
        if (stage.stage_kind === "compensation" && processInstance) {
          this.db.prepare(`
            UPDATE process_compensations
            SET status = 'failed', error = 'LeaseExpired', completed_at = ?
            WHERE compensation_stage_id = ?
          `).run(timestamp, String(stage.id));
          this.db.prepare(`
            UPDATE process_instances
            SET compensation_error = 'LeaseExpired', status = 'compensating', updated_at = ?
            WHERE id = ?
          `).run(timestamp, String(processInstance.id));
          this.db.prepare("UPDATE runs SET status = 'compensating', updated_at = ? WHERE id = ?")
            .run(timestamp, String(stage.run_id));
          this.queueNextCompensation(String(processInstance.id), String(stage.run_id));
        } else if (processInstance) {
          this.triggerProcessFailure(String(processInstance.id), String(stage.run_id), "LeaseExpired", "failed");
        } else {
          this.db.prepare("UPDATE runs SET status = 'failed', completed_at = ?, updated_at = ? WHERE id = ?")
            .run(timestamp, timestamp, String(stage.run_id));
          this.telemetry.endRun(String(stage.run_id), { status: "failed", errorType: "LeaseExpired" });
        }
      }
      this.addEvent(
        String(stage.run_id),
        String(stage.id),
        typeof stage.node_id === "string" ? stage.node_id : null,
        "warn",
        retrying ? "lease.expired" : "stage.failed",
        retrying
          ? "Аренда истекла, этап возвращён в очередь"
          : sideEffectGuard
            ? "Аренда истекла после рискованного MCP-вызова; повтор заблокирован"
            : "Аренда истекла, попытки этапа исчерпаны",
        { attempt: Number(stage.attempt), maxAttempts: Number(stage.max_attempts), retrying, sideEffectGuard },
      );
    }
  }

  private leaseHasUncertainMcpSideEffects(leaseId: string): boolean {
    const row = this.db.prepare(`
      SELECT COUNT(*) AS count FROM mcp_tool_calls
      WHERE lease_id = ? AND risk <> 'read' AND status IN ('executing', 'completed', 'failed')
    `).get(leaseId) as Row;
    return Number(row.count) > 0;
  }

  private expireWaitingMcpCallsForLease(leaseId: string, reason: string): void {
    const rows = this.db.prepare(`
      SELECT id, run_id, stage_id, node_id, public_name FROM mcp_tool_calls
      WHERE lease_id = ? AND status = 'waiting_approval'
    `).all(leaseId) as Row[];
    if (rows.length === 0) return;
    const timestamp = nowIso();
    for (const call of rows) {
      const changed = this.db.prepare(`
        UPDATE mcp_tool_calls SET status = 'expired', error = ?, completed_at = ?, updated_at = ?
        WHERE id = ? AND status = 'waiting_approval'
      `).run(reason.slice(0, 4_000), timestamp, timestamp, String(call.id));
      if (Number(changed.changes) !== 1) continue;
      this.addEvent(
        String(call.run_id),
        String(call.stage_id),
        typeof call.node_id === "string" ? call.node_id : null,
        "warn",
        "mcp.call.cancelled",
        `Ожидающий MCP-вызов ${String(call.public_name)} отменён вместе с lease`,
        { callId: call.id, tool: call.public_name },
      );
    }
  }

  private cleanupExpiredMcpCalls(): void {
    const timestamp = nowIso();
    const expired = this.db.prepare(`
      SELECT c.id, c.run_id, c.stage_id, c.node_id, c.public_name, c.status
      FROM mcp_tool_calls c
      LEFT JOIN stages s ON s.id = c.stage_id AND s.lease_id = c.lease_id
      WHERE c.status IN ('waiting_approval', 'executing')
        AND (c.expires_at < ? OR s.id IS NULL OR s.status <> 'running' OR s.lease_expires_at < ?)
    `).all(timestamp, timestamp) as Row[];
    for (const call of expired) {
      const wasExecuting = call.status === "executing";
      const error = wasExecuting
        ? "Lease или execution window истёк; результат upstream неизвестен"
        : "Lease или approval истёк";
      this.db.prepare(`
        UPDATE mcp_tool_calls SET status = 'expired', error = ?,
          completed_at = ?, updated_at = ? WHERE id = ? AND status IN ('waiting_approval', 'executing')
      `).run(error, timestamp, timestamp, String(call.id));
      this.addEvent(
        String(call.run_id),
        String(call.stage_id),
        typeof call.node_id === "string" ? call.node_id : null,
        wasExecuting ? "error" : "warn",
        wasExecuting ? "mcp.call.outcome_unknown" : "mcp.call.expired",
        wasExecuting
          ? `Окно MCP-вызова ${String(call.public_name)} истекло; результат upstream неизвестен`
          : `MCP-вызов ${String(call.public_name)} истёк и не будет выполнен`,
        { callId: call.id, tool: call.public_name, outcomeUnknown: wasExecuting },
      );
    }
  }

  private runDto(run: Row): Record<string, unknown> {
    const stages = (this.db
      .prepare(`
        SELECT
          s.id, s.position, s.status, s.node_id, s.attempt, s.max_attempts,
          s.requires_approval, s.started_at, s.completed_at, s.output, s.process_node_id,
          s.stage_kind, s.stage_input, s.agent_snapshot_json, s.metrics_json,
          s.lease_traceparent, s.worker_snapshot_json, s.routing_json,
          a.id AS agent_id, a.name AS agent_name, a.role AS agent_role,
          a.system_prompt, a.model, a.runtime, a.runtime_config_json,
          n.name AS node_name
        FROM stages s
        JOIN agents a ON a.id = s.agent_id
        LEFT JOIN nodes n ON n.id = s.node_id
        WHERE s.run_id = ?
        ORDER BY s.position
      `)
      .all(String(run.id)) as Row[]).map((stage) => {
        const snapshot = parseAgentSnapshot(stage.agent_snapshot_json)
          ?? agentSnapshot({
            id: stage.agent_id,
            name: stage.agent_name,
            role: stage.agent_role,
            system_prompt: stage.system_prompt,
            model: stage.model,
            runtime: stage.runtime,
            runtime_config_json: stage.runtime_config_json,
          }, "migration_backfill");
        return {
          id: stage.id,
          position: stage.position,
          status: stage.status,
          nodeId: stage.node_id,
          nodeName: stage.node_name,
          attempt: stage.attempt,
          maxAttempts: stage.max_attempts,
          requiresApproval: Number(stage.requires_approval) === 1,
          startedAt: stage.started_at,
          completedAt: stage.completed_at,
          output: stage.output,
          processNodeId: stage.process_node_id,
          kind: stage.stage_kind,
          input: stage.stage_input,
          traceparent: typeof stage.lease_traceparent === "string" ? stage.lease_traceparent : null,
          metrics: normalizeExecutionMetrics(parseJson<Record<string, unknown>>(stage.metrics_json, {})),
          worker: parseJson<Record<string, unknown> | null>(stage.worker_snapshot_json, null),
          routing: parseJson<ModelRoutingDecision | null>(stage.routing_json, null),
          agent: {
            id: snapshot.id,
            name: snapshot.name,
            role: snapshot.role,
            model: snapshot.model,
            runtime: snapshot.runtime,
            runtimeConfig: snapshot.runtimeConfig,
            promptVersion: snapshot.promptVersion,
            definitionVersion: snapshot.definitionVersion,
          },
        };
      });

    const processInstance = this.db
      .prepare(`
        SELECT pi.id, pi.process_id, pi.process_version, p.name AS process_name
        FROM process_instances pi
        JOIN processes p ON p.id = pi.process_id
        WHERE pi.run_id = ?
      `)
      .get(String(run.id)) as Row | undefined;

    return {
      id: run.id,
      name: run.name,
      input: run.input,
      status: run.status,
      executionMode: run.execution_mode,
      priority: run.priority,
      approvalRequired: Number(run.approval_required) === 1,
      resultDestination: normalizeResultDestination(run.result_destination),
      artifactPath: typeof run.artifact_path === "string" && run.artifact_path ? run.artifact_path : null,
      artifactBasePath: normalizeResultDestination(run.result_destination) === "artifacts"
        ? this.artifactRunBase(String(run.id), run.artifact_path)
        : null,
      traceId: run.trace_id,
      replayOfRunId: typeof run.replay_of_run_id === "string" ? run.replay_of_run_id : null,
      evaluationGroupId: typeof run.evaluation_group_id === "string" ? run.evaluation_group_id : null,
      variantName: typeof run.variant_name === "string" && run.variant_name ? run.variant_name : null,
      knowledgeCollectionIds: normalizeKnowledgeCollectionIds(
        parseJson<unknown>(run.knowledge_collection_ids_json, []),
      ),
      createdAt: run.created_at,
      updatedAt: run.updated_at,
      startedAt: run.started_at,
      completedAt: run.completed_at,
      process: processInstance ? {
        instanceId: processInstance.id,
        processId: processInstance.process_id,
        name: processInstance.process_name,
        version: processInstance.process_version,
      } : null,
      stages,
    };
  }

  private knownAgentIds(projectId = "default"): Set<string> {
    return new Set((this.db
      .prepare(`
        SELECT id FROM agents
        WHERE id <> '__agat_eval_judge__'
          AND (is_builtin = 1 OR project_id = ?)
      `)
      .all(normalizeProjectId(projectId)) as Row[]).map((row) => String(row.id)));
  }

  private effectiveAgentRows(projectId: string): Row[] {
    const project = normalizeProjectId(projectId);
    this.ensurePromptRegistry(project);
    const rows = this.db.prepare(`
      SELECT a.*,
        CASE WHEN pr.id IS NOT NULL THEN pv.content ELSE a.system_prompt END AS effective_system_prompt,
        CASE WHEN pr.id IS NOT NULL THEN pr.active_model ELSE a.model END AS effective_model,
        pr.id AS registry_prompt_id,
        pr.active_version AS registry_prompt_version
      FROM agents a
      LEFT JOIN prompt_registry pr ON pr.project_id = ? AND pr.agent_id = a.id
      LEFT JOIN prompt_versions pv ON pv.prompt_id = pr.id AND pv.version = pr.active_version
      WHERE a.is_builtin = 1 OR a.project_id = ?
    `).all(project, project) as Row[];
    return rows.map((row) => ({
      ...row,
      system_prompt: row.effective_system_prompt ?? row.system_prompt ?? "",
      model: row.effective_model ?? null,
    }));
  }

  private effectiveAgentRow(agentId: string, projectId: string): Row | null {
    return this.effectiveAgentRows(projectId).find((row) => row.id === agentId) ?? null;
  }

  private validateSpecialistTeamConfig(
    config: AgentRuntimeConfig,
    projectId: string,
    teamAgentId?: string,
  ): void {
    if (config.profile !== "specialist_team_v1") return;
    if (teamAgentId && config.specialistAgentIds.includes(teamAgentId)) {
      throw new Error("Команда не может включать саму себя");
    }
    const rows = new Map(this.effectiveAgentRows(projectId).map((row) => [String(row.id), row]));
    for (const specialistId of config.specialistAgentIds) {
      const specialist = rows.get(specialistId);
      if (!specialist || specialistId === "__agat_eval_judge__" || specialistId === "__agat_system__") {
        throw new Error(`Специалист ${specialistId} не найден в проекте`);
      }
      const specialistConfig = normalizeAgentRuntimeConfig(
        parseJson<Record<string, unknown>>(specialist.runtime_config_json, {}),
      );
      if (specialistConfig.profile !== "tool_loop_v1") {
        throw new Error("Вложенные specialist teams запрещены");
      }
    }
    if (!teamAgentId) return;
    for (const candidate of rows.values()) {
      if (String(candidate.id) === teamAgentId) continue;
      const candidateConfig = normalizeAgentRuntimeConfig(
        parseJson<Record<string, unknown>>(candidate.runtime_config_json, {}),
      );
      if (candidateConfig.profile === "specialist_team_v1"
        && candidateConfig.specialistAgentIds.includes(teamAgentId)) {
        throw new Error("Агент уже используется как specialist и не может стать вложенной командой");
      }
    }
  }

  private captureAgentSnapshot(
    row: Record<string, unknown>,
    source: AgentExecutionSnapshot["source"],
    projectId: string,
    capturedAt = nowIso(),
    modelOverride?: string | null,
  ): AgentExecutionSnapshot {
    const runtimeConfig = normalizeAgentRuntimeConfig(
      parseJson<Record<string, unknown>>(row.runtime_config_json, {}),
    );
    const specialists = runtimeConfig.profile === "specialist_team_v1"
      ? (() => {
        const rows = new Map(this.effectiveAgentRows(projectId).map((candidate) => [String(candidate.id), candidate]));
        return runtimeConfig.specialistAgentIds.map((specialistId) => {
          const specialist = rows.get(specialistId);
          if (!specialist) throw new Error(`Специалист ${specialistId} не найден в проекте`);
          return specialistExecutionSnapshot(specialist);
        });
      })()
      : [];
    return agentSnapshot(row, source, capturedAt, modelOverride, specialists);
  }

  private pinSubprocessVersions(processId: string, graph: ProcessGraph, projectId: string): ProcessGraph {
    const pinned = structuredClone(graph);
    for (const node of pinned.nodes) {
      if (node.type !== "subprocess") continue;
      const targetId = node.config.subprocessProcessId;
      if (!targetId) throw new Error(`Шаг ${node.name}: subprocess не выбран`);
      if (targetId === processId) throw new Error(`Шаг ${node.name}: процесс не может вызвать сам себя`);
      const target = this.db.prepare(`
        SELECT id, published_version FROM processes WHERE id = ? AND project_id = ?
      `).get(targetId, projectId) as Row | undefined;
      if (!target || Number(target.published_version) < 1) {
        throw new Error(`Шаг ${node.name}: subprocess не найден или не опубликован`);
      }
      const version = node.config.subprocessVersion ?? Number(target.published_version);
      const versionRow = this.db.prepare(`
        SELECT graph_json FROM process_versions WHERE process_id = ? AND version = ?
      `).get(targetId, version) as Row | undefined;
      if (!versionRow) throw new Error(`Шаг ${node.name}: версия subprocess ${version} не найдена`);
      node.config.subprocessVersion = version;
    }

    const visit = (candidateProcessId: string, candidateGraph: ProcessGraph, stack: string[], depth: number): void => {
      if (depth > 8) throw new Error("Глубина вложенности subprocess превышает 8");
      for (const node of candidateGraph.nodes) {
        if (node.type !== "subprocess" || !node.config.subprocessProcessId) continue;
        const targetId = node.config.subprocessProcessId;
        if (stack.includes(targetId)) {
          throw new Error(`Обнаружен цикл subprocess: ${[...stack, targetId].join(" → ")}`);
        }
        const target = this.db.prepare(`
          SELECT published_version FROM processes WHERE id = ? AND project_id = ?
        `).get(targetId, projectId) as Row | undefined;
        const version = node.config.subprocessVersion ?? Number(target?.published_version ?? 0);
        const row = this.db.prepare(`
          SELECT graph_json FROM process_versions WHERE process_id = ? AND version = ?
        `).get(targetId, version) as Row | undefined;
        if (!row) throw new Error(`Subprocess ${targetId} v${version} не найден`);
        visit(targetId, parseJson<ProcessGraph>(row.graph_json, { nodes: [], edges: [] }), [...stack, targetId], depth + 1);
      }
    };
    visit(processId, pinned, [processId], 0);
    return pinned;
  }

  private processDto(row: Row): Record<string, unknown> {
    const versions = this.db
      .prepare(`
        SELECT version, published_at
        FROM process_versions
        WHERE process_id = ?
        ORDER BY version DESC
      `)
      .all(String(row.id)) as Row[];
    return {
      id: row.id,
      name: row.name,
      description: row.description,
      isTemplate: Number(row.is_template) === 1,
      status: row.status,
      draftGraph: parseJson<ProcessGraph>(row.draft_graph_json, { nodes: [], edges: [] }),
      publishedVersion: Number(row.published_version),
      hasUnpublishedChanges: Number(row.has_unpublished_changes) === 1,
      versions: versions.map((version) => ({
        version: Number(version.version),
        publishedAt: version.published_at,
      })),
      totalInstances: Number(row.total_instances ?? 0),
      activeInstances: Number(row.active_instances ?? 0),
      lastStartedAt: row.last_started_at,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
      publishedAt: row.published_at,
    };
  }

  private processInstanceDto(row: Row): Record<string, unknown> {
    const graph = parseJson<ProcessGraph>(row.graph_json, { nodes: [], edges: [] });
    const currentNode = graph.nodes.find((node) => node.id === row.current_node_id) ?? null;
    const rawLoopCounts = parseJson<Record<string, unknown>>(row.loop_counts_json, {});
    const loopCounts = Object.fromEntries(
      Object.entries(rawLoopCounts)
        .filter(([, value]) => typeof value === "number" && Number.isInteger(value) && value >= 0)
        .map(([key, value]) => [key, Number(value)]),
    );
    const mostAdvancedLoop = Object.entries(loopCounts).sort((left, right) => right[1] - left[1])[0];
    const loopNode = mostAdvancedLoop
      ? graph.nodes.find((node) => node.id === mostAdvancedLoop[0] && node.type === "loop")
      : null;
    const activeNodeIds = [...new Set((this.db.prepare(`
      SELECT current_node_id FROM process_tokens
      WHERE instance_id = ? AND status IN ('active', 'forked', 'waiting_join') AND current_node_id IS NOT NULL
      ORDER BY created_at
    `).all(String(row.id)) as Row[]).map((token) => String(token.current_node_id)))];
    const pendingSignals = (this.db.prepare(`
      SELECT signal_name, correlation_key, expires_at FROM process_signal_waits
      WHERE instance_id = ? AND status = 'waiting' ORDER BY created_at
    `).all(String(row.id)) as Row[]).map((signal) => ({
      name: signal.signal_name,
      correlationKey: signal.correlation_key,
      expiresAt: signal.expires_at,
    }));
    const compensations = (this.db.prepare(`
      SELECT source_node_id, sequence, status, error FROM process_compensations
      WHERE instance_id = ? ORDER BY sequence DESC
    `).all(String(row.id)) as Row[]).map((compensation) => ({
      processNodeId: compensation.source_node_id,
      sequence: Number(compensation.sequence),
      status: compensation.status,
      error: compensation.error,
    }));
    const embeddedSubprocesses = (this.db.prepare(`
      SELECT child.id, child.process_id, child.process_version, child.status,
             child.runtime, psl.status AS link_status, psl.created_at, psl.completed_at
      FROM process_subprocess_links psl
      JOIN process_instances child ON child.id = psl.child_instance_id
      WHERE psl.parent_instance_id = ?
      ORDER BY psl.created_at, child.id
    `).all(String(row.id)) as Row[]).map((child) => ({
      id: child.id,
      processId: child.process_id,
      processVersion: Number(child.process_version),
      status: child.status,
      runtime: child.runtime,
      linkStatus: child.link_status,
      createdAt: child.created_at,
      completedAt: child.completed_at,
    }));
    return {
      id: row.id,
      processId: row.process_id,
      processName: row.process_name,
      processVersion: Number(row.process_version),
      runId: row.run_id,
      runName: row.run_name,
      input: row.run_input,
      status: row.status,
      currentNode: currentNode ? {
        id: currentNode.id,
        type: currentNode.type,
        name: currentNode.name,
      } : null,
      activeNodes: activeNodeIds.flatMap((nodeId) => {
        const node = graph.nodes.find((candidate) => candidate.id === nodeId);
        return node ? [{ id: node.id, type: node.type, name: node.name }] : [];
      }),
      pendingSignals,
      compensations,
      embeddedSubprocesses,
      loopCounts,
      currentIteration: mostAdvancedLoop?.[1] ?? 0,
      maxIterations: loopNode?.config.maxIterations ?? null,
      transitionCount: Number(row.transition_count),
      error: row.error,
      runtime: row.runtime === "temporal" ? "temporal" : row.runtime === "embedded" ? "embedded" : "database",
      workflowId: typeof row.workflow_id === "string" ? row.workflow_id : null,
      replayOfInstanceId: typeof row.replay_of_instance_id === "string" ? row.replay_of_instance_id : null,
      replayMode: row.replay_mode === "safe" ? "safe" : "live",
      compensationError: typeof row.compensation_error === "string" ? row.compensation_error : null,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
      startedAt: row.started_at,
      completedAt: row.completed_at,
    };
  }

  private safeReplayNodeOutput(
    instance: Row,
    currentRunId: string,
    processNodeId: string,
  ): { output: string; sourceStageId: string } | null {
    if (instance.replay_mode !== "safe" || typeof instance.replay_of_instance_id !== "string") return null;
    const source = this.db.prepare(`SELECT run_id FROM process_instances WHERE id = ?`)
      .get(instance.replay_of_instance_id) as Row | undefined;
    if (!source) throw new Error("Исходный экземпляр safe replay не найден");
    const visit = Number((this.db.prepare(`
      SELECT COUNT(*) AS count FROM stages WHERE run_id = ? AND process_node_id = ?
    `).get(currentRunId, processNodeId) as Row).count);
    const stage = this.db.prepare(`
      SELECT id, output FROM stages
      WHERE run_id = ? AND process_node_id = ? AND status = 'completed'
      ORDER BY position ASC LIMIT 1 OFFSET ?
    `).get(String(source.run_id), processNodeId, visit) as Row | undefined;
    if (!stage || typeof stage.output !== "string") {
      throw new Error(`Safe replay не нашёл записанный результат side effect ${processNodeId}, visit ${visit + 1}`);
    }
    return { output: stage.output, sourceStageId: String(stage.id) };
  }

  private advanceProcessInstance(instanceId: string, initialNodeId: string, lastOutput: string | null): boolean {
    const existing = this.db.prepare(`
      SELECT id FROM process_tokens
      WHERE instance_id = ? AND parent_token_id IS NULL
      ORDER BY created_at LIMIT 1
    `).get(instanceId) as Row | undefined;
    const tokenId = existing ? String(existing.id) : randomUUID();
    if (!existing) {
      const timestamp = nowIso();
      this.db.prepare(`
        INSERT INTO process_tokens(
          id, instance_id, parent_token_id, fork_node_id, branch_edge_id,
          current_node_id, last_output, status, created_at, updated_at
        ) VALUES (?, ?, NULL, NULL, NULL, ?, ?, 'active', ?, ?)
      `).run(tokenId, instanceId, initialNodeId, lastOutput, timestamp, timestamp);
    }
    return this.advanceProcessToken(instanceId, tokenId, initialNodeId, lastOutput);
  }

  private advanceProcessToken(
    instanceId: string,
    tokenId: string,
    initialNodeId: string,
    lastOutput: string | null,
  ): boolean {
    const instance = this.db.prepare("SELECT * FROM process_instances WHERE id = ?").get(instanceId) as Row | undefined;
    if (!instance) throw new Error("Экземпляр процесса не найден");
    const token = this.db.prepare("SELECT * FROM process_tokens WHERE id = ? AND instance_id = ?")
      .get(tokenId, instanceId) as Row | undefined;
    if (!token) throw new Error("Execution token процесса не найден");
    const graph = parseJson<ProcessGraph>(instance.graph_json, { nodes: [], edges: [] });
    const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
    const loopCounts = parseJson<Record<string, number>>(instance.loop_counts_json, {});
    const runId = String(instance.run_id);
    const run = this.db.prepare("SELECT input, artifact_path FROM runs WHERE id = ?").get(runId) as Row;
    const expressionContext = () => ({
      input: String(run.input),
      lastOutput,
      loopCounts,
    });
    let currentNodeId = initialNodeId;
    let transitionCount = Number(instance.transition_count);
    let internalTransitions = 0;

    while (internalTransitions < 128) {
      internalTransitions += 1;
      transitionCount += 1;
      if (transitionCount > 1_000) {
        this.failProcessInstance(instanceId, runId, "Превышен общий лимит переходов процесса (1000)");
        return false;
      }
      const node = nodesById.get(currentNodeId);
      if (!node) {
        this.failProcessInstance(instanceId, runId, `Шаг ${currentNodeId} не найден в снимке процесса`);
        return false;
      }

      this.db
        .prepare(`
          UPDATE process_instances
          SET current_node_id = ?, last_output = ?, loop_counts_json = ?,
              transition_count = ?, updated_at = ?
          WHERE id = ?
        `)
        .run(node.id, lastOutput, JSON.stringify(loopCounts), transitionCount, nowIso(), instanceId);
      this.db.prepare(`
        UPDATE process_tokens SET current_node_id = ?, last_output = ?, status = 'active', updated_at = ? WHERE id = ?
      `).run(node.id, lastOutput, nowIso(), tokenId);
      this.addEvent(runId, null, null, "debug", "process.node.entered", `Переход к шагу «${node.name}»`, {
        processInstanceId: instanceId,
        processNodeId: node.id,
        processNodeType: node.type,
        transitionCount,
      });

      if (node.type === "agent") {
        this.queueProcessAgent(instanceId, tokenId, runId, node, lastOutput, loopCounts, transitionCount);
        return false;
      }
      if (node.type === "http") {
        const replay = this.safeReplayNodeOutput(instance, runId, node.id);
        if (replay) {
          this.recordInternalStage(runId, tokenId, node, "http", lastOutput, replay.output);
          lastOutput = replay.output;
          this.addEvent(runId, null, null, "info", "process.replay.side_effect_reused", `Replay переиспользовал результат «${node.name}»`, {
            processInstanceId: instanceId,
            processNodeId: node.id,
            sourceStageId: replay.sourceStageId,
          });
        } else {
          this.queueProcessHttp(instanceId, tokenId, runId, node, lastOutput, loopCounts, transitionCount, expressionContext());
          return false;
        }
      }
      if (node.type === "wait") {
        this.queueProcessWait(instanceId, tokenId, runId, node, lastOutput, loopCounts, transitionCount);
        return false;
      }
      if (node.type === "approval") {
        this.queueProcessApproval(instanceId, tokenId, runId, node, lastOutput, loopCounts, transitionCount);
        return false;
      }
      if (node.type === "signal") {
        this.queueProcessSignal(instanceId, tokenId, runId, node, lastOutput, loopCounts, transitionCount, expressionContext());
        return false;
      }
      if (node.type === "subprocess") {
        const replay = this.safeReplayNodeOutput(instance, runId, node.id);
        if (replay) {
          this.recordInternalStage(runId, tokenId, node, "subprocess", lastOutput, replay.output);
          lastOutput = replay.output;
          this.addEvent(runId, null, null, "info", "process.replay.side_effect_reused", `Replay переиспользовал subprocess «${node.name}»`, {
            processInstanceId: instanceId,
            processNodeId: node.id,
            sourceStageId: replay.sourceStageId,
          });
        } else {
          this.queueProcessSubprocess(instanceId, tokenId, runId, node, lastOutput, loopCounts, transitionCount, expressionContext());
          return false;
        }
      }
      if (node.type === "transform") {
        const output = renderProcessTemplate(node.config.template ?? "", expressionContext());
        this.recordInternalStage(runId, tokenId, node, "transform", lastOutput, output);
        lastOutput = output;
        this.addEvent(runId, null, null, "info", "process.transform.completed", `Преобразование «${node.name}» выполнено`, {
          processInstanceId: instanceId,
          processNodeId: node.id,
          outputCharacters: output.length,
        });
      }
      if (node.type === "artifact") {
        const name = safeArtifactName(renderProcessTemplate(node.config.artifactName ?? "artifact.txt", expressionContext()));
        const content = renderProcessTemplate(node.config.artifactContent ?? "", expressionContext());
        const mediaType = node.config.artifactMediaType && /^[\w.+-]+\/[\w.+-]+(?:;\s*charset=[\w-]+)?$/i.test(node.config.artifactMediaType)
          ? node.config.artifactMediaType
          : "text/plain; charset=utf-8";
        const stage = this.recordInternalStage(runId, tokenId, node, "artifact", lastOutput, lastOutput ?? "");
        const position = String(Number(stage.position) + 1).padStart(2, "0");
        this.persistArtifact(stage, name, "process_artifact", mediaType, content, path.posix.join("artifacts", `${position}-${name}`));
        this.addEvent(runId, String(stage.id), null, "info", "process.artifact.completed", `Артефакт «${name}» сохранён`, {
          processInstanceId: instanceId,
          processNodeId: node.id,
          sizeBytes: Buffer.byteLength(content, "utf8"),
        });
      }
      if (node.type === "parallel_fork") {
        const branches = outgoingEdges(graph, node.id);
        const join = graph.nodes.find((candidate) =>
          candidate.type === "parallel_join" && candidate.config.forkId === node.id
        );
        if (!join || branches.length < 2) {
          this.failProcessInstance(instanceId, runId, `Fork «${node.name}» не имеет корректного парного join`);
          return false;
        }
        const timestamp = nowIso();
        this.db.prepare(`UPDATE process_tokens SET status = 'forked', updated_at = ? WHERE id = ?`)
          .run(timestamp, tokenId);
        const children = branches.map((edge) => ({ edge, tokenId: randomUUID() }));
        const insertToken = this.db.prepare(`
          INSERT INTO process_tokens(
            id, instance_id, parent_token_id, fork_node_id, branch_edge_id,
            current_node_id, last_output, status, created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        `);
        for (const child of children) {
          insertToken.run(
            child.tokenId,
            instanceId,
            tokenId,
            node.id,
            child.edge.id,
            child.edge.target,
            lastOutput,
            timestamp,
            timestamp,
          );
        }
        this.addEvent(runId, null, null, "info", "process.parallel.forked", `Fork «${node.name}» запустил ${children.length} ветки`, {
          processInstanceId: instanceId,
          processNodeId: node.id,
          joinNodeId: join.id,
          tokenId,
          branches: children.map((child) => ({ edgeId: child.edge.id, tokenId: child.tokenId, target: child.edge.target })),
        });
        let completed = false;
        for (const child of children) {
          completed = this.advanceProcessToken(instanceId, child.tokenId, child.edge.target, lastOutput) || completed;
        }
        this.syncProcessAggregateStatus(instanceId);
        return completed;
      }
      if (node.type === "parallel_join") {
        const currentToken = this.db.prepare(`SELECT * FROM process_tokens WHERE id = ?`).get(tokenId) as Row;
        const forkId = String(currentToken.fork_node_id ?? "");
        const parentTokenId = typeof currentToken.parent_token_id === "string" ? currentToken.parent_token_id : "";
        if (!forkId || !parentTokenId || forkId !== node.config.forkId) {
          this.failProcessInstance(instanceId, runId, `Join «${node.name}» получил ветку от другого fork`);
          return false;
        }
        const timestamp = nowIso();
        this.db.prepare(`
          INSERT OR IGNORE INTO process_join_arrivals(
            instance_id, join_node_id, token_id, fork_node_id, branch_edge_id, output, arrived_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?)
        `).run(instanceId, node.id, tokenId, forkId, String(currentToken.branch_edge_id), lastOutput, timestamp);
        this.db.prepare(`UPDATE process_tokens SET status = 'waiting_join', current_node_id = ?, last_output = ?, updated_at = ? WHERE id = ?`)
          .run(node.id, lastOutput, timestamp, tokenId);
        const expectedEdges = outgoingEdges(graph, forkId);
        const arrivals = this.db.prepare(`
          SELECT pja.* FROM process_join_arrivals pja
          JOIN process_tokens pt ON pt.id = pja.token_id
          WHERE pja.instance_id = ? AND pja.join_node_id = ? AND pja.fork_node_id = ?
            AND pt.parent_token_id = ? AND pt.status = 'waiting_join'
        `).all(instanceId, node.id, forkId, parentTokenId) as Row[];
        this.addEvent(runId, null, null, "info", "process.parallel.arrived", `Ветка достигла join «${node.name}»`, {
          processInstanceId: instanceId,
          processNodeId: node.id,
          forkNodeId: forkId,
          tokenId,
          arrived: arrivals.length,
          expected: expectedEdges.length,
        });
        if (arrivals.length < expectedEdges.length) {
          this.syncProcessAggregateStatus(instanceId);
          return false;
        }
        const arrivalsByEdge = new Map(arrivals.map((arrival) => [String(arrival.branch_edge_id), arrival]));
        const mergedOutput = JSON.stringify(expectedEdges.map((edge) => ({
          branch: edge.id,
          output: String(arrivalsByEdge.get(edge.id)?.output ?? "").slice(0, 100_000),
        })));
        this.db.prepare(`
          UPDATE process_tokens SET status = 'merged', updated_at = ?
          WHERE instance_id = ? AND parent_token_id = ? AND fork_node_id = ?
        `).run(timestamp, instanceId, parentTokenId, forkId);
        this.db.prepare(`
          UPDATE process_tokens
          SET status = 'active', current_node_id = ?, last_output = ?, updated_at = ?
          WHERE id = ?
        `).run(node.id, mergedOutput, timestamp, parentTokenId);
        this.db.prepare(`UPDATE process_instances SET last_output = ?, updated_at = ? WHERE id = ?`)
          .run(mergedOutput, timestamp, instanceId);
        this.addEvent(runId, null, null, "info", "process.parallel.joined", `Join «${node.name}» объединил ${arrivals.length} ветки`, {
          processInstanceId: instanceId,
          processNodeId: node.id,
          forkNodeId: forkId,
          parentTokenId,
          outputs: arrivals.length,
        });
        const next = outgoingEdge(graph, node.id, "default");
        if (!next) {
          this.failProcessInstance(instanceId, runId, `После join «${node.name}» отсутствует переход`);
          return false;
        }
        return this.advanceProcessToken(instanceId, parentTokenId, next.target, mergedOutput);
      }
      if (node.type === "end") {
        const timestamp = nowIso();
        this.db.prepare(`UPDATE process_tokens SET status = 'completed', current_node_id = ?, last_output = ?, updated_at = ? WHERE id = ?`)
          .run(node.id, lastOutput, timestamp, tokenId);
        const otherActive = Number((this.db.prepare(`
          SELECT COUNT(*) AS count FROM process_tokens
          WHERE instance_id = ? AND id <> ? AND status IN ('active', 'forked', 'waiting_join')
        `).get(instanceId, tokenId) as Row).count);
        if (otherActive > 0) {
          this.syncProcessAggregateStatus(instanceId);
          return false;
        }
        this.db
          .prepare(`
            UPDATE process_instances
            SET status = 'completed', current_node_id = ?, last_output = ?,
                loop_counts_json = ?, transition_count = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
          `)
          .run(node.id, lastOutput, JSON.stringify(loopCounts), transitionCount, timestamp, timestamp, instanceId);
        this.db
          .prepare("UPDATE runs SET status = 'completed', completed_at = ?, updated_at = ? WHERE id = ?")
          .run(timestamp, timestamp, runId);
        this.addEvent(runId, null, null, "info", "process.instance.completed", "Процесс завершён", {
          processInstanceId: instanceId,
          processNodeId: node.id,
          loopCounts,
        });
        this.addEvent(runId, null, null, "info", "run.completed", "Цепочка процесса завершена", null);
        this.db.prepare(`
          UPDATE process_compensations SET status = 'skipped', completed_at = ?
          WHERE instance_id = ? AND status = 'armed'
        `).run(timestamp, instanceId);
        this.telemetry.endRun(runId, { status: "completed" });
        this.completeParentSubprocess(instanceId);
        return true;
      }

      let branch: "default" | "true" | "false" | "repeat" | "exit" = "default";
      if (node.type === "condition") {
        const result = evaluateProcessCondition(node.config.condition!, lastOutput);
        branch = result ? "true" : "false";
        this.addEvent(runId, null, null, "info", "process.condition.evaluated", `Условие «${node.name}»: ${result ? "да" : "нет"}`, {
          processInstanceId: instanceId,
          processNodeId: node.id,
          branch,
          operator: node.config.condition!.operator,
        });
      }
      if (node.type === "loop") {
        const conditionMatched = evaluateProcessCondition(node.config.condition!, lastOutput);
        const previousCount = Number(loopCounts[node.id] ?? 0);
        const maxIterations = node.config.maxIterations ?? 3;
        const shouldRepeat = conditionMatched && previousCount < maxIterations;
        if (shouldRepeat) loopCounts[node.id] = previousCount + 1;
        branch = shouldRepeat ? "repeat" : "exit";
        this.addEvent(runId, null, null, "info", "process.loop.evaluated", shouldRepeat
          ? `Цикл «${node.name}»: итерация ${previousCount + 1} из ${maxIterations}`
          : `Цикл «${node.name}»: выход`, {
          processInstanceId: instanceId,
          processNodeId: node.id,
          branch,
          iteration: Number(loopCounts[node.id] ?? 0),
          maxIterations,
          conditionMatched,
          limitReached: conditionMatched && previousCount >= maxIterations,
        });
      }

      const edge = outgoingEdge(graph, node.id, branch);
      if (!edge) {
        this.failProcessInstance(instanceId, runId, `Для ветки ${branch} шага «${node.name}» отсутствует переход`);
        return false;
      }
      this.addEvent(runId, null, null, "debug", "process.transition", `${node.name} → ${nodesById.get(edge.target)?.name ?? edge.target}`, {
        processInstanceId: instanceId,
        sourceProcessNodeId: node.id,
        targetProcessNodeId: edge.target,
        branch,
      });
      currentNodeId = edge.target;
    }

    this.failProcessInstance(instanceId, runId, "Превышен лимит мгновенных переходов процесса (128)");
    return false;
  }

  private queueProcessAgent(
    instanceId: string,
    tokenId: string,
    runId: string,
    node: ProcessGraphNode,
    lastOutput: string | null,
    loopCounts: Record<string, number>,
    transitionCount: number,
  ): void {
    const agentId = node.config.agentId;
    const projectId = String((this.db.prepare("SELECT project_id FROM runs WHERE id = ?").get(runId) as Row).project_id);
    if (!agentId || !this.knownAgentIds(projectId).has(agentId)) {
      this.failProcessInstance(instanceId, runId, `Агент шага «${node.name}» не найден`);
      return;
    }
    const agentRow = this.effectiveAgentRow(agentId, projectId) ?? undefined;
    if (!agentRow) {
      this.failProcessInstance(instanceId, runId, `Агент шага «${node.name}» не найден`);
      return;
    }
    const position = Number(
      (this.db.prepare("SELECT COALESCE(MAX(position), -1) + 1 AS position FROM stages WHERE run_id = ?").get(runId) as Row)
        .position,
    );
    const timestamp = nowIso();
    const requiresApproval = node.config.approvalRequired === true;
    const status = requiresApproval ? "waiting_approval" : "queued";
    const stageId = randomUUID();
    this.db
      .prepare(`
        INSERT INTO stages(
          id, run_id, agent_id, position, status, requires_approval,
          process_node_id, process_token_id, stage_kind, stage_input, agent_snapshot_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'agent', ?, ?, ?, ?)
      `)
      .run(
        stageId,
        runId,
        agentId,
        position,
        status,
        requiresApproval ? 1 : 0,
        node.id,
        tokenId,
        lastOutput,
        JSON.stringify(this.captureAgentSnapshot(agentRow, "process_queue", projectId, timestamp)),
        timestamp,
        timestamp,
      );
    this.db
      .prepare("UPDATE runs SET status = ?, updated_at = ? WHERE id = ?")
      .run(status === "queued" ? "queued" : "waiting_approval", timestamp, runId);
    this.db
      .prepare(`
        UPDATE process_instances
        SET status = ?, current_node_id = ?, last_output = ?, loop_counts_json = ?,
            transition_count = ?, updated_at = ?
        WHERE id = ?
      `)
      .run(status, node.id, lastOutput, JSON.stringify(loopCounts), transitionCount, timestamp, instanceId);
    this.addEvent(runId, stageId, null, "info", "process.agent.queued", `Шаг «${node.name}» добавлен в очередь`, {
      processInstanceId: instanceId,
      processNodeId: node.id,
      agentId,
      position,
    });
    if (requiresApproval) {
      this.addEvent(runId, stageId, null, "warn", "approval.requested", `Шаг «${node.name}» ожидает подтверждения`, {
        processInstanceId: instanceId,
        processNodeId: node.id,
        position,
      });
    }
  }

  private nextStagePosition(runId: string): number {
    return Number(
      (this.db.prepare("SELECT COALESCE(MAX(position), -1) + 1 AS position FROM stages WHERE run_id = ?").get(runId) as Row)
        .position,
    );
  }

  private recordInternalStage(
    runId: string,
    tokenId: string,
    node: ProcessGraphNode,
    kind: "transform" | "artifact" | "http" | "subprocess",
    input: string | null,
    output: string,
  ): Row {
    const position = this.nextStagePosition(runId);
    const timestamp = nowIso();
    const stageId = randomUUID();
    this.db.prepare(`
      INSERT INTO stages(
        id, run_id, agent_id, position, status, output, requires_approval,
        process_node_id, process_token_id, stage_kind, stage_input, created_at, updated_at, started_at, completed_at
      ) VALUES (?, ?, '__agat_system__', ?, 'completed', ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(stageId, runId, position, output, node.id, tokenId, kind, input, timestamp, timestamp, timestamp, timestamp);
    this.addEvent(runId, stageId, null, "info", "stage.completed", `Шаг «${node.name}» завершён`, {
      kind: "output",
      processNodeId: node.id,
      stageKind: kind,
      outputCharacters: output.length,
    });
    return {
      id: stageId,
      run_id: runId,
      position,
      artifact_path: (this.db.prepare("SELECT artifact_path FROM runs WHERE id = ?").get(runId) as Row).artifact_path ?? "",
    };
  }

  private queueProcessHttp(
    instanceId: string,
    tokenId: string,
    runId: string,
    node: ProcessGraphNode,
    lastOutput: string | null,
    loopCounts: Record<string, number>,
    transitionCount: number,
    context: { input: string; lastOutput: string | null; loopCounts: Record<string, number> },
  ): void {
    const projectId = String((this.db.prepare("SELECT project_id FROM runs WHERE id = ?").get(runId) as Row).project_id);
    if (node.config.credentialId) {
      try {
        if (!this.httpCredentialData(node.config.credentialId, projectId)) {
          this.failProcessInstance(instanceId, runId, `Credentials шага «${node.name}» не найдены`);
          return;
        }
      } catch (error) {
        this.failProcessInstance(instanceId, runId, error instanceof Error ? error.message : `Credentials шага «${node.name}» недоступны`);
        return;
      }
    }
    if (node.config.compensation?.credentialId) {
      try {
        if (!this.httpCredentialData(node.config.compensation.credentialId, projectId)) {
          this.failProcessInstance(instanceId, runId, `Compensation credentials шага «${node.name}» не найдены`);
          return;
        }
      } catch (error) {
        this.failProcessInstance(instanceId, runId, error instanceof Error ? error.message : `Compensation credentials шага «${node.name}» недоступны`);
        return;
      }
    }
    const position = this.nextStagePosition(runId);
    const visit = Number((this.db.prepare(`
      SELECT COUNT(*) AS count FROM stages WHERE run_id = ? AND process_node_id = ?
    `).get(runId, node.id) as Row).count) + 1;
    const idempotencyKey = `agat-${createHash("sha256").update(`${instanceId}:${node.id}:${visit}`).digest("hex")}`;
    const headers = Object.fromEntries(Object.entries(node.config.headers ?? {}).map(([name, value]) => [
      name,
      renderProcessTemplate(value, context),
    ]));
    if ((node.config.method ?? "GET") !== "GET") {
      headers[node.config.idempotencyHeader ?? "Idempotency-Key"] = idempotencyKey;
    }
    const activity = {
      credentialId: node.config.credentialId ?? "",
      idempotencyHeader: (node.config.method ?? "GET") === "GET"
        ? ""
        : node.config.idempotencyHeader ?? "Idempotency-Key",
      request: {
        method: node.config.method ?? "GET",
        url: renderProcessTemplate(node.config.url ?? "", context),
        headers,
        body: ["GET", "DELETE"].includes(node.config.method ?? "GET")
          ? null
          : renderProcessTemplate(node.config.body ?? "", context),
        timeoutSeconds: node.config.timeoutSeconds ?? 30,
      },
    };
    const timestamp = nowIso();
    const stageId = randomUUID();
    this.db.prepare(`
      INSERT INTO stages(
        id, run_id, agent_id, position, status, requires_approval, process_node_id,
        process_token_id, stage_kind, stage_input, activity_json, idempotency_key, created_at, updated_at
      ) VALUES (?, ?, '__agat_system__', ?, 'queued', 0, ?, ?, 'http', ?, ?, ?, ?, ?)
    `).run(stageId, runId, position, node.id, tokenId, lastOutput, JSON.stringify(activity), idempotencyKey, timestamp, timestamp);
    this.setProcessWaitingState(instanceId, runId, "queued", node.id, lastOutput, loopCounts, transitionCount, timestamp);
    this.addEvent(runId, stageId, null, "info", "process.http.queued", `HTTP-шаг «${node.name}» добавлен в очередь`, {
      processInstanceId: instanceId,
      processNodeId: node.id,
      method: activity.request.method,
      urlHost: this.safeUrlHost(activity.request.url),
    });
  }

  private armProcessCompensation(
    instance: Row,
    stage: Row,
    node: ProcessGraphNode,
    output: string,
  ): void {
    const compensation = node.config.compensation;
    if (!compensation) return;
    const run = this.db.prepare("SELECT input FROM runs WHERE id = ?").get(String(stage.run_id)) as Row;
    const context = {
      input: String(run.input),
      lastOutput: output,
      loopCounts: parseJson<Record<string, number>>(instance.loop_counts_json, {}),
    };
    const sourceKey = typeof stage.idempotency_key === "string" && stage.idempotency_key
      ? stage.idempotency_key
      : `agat-${createHash("sha256").update(`${String(instance.id)}:${node.id}:${String(stage.id)}`).digest("hex")}`;
    const compensationKey = `${sourceKey}-compensate`;
    const headers = Object.fromEntries(Object.entries(compensation.headers).map(([name, value]) => [
      name,
      renderProcessTemplate(value, context),
    ]));
    headers[node.config.idempotencyHeader ?? "Idempotency-Key"] = compensationKey;
    const activity = {
      credentialId: compensation.credentialId ?? "",
      idempotencyHeader: node.config.idempotencyHeader ?? "Idempotency-Key",
      request: {
        method: compensation.method,
        url: renderProcessTemplate(compensation.url, context),
        headers,
        body: ["GET", "DELETE"].includes(compensation.method)
          ? null
          : renderProcessTemplate(compensation.body, context),
        timeoutSeconds: compensation.timeoutSeconds,
      },
    };
    const timestamp = nowIso();
    this.db.prepare(`
      INSERT INTO process_compensations(
        id, instance_id, source_node_id, source_stage_id, sequence, status,
        idempotency_key, activity_json, created_at
      ) VALUES (?, ?, ?, ?, ?, 'armed', ?, ?, ?)
    `).run(
      randomUUID(),
      String(instance.id),
      node.id,
      String(stage.id),
      Number(stage.position),
      compensationKey,
      JSON.stringify(activity),
      timestamp,
    );
    this.addEvent(String(stage.run_id), String(stage.id), null, "info", "process.compensation.armed", `Зарегистрирована compensation шага «${node.name}»`, {
      processInstanceId: instance.id,
      processNodeId: node.id,
      idempotencyKey: compensationKey,
    });
  }

  private queueProcessWait(
    instanceId: string,
    tokenId: string,
    runId: string,
    node: ProcessGraphNode,
    lastOutput: string | null,
    loopCounts: Record<string, number>,
    transitionCount: number,
  ): void {
    const position = this.nextStagePosition(runId);
    const timestamp = nowIso();
    const availableAt = new Date(Date.now() + (node.config.waitSeconds ?? 60) * 1_000).toISOString();
    const stageId = randomUUID();
    this.db.prepare(`
      INSERT INTO stages(
        id, run_id, agent_id, position, status, requires_approval, process_node_id,
        process_token_id, stage_kind, stage_input, available_at, created_at, updated_at
      ) VALUES (?, ?, '__agat_system__', ?, 'queued', 0, ?, ?, 'wait', ?, ?, ?, ?)
    `).run(stageId, runId, position, node.id, tokenId, lastOutput, availableAt, timestamp, timestamp);
    this.setProcessWaitingState(instanceId, runId, "queued", node.id, lastOutput, loopCounts, transitionCount, timestamp);
    this.addEvent(runId, stageId, null, "info", "process.wait.started", `Ожидание «${node.name}» до ${availableAt}`, {
      processInstanceId: instanceId,
      processNodeId: node.id,
      availableAt,
      waitSeconds: node.config.waitSeconds ?? 60,
    });
  }

  private queueProcessApproval(
    instanceId: string,
    tokenId: string,
    runId: string,
    node: ProcessGraphNode,
    lastOutput: string | null,
    loopCounts: Record<string, number>,
    transitionCount: number,
  ): void {
    const position = this.nextStagePosition(runId);
    const timestamp = nowIso();
    const stageId = randomUUID();
    this.db.prepare(`
      INSERT INTO stages(
        id, run_id, agent_id, position, status, requires_approval, process_node_id,
        process_token_id, stage_kind, stage_input, activity_json, created_at, updated_at
      ) VALUES (?, ?, '__agat_system__', ?, 'waiting_approval', 1, ?, ?, 'approval', ?, ?, ?, ?)
    `).run(
      stageId,
      runId,
      position,
      node.id,
      tokenId,
      lastOutput,
      JSON.stringify({ message: node.config.approvalMessage ?? "" }),
      timestamp,
      timestamp,
    );
    this.setProcessWaitingState(instanceId, runId, "waiting_approval", node.id, lastOutput, loopCounts, transitionCount, timestamp);
    this.addEvent(runId, stageId, null, "warn", "approval.requested", node.config.approvalMessage || `Шаг «${node.name}» ожидает подтверждения`, {
      processInstanceId: instanceId,
      processNodeId: node.id,
      position,
      stageKind: "approval",
    });
  }

  private queueProcessSignal(
    instanceId: string,
    tokenId: string,
    runId: string,
    node: ProcessGraphNode,
    lastOutput: string | null,
    loopCounts: Record<string, number>,
    transitionCount: number,
    context: { input: string; lastOutput: string | null; loopCounts: Record<string, number> },
  ): void {
    const run = this.db.prepare(`SELECT project_id FROM runs WHERE id = ?`).get(runId) as Row;
    const instance = this.db.prepare(`SELECT process_id FROM process_instances WHERE id = ?`).get(instanceId) as Row;
    const position = this.nextStagePosition(runId);
    const timestamp = nowIso();
    const stageId = randomUUID();
    const waitId = randomUUID();
    const correlationKey = renderProcessTemplate(node.config.signalCorrelationKey ?? "", context).slice(0, 1_000);
    const timeout = node.config.signalTimeoutSeconds ?? 0;
    const expiresAt = timeout > 0 ? new Date(Date.now() + timeout * 1_000).toISOString() : null;
    this.db.prepare(`
      INSERT INTO stages(
        id, run_id, agent_id, position, status, requires_approval, process_node_id,
        process_token_id, stage_kind, stage_input, available_at, created_at, updated_at
      ) VALUES (?, ?, '__agat_system__', ?, 'waiting_external', 0, ?, ?, 'signal', ?, ?, ?, ?)
    `).run(stageId, runId, position, node.id, tokenId, lastOutput, expiresAt, timestamp, timestamp);
    this.db.prepare(`
      INSERT INTO process_signal_waits(
        id, instance_id, stage_id, process_id, project_id, signal_name,
        correlation_key, status, expires_at, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?)
    `).run(
      waitId,
      instanceId,
      stageId,
      String(instance.process_id),
      String(run.project_id),
      node.config.signalName ?? "",
      correlationKey,
      expiresAt,
      timestamp,
    );
    this.setProcessWaitingState(instanceId, runId, "waiting_external", node.id, lastOutput, loopCounts, transitionCount, timestamp);
    this.addEvent(runId, stageId, null, "info", "process.signal.waiting", `Ожидание внешнего signal «${node.config.signalName}»`, {
      processInstanceId: instanceId,
      processNodeId: node.id,
      signalName: node.config.signalName,
      correlationKey: correlationKey || null,
      expiresAt,
    });
  }

  private deliverProcessSignalLocked(input: {
    processId: string;
    projectId: string;
    signalName: string;
    instanceId?: string;
    correlationKey?: string;
    payload: unknown;
  }): { delivered: boolean; instanceIds: string[] } {
    const conditions = [
      "psw.process_id = ?",
      "psw.project_id = ?",
      "psw.signal_name = ?",
      "psw.status = 'waiting'",
      "s.status = 'waiting_external'",
      "pi.status IN ('queued', 'running', 'waiting_approval', 'waiting_external')",
      "(psw.expires_at IS NULL OR psw.expires_at > ?)",
    ];
    const parameters: SqlScalar[] = [input.processId, input.projectId, input.signalName, nowIso()];
    if (input.instanceId) {
      conditions.push("psw.instance_id = ?");
      parameters.push(input.instanceId);
    }
    if (input.correlationKey !== undefined) {
      conditions.push("psw.correlation_key = ?");
      parameters.push(input.correlationKey.slice(0, 1_000));
    }
    const wait = this.db.prepare(`
      SELECT psw.*, s.run_id, s.process_node_id, s.process_token_id
      FROM process_signal_waits psw
      JOIN stages s ON s.id = psw.stage_id
      JOIN process_instances pi ON pi.id = psw.instance_id
      WHERE ${conditions.join(" AND ")}
      ORDER BY psw.created_at ASC
      LIMIT 1
    `).get(...parameters) as Row | undefined;
    if (!wait) return { delivered: false, instanceIds: [] };
    const payload = typeof input.payload === "string" ? input.payload : JSON.stringify(input.payload ?? null);
    if (payload.length > 900_000) throw new Error("Signal payload превышает 900000 символов");
    const timestamp = nowIso();
    this.db.prepare(`UPDATE process_signal_waits SET status = 'delivered', payload = ?, completed_at = ? WHERE id = ?`)
      .run(payload, timestamp, String(wait.id));
    this.db.prepare(`
      UPDATE stages SET status = 'completed', output = ?, started_at = COALESCE(started_at, ?),
        completed_at = ?, updated_at = ? WHERE id = ?
    `).run(payload, timestamp, timestamp, timestamp, String(wait.stage_id));
    this.db.prepare(`UPDATE process_instances SET last_output = ?, updated_at = ? WHERE id = ?`)
      .run(payload, timestamp, String(wait.instance_id));
    this.addEvent(String(wait.run_id), String(wait.stage_id), null, "info", "process.signal.received", `Получен signal «${input.signalName}»`, {
      processInstanceId: wait.instance_id,
      processNodeId: wait.process_node_id,
      correlationKey: wait.correlation_key || null,
      payloadCharacters: payload.length,
    });
    const instance = this.db.prepare(`SELECT graph_json FROM process_instances WHERE id = ?`).get(String(wait.instance_id)) as Row;
    const graph = parseJson<ProcessGraph>(instance.graph_json, { nodes: [], edges: [] });
    const edge = outgoingEdge(graph, String(wait.process_node_id), "default");
    if (!edge) {
      this.triggerProcessFailure(String(wait.instance_id), String(wait.run_id), "После внешнего signal отсутствует переход", "failed");
    } else {
      this.advanceProcessToken(String(wait.instance_id), String(wait.process_token_id), edge.target, payload);
      this.syncProcessAggregateStatus(String(wait.instance_id));
    }
    return { delivered: true, instanceIds: [String(wait.instance_id)] };
  }

  private queueProcessSubprocess(
    instanceId: string,
    tokenId: string,
    runId: string,
    node: ProcessGraphNode,
    lastOutput: string | null,
    loopCounts: Record<string, number>,
    transitionCount: number,
    context: { input: string; lastOutput: string | null; loopCounts: Record<string, number> },
  ): void {
    const parentRun = this.db.prepare(`
      SELECT project_id, priority, result_destination, artifact_path, knowledge_collection_ids_json
      FROM runs WHERE id = ?
    `).get(runId) as Row;
    const childProcessId = node.config.subprocessProcessId;
    const childVersion = node.config.subprocessVersion;
    if (!childProcessId || !childVersion) {
      this.triggerProcessFailure(instanceId, runId, `Шаг «${node.name}»: subprocess version не закреплена`, "failed");
      return;
    }
    const position = this.nextStagePosition(runId);
    const timestamp = nowIso();
    const stageId = randomUUID();
    const childInput = renderProcessTemplate(node.config.subprocessInputTemplate ?? "{{ lastOutput }}", context);
    if (!childInput.trim() || childInput.trim().length > 100_000) {
      this.triggerProcessFailure(
        instanceId,
        runId,
        `Шаг «${node.name}»: вход subprocess должен содержать от 1 до 100000 символов`,
        "failed",
      );
      return;
    }
    this.db.prepare(`
      INSERT INTO stages(
        id, run_id, agent_id, position, status, requires_approval, process_node_id,
        process_token_id, stage_kind, stage_input, created_at, updated_at
      ) VALUES (?, ?, '__agat_system__', ?, 'waiting_external', 0, ?, ?, 'subprocess', ?, ?, ?)
    `).run(stageId, runId, position, node.id, tokenId, lastOutput, timestamp, timestamp);
    this.setProcessWaitingState(instanceId, runId, "waiting_external", node.id, lastOutput, loopCounts, transitionCount, timestamp);
    const child = this.startProcessLocked(
      childProcessId,
      {
        input: childInput.trim(),
        priority: Number(parentRun.priority),
        resultDestination: normalizeResultDestination(parentRun.result_destination),
        artifactPath: String(parentRun.artifact_path ?? ""),
        knowledgeCollectionIds: parseJson<string[]>(parentRun.knowledge_collection_ids_json, []),
      },
      String(parentRun.project_id),
      {
        knowledgeCollectionIds: parseJson<string[]>(parentRun.knowledge_collection_ids_json, []),
        version: childVersion,
        runtime: "embedded",
      },
    );
    if (!child) {
      this.triggerProcessFailure(instanceId, runId, `Subprocess ${childProcessId} v${childVersion} не найден`, "failed");
      return;
    }
    this.db.prepare(`
      INSERT INTO process_subprocess_links(
        id, parent_instance_id, parent_stage_id, parent_token_id, child_instance_id, status, created_at
      ) VALUES (?, ?, ?, ?, ?, 'running', ?)
    `).run(randomUUID(), instanceId, stageId, tokenId, String(child.id), timestamp);
    this.addEvent(runId, stageId, null, "info", "process.subprocess.started", `Запущен subprocess «${node.name}»`, {
      processInstanceId: instanceId,
      processNodeId: node.id,
      childInstanceId: child.id,
      childProcessId,
      childVersion,
    });
    if (["completed", "failed", "cancelled"].includes(String(child.status))) {
      this.completeParentSubprocess(String(child.id));
    }
  }

  private setProcessWaitingState(
    instanceId: string,
    runId: string,
    status: "queued" | "waiting_approval" | "waiting_external",
    nodeId: string,
    lastOutput: string | null,
    loopCounts: Record<string, number>,
    transitionCount: number,
    timestamp: string,
  ): void {
    this.db.prepare("UPDATE runs SET status = ?, updated_at = ? WHERE id = ?").run(status, timestamp, runId);
    this.db.prepare(`
      UPDATE process_instances
      SET status = ?, current_node_id = ?, last_output = ?, loop_counts_json = ?, transition_count = ?, updated_at = ?
      WHERE id = ?
    `).run(status, nodeId, lastOutput, JSON.stringify(loopCounts), transitionCount, timestamp, instanceId);
  }

  private syncProcessAggregateStatus(instanceId: string): void {
    const instance = this.db.prepare(`SELECT run_id, status FROM process_instances WHERE id = ?`).get(instanceId) as Row | undefined;
    if (!instance || ["completed", "failed", "cancelled", "compensating"].includes(String(instance.status))) return;
    const stages = this.db.prepare(`
      SELECT status, COUNT(*) AS count FROM stages
      WHERE run_id = ? AND status IN ('running', 'queued', 'waiting_approval', 'waiting_external')
      GROUP BY status
    `).all(String(instance.run_id)) as Row[];
    const counts = new Map(stages.map((row) => [String(row.status), Number(row.count)]));
    const tokenCount = Number((this.db.prepare(`
      SELECT COUNT(*) AS count FROM process_tokens
      WHERE instance_id = ? AND status IN ('active', 'forked', 'waiting_join')
    `).get(instanceId) as Row).count);
    const status = (counts.get("running") ?? 0) > 0 ? "running"
      : (counts.get("queued") ?? 0) > 0 ? "queued"
        : (counts.get("waiting_approval") ?? 0) > 0 ? "waiting_approval"
          : (counts.get("waiting_external") ?? 0) > 0 ? "waiting_external"
            : tokenCount > 0 ? "running"
              : String(instance.status);
    const timestamp = nowIso();
    this.db.prepare(`UPDATE runs SET status = ?, updated_at = ? WHERE id = ?`)
      .run(status, timestamp, String(instance.run_id));
    this.db.prepare(`UPDATE process_instances SET status = ?, updated_at = ? WHERE id = ?`)
      .run(status, timestamp, instanceId);
  }

  private safeUrlHost(value: string): string {
    try {
      return new URL(value).hostname.slice(0, 253);
    } catch {
      return "";
    }
  }

  private completeParentSubprocess(childInstanceId: string): string | null {
    const link = this.db.prepare(`
      SELECT psl.*, s.run_id, s.process_node_id, s.process_token_id,
             child.status AS child_status, child.last_output AS child_output, child.error AS child_error,
             parent.graph_json AS parent_graph_json, parent.status AS parent_status
      FROM process_subprocess_links psl
      JOIN stages s ON s.id = psl.parent_stage_id
      JOIN process_instances child ON child.id = psl.child_instance_id
      JOIN process_instances parent ON parent.id = psl.parent_instance_id
      WHERE psl.child_instance_id = ? AND psl.status = 'running'
    `).get(childInstanceId) as Row | undefined;
    if (!link || !["completed", "failed", "cancelled"].includes(String(link.child_status))) return null;
    const timestamp = nowIso();
    if (["completed", "failed", "cancelled", "compensating"].includes(String(link.parent_status))) {
      this.db.prepare(`UPDATE process_subprocess_links SET status = 'cancelled', completed_at = ? WHERE id = ?`)
        .run(timestamp, String(link.id));
      return String(link.parent_instance_id);
    }
    if (link.child_status === "completed") {
      const output = String(link.child_output ?? "");
      this.db.prepare(`
        UPDATE stages SET status = 'completed', output = ?, started_at = COALESCE(started_at, ?),
          completed_at = ?, updated_at = ? WHERE id = ?
      `).run(output, timestamp, timestamp, timestamp, String(link.parent_stage_id));
      this.db.prepare(`UPDATE process_subprocess_links SET status = 'completed', completed_at = ? WHERE id = ?`)
        .run(timestamp, String(link.id));
      const graph = parseJson<ProcessGraph>(link.parent_graph_json, { nodes: [], edges: [] });
      const edge = outgoingEdge(graph, String(link.process_node_id), "default");
      this.addEvent(String(link.run_id), String(link.parent_stage_id), null, "info", "process.subprocess.completed", "Subprocess завершён", {
        parentInstanceId: link.parent_instance_id,
        childInstanceId,
        outputCharacters: output.length,
      });
      if (!edge) {
        this.triggerProcessFailure(String(link.parent_instance_id), String(link.run_id), "После subprocess отсутствует переход", "failed");
      } else {
        this.advanceProcessToken(
          String(link.parent_instance_id),
          String(link.process_token_id),
          edge.target,
          output,
        );
        this.syncProcessAggregateStatus(String(link.parent_instance_id));
      }
    } else {
      this.db.prepare(`UPDATE process_subprocess_links SET status = ?, completed_at = ? WHERE id = ?`)
        .run(String(link.child_status), timestamp, String(link.id));
      this.db.prepare(`UPDATE stages SET status = 'failed', completed_at = ?, updated_at = ? WHERE id = ?`)
        .run(timestamp, timestamp, String(link.parent_stage_id));
      this.triggerProcessFailure(
        String(link.parent_instance_id),
        String(link.run_id),
        `Subprocess завершился со статусом ${String(link.child_status)}: ${String(link.child_error ?? "")}`.slice(0, 4_000),
        "failed",
      );
    }
    return String(link.parent_instance_id);
  }

  private triggerProcessFailure(
    instanceId: string,
    runId: string,
    message: string,
    terminalStatus: "failed" | "cancelled",
  ): void {
    const instance = this.db.prepare(`SELECT status FROM process_instances WHERE id = ?`).get(instanceId) as Row | undefined;
    if (!instance || ["completed", "failed", "cancelled", "compensating"].includes(String(instance.status))) return;
    const timestamp = nowIso();
    this.quiesceProcessExecution(instanceId, message, timestamp);
    const armed = Number((this.db.prepare(`
      SELECT COUNT(*) AS count FROM process_compensations WHERE instance_id = ? AND status = 'armed'
    `).get(instanceId) as Row).count);
    if (armed > 0) {
      this.db.prepare(`
        UPDATE stages SET status = 'cancelled', node_id = NULL, lease_id = NULL, lease_expires_at = NULL,
          completed_at = COALESCE(completed_at, ?), updated_at = ?
        WHERE run_id = ? AND status IN ('pending', 'queued', 'running', 'waiting_approval', 'waiting_external')
      `).run(timestamp, timestamp, runId);
      this.db.prepare(`
        UPDATE process_instances
        SET status = 'compensating', terminal_status_after_compensation = ?, error = ?, updated_at = ?
        WHERE id = ?
      `).run(terminalStatus, message.slice(0, 4_000), timestamp, instanceId);
      this.db.prepare(`UPDATE runs SET status = 'compensating', updated_at = ? WHERE id = ?`)
        .run(timestamp, runId);
      this.addEvent(runId, null, null, "warn", "process.compensation.started", `Запущена compensation: ${message}`.slice(0, 4_000), {
        processInstanceId: instanceId,
        terminalStatus,
        armed,
      });
      this.queueNextCompensation(instanceId, runId);
      return;
    }
    this.finalizeProcessTerminal(instanceId, runId, terminalStatus, message);
  }

  private quiesceProcessExecution(instanceId: string, reason: string, timestamp: string): void {
    this.db.prepare(`
      UPDATE process_signal_waits
      SET status = 'cancelled', completed_at = ?
      WHERE instance_id = ? AND status = 'waiting'
    `).run(timestamp, instanceId);
    this.db.prepare(`
      UPDATE process_tokens SET status = 'cancelled', updated_at = ?
      WHERE instance_id = ? AND status IN ('active', 'forked', 'waiting_join')
    `).run(timestamp, instanceId);

    const children = this.db.prepare(`
      SELECT psl.id, psl.parent_stage_id, psl.child_instance_id,
             s.run_id AS parent_run_id, child.run_id AS child_run_id, child.status AS child_status
      FROM process_subprocess_links psl
      JOIN stages s ON s.id = psl.parent_stage_id
      JOIN process_instances child ON child.id = psl.child_instance_id
      WHERE psl.parent_instance_id = ? AND psl.status = 'running'
      ORDER BY psl.created_at, psl.id
    `).all(instanceId) as Row[];
    for (const child of children) {
      const detached = this.db.prepare(`
        UPDATE process_subprocess_links SET status = 'cancelled', completed_at = ?
        WHERE id = ? AND status = 'running'
      `).run(timestamp, String(child.id));
      if (Number(detached.changes) !== 1) continue;
      this.addEvent(
        String(child.parent_run_id),
        String(child.parent_stage_id),
        null,
        "warn",
        "process.subprocess.cancelled",
        "Subprocess остановлен вместе с родительским процессом",
        {
          parentInstanceId: instanceId,
          childInstanceId: child.child_instance_id,
          reason: reason.slice(0, 1_000),
        },
      );
      if (["queued", "running", "waiting_approval", "waiting_external"].includes(String(child.child_status))) {
        this.triggerProcessFailure(
          String(child.child_instance_id),
          String(child.child_run_id),
          `Родительский subprocess остановлен: ${reason}`.slice(0, 4_000),
          "cancelled",
        );
      }
    }
  }

  private queueNextCompensation(instanceId: string, runId: string): void {
    const compensation = this.db.prepare(`
      SELECT * FROM process_compensations
      WHERE instance_id = ? AND status = 'armed'
      ORDER BY sequence DESC, created_at DESC
      LIMIT 1
    `).get(instanceId) as Row | undefined;
    if (!compensation) {
      const instance = this.db.prepare(`
        SELECT terminal_status_after_compensation, error, compensation_error
        FROM process_instances WHERE id = ?
      `).get(instanceId) as Row;
      const status = instance.terminal_status_after_compensation === "cancelled" ? "cancelled" : "failed";
      const suffix = instance.compensation_error ? `; compensation: ${String(instance.compensation_error)}` : "";
      this.finalizeProcessTerminal(instanceId, runId, status, `${String(instance.error ?? "Процесс остановлен")}${suffix}`);
      return;
    }
    const stageId = randomUUID();
    const timestamp = nowIso();
    const position = this.nextStagePosition(runId);
    this.db.prepare(`
      INSERT INTO stages(
        id, run_id, agent_id, position, status, requires_approval, process_node_id,
        stage_kind, stage_input, activity_json, idempotency_key, created_at, updated_at
      ) VALUES (?, ?, '__agat_system__', ?, 'queued', 0, ?, 'compensation', NULL, ?, ?, ?, ?)
    `).run(
      stageId,
      runId,
      position,
      String(compensation.source_node_id),
      String(compensation.activity_json),
      String(compensation.idempotency_key),
      timestamp,
      timestamp,
    );
    this.db.prepare(`
      UPDATE process_compensations SET status = 'queued', compensation_stage_id = ? WHERE id = ?
    `).run(stageId, String(compensation.id));
    this.addEvent(runId, stageId, null, "warn", "process.compensation.queued", "Compensation добавлена в очередь", {
      processInstanceId: instanceId,
      processNodeId: compensation.source_node_id,
      idempotencyKey: compensation.idempotency_key,
    });
  }

  private completeCompensation(stage: Row, output: string): void {
    const compensation = this.db.prepare(`
      SELECT pc.*, pi.run_id FROM process_compensations pc
      JOIN process_instances pi ON pi.id = pc.instance_id
      WHERE pc.compensation_stage_id = ?
    `).get(String(stage.id)) as Row | undefined;
    if (!compensation) throw new Error("Compensation record не найден");
    const timestamp = nowIso();
    this.db.prepare(`
      UPDATE process_compensations SET status = 'completed', completed_at = ? WHERE id = ?
    `).run(timestamp, String(compensation.id));
    this.addEvent(String(stage.run_id), String(stage.id), null, "info", "process.compensation.completed", "Compensation завершена", {
      processInstanceId: compensation.instance_id,
      processNodeId: compensation.source_node_id,
      outputCharacters: output.length,
    });
    this.queueNextCompensation(String(compensation.instance_id), String(stage.run_id));
  }

  private finalizeProcessTerminal(
    instanceId: string,
    runId: string,
    status: "failed" | "cancelled",
    message: string,
  ): void {
    const timestamp = nowIso();
    this.db.prepare(`
      UPDATE stages
      SET status = CASE WHEN status = 'completed' THEN status ELSE ? END,
          completed_at = CASE WHEN status = 'completed' THEN completed_at ELSE COALESCE(completed_at, ?) END,
          updated_at = ?
      WHERE run_id = ? AND status IN ('pending', 'queued', 'running', 'waiting_approval', 'waiting_external')
    `).run(status === "cancelled" ? "cancelled" : "failed", timestamp, timestamp, runId);
    this.db.prepare(`UPDATE runs SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?`)
      .run(status, timestamp, timestamp, runId);
    this.db.prepare(`
      UPDATE process_instances
      SET status = ?, error = ?, completed_at = ?, updated_at = ?
      WHERE id = ?
    `).run(status, message.slice(0, 4_000), timestamp, timestamp, instanceId);
    this.addEvent(runId, null, null, status === "failed" ? "error" : "warn", `process.instance.${status}`, message.slice(0, 4_000), {
      processInstanceId: instanceId,
    });
    this.telemetry.endRun(runId, { status, ...(status === "failed" ? { errorType: "ProcessFailed" } : {}) });
    this.completeParentSubprocess(instanceId);
  }

  private failProcessInstance(instanceId: string, runId: string, message: string): void {
    this.triggerProcessFailure(instanceId, runId, message, "failed");
  }

  private artifactRunBase(runId: string, configuredPath: unknown): string {
    const prefix = typeof configuredPath === "string" && configuredPath ? configuredPath : "runs";
    return path.posix.join(prefix, runId);
  }

  private persistStageOutputArtifact(stage: Row, output: string): void {
    const position = String(Number(stage.position) + 1).padStart(2, "0");
    const agentSlug = safeArtifactSlug(stage.agent_name);
    this.persistArtifact(
      stage,
      `${position}-${agentSlug}.md`,
      "stage_output",
      "text/markdown; charset=utf-8",
      output,
      path.posix.join("stages", `${position}-${agentSlug}.md`),
    );
  }

  private persistFinalResultArtifact(stage: Row, output: string): void {
    this.persistArtifact(
      stage,
      "result.md",
      "result",
      "text/markdown; charset=utf-8",
      output,
      "result.md",
    );
  }

  private persistWorkerArtifacts(stage: Row, artifacts: WorkerArtifactInput[]): number {
    if (!Array.isArray(artifacts)) throw new Error("artifacts должен быть массивом");
    if (artifacts.length > 8) throw new Error("За один этап можно сохранить не более 8 артефактов");
    let totalBytes = 0;
    const decoded: Buffer[] = [];
    for (const artifact of artifacts) {
      if (!artifact || typeof artifact !== "object" || typeof artifact.content !== "string") {
        throw new Error("Содержимое артефакта должно быть строкой");
      }
      if (artifact.encoding !== undefined && artifact.encoding !== "utf8" && artifact.encoding !== "base64") {
        throw new Error("encoding артефакта должен быть utf8 или base64");
      }
      let bytes: Buffer;
      if (artifact.encoding === "base64") {
        if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(artifact.content)) {
          throw new Error("Binary artifact должен содержать canonical base64");
        }
        bytes = Buffer.from(artifact.content, "base64");
        if (bytes.toString("base64") !== artifact.content) throw new Error("Binary artifact повреждён");
      } else {
        bytes = Buffer.from(artifact.content, "utf8");
      }
      decoded.push(bytes);
      totalBytes += bytes.byteLength;
    }
    if (totalBytes > 800_000) throw new Error("Суммарный размер артефактов этапа превышает 800000 байт");

    for (let index = 0; index < artifacts.length; index += 1) {
      const artifact = artifacts[index]!;
      const name = safeArtifactName(artifact.name);
      const mediaType = typeof artifact.mediaType === "string"
        && /^[\w.+-]+\/[\w.+-]+(?:;\s*charset=[\w-]+)?$/i.test(artifact.mediaType)
        ? artifact.mediaType.slice(0, 120)
        : "text/plain; charset=utf-8";
      const storageName = `${randomUUID().slice(0, 8)}-${name}`;
      this.persistArtifact(
        stage,
        name,
        "agent_artifact",
        mediaType,
        decoded[index]!,
        path.posix.join("artifacts", storageName),
      );
    }
    return artifacts.length;
  }

  private persistArtifact(
    stage: Row,
    name: string,
    kind: string,
    mediaType: string,
    content: string | Buffer,
    pathWithinRun: string,
  ): void {
    const artifactId = randomUUID();
    const relativePath = path.posix.join(
      this.artifactRunBase(String(stage.run_id), stage.artifact_path),
      pathWithinRun,
    );
    const segments = relativePath.split("/");
    const filename = segments.pop();
    if (!filename || segments.some((segment) => !segment || segment === "." || segment === "..")) {
      throw new Error("Не удалось сформировать безопасный путь артефакта");
    }

    fs.mkdirSync(this.artifactsDir, { recursive: true, mode: 0o700 });
    if (fs.lstatSync(this.artifactsDir).isSymbolicLink()) {
      throw new Error("Корневой каталог артефактов не может быть символической ссылкой");
    }
    let directory = this.artifactsDir;
    for (const segment of segments) {
      directory = path.join(directory, segment);
      if (fs.existsSync(directory)) {
        const status = fs.lstatSync(directory);
        if (status.isSymbolicLink() || !status.isDirectory()) {
          throw new Error("Путь артефакта пересекает небезопасный объект файловой системы");
        }
      } else {
        fs.mkdirSync(directory, { mode: 0o700 });
      }
    }

    const filePath = path.join(directory, filename);
    const rootPrefix = `${path.resolve(this.artifactsDir)}${path.sep}`;
    if (!path.resolve(filePath).startsWith(rootPrefix)) throw new Error("Путь артефакта вышел за пределы хранилища");
    const temporaryPath = path.join(directory, `.${filename}.${artifactId}.tmp`);
    const buffer = Buffer.isBuffer(content) ? content : Buffer.from(content, "utf8");
    try {
      fs.writeFileSync(temporaryPath, buffer, { flag: "wx", mode: 0o600 });
      fs.linkSync(temporaryPath, filePath);
    } finally {
      if (fs.existsSync(temporaryPath)) fs.rmSync(temporaryPath, { force: true });
    }

    const createdAt = nowIso();
    const sha256 = createHash("sha256").update(buffer).digest("hex");
    try {
      this.db
        .prepare(`
          INSERT INTO artifacts(
            id, run_id, stage_id, name, kind, media_type, relative_path,
            size_bytes, sha256, created_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        `)
        .run(
          artifactId,
          String(stage.run_id),
          stage.id === null || stage.id === undefined ? null : String(stage.id),
          name,
          kind,
          mediaType,
          relativePath,
          buffer.byteLength,
          sha256,
          createdAt,
        );
      this.addEvent(
        String(stage.run_id),
        stage.id === null || stage.id === undefined ? null : String(stage.id),
        null,
        "info",
        "artifact.created",
        `Сохранён артефакт «${name}»`,
        {
        kind: "artifact",
        artifactId,
        artifactKind: kind,
        name,
        mediaType,
        relativePath,
        sizeBytes: buffer.byteLength,
        sha256,
        },
      );
    } catch (error) {
      const status = fs.lstatSync(filePath);
      if (status.isFile() && !status.isSymbolicLink()) fs.rmSync(filePath);
      throw error;
    }
  }

  private nodeDto(row: Row): Record<string, unknown> {
    const lastSeen = new Date(String(row.last_seen)).getTime();
    const ageSeconds = Math.max(0, (Date.now() - lastSeen) / 1_000);
    let status = String(row.status);
    if (!String(row.id).startsWith("demo-") && ageSeconds > 90) status = ageSeconds > 300 ? "offline" : "sleeping";

    const models = parseJson<string[]>(row.models_json, []);
    const reportedProfiles = normalizeModelProfiles(
      parseJson<unknown>(row.model_profiles_json, []),
      models,
    );
    const reportedByName = new Map(reportedProfiles.map((profile) => [profile.name, profile]));
    const benchmarks = this.modelBenchmarks(String(row.id));
    const modelProfiles = models.map((name) => {
      const reported = reportedByName.get(name);
      return {
        ...(reported ?? { name, provider: "openai-compatible" }),
        profileKnown: Boolean(reported),
        benchmark: benchmarks.get(name) ?? null,
      };
    });

    return {
      id: row.id,
      name: row.name,
      platform: row.platform,
      architecture: row.architecture,
      endpoint: row.endpoint,
      models,
      modelProfiles,
      labels: parseJson<Record<string, string>>(row.labels_json, {}),
      agentRuntimes: normalizeAgentRuntimes(
        parseJson<unknown>(row.agent_runtimes_json, ["single"]),
      ),
      agentRuntimeProfiles: normalizeAgentRuntimeProfiles(
        parseJson<unknown>(row.agent_runtime_profiles_json, ["tool_loop_v1"]),
      ),
      embeddingModels: normalizeEmbeddingModels(parseJson<unknown>(row.embedding_models_json, [])),
      cpuCores: row.cpu_cores,
      memoryMb: row.memory_mb,
      vramMb: row.vram_mb,
      gpu: row.gpu,
      maxConcurrency: row.max_concurrency,
      usedConcurrency: row.used_concurrency,
      status,
      metrics: parseJson<WorkerMetrics>(row.metrics_json, {}),
      lastSeen: row.last_seen,
    };
  }

  private modelBenchmarks(nodeId: string): Map<string, StoredModelBenchmark> {
    const rows = this.db.prepare(`
      SELECT model, samples, output_tokens, model_duration_ms, ewma_tokens_per_second,
        ewma_joules_per_1k_tokens, last_observed_at
      FROM model_benchmarks WHERE node_id = ?
    `).all(nodeId) as Row[];
    return new Map(rows.map((row) => [String(row.model), {
      samples: Number(row.samples),
      outputTokens: Number(row.output_tokens),
      modelDurationMs: Number(row.model_duration_ms),
      tokensPerSecond: Math.round(Number(row.ewma_tokens_per_second) * 100) / 100,
      joulesPer1kTokens: row.ewma_joules_per_1k_tokens === null
        ? null
        : Math.round(Number(row.ewma_joules_per_1k_tokens) * 100) / 100,
      lastObservedAt: String(row.last_observed_at),
    }]));
  }

  private recordModelBenchmark(
    nodeId: string,
    model: string,
    metrics: WorkerExecutionMetrics,
    observedAt: string,
  ): void {
    const outputTokens = metrics.outputTokens ?? 0;
    const modelDurationMs = metrics.modelDurationMs ?? 0;
    if (outputTokens <= 0 || modelDurationMs <= 0) return;
    const tokensPerSecond = Math.min(1_000_000, outputTokens / (modelDurationMs / 1_000));
    const joulesPer1kTokens = metrics.energyJoules !== undefined
      ? Math.min(1_000_000_000, metrics.energyJoules / outputTokens * 1_000)
      : null;
    this.db.prepare(`
      INSERT INTO model_benchmarks(
        node_id, model, samples, output_tokens, model_duration_ms,
        ewma_tokens_per_second, ewma_joules_per_1k_tokens, last_observed_at
      ) VALUES (?, ?, 1, ?, ?, ?, ?, ?)
      ON CONFLICT(node_id, model) DO UPDATE SET
        samples = model_benchmarks.samples + 1,
        output_tokens = model_benchmarks.output_tokens + excluded.output_tokens,
        model_duration_ms = model_benchmarks.model_duration_ms + excluded.model_duration_ms,
        ewma_tokens_per_second = model_benchmarks.ewma_tokens_per_second * 0.7
          + excluded.ewma_tokens_per_second * 0.3,
        ewma_joules_per_1k_tokens = CASE
          WHEN excluded.ewma_joules_per_1k_tokens IS NULL THEN model_benchmarks.ewma_joules_per_1k_tokens
          WHEN model_benchmarks.ewma_joules_per_1k_tokens IS NULL THEN excluded.ewma_joules_per_1k_tokens
          ELSE model_benchmarks.ewma_joules_per_1k_tokens * 0.7
            + excluded.ewma_joules_per_1k_tokens * 0.3
        END,
        last_observed_at = excluded.last_observed_at
    `).run(
      nodeId,
      model.slice(0, 200),
      outputTokens,
      modelDurationMs,
      tokensPerSecond,
      joulesPer1kTokens,
      observedAt,
    );
  }

  private pruneNodeBenchmarks(nodeId: string, models: string[]): void {
    if (models.length === 0) {
      this.db.prepare("DELETE FROM model_benchmarks WHERE node_id = ?").run(nodeId);
      return;
    }
    const placeholders = models.map(() => "?").join(", ");
    this.db.prepare(`DELETE FROM model_benchmarks WHERE node_id = ? AND model NOT IN (${placeholders})`)
      .run(nodeId, ...models);
  }

  private artifactDto(row: Row): Record<string, unknown> {
    return {
      id: row.id,
      runId: row.run_id,
      stageId: row.stage_id,
      name: row.name,
      kind: row.kind,
      mediaType: row.media_type,
      relativePath: row.relative_path,
      sizeBytes: Number(row.size_bytes),
      sha256: row.sha256,
      createdAt: row.created_at,
    };
  }

  private eventDto(row: Row): EventRecord {
    return {
      id: Number(row.id),
      runId: typeof row.run_id === "string" ? row.run_id : null,
      stageId: typeof row.stage_id === "string" ? row.stage_id : null,
      nodeId: typeof row.node_id === "string" ? row.node_id : null,
      level: String(row.level),
      type: String(row.type),
      message: String(row.message),
      data: parseJson<Record<string, unknown> | null>(row.data_json, null),
      createdAt: String(row.created_at),
    };
  }

  private addEvent(
    runId: string | null,
    stageId: string | null,
    nodeId: string | null,
    level: string,
    type: string,
    message: string,
    data: Record<string, unknown> | null,
  ): EventRecord {
    const createdAt = nowIso();
    let projectId = "global";
    if (runId) {
      const run = this.db.prepare("SELECT project_id FROM runs WHERE id = ?").get(runId) as Row | undefined;
      if (typeof run?.project_id === "string") projectId = run.project_id;
    } else if (typeof data?.projectId === "string" && /^[a-z0-9][a-z0-9_-]{0,63}$/.test(data.projectId)) {
      projectId = data.projectId;
    } else if (typeof data?.processId === "string") {
      const process = this.db.prepare("SELECT project_id FROM processes WHERE id = ?").get(data.processId) as Row | undefined;
      if (typeof process?.project_id === "string") projectId = process.project_id;
    } else if (typeof data?.agentId === "string") {
      const agent = this.db.prepare("SELECT project_id FROM agents WHERE id = ?").get(data.agentId) as Row | undefined;
      if (typeof agent?.project_id === "string" && agent.project_id !== "__system__") projectId = agent.project_id;
    } else if (typeof data?.credentialId === "string") {
      const credential = this.db.prepare("SELECT project_id FROM credentials WHERE id = ?").get(data.credentialId) as Row | undefined;
      if (typeof credential?.project_id === "string") projectId = credential.project_id;
    }
    const result = this.db
      .prepare(`
        INSERT INTO events(run_id, stage_id, node_id, level, type, message, data_json, project_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      `)
      .run(runId, stageId, nodeId, level, type, message, data ? JSON.stringify(data) : null, projectId, createdAt);
    return {
      id: Number(result.lastInsertRowid),
      runId,
      stageId,
      nodeId,
      level,
      type,
      message,
      data,
      createdAt,
    };
  }

  private transaction<T>(callback: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const result = callback();
      this.db.exec("COMMIT");
      return result;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }
}
