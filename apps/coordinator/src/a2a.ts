import { createHash, randomUUID } from "node:crypto";

import type {
  A2AEndpointConnection,
  A2AInputMode,
  A2AMessage,
  A2ANormalizedMessage,
  A2APart,
  A2ASendMessageRequest,
  A2ATaskState,
  CreateA2AEndpointInput,
} from "./types.js";

export const A2A_PROTOCOL_VERSION = "1.0";
export const A2A_MEDIA_TYPE = "application/a2a+json";
export const A2A_ADAPTER_VERSION = "1.0.0";
export const A2A_SUPPORTED_OUTPUT_MODES = ["text/plain"] as const;

const INPUT_MODES = new Set<A2AInputMode>(["text/plain", "application/json"]);
const TERMINAL_OR_INTERRUPTED_STATES = new Set<A2ATaskState>([
  "TASK_STATE_COMPLETED",
  "TASK_STATE_FAILED",
  "TASK_STATE_CANCELED",
  "TASK_STATE_REJECTED",
  "TASK_STATE_INPUT_REQUIRED",
  "TASK_STATE_AUTH_REQUIRED",
]);

export class A2AProtocolError extends Error {
  constructor(
    readonly httpStatus: number,
    readonly rpcStatus: string,
    readonly reason: string,
    message: string,
    readonly metadata: Record<string, string> = {},
  ) {
    super(message);
  }
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

function optionalText(value: unknown, fallback: string, field: string, maxLength: number): string {
  if (value === undefined) return requiredText(fallback, field, maxLength);
  return requiredText(value, field, maxLength);
}

function normalizedStringArray(
  value: unknown,
  fallback: string[],
  field: string,
  maxItems: number,
  maxLength: number,
): string[] {
  const source = value === undefined ? fallback : value;
  if (!Array.isArray(source)) throw new Error(`${field} должен быть массивом`);
  if (source.length > maxItems) throw new Error(`${field}: максимум ${maxItems} значений`);
  const result = source.map((item) => requiredText(item, field, maxLength));
  return [...new Set(result)];
}

function slug(value: string): string {
  return value
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, "")
    .slice(0, 64) || `skill-${randomUUID().slice(0, 8)}`;
}

export function normalizeA2AEndpointInput(
  input: CreateA2AEndpointInput,
  defaults: Pick<A2AEndpointConnection,
    | "agentId"
    | "agentName"
    | "agentRole"
    | "name"
    | "description"
    | "version"
    | "skillId"
    | "skillName"
    | "skillDescription"
    | "tags"
    | "examples"
    | "inputModes"
    | "knowledgeCollectionIds"
    | "approvalRequired"
    | "enabled"
    | "priority"
    | "maxInputCharacters"
    | "maxActiveTasks"
  >,
): Omit<CreateA2AEndpointInput, "tags" | "examples" | "inputModes" | "knowledgeCollectionIds"> & {
  agentId: string;
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
} {
  const agentId = requiredText(input.agentId ?? defaults.agentId, "Агент A2A endpoint", 100);
  const name = optionalText(input.name, defaults.name || defaults.agentName, "Название A2A endpoint", 120);
  const description = optionalText(
    input.description,
    defaults.description || defaults.agentRole,
    "Описание A2A endpoint",
    2_000,
  );
  const version = optionalText(input.version, defaults.version || "1.0.0", "Версия A2A endpoint", 64);
  if (!/^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$/.test(version)) {
    throw new Error("Версия A2A endpoint должна быть SemVer, например 1.0.0");
  }
  const skillName = optionalText(input.skillName, defaults.skillName || name, "Название A2A skill", 120);
  const requestedSkillId = typeof input.skillId === "string" ? input.skillId.trim() : defaults.skillId;
  const skillId = requestedSkillId || slug(skillName);
  if (!/^[a-z0-9][a-z0-9._-]{0,63}$/.test(skillId)) {
    throw new Error("A2A skill ID должен содержать только a-z, 0-9, точку, _ или -");
  }
  const skillDescription = optionalText(
    input.skillDescription,
    defaults.skillDescription || description,
    "Описание A2A skill",
    2_000,
  );
  const tags = normalizedStringArray(input.tags, defaults.tags.length ? defaults.tags : ["agat", "local-agent"], "A2A tags", 16, 64);
  if (tags.length === 0) throw new Error("Нужен хотя бы один A2A tag");
  const examples = normalizedStringArray(input.examples, defaults.examples, "A2A examples", 8, 500);
  const rawModes = input.inputModes === undefined ? defaults.inputModes : input.inputModes;
  if (!Array.isArray(rawModes) || rawModes.length === 0) throw new Error("Нужен хотя бы один входной media type");
  const inputModes = [...new Set(rawModes.map((mode) => {
    if (!INPUT_MODES.has(mode)) throw new Error(`A2A input mode ${String(mode)} не поддерживается`);
    return mode;
  }))];
  const knowledgeCollectionIds = normalizedStringArray(
    input.knowledgeCollectionIds,
    defaults.knowledgeCollectionIds,
    "Knowledge collections A2A endpoint",
    32,
    100,
  );
  return {
    agentId,
    name,
    description,
    version,
    skillId,
    skillName,
    skillDescription,
    tags,
    examples,
    inputModes,
    knowledgeCollectionIds,
    approvalRequired: input.approvalRequired ?? defaults.approvalRequired,
    enabled: input.enabled ?? defaults.enabled,
    priority: boundedInteger(input.priority, 0, 100, defaults.priority),
    maxInputCharacters: boundedInteger(input.maxInputCharacters, 1_000, 100_000, defaults.maxInputCharacters),
    maxActiveTasks: boundedInteger(input.maxActiveTasks, 1, 100, defaults.maxActiveTasks),
  };
}

