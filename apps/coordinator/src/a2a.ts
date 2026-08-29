import { createHash, randomUUID } from "node:crypto";

import type {
  A2AEndpointConnection,
  A2AInputMode,
  A2AMessage,
  A2ANormalizedFile,
  A2ANormalizedMessage,
  A2ANormalizedPushConfig,
  A2APart,
  A2ARemoteConnection,
  A2ASendMessageRequest,
  A2ATaskState,
  CreateA2AEndpointInput,
} from "./types.js";

export const A2A_PROTOCOL_VERSION = "1.0";
export const A2A_MEDIA_TYPE = "application/a2a+json";
export const A2A_ADAPTER_VERSION = "1.1.0";
export const A2A_SUPPORTED_OUTPUT_MODES = ["text/plain"] as const;

const INLINE_MODES = new Set<A2AInputMode>(["text/plain", "application/json"]);
const MEDIA_TYPE = /^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}\/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$/;
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "::1", "[::1]", "localhost"]);
const A2A_TASK_STATES = new Set<A2ATaskState>([
  "TASK_STATE_UNSPECIFIED",
  "TASK_STATE_SUBMITTED",
  "TASK_STATE_WORKING",
  "TASK_STATE_COMPLETED",
  "TASK_STATE_FAILED",
  "TASK_STATE_CANCELED",
  "TASK_STATE_INPUT_REQUIRED",
  "TASK_STATE_REJECTED",
  "TASK_STATE_AUTH_REQUIRED",
]);
const TERMINAL_OR_INTERRUPTED_STATES = new Set<A2ATaskState>([
  "TASK_STATE_COMPLETED",
  "TASK_STATE_FAILED",
  "TASK_STATE_CANCELED",
  "TASK_STATE_REJECTED",
  "TASK_STATE_INPUT_REQUIRED",
  "TASK_STATE_AUTH_REQUIRED",
]);

type A2AOutboundValidationRemote = Pick<
  A2ARemoteConnection,
  "allowFileArtifacts" | "inputModes" | "outputModes"
>;

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
    | "outputModes"
    | "knowledgeCollectionIds"
    | "approvalRequired"
    | "streamingEnabled"
    | "pushNotificationsEnabled"
    | "fileArtifactsEnabled"
    | "enabled"
    | "priority"
    | "maxInputCharacters"
    | "maxActiveTasks"
    | "maxFileBytes"
    | "maxFiles"
  >,
): Omit<CreateA2AEndpointInput, "tags" | "examples" | "inputModes" | "outputModes" | "knowledgeCollectionIds"> & {
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
  const fileArtifactsEnabled = input.fileArtifactsEnabled ?? defaults.fileArtifactsEnabled;
  const normalizeModes = (value: unknown, fallback: string[], field: string): string[] => {
    const source = value === undefined ? fallback : value;
    if (!Array.isArray(source) || source.length === 0 || source.length > 16) {
      throw new Error(`${field} должен содержать 1..16 media types`);
    }
    const modes = source.map((mode) => requiredText(mode, field, 255).toLowerCase());
    if (modes.some((mode) => !MEDIA_TYPE.test(mode))) throw new Error(`${field} содержит некорректный media type`);
    if (!fileArtifactsEnabled && modes.some((mode) => !INLINE_MODES.has(mode))) {
      throw new Error(`${field}: file media types требуют включённые file artifacts`);
    }
    return [...new Set(modes)];
  };
  const inputModes = normalizeModes(input.inputModes, defaults.inputModes, "A2A inputModes");
  const outputModes = normalizeModes(input.outputModes, defaults.outputModes, "A2A outputModes");
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
    outputModes,
    knowledgeCollectionIds,
    approvalRequired: input.approvalRequired ?? defaults.approvalRequired,
    streamingEnabled: input.streamingEnabled ?? defaults.streamingEnabled,
    pushNotificationsEnabled: input.pushNotificationsEnabled ?? defaults.pushNotificationsEnabled,
    fileArtifactsEnabled,
    enabled: input.enabled ?? defaults.enabled,
    priority: boundedInteger(input.priority, 0, 100, defaults.priority),
    maxInputCharacters: boundedInteger(input.maxInputCharacters, 1_000, 100_000, defaults.maxInputCharacters),
    maxActiveTasks: boundedInteger(input.maxActiveTasks, 1, 100, defaults.maxActiveTasks),
    maxFileBytes: boundedInteger(input.maxFileBytes, 1_024, 2_000_000, defaults.maxFileBytes),
    maxFiles: boundedInteger(input.maxFiles, 1, 8, defaults.maxFiles),
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

function normalizedFilename(value: unknown, index: number): string {
  const filename = requestText(value, `message.parts[${index}].filename`, 180).trim();
  if (filename === "." || filename === ".." || /[\\/\0\r\n]/.test(filename)) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", `message.parts[${index}].filename небезопасен`);
  }
  return filename;
}

function decodeBase64File(value: unknown, index: number, maxFileBytes: number): Buffer {
  if (typeof value !== "string" || !value || value.length > Math.ceil(maxFileBytes / 3) * 4 + 4) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", `message.parts[${index}].raw превышает лимит`);
  }
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", `message.parts[${index}].raw должен быть canonical base64`);
  }
  const bytes = Buffer.from(value, "base64");
  if (bytes.length === 0 || bytes.length > maxFileBytes || bytes.toString("base64") !== value) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", `message.parts[${index}].raw превышает лимит или повреждён`);
  }
  return bytes;
}

function normalizePart(
  part: unknown,
  index: number,
  endpoint: Pick<A2AEndpointConnection, "inputModes" | "fileArtifactsEnabled" | "maxFileBytes">,
): { part: A2APart; input: string; file: A2ANormalizedFile | null } {
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
  if (kind === "url") {
    throw new A2AProtocolError(
      400,
      "INVALID_ARGUMENT",
      "CONTENT_TYPE_NOT_SUPPORTED",
      "A2A boundary АГАТ не скачивает URL file parts; передайте bounded inline raw",
      { part: String(index), kind },
    );
  }
  const allowedModes = new Set(endpoint.inputModes);
  if (kind === "text") {
    if (!allowedModes.has("text/plain")) {
      throw new A2AProtocolError(400, "INVALID_ARGUMENT", "CONTENT_TYPE_NOT_SUPPORTED", "Этот endpoint не принимает text/plain");
    }
    if (candidate.mediaType && candidate.mediaType !== "text/plain") {
      throw new A2AProtocolError(400, "INVALID_ARGUMENT", "CONTENT_TYPE_NOT_SUPPORTED", `Media type ${candidate.mediaType} не поддерживается`);
    }
    const text = requestText(candidate.text, `message.parts[${index}].text`, 100_000);
    return { part: { text, mediaType: "text/plain" }, input: text, file: null };
  }
  if (kind === "raw") {
    if (!endpoint.fileArtifactsEnabled) {
      throw new A2AProtocolError(400, "FAILED_PRECONDITION", "CONTENT_TYPE_NOT_SUPPORTED", "File artifacts выключены policy endpoint");
    }
    const mediaType = requestText(candidate.mediaType, `message.parts[${index}].mediaType`, 255).trim().toLowerCase();
    if (!MEDIA_TYPE.test(mediaType) || !allowedModes.has(mediaType) || INLINE_MODES.has(mediaType)) {
      throw new A2AProtocolError(400, "INVALID_ARGUMENT", "CONTENT_TYPE_NOT_SUPPORTED", `Media type ${mediaType} не разрешён endpoint`);
    }
    const filename = normalizedFilename(candidate.filename, index);
    const bytes = decodeBase64File(candidate.raw, index, endpoint.maxFileBytes ?? 512_000);
    const sha256 = createHash("sha256").update(bytes).digest("hex");
    return {
      part: { raw: bytes.toString("base64"), filename, mediaType },
      input: `[A2A file artifact: ${filename}; mediaType=${mediaType}; bytes=${bytes.length}; sha256=${sha256}]`,
      file: { filename, mediaType, bytes, sha256 },
    };
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
    file: null,
  };
}