export function normalizeA2APublicBaseUrl(value: string, fallback: string): string {
  const raw = value.trim() || fallback;
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error("AGAT_A2A_PUBLIC_BASE_URL должен быть абсолютным HTTP(S) URL");
  }
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("AGAT_A2A_PUBLIC_BASE_URL должен быть HTTP(S) URL без credentials, query и fragment");
  }
  return parsed.toString().replace(/\/$/, "");
}

export function validateA2AProtocolVersion(value: string | undefined): void {
  if (value === A2A_PROTOCOL_VERSION) return;
  throw new A2AProtocolError(
    400,
    "FAILED_PRECONDITION",
    "VERSION_NOT_SUPPORTED",
    `Поддерживается A2A-Version: ${A2A_PROTOCOL_VERSION}`,
    { requestedVersion: value ?? "0.3", supportedVersion: A2A_PROTOCOL_VERSION },
  );
}

export function validateA2AContentType(value: string | undefined): void {
  const mediaType = value?.split(";", 1)[0]?.trim().toLowerCase();
  if (mediaType === A2A_MEDIA_TYPE) return;
  throw new A2AProtocolError(
    400,
    "INVALID_ARGUMENT",
    "CONTENT_TYPE_NOT_SUPPORTED",
    `Используйте Content-Type: ${A2A_MEDIA_TYPE}`,
    { receivedContentType: mediaType || "missing" },
  );
}

function requestText(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", `${field} обязателен`, { field });
  }
  if (value.length > maxLength) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", `${field}: максимум ${maxLength} символов`, { field });
  }
  return value;
}

function requestInteger(value: unknown, field: string, min: number, max: number, fallback: number): number {
  if (value === undefined) return fallback;
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", `${field} должен быть целым числом ${min}..${max}`, { field });
  }
  return value;
}

function requestStringArray(
  value: unknown,
  field: string,
  maxItems: number,
  maxLength: number,
): string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > maxItems) {
    throw new A2AProtocolError(
      400,
      "INVALID_ARGUMENT",
      "INVALID_PARAMETER",
      `${field} должен быть массивом не более чем из ${maxItems} строк`,
      { field },
    );
  }
  return value.map((item, index) => requestText(item, `${field}[${index}]`, maxLength).trim());
}

function validateOptionalObject(value: unknown, field: string): void {
  if (value !== undefined && (!value || typeof value !== "object" || Array.isArray(value))) {
    throw new A2AProtocolError(
      400,
      "INVALID_ARGUMENT",
      "INVALID_PARAMETER",
      `${field} должен быть JSON-объектом`,
      { field },
    );
  }
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, field]) => field !== undefined)
    .sort(([left], [right]) => left.localeCompare(right));
  return `{${entries.map(([key, field]) => `${JSON.stringify(key)}:${canonicalJson(field)}`).join(",")}}`;
}