export function normalizeA2APushNotificationConfig(
  value: unknown,
  fallbackId?: string,
  expectedTaskId?: string,
): A2ANormalizedPushConfig {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", "taskPushNotificationConfig должен быть объектом");
  }
  const config = value as Record<string, unknown>;
  if (config.taskId !== undefined && config.taskId !== "") {
    const configuredTaskId = requestText(config.taskId, "taskPushNotificationConfig.taskId", 200).trim();
    if (!expectedTaskId || configuredTaskId !== expectedTaskId) {
      throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", "taskPushNotificationConfig.taskId не совпадает с task route");
    }
  }
  const id = config.id === undefined || config.id === ""
    ? fallbackId ?? randomUUID()
    : requestText(config.id, "taskPushNotificationConfig.id", 200).trim();
  const rawUrl = requestText(config.url, "taskPushNotificationConfig.url", 2_048).trim();
  let parsed: URL;
  try {
    parsed = new URL(rawUrl);
  } catch {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", "Push callback URL должен быть абсолютным URL");
  }
  if (parsed.username || parsed.password || parsed.hash
    || (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && LOOPBACK_HOSTS.has(parsed.hostname)))) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", "Push callback требует HTTPS; HTTP разрешён только для loopback");
  }
  const token = config.token === undefined ? "" : requestText(config.token, "taskPushNotificationConfig.token", 1_024);
  let authentication: A2ANormalizedPushConfig["authentication"] = null;
  if (config.authentication !== undefined) {
    validateOptionalObject(config.authentication, "taskPushNotificationConfig.authentication");
    const auth = config.authentication as Record<string, unknown>;
    if (typeof auth.scheme !== "string" || auth.scheme.toLowerCase() !== "bearer") {
      throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", "Push authentication поддерживает только Bearer");
    }
    const credentials = requestText(auth.credentials, "taskPushNotificationConfig.authentication.credentials", 8_000);
    if (/[\r\n]/.test(credentials)) {
      throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", "Push credentials содержат управляющие символы");
    }
    authentication = { scheme: "Bearer", credentials };
  }
  return { id, url: parsed.toString(), token, authentication };
}

export function normalizeA2ASendMessageRequest(
  input: unknown,
  endpoint: Pick<A2AEndpointConnection,
    | "inputModes"
    | "outputModes"
    | "maxInputCharacters"
    | "fileArtifactsEnabled"
    | "maxFileBytes"
    | "maxFiles"
    | "pushNotificationsEnabled"
  >,
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
  if (request.configuration?.taskPushNotificationConfig !== undefined && endpoint.pushNotificationsEnabled !== true) {
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
  const outputModes = endpoint.outputModes ?? ["text/plain"];
  if (acceptedModes.length > 0) {
    const unsupported = acceptedModes.find((mode) => !outputModes.includes(mode.toLowerCase()));
    if (unsupported) {
      throw new A2AProtocolError(
        400,
        "INVALID_ARGUMENT",
        "CONTENT_TYPE_NOT_SUPPORTED",
        `Endpoint не возвращает ${unsupported}`,
      );
    }
  }
  if (request.configuration?.returnImmediately !== undefined && typeof request.configuration.returnImmediately !== "boolean") {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", "returnImmediately должен быть boolean");
  }
  const normalizedParts = request.message.parts.map((part, index) => normalizePart(part, index, endpoint));
  const files = normalizedParts.flatMap((part) => part.file ? [part.file] : []);
  const maxFiles = endpoint.maxFiles ?? 4;
  if (files.length > maxFiles) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_MESSAGE", `Endpoint принимает не более ${maxFiles} files`);
  }
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
    taskPushNotificationConfig: request.configuration?.taskPushNotificationConfig,
  })).digest("hex");
  const pushNotificationConfig = request.configuration?.taskPushNotificationConfig === undefined
    ? null
    : normalizeA2APushNotificationConfig(
      request.configuration.taskPushNotificationConfig,
      `push-${createHash("sha256").update(canonicalJson({
        messageId,
        config: request.configuration.taskPushNotificationConfig,
      })).digest("hex").slice(0, 32)}`,
    );
  return {
    message,
    input: flattenedInput,
    contextId,
    historyLength: requestInteger(request.configuration?.historyLength, "historyLength", 0, 100, 1),
    returnImmediately: request.configuration?.returnImmediately ?? false,
    requestSha256,
    files,
    pushNotificationConfig,
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
      streaming: endpoint.streamingEnabled,
      pushNotifications: endpoint.pushNotificationsEnabled,
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
    defaultOutputModes: endpoint.outputModes,
    skills: [{
      id: endpoint.skillId,
      name: endpoint.skillName,
      description: endpoint.skillDescription,
      tags: endpoint.tags,
      ...(endpoint.examples.length ? { examples: endpoint.examples } : {}),
      inputModes: endpoint.inputModes,
      outputModes: endpoint.outputModes,
    }],
  };
}

function validateOutboundParts(
  value: unknown,
  remote: A2AOutboundValidationRemote,
  field: string,
  allowedModes = remote.outputModes,
): void {
  if (!Array.isArray(value) || value.length < 1 || value.length > 32) {
    throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field} должен содержать 1..32 parts`);
  }
  for (let index = 0; index < value.length; index += 1) {
    const rawPart = value[index];
    if (!rawPart || typeof rawPart !== "object" || Array.isArray(rawPart)) {
      throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field}[${index}] некорректен`);
    }
    const part = rawPart as A2APart;
    const kinds = (["text", "raw", "url", "data"] as const).filter((kind) => Object.hasOwn(part, kind));
    if (kinds.length !== 1) {
      throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field}[${index}] нарушает Part oneof`);
    }
    const kind = kinds[0]!;
    const mediaType = typeof part.mediaType === "string"
      ? part.mediaType.toLowerCase()
      : kind === "text" ? "text/plain" : kind === "data" ? "application/json" : "";
    if (mediaType && !MEDIA_TYPE.test(mediaType)) {
      throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field}[${index}].mediaType некорректен`);
    }
    if (mediaType && !allowedModes.includes(mediaType)) {
      throw new A2AProtocolError(502, "DATA_LOSS", "CONTENT_TYPE_NOT_SUPPORTED", `Peer вернул незаявленный media type ${mediaType}`);
    }
    if (kind === "text") {
      if (typeof part.text !== "string" || part.text.length > 1_000_000) {
        throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field}[${index}].text некорректен`);
      }
      continue;
    }
    if (kind === "data") {
      let serialized = "";
      try { serialized = JSON.stringify(part.data); } catch { /* handled below */ }
      if (!serialized || serialized.length > 1_000_000) {
        throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field}[${index}].data некорректен`);
      }
      continue;
    }
    if (!remote.allowFileArtifacts) {
      throw new A2AProtocolError(502, "DATA_LOSS", "CONTENT_TYPE_NOT_SUPPORTED", "Peer вернул file artifact, выключенный policy");
    }
    if (!mediaType || !MEDIA_TYPE.test(mediaType)) {
      throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field}[${index}].mediaType обязателен для file`);
    }
    normalizedFilename(part.filename, index);
    if (kind === "raw") {
      decodeBase64File(part.raw, index, 2_000_000);
      continue;
    }
    if (typeof part.url !== "string" || part.url.length > 2_048) {
      throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field}[${index}].url некорректен`);
    }
    let url: URL;
    try { url = new URL(part.url); } catch {
      throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field}[${index}].url не является абсолютным URL`);
    }
    if (url.protocol !== "https:" || url.username || url.password || url.hash) {
      throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field}[${index}].url должен быть HTTPS без credentials и fragment`);
    }
  }
}