function normalizePart(part: unknown, index: number, allowedModes: Set<A2AInputMode>): { part: A2APart; input: string } {
  if (!part || typeof part !== "object" || Array.isArray(part)) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", `message.parts[${index}] должен быть объектом`);
  }
  const candidate = part as A2APart;
  validateOptionalObject(candidate.metadata, `message.parts[${index}].metadata`);
  if (candidate.filename !== undefined && typeof candidate.filename !== "string") {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", `message.parts[${index}].filename должен быть строкой`);
  }
  if (candidate.mediaType !== undefined && typeof candidate.mediaType !== "string") {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", `message.parts[${index}].mediaType должен быть строкой`);
  }
  const kinds = (["text", "raw", "url", "data"] as const).filter((key) => Object.hasOwn(candidate, key));
  if (kinds.length !== 1) {
    throw new A2AProtocolError(
      400,
      "INVALID_ARGUMENT",
      "INVALID_MESSAGE",
      `message.parts[${index}] должен содержать ровно одно из text, raw, url или data`,
    );
  }
  const kind = kinds[0]!;
  if (kind === "raw" || kind === "url") {
    throw new A2AProtocolError(
      400,
      "INVALID_ARGUMENT",
      "CONTENT_TYPE_NOT_SUPPORTED",
      "A2A boundary АГАТ принимает только inline text и JSON data; file/raw/url parts запрещены",
      { part: String(index), kind },
    );
  }
  if (kind === "text") {
    if (!allowedModes.has("text/plain")) {
      throw new A2AProtocolError(400, "INVALID_ARGUMENT", "CONTENT_TYPE_NOT_SUPPORTED", "Этот endpoint не принимает text/plain");
    }
    if (candidate.mediaType && candidate.mediaType !== "text/plain") {
      throw new A2AProtocolError(400, "INVALID_ARGUMENT", "CONTENT_TYPE_NOT_SUPPORTED", `Media type ${candidate.mediaType} не поддерживается`);
    }
    const text = requestText(candidate.text, `message.parts[${index}].text`, 100_000);
    return { part: { text, mediaType: "text/plain" }, input: text };
  }
  if (!allowedModes.has("application/json")) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "CONTENT_TYPE_NOT_SUPPORTED", "Этот endpoint не принимает application/json");
  }
  if (candidate.mediaType && candidate.mediaType !== "application/json") {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "CONTENT_TYPE_NOT_SUPPORTED", `Media type ${candidate.mediaType} не поддерживается`);
  }
  let serialized: string;
  try {
    serialized = JSON.stringify(candidate.data);
  } catch {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", `message.parts[${index}].data не сериализуется в JSON`);
  }
  if (serialized === undefined || serialized.length > 100_000) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", `message.parts[${index}].data превышает лимит`);
  }
  return {
    part: { data: candidate.data, mediaType: "application/json" },
    input: `[application/json]\n${serialized}`,
  };
}

export function normalizeA2ASendMessageRequest(
  input: unknown,
  endpoint: Pick<A2AEndpointConnection, "inputModes" | "maxInputCharacters">,
): A2ANormalizedMessage {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_REQUEST", "SendMessageRequest должен быть JSON-объектом");
  }
  const request = input as A2ASendMessageRequest;
  validateOptionalObject(request.configuration, "configuration");
  validateOptionalObject(request.metadata, "metadata");
  if (request.tenant !== undefined) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", "Этот interface не использует tenant routing", { field: "tenant" });
  }
  if (!request.message || typeof request.message !== "object" || Array.isArray(request.message)) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", "message обязателен");
  }
  validateOptionalObject(request.message.metadata, "message.metadata");
  const messageId = requestText(request.message.messageId, "message.messageId", 200).trim();
  if (request.message.role !== "ROLE_USER") {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", "Входное message.role должно быть ROLE_USER");
  }
  if (request.message.taskId !== undefined) {
    throw new A2AProtocolError(
      400,
      "FAILED_PRECONDITION",
      "UNSUPPORTED_OPERATION",
      "Продолжение существующего task пока не поддерживается; отправьте новый Message с тем же contextId",
    );
  }
  if (!Array.isArray(request.message.parts) || request.message.parts.length < 1 || request.message.parts.length > 32) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", "message.parts должен содержать 1..32 parts");
  }
  if (request.configuration?.taskPushNotificationConfig !== undefined) {
    throw new A2AProtocolError(
      400,
      "FAILED_PRECONDITION",
      "PUSH_NOTIFICATION_NOT_SUPPORTED",
      "Push notifications не поддерживаются этим endpoint",
    );
  }
  const acceptedModes = requestStringArray(
    request.configuration?.acceptedOutputModes,
    "acceptedOutputModes",
    16,
    200,
  );
  if (acceptedModes.length > 0) {
    if (acceptedModes.length > 0 && !acceptedModes.includes("text/plain")) {
      throw new A2AProtocolError(
        400,
        "INVALID_ARGUMENT",
        "CONTENT_TYPE_NOT_SUPPORTED",
        "Endpoint возвращает только text/plain artifacts",
      );
    }
  }
  if (request.configuration?.returnImmediately !== undefined && typeof request.configuration.returnImmediately !== "boolean") {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", "returnImmediately должен быть boolean");
  }
  const allowedModes = new Set(endpoint.inputModes);
  const normalizedParts = request.message.parts.map((part, index) => normalizePart(part, index, allowedModes));
  const flattenedInput = normalizedParts.map((part) => part.input).join("\n\n");
  if (!flattenedInput.trim()) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", "Сообщение не содержит входных данных");
  }
  if (flattenedInput.length > endpoint.maxInputCharacters) {
    throw new A2AProtocolError(
      400,
      "INVALID_ARGUMENT",
      "INVALID_MESSAGE",
      `Вход превышает policy endpoint: ${endpoint.maxInputCharacters} символов`,
      { maxInputCharacters: String(endpoint.maxInputCharacters) },
    );
  }
  const contextId = request.message.contextId === undefined
    ? randomUUID()
    : requestText(request.message.contextId, "message.contextId", 200).trim();
  const extensions = requestStringArray(request.message.extensions, "message.extensions", 32, 2_000);
  const referenceTaskIds = requestStringArray(
    request.message.referenceTaskIds,
    "message.referenceTaskIds",
    32,
    200,
  );
  const message: A2AMessage = {
    messageId,
    contextId,
    role: "ROLE_USER",
    parts: normalizedParts.map((part) => part.part),
    ...(extensions.length ? { extensions } : {}),
    ...(referenceTaskIds.length ? { referenceTaskIds } : {}),
  };
  const requestSha256 = createHash("sha256").update(canonicalJson({
    message,
    acceptedOutputModes: acceptedModes,
  })).digest("hex");
  return {
    message,
    input: flattenedInput,
    contextId,
    historyLength: requestInteger(request.configuration?.historyLength, "historyLength", 0, 100, 1),
    returnImmediately: request.configuration?.returnImmediately ?? false,
    requestSha256,
  };
}