function validateOutboundMessage(
  value: unknown,
  remote: A2AOutboundValidationRemote,
  field: string,
  serverMessage: boolean,
): void {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field} некорректен`);
  }
  const message = value as Record<string, unknown>;
  if (typeof message.messageId !== "string" || !message.messageId || message.messageId.length > 200) {
    throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field}.messageId обязателен`);
  }
  const allowedRoles = serverMessage ? ["ROLE_AGENT"] : ["ROLE_USER", "ROLE_AGENT"];
  if (typeof message.role !== "string" || !allowedRoles.includes(message.role)) {
    throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field}.role некорректен`);
  }
  if (serverMessage && (typeof message.contextId !== "string" || !message.contextId || message.contextId.length > 200)) {
    throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field}.contextId обязателен для server message`);
  }
  for (const key of ["contextId", "taskId"] as const) {
    if (message[key] !== undefined && (typeof message[key] !== "string" || !message[key] || message[key].length > 200)) {
      throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `${field}.${key} некорректен`);
    }
  }
  validateOutboundParts(
    message.parts,
    remote,
    `${field}.parts`,
    message.role === "ROLE_USER" ? remote.inputModes : remote.outputModes,
  );
}

export function validateA2AOutboundResponse(
  response: Record<string, unknown>,
  remote: A2AOutboundValidationRemote,
): void {
  const payloads = (["task", "message"] as const).filter((key) => Object.hasOwn(response, key));
  if (payloads.length !== 1) {
    throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", "SendMessageResponse должен содержать ровно task или message");
  }
  const payload = response[payloads[0]!];
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", "Outbound A2A payload некорректен");
  }
  const record = payload as Record<string, unknown>;
  if (payloads[0] === "message") {
    validateOutboundMessage(record, remote, "message", true);
    return;
  }
  if (typeof record.id !== "string" || !record.id || record.id.length > 200) {
    throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", "Outbound Task не содержит корректный id");
  }
  if (record.contextId !== undefined
    && (typeof record.contextId !== "string" || !record.contextId || record.contextId.length > 200)) {
    throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", "Outbound Task содержит некорректный contextId");
  }
  const status = record.status && typeof record.status === "object" && !Array.isArray(record.status)
    ? record.status as Record<string, unknown>
    : null;
  if (!status || typeof status.state !== "string" || !A2A_TASK_STATES.has(status.state as A2ATaskState)) {
    throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", "Outbound Task не содержит status.state");
  }
  if (status.timestamp !== undefined
    && (typeof status.timestamp !== "string" || !status.timestamp || Number.isNaN(Date.parse(status.timestamp)))) {
    throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", "Outbound Task содержит некорректный status.timestamp");
  }
  if (status.message !== undefined) validateOutboundMessage(status.message, remote, "task.status.message", true);
  if (record.artifacts !== undefined) {
    if (!Array.isArray(record.artifacts) || record.artifacts.length > 16) {
      throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", "Outbound Task artifacts некорректны");
    }
    const artifactIds = new Set<string>();
    record.artifacts.forEach((artifact, index) => {
      if (!artifact || typeof artifact !== "object" || Array.isArray(artifact)) {
        throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `task.artifacts[${index}] некорректен`);
      }
      const artifactRecord = artifact as Record<string, unknown>;
      if (typeof artifactRecord.artifactId !== "string" || !artifactRecord.artifactId || artifactRecord.artifactId.length > 200
        || artifactIds.has(artifactRecord.artifactId)) {
        throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", `task.artifacts[${index}].artifactId некорректен`);
      }
      artifactIds.add(artifactRecord.artifactId);
      validateOutboundParts(artifactRecord.parts, remote, `task.artifacts[${index}].parts`);
    });
  }
  if (record.history !== undefined) {
    if (!Array.isArray(record.history) || record.history.length > 1_000) {
      throw new A2AProtocolError(502, "DATA_LOSS", "INVALID_RESPONSE", "Outbound Task history некорректна");
    }
    record.history.forEach((message, index) => validateOutboundMessage(message, remote, `task.history[${index}]`, false));
  }
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