export function buildA2AAgentCard(endpoint: A2AEndpointConnection, publicBaseUrl: string): Record<string, unknown> {
  const interfaceUrl = `${publicBaseUrl}/a2a/v1/endpoints/${encodeURIComponent(endpoint.id)}`;
  return {
    name: endpoint.name,
    description: endpoint.description,
    supportedInterfaces: [{
      url: interfaceUrl,
      protocolBinding: "HTTP+JSON",
      protocolVersion: A2A_PROTOCOL_VERSION,
    }],
    provider: {
      organization: "АГАТ",
      url: publicBaseUrl,
    },
    version: endpoint.version,
    capabilities: {
      streaming: false,
      pushNotifications: false,
      extendedAgentCard: false,
    },
    securitySchemes: {
      agatBearer: {
        httpAuthSecurityScheme: {
          scheme: "Bearer",
          bearerFormat: "opaque",
          description: "Одноразово выданный project-scoped token АГАТ",
        },
      },
    },
    securityRequirements: [{ schemes: { agatBearer: { list: [] } } }],
    defaultInputModes: endpoint.inputModes,
    defaultOutputModes: [...A2A_SUPPORTED_OUTPUT_MODES],
    skills: [{
      id: endpoint.skillId,
      name: endpoint.skillName,
      description: endpoint.skillDescription,
      tags: endpoint.tags,
      ...(endpoint.examples.length ? { examples: endpoint.examples } : {}),
      inputModes: endpoint.inputModes,
      outputModes: [...A2A_SUPPORTED_OUTPUT_MODES],
    }],
  };
}

export function runStatusToA2AState(status: string): A2ATaskState {
  switch (status) {
    case "queued": return "TASK_STATE_SUBMITTED";
    case "running": return "TASK_STATE_WORKING";
    case "waiting_approval": return "TASK_STATE_AUTH_REQUIRED";
    case "completed": return "TASK_STATE_COMPLETED";
    case "failed": return "TASK_STATE_FAILED";
    case "cancelled": return "TASK_STATE_CANCELED";
    default: return "TASK_STATE_UNSPECIFIED";
  }
}

export function isA2ASettledState(state: A2ATaskState): boolean {
  return TERMINAL_OR_INTERRUPTED_STATES.has(state);
}

export function a2aErrorBody(error: A2AProtocolError): Record<string, unknown> {
  return {
    error: {
      code: error.httpStatus,
      status: error.rpcStatus,
      message: error.message,
      details: [{
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        reason: error.reason,
        domain: "a2a-protocol.org",
        metadata: {
          ...error.metadata,
          timestamp: new Date().toISOString(),
        },
      }],
    },
  };
}
