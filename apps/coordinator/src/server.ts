import fs from "node:fs";
import { createHash } from "node:crypto";
import http, { type IncomingMessage, type ServerResponse } from "node:http";
import path from "node:path";
import { pathToFileURL } from "node:url";

import {
  loadConfig,
  validateTemporalCoordinatorConfig,
  type CoordinatorConfig,
} from "./config.js";
import {
  AuthenticationError,
  localAdminContext,
  OidcVerifier,
  selectProject,
  type AgatRole,
  type AuthContext,
} from "./auth.js";
import { AgatStore } from "./database.js";
import { enterPostgresTenantScope, runWithPostgresSystemScope } from "./postgres-database.js";
import {
  createLocalWorkerLauncher,
  type LocalWorkerLauncher,
  WorkerLauncherError,
} from "./local-workers.js";
import {
  createDatabaseProcessRuntime,
  createProcessRuntime,
  normalizeProcessScheduleInput,
  type ProcessRuntime,
} from "./process-runtime.js";
import { bearerToken, tokensEqual } from "./security.js";
import { CoordinatorTelemetry } from "./telemetry.js";
import { McpGateway } from "./mcp.js";
import { createSandboxExecutor } from "./sandbox.js";
import { evaluateSiemResponse } from "./siem-export.js";
import {
  createEdgeAttestationVerifier,
  EdgeAttestationError,
  type EdgeAttestationVerifier,
} from "./edge-attestation.js";
import {
  A2A_MEDIA_TYPE,
  A2A_PROTOCOL_VERSION,
  A2AProtocolError,
  a2aErrorBody,
  buildA2AAgentCard,
  isA2ASettledState,
  normalizeA2APublicBaseUrl,
  normalizeA2APushNotificationConfig,
  normalizeA2ASendMessageRequest,
  validateA2AOutboundResponse,
  validateA2AContentType,
  validateA2AProtocolVersion,
} from "./a2a.js";
import {
  A2ATransportError,
  discoverA2ARemote,
  invokeA2ARemote,
  sendA2APush,
  validateA2AOutboundTarget,
  type A2AOutboundPolicy,
} from "./a2a-transport.js";
import type {
  A2AEndpointConnection,
  A2ASendMessageRequest,
  A2ATaskState,
  A2AOutboundInvocationInput,
  CreateA2AEndpointInput,
  CreateA2ARemoteInput,
  CreateAgentInput,
  CreateCredentialInput,
  CreateEvalDatasetInput,
  CreateEvalDatasetVersionInput,
  CreateEvalExperimentInput,
  EdgeEnrollmentChallengeInput,
  EdgeWipeAcknowledgement,
  EdgeWorkerRegistration,
  CreateKnowledgeCollectionInput,
  CreatePromptInput,
  CreatePromptVersionInput,
  CreateProcessInput,
  CreateProcessWebhookInput,
  CreateProjectInput,
  CreateRunInput,
  CreateMcpServerInput,
  LaunchLocalWorkersInput,
  IngestKnowledgeDocumentInput,
  KnowledgeEmbeddingResult,
  KnowledgeSearchRequest,
  HumanEvalReviewInput,
  JudgeEvalExperimentInput,
  ModelRouterPolicy,
  ProjectFleetPolicyInput,
  RegisterWorkerReleaseInput,
  ReplayRunInput,
  ReplayProcessInstanceInput,
  PromotePromptInput,
  SchedulerMode,
  SaveMemoryInput,
  McpToolPolicy,
  StartProcessInput,
  DeliverProcessWebhookInput,
  TestProcessNodeInput,
  UpdateProcessInput,
  UpdateMcpServerInput,
  UpdateA2AEndpointInput,
  UpdateA2ARemoteInput,
  WorkerCapabilities,
  WorkerArtifactInput,
  WorkerExecutionMetrics,
  WorkerMetrics,
  WorkerRegistration,
  WorkerRuntimeAttestationChallengeInput,
  WorkerRuntimeAttestationEnvelope,
  WorkerRolloutInput,
} from "./types.js";

const JSON_LIMIT_BYTES = 1_048_576;
const A2A_JSON_LIMIT_BYTES = 24_000_000;
const KNOWLEDGE_JSON_LIMIT_BYTES = 8_388_608;
const STATIC_CONTENT_TYPES: Record<string, string> = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".webmanifest": "application/manifest+json; charset=utf-8",
};

class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function json(response: ServerResponse, status: number, body: unknown): void {
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  response.end(JSON.stringify(body));
}

function a2aJson(
  response: ServerResponse,
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): void {
  response.writeHead(status, {
    "content-type": `${A2A_MEDIA_TYPE}; charset=utf-8`,
    "cache-control": "no-store",
    "a2a-version": A2A_PROTOCOL_VERSION,
    ...headers,
  });
  response.end(JSON.stringify(body));
}

function noContent(response: ServerResponse): void {
  response.writeHead(204);
  response.end();
}

function xml(response: ServerResponse, status: number, body: string, filename?: string): void {
  response.writeHead(status, {
    "content-type": "application/xml; charset=utf-8",
    "cache-control": "no-store",
    ...(filename ? { "content-disposition": attachmentDisposition(filename) } : {}),
  });
  response.end(body);
}

function attachmentDisposition(filename: string): string {
  const fallback = filename
    .normalize("NFKD")
    .replace(/[^\x20-\x7E]+/g, "-")
    .replace(/["\\]/g, "-")
    .slice(0, 120) || "artifact";
  return `attachment; filename="${fallback}"; filename*=UTF-8''${encodeURIComponent(filename)}`;
}

async function readJson<T>(request: IncomingMessage, limitBytes = JSON_LIMIT_BYTES): Promise<T> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > limitBytes) throw new HttpError(413, "Тело запроса слишком большое");
    chunks.push(buffer);
  }
  if (chunks.length === 0) return {} as T;
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8")) as T;
  } catch {
    throw new HttpError(400, "Некорректный JSON");
  }
}

async function readText(request: IncomingMessage, limitBytes = JSON_LIMIT_BYTES): Promise<string> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > limitBytes) throw new HttpError(413, "Тело запроса слишком большое");
    chunks.push(buffer);
  }
  return Buffer.concat(chunks).toString("utf8");
}

async function readA2AJson(request: IncomingMessage): Promise<unknown> {
  try {
    return await readJson<unknown>(request, A2A_JSON_LIMIT_BYTES);
  } catch (error) {
    if (error instanceof HttpError) {
      throw new A2AProtocolError(
        error.status,
        error.status === 413 ? "RESOURCE_EXHAUSTED" : "INVALID_ARGUMENT",
        error.status === 413 ? "REQUEST_TOO_LARGE" : "INVALID_REQUEST",
        error.message,
      );
    }
    throw error;
  }
}

function setSecurityHeaders(response: ServerResponse, config: CoordinatorConfig): void {
  const connectSources = ["'self'"];
  if (config.oidcEnabled && config.oidcIssuer) {
    try {
      const issuer = new URL(config.oidcIssuer);
      if (issuer.protocol === "http:" || issuer.protocol === "https:") connectSources.push(issuer.origin);
    } catch {
      // Invalid issuer configuration is reported by the OIDC verifier. Keep CSP closed here.
    }
  }
  response.setHeader("content-security-policy", `default-src 'self'; connect-src ${connectSources.join(" ")}; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'`);
  response.setHeader("cross-origin-resource-policy", "same-origin");
  response.setHeader("referrer-policy", "no-referrer");
  response.setHeader("x-content-type-options", "nosniff");
  response.setHeader("x-frame-options", "DENY");
}

function setCors(request: IncomingMessage, response: ServerResponse, config: CoordinatorConfig): boolean {
  const origin = request.headers.origin;
  if (!origin) return true;
  const host = request.headers.host;
  const sameOrigin = Boolean(host) && (origin === `http://${host}` || origin === `https://${host}`);
  const allowed = sameOrigin || config.allowedOrigins.includes(origin) || config.allowedOrigins.includes("*");
  if (!allowed) return false;
  response.setHeader("access-control-allow-origin", origin);
  response.setHeader("vary", "Origin");
  response.setHeader("access-control-allow-methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS");
  response.setHeader("access-control-allow-headers", "content-type, authorization, idempotency-key, x-agat-admin-token, x-agat-project-id, a2a-version, a2a-extensions, traceparent");
  return true;
}

const READ_ROLES: AgatRole[] = ["admin", "designer", "operator", "viewer", "auditor"];

async function authorize(
  request: IncomingMessage,
  config: CoordinatorConfig,
  verifier: OidcVerifier | null,
  allowedRoles: AgatRole[],
  requireLegacyAdmin: boolean,
  allowProjectFallback = false,
): Promise<AuthContext & { projectId: string }> {
  let context: AuthContext;
  if (config.oidcEnabled) {
    const token = bearerToken(request.headers.authorization);
    if (!token || !verifier) throw new AuthenticationError(401, "Нужен OIDC access token");
    context = await verifier.verify(token);
    if (![...context.roles].some((role) => allowedRoles.includes(role))) {
      throw new AuthenticationError(403, "Недостаточно прав для операции");
    }
  } else {
    if (requireLegacyAdmin && config.adminToken) {
      const supplied = request.headers["x-agat-admin-token"];
      if (typeof supplied !== "string" || !tokensEqual(supplied, config.adminToken)) {
        throw new HttpError(401, "Нужен токен администратора");
      }
    }
    context = localAdminContext();
  }
  const projectHeader = request.headers["x-agat-project-id"];
  let projectId: string;
  try {
    projectId = selectProject(context, typeof projectHeader === "string" ? projectHeader : undefined);
  } catch (error) {
    if (!allowProjectFallback || !(error instanceof AuthenticationError)) throw error;
    projectId = context.roles.has("admin") ? "default" : [...context.projectIds].sort()[0] ?? "default";
  }
  enterPostgresTenantScope(projectId);
  return Object.assign(context, { projectId });
}

function requireWorker(
  request: IncomingMessage,
  store: AgatStore,
  allowWipeControl = false,
): Record<string, unknown> {
  const token = bearerToken(request.headers.authorization);
  if (!token) throw new HttpError(401, "Нужен токен узла");
  const node = store.authenticateNode(token, allowWipeControl);
  if (!node) throw new HttpError(401, "Токен узла недействителен");
  return node;
}

function safeMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Неизвестная ошибка";
}

function sha256Text(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function parseSchedulerMode(value: unknown): SchedulerMode {
  if (value === "sequential" || value === "parallel" || value === "auto") return value;
  throw new HttpError(400, "Неизвестный режим планировщика");
}

function routeParam(pathname: string, pattern: RegExp): string | null {
  const match = pattern.exec(pathname);
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

function processVersion(value: string | null, fallback: number | "draft" = "draft"): number | "draft" {
  if (value === null || value === "") return fallback;
  if (value === "draft") return "draft";
  if (!/^\d+$/.test(value)) throw new HttpError(400, "Версия процесса задана некорректно");
  const parsed = Number.parseInt(value, 10);
  if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > 1_000_000) {
    throw new HttpError(400, "Версия процесса задана некорректно");
  }
  return parsed;
}

function singleHeader(value: string | string[] | undefined): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function requireA2AEndpoint(request: IncomingMessage, store: AgatStore, endpointId: string): A2AEndpointConnection {
  const token = bearerToken(request.headers.authorization);
  const endpoint = token ? store.authenticateA2AEndpoint(endpointId, token) : null;
  if (!endpoint) {
    throw new A2AProtocolError(
      401,
      "UNAUTHENTICATED",
      "AUTHENTICATION_REQUIRED",
      "Нужен действующий bearer token A2A endpoint",
    );
  }
  return endpoint;
}

function a2aInteger(value: string | null, field: string, min: number, max: number, fallback: number): number {
  if (value === null) return fallback;
  if (!/^\d+$/.test(value)) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", `${field} должен быть целым числом`, { field });
  }
  const parsed = Number.parseInt(value, 10);
  if (parsed < min || parsed > max) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", `${field} должен быть ${min}..${max}`, { field });
  }
  return parsed;
}

function a2aBoolean(value: string | null, field: string, fallback: boolean): boolean {
  if (value === null) return fallback;
  if (value === "true") return true;
  if (value === "false") return false;
  throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", `${field} должен быть true или false`, { field });
}

function a2aTraceparent(value: string | string[] | undefined): string | null {
  if (value === undefined) return null;
  if (typeof value !== "string" || !/^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$/i.test(value)) {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", "Некорректный W3C traceparent", { field: "traceparent" });
  }
  return value.toLowerCase();
}

function decodeA2APathSegment(value: string, field: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", `${field} некорректно URL-encoded`, { field });
  }
}

function a2aTaskState(task: Record<string, unknown>): A2ATaskState {
  const status = task.status && typeof task.status === "object" ? task.status as Record<string, unknown> : {};
  return String(status.state ?? "TASK_STATE_UNSPECIFIED") as A2ATaskState;
}

async function waitForA2ATask(
  store: AgatStore,
  endpointId: string,
  taskId: string,
  historyLength: number,
  response: ServerResponse,
): Promise<Record<string, unknown>> {
  let task = store.getA2ATask(endpointId, taskId, historyLength, true);
  while (task && !isA2ASettledState(a2aTaskState(task)) && !response.destroyed) {
    await new Promise<void>((resolve) => setTimeout(resolve, 250));
    task = store.getA2ATask(endpointId, taskId, historyLength, true);
  }
  if (!task) {
    throw new A2AProtocolError(404, "NOT_FOUND", "TASK_NOT_FOUND", "A2A task не найден");
  }
  return task;
}

function a2aSseEvent(response: ServerResponse, payload: Record<string, unknown>): void {
  response.write(`data: ${JSON.stringify(payload)}\n\n`);
}

async function streamA2ATask(
  request: IncomingMessage,
  response: ServerResponse,
  store: AgatStore,
  endpointId: string,
  taskId: string,
  initialTask: Record<string, unknown>,
): Promise<void> {
  response.writeHead(200, {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache, no-transform",
    connection: "keep-alive",
    "x-accel-buffering": "no",
    "a2a-version": A2A_PROTOCOL_VERSION,
  });
  let closed = false;
  response.once("close", () => { closed = true; });
  a2aSseEvent(response, { task: initialTask });
  let previousState = a2aTaskState(initialTask);
  if (isA2ASettledState(previousState)) {
    response.end();
    return;
  }
  let lastHeartbeat = Date.now();
  while (!closed && !response.destroyed) {
    await new Promise<void>((resolve) => setTimeout(resolve, 250));
    const task = store.getA2ATask(endpointId, taskId, 0, true);
    if (!task) break;
    const state = a2aTaskState(task);
    if (state !== previousState) {
      if (state === "TASK_STATE_COMPLETED" && Array.isArray(task.artifacts)) {
        for (const artifact of task.artifacts) {
          a2aSseEvent(response, {
            artifactUpdate: {
              taskId,
              contextId: task.contextId,
              artifact,
              append: false,
              lastChunk: true,
            },
          });
        }
      }
      a2aSseEvent(response, {
        statusUpdate: {
          taskId,
          contextId: task.contextId,
          status: task.status,
        },
      });
      previousState = state;
      if (isA2ASettledState(state)) {
        response.end();
        return;
      }
    }
    if (Date.now() - lastHeartbeat >= 15_000) {
      response.write(": heartbeat\n\n");
      lastHeartbeat = Date.now();
    }
  }
  if (!response.destroyed) response.end();
}

function a2aStoreError(error: unknown): A2AProtocolError {
  if (error instanceof A2AProtocolError) return error;
  const message = safeMessage(error);
  if (message.includes("лимит активных A2A tasks")) {
    return new A2AProtocolError(429, "RESOURCE_EXHAUSTED", "TASK_LIMIT_REACHED", message);
  }
  if (message.includes("messageId уже использован")) {
    return new A2AProtocolError(409, "ALREADY_EXISTS", "MESSAGE_ID_CONFLICT", message);
  }
  return new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_REQUEST", message);
}

function serveStatic(config: CoordinatorConfig, pathname: string, response: ServerResponse): boolean {
  if (!config.serveWeb || !fs.existsSync(config.webDistPath)) return false;

  const requested = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
  const candidate = path.resolve(config.webDistPath, requested);
  const distPrefix = `${path.resolve(config.webDistPath)}${path.sep}`;
  let target = candidate.startsWith(distPrefix) ? candidate : path.join(config.webDistPath, "index.html");

  if (!fs.existsSync(target) || !fs.statSync(target).isFile()) {
    target = path.join(config.webDistPath, "index.html");
  }
  if (!fs.existsSync(target)) return false;

  const contentType = STATIC_CONTENT_TYPES[path.extname(target)] ?? "application/octet-stream";
  const staticName = path.basename(target);
  const revalidate = staticName === "index.html" || staticName === "sw.js" || staticName === "manifest.webmanifest";
  response.writeHead(200, {
    "content-type": contentType,
    "cache-control": revalidate ? "no-cache" : "public, max-age=31536000, immutable",
  });
  fs.createReadStream(target).pipe(response);
  return true;
}

function validateRemoteBinding(config: CoordinatorConfig): void {
  validateTemporalCoordinatorConfig(config);
  if (config.edgeEnabled) {
    if (config.edgeAttestationMode !== "broker") {
      throw new Error("Native edge workers требуют AGAT_EDGE_ATTESTATION_MODE=broker");
    }
    if (!config.edgeAttestationBrokerUrl || !config.edgeAttestationBrokerToken) {
      throw new Error("Native edge workers требуют URL и token attestation broker");
    }
    if (!config.edgeAndroidApplicationId || !config.edgeIosApplicationId) {
      throw new Error("Native edge workers требуют Android application ID и iOS App ID");
    }
    const brokerUrl = new URL(config.edgeAttestationBrokerUrl);
    if (brokerUrl.protocol !== "https:") throw new Error("Attestation broker должен использовать HTTPS");
  }
  const a2aPublicBaseUrl = config.a2aEnabled
    ? normalizeA2APublicBaseUrl(config.a2aPublicBaseUrl, `http://127.0.0.1:${config.port}`)
    : "";
  const loopback = new Set(["127.0.0.1", "::1", "[::1]", "localhost"]);
  const a2aPublicUrl = a2aPublicBaseUrl ? new URL(a2aPublicBaseUrl) : null;
  if (a2aPublicUrl && !loopback.has(a2aPublicUrl.hostname) && a2aPublicUrl.protocol !== "https:") {
    throw new Error("AGAT_A2A_PUBLIC_BASE_URL должен использовать HTTPS при удалённой публикации координатора");
  }
  if (loopback.has(config.host)) return;
  if (!config.adminToken) {
    throw new Error("AGAT_ADMIN_TOKEN обязателен при публикации координатора не на loopback-интерфейсе");
  }
  if (config.enrollmentToken === "agat-local-enrollment") {
    throw new Error("Замените стандартный AGAT_ENROLLMENT_TOKEN перед удалённым запуском");
  }
  if (config.oidcEnabled && (!config.oidcIssuer || !config.oidcClientId)) {
    throw new Error("AGAT_OIDC_ISSUER и AGAT_OIDC_CLIENT_ID обязательны при включённом OIDC");
  }
  if (config.credentialsKey === "agat-local-credentials-key") {
    throw new Error("AGAT_CREDENTIALS_KEY обязателен при публикации координатора не на loopback-интерфейсе");
  }
}

function notifyProcessRuntime(runtime: ProcessRuntime, store: AgatStore, instanceId: string | null, reason: string): void {
  if (!instanceId) return;
  const ownerInstanceId = store.processRuntimeOwnerInstance(instanceId) ?? instanceId;
  void runtime.notifyProcess(ownerInstanceId, reason).catch((error) => {
    console.warn(`Не удалось отправить сигнал Temporal для ${ownerInstanceId}: ${safeMessage(error)}`);
  });
}

export function createCoordinatorServer(
  config: CoordinatorConfig,
  store: AgatStore,
  localWorkerLauncher: LocalWorkerLauncher = createLocalWorkerLauncher(config),
  processRuntime: ProcessRuntime = createDatabaseProcessRuntime(),
  mcpGateway: McpGateway = new McpGateway(store, new CoordinatorTelemetry({
    enabled: false,
    serviceName: "agat-coordinator",
    exporterEndpoint: "",
  }), {
    enabled: config.mcpEnabled,
    requestTimeoutSeconds: config.mcpRequestTimeoutSeconds,
    maxResponseBytes: config.mcpMaxResponseBytes,
    approvalTtlSeconds: config.mcpApprovalTtlSeconds,
  }, undefined, createSandboxExecutor(config)),
  edgeAttestation: EdgeAttestationVerifier = createEdgeAttestationVerifier(config),
): http.Server {
  const a2aPublicBaseUrl = normalizeA2APublicBaseUrl(
    config.a2aPublicBaseUrl,
    `http://127.0.0.1:${config.port}`,
  );
  const oidcVerifier = config.oidcEnabled
    ? new OidcVerifier({
      issuer: config.oidcIssuer,
      clientId: config.oidcClientId,
      jwksUrl: config.oidcJwksUrl || undefined,
    })
    : null;
  const a2aOutboundPolicy: A2AOutboundPolicy = {
    allowLoopback: config.a2aAllowLoopbackOutbound,
    timeoutMs: config.a2aOutboundTimeoutSeconds * 1_000,
    maxResponseBytes: config.a2aMaxResponseBytes,
  };
  const server = http.createServer((request, response) => {
    const requestTask = runWithPostgresSystemScope(async () => {
    setSecurityHeaders(response, config);
    const corsAllowed = setCors(request, response, config);
    if (request.method === "OPTIONS") {
      if (!corsAllowed) {
        json(response, 403, { error: "Origin не разрешён" });
        return;
      }
      noContent(response);
      return;
    }

    const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`);
    const { pathname } = url;

    try {
      if (request.method === "GET" && pathname === "/api/v1/health") {
        json(response, 200, {
          status: "ok",
          time: new Date().toISOString(),
          version: "1.7.0",
          stateStore: runWithPostgresSystemScope(() => store.stateStoreSnapshot()),
          processRuntime: processRuntime.snapshot(),
          sandbox: mcpGateway.sandboxSnapshot(),
          edge: {
            enabled: config.edgeEnabled,
            attestationAvailable: edgeAttestation.available,
            attestationMode: edgeAttestation.mode,
            reason: edgeAttestation.reason,
          },
          workerTrust: {
            signedReleasesRequired: config.requireSignedWorkerReleases,
            provenanceRequired: config.requireWorkerProvenance,
            runtimeAttestationRequired: config.requireWorkerRuntimeAttestation,
            runtimeProviders: config.workerRuntimeAttestationProviders,
          },
          siem: {
            enabled: config.siemEnabled,
            requireAck: config.siemRequireAck,
            maxAttempts: config.siemMaxAttempts,
          },
        });
        return;
      }

      const publicProcessWebhookId = routeParam(pathname, /^\/api\/v1\/process-webhooks\/([^/]+)$/);
      if (request.method === "POST" && publicProcessWebhookId) {
        const token = bearerToken(request.headers.authorization);
        if (!token) {
          response.setHeader("www-authenticate", 'Bearer realm="agat-process-webhook"');
          throw new HttpError(401, "Нужен действующий bearer token process webhook");
        }
        const body = await readJson<DeliverProcessWebhookInput>(request);
        const idempotencyKey = singleHeader(request.headers["idempotency-key"]) ?? null;
        const result = store.invokeProcessWebhook(publicProcessWebhookId, token, body, idempotencyKey);
        if (!result) {
          response.setHeader("www-authenticate", 'Bearer realm="agat-process-webhook"');
          throw new HttpError(401, "Process webhook не найден или token недействителен");
        }
        const instance = result.instance && typeof result.instance === "object"
          ? result.instance as Record<string, unknown>
          : null;
        if (result.kind === "start" && instance && typeof instance.id === "string") {
          try {
            await processRuntime.startProcess({
              instanceId: instance.id,
              processId: String(result.processId),
              projectId: String(result.projectId),
              priority: typeof body.priority === "number" ? body.priority : 50,
            });
          } catch (error) {
            // The idempotency receipt remains retryable: a repeated request starts the same instance.
            throw new HttpError(503, `Temporal не принял webhook process: ${safeMessage(error)}`);
          }
        }
        if (result.kind === "signal" && Array.isArray(result.instanceIds)) {
          for (const instanceId of result.instanceIds) {
            if (typeof instanceId === "string") notifyProcessRuntime(processRuntime, store, instanceId, "process.signal.received");
          }
        }
        json(response, result.duplicate === true ? 200 : result.kind === "start" ? 201 : 202, result);
        return;
      }

      const a2aAgentCardMatch = /^\/a2a\/v1\/endpoints\/([^/]+)\/agent-card\.json$/.exec(pathname);
      const a2aAgentCardEndpointId = a2aAgentCardMatch?.[1]
        ? decodeA2APathSegment(a2aAgentCardMatch[1], "endpointId")
        : null;
      if (request.method === "GET" && a2aAgentCardEndpointId) {
        if (!config.a2aEnabled) {
          throw new A2AProtocolError(404, "NOT_FOUND", "ENDPOINT_NOT_FOUND", "A2A adapter выключен");
        }
        const endpoint = store.getA2AEndpointConnection(a2aAgentCardEndpointId, undefined, true);
        if (!endpoint) {
          throw new A2AProtocolError(404, "NOT_FOUND", "ENDPOINT_NOT_FOUND", "A2A endpoint не найден");
        }
        a2aJson(response, 200, buildA2AAgentCard(endpoint, a2aPublicBaseUrl), {
          "cache-control": "public, max-age=60",
        });
        return;
      }

      const a2aMatch = /^\/a2a\/v1\/endpoints\/([^/]+)(\/.*)?$/.exec(pathname);
      if (a2aMatch?.[1]) {
        if (!config.a2aEnabled) {
          throw new A2AProtocolError(404, "NOT_FOUND", "ENDPOINT_NOT_FOUND", "A2A adapter выключен");
        }
        const endpointId = decodeA2APathSegment(a2aMatch[1], "endpointId");
        const operationPath = a2aMatch[2] ?? "";
        validateA2AProtocolVersion(
          singleHeader(request.headers["a2a-version"])
            ?? url.searchParams.get("A2A-Version")
            ?? undefined,
        );
        const endpoint = requireA2AEndpoint(request, store, endpointId);
        if (request.method === "POST") validateA2AContentType(singleHeader(request.headers["content-type"]));
        if (operationPath.includes("/pushNotificationConfigs") && !endpoint.pushNotificationsEnabled) {
          throw new A2AProtocolError(400, "FAILED_PRECONDITION", "PUSH_NOTIFICATION_NOT_SUPPORTED", "Push notifications выключены policy endpoint");
        }

        if (request.method === "POST" && operationPath === "/message:send") {
          const body = await readA2AJson(request) as A2ASendMessageRequest;
          const normalized = normalizeA2ASendMessageRequest(body, endpoint);
          if (normalized.pushNotificationConfig) {
            await validateA2AOutboundTarget(normalized.pushNotificationConfig.url, a2aOutboundPolicy, true);
          }
          const traceparent = a2aTraceparent(request.headers.traceparent);
          let task: Record<string, unknown>;
          try {
            task = store.createA2ATask(endpoint, normalized, traceparent);
          } catch (error) {
            throw a2aStoreError(error);
          }
          if (normalized.pushNotificationConfig) {
            try {
              store.createA2APushConfig(endpoint.id, String(task.id), normalized.pushNotificationConfig);
            } catch (error) {
              throw a2aStoreError(error);
            }
          }
          if (!normalized.returnImmediately) {
            task = await waitForA2ATask(store, endpoint.id, String(task.id), normalized.historyLength, response);
          }
          if (!response.destroyed) a2aJson(response, 200, { task });
          return;
        }

        if (request.method === "POST" && operationPath === "/message:stream") {
          if (!endpoint.streamingEnabled) {
            throw new A2AProtocolError(400, "FAILED_PRECONDITION", "UNSUPPORTED_OPERATION", "Streaming выключен policy endpoint");
          }
          const body = await readA2AJson(request) as A2ASendMessageRequest;
          const normalized = normalizeA2ASendMessageRequest(body, endpoint);
          if (normalized.pushNotificationConfig) {
            await validateA2AOutboundTarget(normalized.pushNotificationConfig.url, a2aOutboundPolicy, true);
          }
          const traceparent = a2aTraceparent(request.headers.traceparent);
          let task: Record<string, unknown>;
          try {
            task = store.createA2ATask(endpoint, normalized, traceparent);
            if (normalized.pushNotificationConfig) {
              store.createA2APushConfig(endpoint.id, String(task.id), normalized.pushNotificationConfig);
            }
          } catch (error) {
            throw a2aStoreError(error);
          }
          await streamA2ATask(request, response, store, endpoint.id, String(task.id), task);
          return;
        }

        const subscribeTaskMatch = /^\/tasks\/([^/:]+):subscribe$/.exec(operationPath);
        if ((request.method === "GET" || request.method === "POST") && subscribeTaskMatch?.[1]) {
          if (!endpoint.streamingEnabled) {
            throw new A2AProtocolError(400, "FAILED_PRECONDITION", "UNSUPPORTED_OPERATION", "Streaming выключен policy endpoint");
          }
          if (request.method === "POST") await readA2AJson(request);
          const taskId = decodeA2APathSegment(subscribeTaskMatch[1], "taskId");
          const task = store.getA2ATask(endpoint.id, taskId, 0, true);
          if (!task) throw new A2AProtocolError(404, "NOT_FOUND", "TASK_NOT_FOUND", "A2A task не найден");
          if (isA2ASettledState(a2aTaskState(task))) {
            throw new A2AProtocolError(400, "FAILED_PRECONDITION", "UNSUPPORTED_OPERATION", "Terminal или interrupted task нельзя подписать повторно");
          }
          await streamA2ATask(request, response, store, endpoint.id, taskId, task);
          return;
        }

        const pushConfigItemMatch = /^\/tasks\/([^/:]+)\/pushNotificationConfigs\/([^/]+)$/.exec(operationPath);
        if (pushConfigItemMatch?.[1] && pushConfigItemMatch[2]) {
          const taskId = decodeA2APathSegment(pushConfigItemMatch[1], "taskId");
          const configId = decodeA2APathSegment(pushConfigItemMatch[2], "configId");
          if (request.method === "GET") {
            const pushConfig = store.getA2APushConfig(endpoint.id, taskId, configId);
            if (!pushConfig) throw new A2AProtocolError(404, "NOT_FOUND", "TASK_NOT_FOUND", "A2A push config не найден");
            a2aJson(response, 200, pushConfig);
            return;
          }
          if (request.method === "DELETE") {
            store.deleteA2APushConfig(endpoint.id, taskId, configId);
            a2aJson(response, 200, {});
            return;
          }
        }

        const pushConfigCollectionMatch = /^\/tasks\/([^/:]+)\/pushNotificationConfigs$/.exec(operationPath);
        if (pushConfigCollectionMatch?.[1]) {
          const taskId = decodeA2APathSegment(pushConfigCollectionMatch[1], "taskId");
          if (request.method === "POST") {
            const rawConfig = await readA2AJson(request);
            const config = normalizeA2APushNotificationConfig(rawConfig, undefined, taskId);
            try {
              await validateA2AOutboundTarget(config.url, a2aOutboundPolicy, true);
              a2aJson(response, 200, store.createA2APushConfig(endpoint.id, taskId, config));
            } catch (error) {
              throw a2aStoreError(error);
            }
            return;
          }
          if (request.method === "GET") {
            try {
              a2aJson(response, 200, store.listA2APushConfigs(endpoint.id, taskId));
            } catch (error) {
              throw a2aStoreError(error);
            }
            return;
          }
        }

        const cancelTaskMatch = /^\/tasks\/([^/:]+):cancel$/.exec(operationPath);
        if (request.method === "POST" && cancelTaskMatch?.[1]) {
          await readA2AJson(request);
          const result = store.cancelA2ATask(endpoint.id, decodeA2APathSegment(cancelTaskMatch[1], "taskId"));
          if (result.kind === "not_found") {
            throw new A2AProtocolError(404, "NOT_FOUND", "TASK_NOT_FOUND", "A2A task не найден");
          }
          if (result.kind === "not_cancelable") {
            throw new A2AProtocolError(400, "FAILED_PRECONDITION", "TASK_NOT_CANCELABLE", "A2A task уже завершён и не может быть отменён");
          }
          a2aJson(response, 200, result.task);
          return;
        }

        const getTaskMatch = /^\/tasks\/([^/:]+)$/.exec(operationPath);
        if (request.method === "GET" && getTaskMatch?.[1]) {
          const historyLength = a2aInteger(url.searchParams.get("historyLength"), "historyLength", 0, 100, 1);
          const task = store.getA2ATask(endpoint.id, decodeA2APathSegment(getTaskMatch[1], "taskId"), historyLength, true);
          if (!task) throw new A2AProtocolError(404, "NOT_FOUND", "TASK_NOT_FOUND", "A2A task не найден");
          a2aJson(response, 200, task);
          return;
        }

        if (request.method === "GET" && operationPath === "/tasks") {
          const requestedStatus = url.searchParams.get("status");
          const supportedStates = new Set<A2ATaskState>([
            "TASK_STATE_SUBMITTED",
            "TASK_STATE_WORKING",
            "TASK_STATE_AUTH_REQUIRED",
            "TASK_STATE_COMPLETED",
            "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED",
          ]);
          if (requestedStatus && !supportedStates.has(requestedStatus as A2ATaskState)) {
            throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", "Фильтр status не поддерживается", { field: "status" });
          }
          const statusTimestampAfter = url.searchParams.get("statusTimestampAfter");
          if (statusTimestampAfter && Number.isNaN(Date.parse(statusTimestampAfter))) {
            throw new A2AProtocolError(400, "INVALID_ARGUMENT", "INVALID_PARAMETER", "statusTimestampAfter должен быть ISO timestamp", { field: "statusTimestampAfter" });
          }
          let result: Record<string, unknown>;
          try {
            result = store.listA2ATasks(endpoint.id, {
              contextId: url.searchParams.get("contextId") || undefined,
              status: requestedStatus as A2ATaskState | undefined,
              pageSize: a2aInteger(url.searchParams.get("pageSize"), "pageSize", 1, 100, 50),
              pageToken: url.searchParams.get("pageToken") || undefined,
              historyLength: a2aInteger(url.searchParams.get("historyLength"), "historyLength", 0, 100, 0),
              statusTimestampAfter: statusTimestampAfter || undefined,
              includeArtifacts: a2aBoolean(url.searchParams.get("includeArtifacts"), "includeArtifacts", false),
            });
          } catch (error) {
            throw a2aStoreError(error);
          }
          a2aJson(response, 200, result);
          return;
        }

        if (
          operationPath === "/extendedAgentCard"
        ) {
          throw new A2AProtocolError(400, "FAILED_PRECONDITION", "UNSUPPORTED_OPERATION", "Эта A2A-операция не поддерживается endpoint");
        }
        if (operationPath.includes("/pushNotificationConfigs")) {
          throw new A2AProtocolError(400, "FAILED_PRECONDITION", "PUSH_NOTIFICATION_NOT_SUPPORTED", "A2A push notifications не поддерживаются endpoint");
        }
        throw new A2AProtocolError(404, "NOT_FOUND", "OPERATION_NOT_FOUND", "A2A-операция не найдена");
      }

      const temporalTickInstanceId = routeParam(pathname, /^\/api\/v1\/internal\/processes\/([^/]+)\/tick$/);
      if (request.method === "POST" && temporalTickInstanceId) {
        if (!config.temporalEnabled || !config.temporalInternalToken) throw new HttpError(404, "Маршрут API не найден");
        const supplied = request.headers["x-agat-temporal-token"];
        if (typeof supplied !== "string" || !tokensEqual(supplied, config.temporalInternalToken)) {
          throw new HttpError(401, "Внутренний токен Temporal недействителен");
        }
        const body = await readJson<{ projectId?: unknown }>(request);
        if (typeof body.projectId !== "string") throw new HttpError(400, "projectId обязателен");
        const state = store.temporalProcessTick(temporalTickInstanceId, body.projectId);
        if (!state) throw new HttpError(404, "Экземпляр Temporal-процесса не найден");
        json(response, 200, state);
        return;
      }

      const scheduledProcessId = routeParam(
        pathname,
        /^\/api\/v1\/internal\/processes\/([^/]+)\/scheduled-start$/,
      );
      if (request.method === "POST" && scheduledProcessId) {
        if (!config.temporalEnabled || !config.temporalInternalToken) throw new HttpError(404, "Маршрут API не найден");
        const supplied = request.headers["x-agat-temporal-token"];
        if (typeof supplied !== "string" || !tokensEqual(supplied, config.temporalInternalToken)) {
          throw new HttpError(401, "Внутренний токен Temporal недействителен");
        }
        const body = await readJson<StartProcessInput & { projectId?: unknown }>(request);
        if (typeof body.projectId !== "string") throw new HttpError(400, "projectId обязателен");
        const instance = store.startProcess(scheduledProcessId, body, body.projectId);
        if (!instance) throw new HttpError(404, "Процесс расписания не найден");
        json(response, 201, {
          instanceId: String(instance.id),
          processId: scheduledProcessId,
          projectId: body.projectId,
        });
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/auth/config") {
        const realmMarker = "/realms/";
        const realmIndex = config.oidcIssuer.lastIndexOf(realmMarker);
        json(response, 200, {
          enabled: config.oidcEnabled,
          url: config.oidcEnabled && realmIndex > 0 ? config.oidcIssuer.slice(0, realmIndex) : "",
          realm: config.oidcEnabled && realmIndex > 0 ? config.oidcIssuer.slice(realmIndex + realmMarker.length) : "",
          clientId: config.oidcClientId,
        });
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/auth/me") {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false, true);
        const availableProjects = runWithPostgresSystemScope(() =>
          store.listProjects(auth.roles.has("admin") ? undefined : auth.projectIds));
        const activeProjectId = availableProjects.some((project) => project.id === auth.projectId)
          ? auth.projectId
          : availableProjects[0]?.id ?? auth.projectId;
        json(response, 200, {
          subject: auth.subject,
          username: auth.username,
          email: auth.email,
          roles: [...auth.roles],
          projectIds: [...auth.projectIds],
          activeProjectId,
          local: auth.local,
        });
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/overview") {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        const overview = store.getOverview(auth.projectId);
        const mcp = overview.mcp && typeof overview.mcp === "object" ? overview.mcp as Record<string, unknown> : {};
        json(response, 200, {
          ...overview,
          mcp: { ...mcp, enabled: config.mcpEnabled, sandbox: mcpGateway.sandboxSnapshot() },
          processRuntime: processRuntime.snapshot(),
        });
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/a2a") {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        const snapshot = store.getA2ASnapshot(auth.projectId);
        const endpoints = Array.isArray(snapshot.endpoints)
          ? snapshot.endpoints.map((item) => {
            const endpoint = item as Record<string, unknown>;
            const encodedId = encodeURIComponent(String(endpoint.id));
            return {
              ...endpoint,
              agentCardUrl: `${a2aPublicBaseUrl}/a2a/v1/endpoints/${encodedId}/agent-card.json`,
              interfaceUrl: `${a2aPublicBaseUrl}/a2a/v1/endpoints/${encodedId}`,
            };
          })
          : [];
        json(response, 200, {
          ...snapshot,
          enabled: config.a2aEnabled,
          outboundEnabled: config.a2aOutboundEnabled,
          loopbackOutboundAllowed: config.a2aAllowLoopbackOutbound,
          publicBaseUrl: a2aPublicBaseUrl,
          endpoints,
        });
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/a2a/endpoints") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<CreateA2AEndpointInput>(request);
        json(response, 201, store.createA2AEndpoint(body, auth.projectId, auth.username));
        return;
      }

      const rotateA2AEndpointId = routeParam(pathname, /^\/api\/v1\/a2a\/endpoints\/([^/]+)\/token\/rotate$/);
      if (request.method === "POST" && rotateA2AEndpointId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const rotated = store.rotateA2AEndpointToken(rotateA2AEndpointId, auth.projectId, auth.username);
        if (!rotated) throw new HttpError(404, "A2A endpoint не найден");
        json(response, 200, rotated);
        return;
      }

      const manageA2AEndpointId = routeParam(pathname, /^\/api\/v1\/a2a\/endpoints\/([^/]+)$/);
      if (request.method === "PATCH" && manageA2AEndpointId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<UpdateA2AEndpointInput>(request);
        const updated = store.updateA2AEndpoint(manageA2AEndpointId, body, auth.projectId, auth.username);
        if (!updated) throw new HttpError(404, "A2A endpoint не найден");
        json(response, 200, updated);
        return;
      }
      if (request.method === "DELETE" && manageA2AEndpointId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        if (!store.deleteA2AEndpoint(manageA2AEndpointId, auth.projectId, auth.username)) {
          throw new HttpError(404, "A2A endpoint не найден");
        }
        noContent(response);
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/a2a/remotes") {
        if (!config.a2aOutboundEnabled) throw new HttpError(503, "Outbound A2A выключен централизованной конфигурацией");
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<CreateA2ARemoteInput>(request);
        if (typeof body.agentCardUrl !== "string" || !body.auth) throw new HttpError(400, "agentCardUrl и auth обязательны");
        if (body.auth.mode === "oauth2_token_exchange") {
          if (!auth.roles.has("admin")) {
            throw new HttpError(403, "Delegated OAuth A2A peer может настраивать только admin");
          }
          if (typeof body.auth.tokenUrl === "string") {
            await validateA2AOutboundTarget(body.auth.tokenUrl, a2aOutboundPolicy);
          }
        }
        const discovered = await discoverA2ARemote(body.agentCardUrl, body.skillId, a2aOutboundPolicy);
        json(response, 201, store.createA2ARemote(body, discovered, auth.projectId, auth.username));
        return;
      }

      const invokeA2ARemoteId = routeParam(pathname, /^\/api\/v1\/a2a\/remotes\/([^/]+)\/message:send$/);
      if (request.method === "POST" && invokeA2ARemoteId) {
        if (!config.a2aOutboundEnabled) throw new HttpError(503, "Outbound A2A выключен централизованной конфигурацией");
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer", "operator"], true);
        const remote = store.getA2ARemoteConnection(invokeA2ARemoteId, auth.projectId, true);
        const remoteAuth = store.getA2ARemoteAuth(invokeA2ARemoteId, auth.projectId);
        if (!remote || !remoteAuth) throw new HttpError(404, "Outbound A2A peer не найден или выключен");
        const body = await readJson<A2AOutboundInvocationInput>(request, A2A_JSON_LIMIT_BYTES);
        const normalized = normalizeA2ASendMessageRequest(body, {
          inputModes: remote.inputModes,
          outputModes: remote.outputModes,
          maxInputCharacters: 100_000,
          fileArtifactsEnabled: remote.allowFileArtifacts,
          maxFileBytes: 2_000_000,
          maxFiles: 8,
          pushNotificationsEnabled: false,
        });
        const outboundRequest: Record<string, unknown> = {
          message: normalized.message,
          configuration: {
            acceptedOutputModes: body.configuration?.acceptedOutputModes ?? remote.outputModes,
            historyLength: normalized.historyLength,
            returnImmediately: normalized.returnImmediately,
          },
          ...(body.metadata ? { metadata: body.metadata } : {}),
        };
        const subjectToken = auth.local ? null : bearerToken(request.headers.authorization);
        const upstream = await invokeA2ARemote(
          remote,
          remoteAuth,
          subjectToken,
          "message:send",
          outboundRequest,
          a2aOutboundPolicy,
        );
        validateA2AOutboundResponse(upstream, remote);
        const outboundTask = store.recordA2AOutboundTask(
          remote,
          outboundRequest,
          upstream,
          { subject: auth.subject, display: auth.username },
          remoteAuth.mode === "oauth2_token_exchange",
        );
        json(response, 200, { outboundTask, response: upstream });
        return;
      }

      const outboundTaskMatch = /^\/api\/v1\/a2a\/remotes\/([^/]+)\/outbound-tasks\/([^/:]+)(:cancel)?$/.exec(pathname);
      if (outboundTaskMatch?.[1] && outboundTaskMatch[2]
        && (request.method === "GET" || (request.method === "POST" && outboundTaskMatch[3] === ":cancel"))) {
        if (!config.a2aOutboundEnabled) throw new HttpError(503, "Outbound A2A выключен централизованной конфигурацией");
        const cancelling = outboundTaskMatch[3] === ":cancel";
        const auth = await authorize(
          request,
          config,
          oidcVerifier,
          cancelling ? ["admin", "designer", "operator"] : READ_ROLES,
          cancelling,
        );
        const remoteId = decodeURIComponent(outboundTaskMatch[1]);
        const outboundTaskId = decodeURIComponent(outboundTaskMatch[2]);
        const remote = store.getA2ARemoteConnection(remoteId, auth.projectId, true);
        const remoteAuth = store.getA2ARemoteAuth(remoteId, auth.projectId);
        const outboundTask = store.getA2AOutboundTask(outboundTaskId, auth.projectId);
        if (!remote || !remoteAuth || !outboundTask || outboundTask.remoteId !== remoteId || typeof outboundTask.remoteTaskId !== "string") {
          throw new HttpError(404, "Outbound A2A task не найден");
        }
        const subjectToken = auth.local ? null : bearerToken(request.headers.authorization);
        const remoteTaskId = encodeURIComponent(outboundTask.remoteTaskId);
        const upstream = await invokeA2ARemote(
          remote,
          remoteAuth,
          subjectToken,
          cancelling ? `tasks/${remoteTaskId}:cancel` : `tasks/${remoteTaskId}`,
          cancelling ? {} : null,
          a2aOutboundPolicy,
        );
        validateA2AOutboundResponse({ task: upstream }, remote);
        const updated = store.updateA2AOutboundTask(remote.id, outboundTask.remoteTaskId, upstream, auth.projectId);
        json(response, 200, { outboundTask: updated, response: upstream });
        return;
      }

      const manageA2ARemoteId = routeParam(pathname, /^\/api\/v1\/a2a\/remotes\/([^/]+)$/);
      if (request.method === "PATCH" && manageA2ARemoteId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<UpdateA2ARemoteInput>(request);
        if (body.skillId !== undefined) throw new HttpError(400, "Skill outbound peer неизменяем; создайте новую запись");
        if (body.auth?.mode === "oauth2_token_exchange") {
          if (!auth.roles.has("admin")) {
            throw new HttpError(403, "Delegated OAuth A2A peer может настраивать только admin");
          }
          if (typeof body.auth.tokenUrl === "string") {
            await validateA2AOutboundTarget(body.auth.tokenUrl, a2aOutboundPolicy);
          }
        }
        const updated = store.updateA2ARemote(manageA2ARemoteId, body, auth.projectId, auth.username);
        if (!updated) throw new HttpError(404, "Outbound A2A peer не найден");
        json(response, 200, updated);
        return;
      }
      if (request.method === "DELETE" && manageA2ARemoteId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        if (!store.deleteA2ARemote(manageA2ARemoteId, auth.projectId, auth.username)) {
          throw new HttpError(404, "Outbound A2A peer не найден");
        }
        noContent(response);
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/projects") {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false, true);
        const projects = runWithPostgresSystemScope(() =>
          store.listProjects(auth.roles.has("admin") ? undefined : auth.projectIds));
        json(response, 200, {
          projects,
          activeProjectId: projects.some((project) => project.id === auth.projectId)
            ? auth.projectId
            : projects[0]?.id ?? auth.projectId,
        });
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/projects") {
        await authorize(request, config, oidcVerifier, ["admin"], true);
        const body = await readJson<CreateProjectInput>(request);
        json(response, 201, runWithPostgresSystemScope(() => store.createProject(body)));
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/fleet") {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        json(response, 200, runWithPostgresSystemScope(() => store.getFleetSnapshot(auth.projectId)));
        return;
      }

      if (request.method === "PATCH" && pathname === "/api/v1/fleet/project-policy") {
        const auth = await authorize(request, config, oidcVerifier, ["admin"], true);
        const body = await readJson<ProjectFleetPolicyInput>(request);
        json(response, 200, store.updateProjectFleetPolicy(body, auth.projectId, auth.username));
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/fleet/releases") {
        const auth = await authorize(request, config, oidcVerifier, ["admin"], true);
        const body = await readJson<RegisterWorkerReleaseInput>(request);
        json(response, 201, runWithPostgresSystemScope(() => store.registerWorkerRelease(body, auth.username)));
        return;
      }

      const revokeReleaseId = routeParam(pathname, /^\/api\/v1\/fleet\/releases\/([^/]+)\/revoke$/);
      if (request.method === "POST" && revokeReleaseId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin"], true);
        const body = await readJson<{ reason?: string }>(request);
        if (!body.reason?.trim()) throw new HttpError(400, "Причина revoke release обязательна");
        const revoked = runWithPostgresSystemScope(() =>
          store.revokeWorkerRelease(revokeReleaseId, body.reason!, auth.username));
        if (!revoked) throw new HttpError(404, "Активный worker release не найден");
        noContent(response);
        return;
      }

      if (request.method === "PUT" && pathname === "/api/v1/fleet/rollouts") {
        const auth = await authorize(request, config, oidcVerifier, ["admin"], true);
        const body = await readJson<WorkerRolloutInput>(request);
        json(response, 200, runWithPostgresSystemScope(() =>
          store.updateWorkerRollout(body, auth.projectId, auth.username)));
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/fleet/siem/dead-letters") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "auditor"], true);
        const limit = Number.parseInt(url.searchParams.get("limit") ?? "100", 10);
        json(response, 200, {
          deadLetters: runWithPostgresSystemScope(() => store.listAuditExportDeadLetters(
            auth.roles.has("admin") ? undefined : auth.projectId,
            Number.isFinite(limit) ? limit : 100,
          )),
        });
        return;
      }

      const replaySiemEventId = routeParam(pathname, /^\/api\/v1\/fleet\/siem\/dead-letters\/(\d+)\/replay$/);
      if (request.method === "POST" && replaySiemEventId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin"], true);
        const body = await readJson<{ reason?: string; confirm?: string }>(request);
        if (body.confirm !== "REPLAY_SIEM_DEAD_LETTER" || !body.reason?.trim()) {
          throw new HttpError(400, "Replay требует reason и confirm=REPLAY_SIEM_DEAD_LETTER");
        }
        const replayed = runWithPostgresSystemScope(() => store.replayAuditExportDeadLetter(
          Number(replaySiemEventId),
          auth.username,
          body.reason!,
        ));
        if (!replayed) throw new HttpError(404, "Открытый SIEM dead-letter не найден");
        json(response, 202, { eventId: Number(replaySiemEventId), status: "pending" });
        return;
      }

      const resolveSiemEventId = routeParam(pathname, /^\/api\/v1\/fleet\/siem\/dead-letters\/(\d+)\/resolve$/);
      if (request.method === "POST" && resolveSiemEventId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin"], true);
        const body = await readJson<{ reason?: string; confirm?: string }>(request);
        if (body.confirm !== "RESOLVE_SIEM_DEAD_LETTER" || !body.reason?.trim()) {
          throw new HttpError(400, "Resolve требует reason и confirm=RESOLVE_SIEM_DEAD_LETTER");
        }
        const resolved = runWithPostgresSystemScope(() => store.resolveAuditExportDeadLetter(
          Number(resolveSiemEventId),
          auth.username,
          body.reason!,
        ));
        if (!resolved) throw new HttpError(404, "Открытый SIEM dead-letter не найден");
        json(response, 200, { eventId: Number(resolveSiemEventId), status: "resolved" });
        return;
      }

      const nodeRingId = routeParam(pathname, /^\/api\/v1\/fleet\/nodes\/([^/]+)\/ring$/);
      if (request.method === "PATCH" && nodeRingId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin"], true);
        const body = await readJson<{ ring?: string }>(request);
        if (!body.ring) throw new HttpError(400, "rollout ring обязателен");
        const node = runWithPostgresSystemScope(() => store.setNodeRolloutRing(nodeRingId, body.ring!, auth.username));
        if (!node) throw new HttpError(404, "Worker-узел не найден");
        json(response, 200, node);
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/agents") {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        json(response, 200, { agents: store.listAgents(auth.projectId) });
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/credentials") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        json(response, 200, { credentials: store.listCredentials(auth.projectId) });
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/credentials") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<CreateCredentialInput>(request);
        json(response, 201, store.createCredential(body, auth.projectId));
        return;
      }

      const credentialId = routeParam(pathname, /^\/api\/v1\/credentials\/([^/]+)$/);
      if (request.method === "PATCH" && credentialId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<CreateCredentialInput>(request);
        const updated = store.updateCredential(credentialId, body, auth.projectId);
        if (!updated) throw new HttpError(404, "Credentials не найдены");
        json(response, 200, updated);
        return;
      }
      if (request.method === "DELETE" && credentialId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        if (!store.deleteCredential(credentialId, auth.projectId)) throw new HttpError(404, "Credentials не найдены");
        noContent(response);
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/mcp/servers") {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        json(response, 200, {
          enabled: config.mcpEnabled,
          sandbox: mcpGateway.sandboxSnapshot(),
          servers: mcpGateway.listServers(auth.projectId),
          policy: store.getMcpPolicySnapshot(auth.projectId),
        });
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/mcp/policy") {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        json(response, 200, store.getMcpPolicySnapshot(auth.projectId));
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/mcp/policy/preview") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<{ document?: unknown }>(request);
        json(response, 200, store.previewMcpPolicy(body.document, auth.projectId));
        return;
      }

      if (request.method === "PUT" && pathname === "/api/v1/mcp/policy") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<{ document?: unknown; baseSha256?: unknown }>(request);
        if (typeof body.baseSha256 !== "string") throw new HttpError(400, "baseSha256 обязателен");
        json(response, 200, store.activateMcpPolicy(body.document, body.baseSha256, auth.projectId, {
          subject: auth.subject,
          display: auth.username,
        }));
        return;
      }

      if (request.method === "PUT" && pathname === "/api/v1/mcp/emergency-deny") {
        const auth = await authorize(request, config, oidcVerifier, ["admin"], true);
        const body = await readJson<{ enabled?: unknown; reason?: unknown }>(request);
        if (typeof body.enabled !== "boolean") throw new HttpError(400, "enabled должен быть boolean");
        if (typeof body.reason !== "string") throw new HttpError(400, "reason обязателен");
        json(response, 200, store.setMcpEmergencyDeny(body.enabled, body.reason, {
          subject: auth.subject,
          display: auth.username,
        }));
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/mcp/servers") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<CreateMcpServerInput>(request);
        if ((body.transport && body.transport !== "http") || body.sandbox) {
          if (!auth.roles.has("admin")) throw new HttpError(403, "Изолированные MCP tools может настраивать только admin");
        }
        json(response, 201, await mcpGateway.createServer(body, auth.projectId));
        return;
      }

      const syncMcpServerId = routeParam(pathname, /^\/api\/v1\/mcp\/servers\/([^/]+)\/sync$/);
      if (request.method === "POST" && syncMcpServerId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const synced = await mcpGateway.syncServer(syncMcpServerId, auth.projectId);
        if (!synced) throw new HttpError(404, "MCP-сервер не найден");
        json(response, 200, synced);
        return;
      }

      const mcpToolPolicyMatch = /^\/api\/v1\/mcp\/servers\/([^/]+)\/tools\/([^/]+)$/.exec(pathname);
      if (request.method === "PATCH" && mcpToolPolicyMatch?.[1] && mcpToolPolicyMatch[2]) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<{ policy?: McpToolPolicy }>(request);
        if (!body.policy) throw new HttpError(400, "policy обязательна");
        const updated = mcpGateway.setToolPolicy(
          decodeURIComponent(mcpToolPolicyMatch[1]),
          decodeURIComponent(mcpToolPolicyMatch[2]),
          body.policy,
          auth.projectId,
        );
        if (!updated) throw new HttpError(404, "MCP-сервер не найден");
        json(response, 200, updated);
        return;
      }

      const mcpServerId = routeParam(pathname, /^\/api\/v1\/mcp\/servers\/([^/]+)$/);
      if (request.method === "PATCH" && mcpServerId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<UpdateMcpServerInput>(request);
        const current = mcpGateway.listServers(auth.projectId).find((item) => item.id === mcpServerId);
        if (!current) throw new HttpError(404, "MCP-сервер не найден");
        if ((current.transport !== "http" || (body.transport && body.transport !== "http") || body.sandbox !== undefined)
          && !auth.roles.has("admin")) {
          throw new HttpError(403, "Изолированные MCP tools может настраивать только admin");
        }
        const updated = mcpGateway.updateServer(mcpServerId, body, auth.projectId);
        if (!updated) throw new HttpError(404, "MCP-сервер не найден");
        json(response, 200, updated);
        return;
      }
      if (request.method === "DELETE" && mcpServerId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const current = mcpGateway.listServers(auth.projectId).find((item) => item.id === mcpServerId);
        if (!current) throw new HttpError(404, "MCP-сервер не найден");
        if (current.transport !== "http" && !auth.roles.has("admin")) {
          throw new HttpError(403, "Изолированные MCP tools может удалять только admin");
        }
        if (!mcpGateway.deleteServer(mcpServerId, auth.projectId)) throw new HttpError(404, "MCP-сервер не найден");
        noContent(response);
        return;
      }

      const mcpDecisionCallId = routeParam(pathname, /^\/api\/v1\/mcp\/tool-calls\/([^/]+)\/decision$/);
      if (request.method === "POST" && mcpDecisionCallId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "operator"], true);
        const body = await readJson<{ decision?: "approve" | "reject" }>(request);
        if (body.decision !== "approve" && body.decision !== "reject") {
          throw new HttpError(400, "decision должен быть approve или reject");
        }
        const result = await mcpGateway.decideCall(
          mcpDecisionCallId,
          body.decision,
          auth.projectId,
          { subject: auth.subject, display: auth.username },
        );
        json(response, result.status === "waiting_approval" || result.status === "executing" ? 202 : 200, result);
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/local-workers") {
        await authorize(request, config, oidcVerifier, READ_ROLES, false);
        json(response, 200, await localWorkerLauncher.snapshot());
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/local-workers") {
        await authorize(request, config, oidcVerifier, ["admin"], true);
        const body = await readJson<LaunchLocalWorkersInput>(request);
        const pool = await localWorkerLauncher.launch(body);
        store.recordPlatformEvent(
          "local-worker.pool.created",
          `Запущен локальный worker-пул «${pool.name}»`,
          { poolId: pool.id, model: pool.model, workers: pool.workers, concurrency: pool.concurrency },
        );
        json(response, 201, pool);
        return;
      }

      const localWorkerAction = /^\/api\/v1\/local-workers\/([^/]+)\/(start|stop)$/.exec(pathname);
      if (request.method === "POST" && localWorkerAction?.[1] && localWorkerAction[2]) {
        await authorize(request, config, oidcVerifier, ["admin"], true);
        const poolId = decodeURIComponent(localWorkerAction[1]);
        const action = localWorkerAction[2];
        const pool = action === "start"
          ? await localWorkerLauncher.startPool(poolId)
          : await localWorkerLauncher.stopPool(poolId);
        if (action === "stop") store.markWorkerPoolOffline(pool.id);
        store.recordPlatformEvent(
          action === "start" ? "local-worker.pool.started" : "local-worker.pool.stopped",
          `${action === "start" ? "Запущен" : "Остановлен"} локальный worker-пул «${pool.name}»`,
          { poolId: pool.id, model: pool.model, workers: pool.workers },
        );
        json(response, 200, pool);
        return;
      }

      const localWorkerPoolId = routeParam(pathname, /^\/api\/v1\/local-workers\/([^/]+)$/);
      if (request.method === "DELETE" && localWorkerPoolId) {
        await authorize(request, config, oidcVerifier, ["admin"], true);
        const pool = await localWorkerLauncher.deletePool(localWorkerPoolId);
        store.deleteWorkerPoolNodes(pool.id);
        store.recordPlatformEvent(
          "local-worker.pool.deleted",
          `Удалён локальный worker-пул «${pool.name}»`,
          { poolId: pool.id, model: pool.model, workers: pool.workers },
          "warn",
        );
        json(response, 200, pool);
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/agents") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<CreateAgentInput>(request);
        json(response, 201, store.createAgent(body, auth.projectId));
        return;
      }

      const agentId = routeParam(pathname, /^\/api\/v1\/agents\/([^/]+)$/);
      if (request.method === "PATCH" && agentId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<CreateAgentInput>(request);
        const updated = store.updateAgent(agentId, body, auth.projectId);
        if (!updated) throw new HttpError(404, "Агент не найден");
        json(response, 200, updated);
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/evals") {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        json(response, 200, store.getEvalSnapshot(auth.projectId));
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/evals/prompts") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<CreatePromptInput>(request);
        json(response, 201, store.createPrompt(body, auth.projectId, auth.username));
        return;
      }

      const promptVersionId = routeParam(pathname, /^\/api\/v1\/evals\/prompts\/([^/]+)\/versions$/);
      if (request.method === "POST" && promptVersionId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<CreatePromptVersionInput>(request);
        const created = store.createPromptVersion(promptVersionId, body, auth.projectId, auth.username);
        if (!created) throw new HttpError(404, "Prompt registry не найден");
        json(response, 201, created);
        return;
      }

      const promotePromptId = routeParam(pathname, /^\/api\/v1\/evals\/prompts\/([^/]+)\/promote$/);
      if (request.method === "POST" && promotePromptId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<PromotePromptInput>(request);
        const promoted = store.promotePrompt(promotePromptId, body, auth.projectId, auth.username);
        if (!promoted) throw new HttpError(404, "Prompt registry не найден");
        json(response, 200, promoted);
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/evals/datasets") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<CreateEvalDatasetInput>(request, KNOWLEDGE_JSON_LIMIT_BYTES);
        json(response, 201, store.createEvalDataset(body, auth.projectId, auth.username));
        return;
      }

      const datasetVersionId = routeParam(pathname, /^\/api\/v1\/evals\/datasets\/([^/]+)\/versions$/);
      if (request.method === "POST" && datasetVersionId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<CreateEvalDatasetVersionInput>(request, KNOWLEDGE_JSON_LIMIT_BYTES);
        const created = store.createEvalDatasetVersion(datasetVersionId, body, auth.projectId, auth.username);
        if (!created) throw new HttpError(404, "Golden dataset не найден");
        json(response, 201, created);
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/evals/experiments") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer", "operator"], true);
        const body = await readJson<CreateEvalExperimentInput>(request);
        json(response, 201, store.createEvalExperiment(body, auth.projectId, auth.username));
        return;
      }

      const judgeExperimentId = routeParam(pathname, /^\/api\/v1\/evals\/experiments\/([^/]+)\/judge$/);
      if (request.method === "POST" && judgeExperimentId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer", "operator"], true);
        const body = await readJson<JudgeEvalExperimentInput>(request);
        const experiment = store.judgeEvalExperiment(judgeExperimentId, body, auth.projectId, auth.username);
        if (!experiment) throw new HttpError(404, "Golden experiment не найден");
        json(response, 201, experiment);
        return;
      }

      const evalExperimentId = routeParam(pathname, /^\/api\/v1\/evals\/experiments\/([^/]+)$/);
      if (request.method === "GET" && evalExperimentId) {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        const experiment = store.getEvalExperiment(evalExperimentId, auth.projectId);
        if (!experiment) throw new HttpError(404, "Golden experiment не найден");
        json(response, 200, experiment);
        return;
      }

      const reviewEvalItemId = routeParam(pathname, /^\/api\/v1\/evals\/items\/([^/]+)\/reviews$/);
      if (request.method === "POST" && reviewEvalItemId) {
        const auth = await authorize(
          request,
          config,
          oidcVerifier,
          ["admin", "designer", "operator", "auditor"],
          true,
        );
        const body = await readJson<HumanEvalReviewInput>(request);
        const experiment = store.reviewEvalItem(reviewEvalItemId, body, auth.projectId, auth.username);
        if (!experiment) throw new HttpError(404, "Golden eval item не найден");
        json(response, 201, experiment);
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/knowledge") {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        json(response, 200, store.getKnowledgeSnapshot(auth.projectId));
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/knowledge/export") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer", "auditor"], true);
        const payload = Buffer.from(JSON.stringify(store.exportKnowledge(auth.projectId), null, 2), "utf8");
        response.writeHead(200, {
          "content-type": "application/json; charset=utf-8",
          "content-length": payload.byteLength,
          "content-disposition": attachmentDisposition(`agat-knowledge-${auth.projectId}.json`),
          "cache-control": "private, no-store",
        });
        response.end(payload);
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/knowledge/collections") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<CreateKnowledgeCollectionInput>(request);
        json(response, 201, store.createKnowledgeCollection(body, auth.projectId));
        return;
      }

      const ingestKnowledgeMatch = /^\/api\/v1\/knowledge\/collections\/([^/]+)\/documents$/.exec(pathname);
      if (request.method === "POST" && ingestKnowledgeMatch?.[1]) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<IngestKnowledgeDocumentInput>(request, KNOWLEDGE_JSON_LIMIT_BYTES);
        json(response, 201, store.ingestKnowledgeDocument(
          decodeURIComponent(ingestKnowledgeMatch[1]),
          body,
          auth.projectId,
        ));
        return;
      }

      const knowledgeCollectionId = routeParam(pathname, /^\/api\/v1\/knowledge\/collections\/([^/]+)$/);
      if (request.method === "DELETE" && knowledgeCollectionId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        if (!store.deleteKnowledgeCollection(knowledgeCollectionId, auth.projectId)) {
          throw new HttpError(404, "Knowledge collection не найдена");
        }
        noContent(response);
        return;
      }

      const knowledgeDocumentId = routeParam(pathname, /^\/api\/v1\/knowledge\/documents\/([^/]+)$/);
      if (request.method === "DELETE" && knowledgeDocumentId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        if (!store.deleteKnowledgeDocument(knowledgeDocumentId, auth.projectId)) {
          throw new HttpError(404, "Knowledge document не найден");
        }
        noContent(response);
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/knowledge/memory") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer", "operator"], true);
        const body = await readJson<SaveMemoryInput>(request);
        json(response, 201, store.saveMemory(body, auth.projectId));
        return;
      }

      const memoryId = routeParam(pathname, /^\/api\/v1\/knowledge\/memory\/([^/]+)$/);
      if (request.method === "DELETE" && memoryId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        if (!store.deleteMemory(memoryId, auth.projectId)) throw new HttpError(404, "Memory entry не найдена");
        noContent(response);
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/processes") {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        json(response, 200, { processes: store.listProcesses(auth.projectId) });
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/processes/import/bpmn") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const contentType = singleHeader(request.headers["content-type"])?.split(";", 1)[0]?.trim().toLowerCase();
        if (contentType && !["application/xml", "text/xml", "application/bpmn+xml"].includes(contentType)) {
          throw new HttpError(415, "BPMN import принимает application/xml, text/xml или application/bpmn+xml");
        }
        const imported = store.importProcessBpmn(await readText(request), {
          name: url.searchParams.get("name") ?? undefined,
          description: url.searchParams.get("description") ?? undefined,
          isTemplate: url.searchParams.get("template") === "true",
        }, auth.projectId);
        json(response, 201, imported);
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/processes") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<CreateProcessInput>(request);
        json(response, 201, store.createProcess(body, auth.projectId));
        return;
      }

      const publishProcessId = routeParam(pathname, /^\/api\/v1\/processes\/([^/]+)\/publish$/);
      if (request.method === "POST" && publishProcessId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const published = store.publishProcess(publishProcessId, auth.projectId);
        if (!published) throw new HttpError(404, "Процесс не найден");
        json(response, 200, published);
        return;
      }

      const processVersionMatch = /^\/api\/v1\/processes\/([^/]+)\/versions\/([^/]+)$/.exec(pathname);
      if (request.method === "GET" && processVersionMatch?.[1] && processVersionMatch[2]) {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        const processId = decodeURIComponent(processVersionMatch[1]);
        const version = processVersion(decodeURIComponent(processVersionMatch[2]));
        const document = store.getProcessVersion(processId, version, auth.projectId);
        if (!document) throw new HttpError(404, "Версия процесса не найдена");
        json(response, 200, document);
        return;
      }

      const diffProcessId = routeParam(pathname, /^\/api\/v1\/processes\/([^/]+)\/diff$/);
      if (request.method === "GET" && diffProcessId) {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        const from = processVersion(url.searchParams.get("from"));
        const to = processVersion(url.searchParams.get("to"), "draft");
        const diff = store.diffProcessVersions(diffProcessId, from, to, auth.projectId);
        if (!diff) throw new HttpError(404, "Одна из сравниваемых версий процесса не найдена");
        json(response, 200, diff);
        return;
      }

      const exportBpmnProcessId = routeParam(pathname, /^\/api\/v1\/processes\/([^/]+)\/export\/bpmn$/);
      if (request.method === "GET" && exportBpmnProcessId) {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        const version = processVersion(url.searchParams.get("version"));
        const document = store.exportProcessBpmn(exportBpmnProcessId, version, auth.projectId);
        if (!document) throw new HttpError(404, "Версия процесса не найдена");
        xml(response, 200, document, `agat-${exportBpmnProcessId}-${version}.bpmn`);
        return;
      }

      const processWebhooksId = routeParam(pathname, /^\/api\/v1\/processes\/([^/]+)\/webhooks$/);
      if (request.method === "GET" && processWebhooksId) {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        if (!store.getProcess(processWebhooksId, auth.projectId)) throw new HttpError(404, "Процесс не найден");
        json(response, 200, { webhooks: store.listProcessWebhooks(processWebhooksId, auth.projectId) });
        return;
      }
      if (request.method === "POST" && processWebhooksId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<CreateProcessWebhookInput>(request);
        json(response, 201, store.createProcessWebhook(processWebhooksId, body, auth.projectId));
        return;
      }

      const rotateProcessWebhookId = routeParam(pathname, /^\/api\/v1\/process-webhooks\/([^/]+)\/rotate$/);
      if (request.method === "POST" && rotateProcessWebhookId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const webhook = store.rotateProcessWebhook(rotateProcessWebhookId, auth.projectId);
        if (!webhook) throw new HttpError(404, "Process webhook не найден");
        json(response, 200, webhook);
        return;
      }

      const managedProcessWebhookId = routeParam(pathname, /^\/api\/v1\/process-webhooks\/([^/]+)$/);
      if (request.method === "DELETE" && managedProcessWebhookId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        if (!store.deleteProcessWebhook(managedProcessWebhookId, auth.projectId)) {
          throw new HttpError(404, "Process webhook не найден");
        }
        noContent(response);
        return;
      }

      const processSignalMatch = /^\/api\/v1\/processes\/([^/]+)\/signals\/([^/]+)$/.exec(pathname);
      if (request.method === "POST" && processSignalMatch?.[1] && processSignalMatch[2]) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer", "operator"], true);
        const processId = decodeURIComponent(processSignalMatch[1]);
        const signalName = decodeURIComponent(processSignalMatch[2]);
        const body = await readJson<{ instanceId?: string; correlationKey?: string; payload?: unknown }>(request);
        const delivered = store.deliverProcessSignal(processId, signalName, body, auth.projectId);
        if (!delivered) throw new HttpError(404, "Процесс не найден");
        for (const instanceId of delivered.instanceIds) notifyProcessRuntime(processRuntime, store, instanceId, "process.signal.received");
        json(response, delivered.delivered ? 202 : 200, delivered);
        return;
      }

      const startProcessId = routeParam(pathname, /^\/api\/v1\/processes\/([^/]+)\/start$/);
      if (request.method === "POST" && startProcessId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer", "operator"], true);
        const body = await readJson<StartProcessInput>(request);
        const instance = store.startProcess(startProcessId, body, auth.projectId);
        if (!instance) throw new HttpError(404, "Процесс не найден");
        const instanceId = String(instance.id);
        try {
          await processRuntime.startProcess({
            instanceId,
            processId: startProcessId,
            projectId: auth.projectId,
            priority: typeof body.priority === "number" ? body.priority : 50,
          });
        } catch (error) {
          store.cancelProcessInstance(instanceId, auth.projectId);
          throw new HttpError(503, `Temporal не принял процесс: ${safeMessage(error)}`);
        }
        json(response, 201, instance);
        return;
      }

      const scheduleProcessId = routeParam(pathname, /^\/api\/v1\/processes\/([^/]+)\/schedule$/);
      if (scheduleProcessId && request.method === "GET") {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        if (!store.getProcess(scheduleProcessId, auth.projectId)) throw new HttpError(404, "Процесс не найден");
        if (processRuntime.snapshot().mode !== "temporal") {
          throw new HttpError(503, "Расписания процессов требуют включённого Temporal runtime");
        }
        const schedule = await processRuntime.getProcessSchedule(scheduleProcessId, auth.projectId);
        if (!schedule) throw new HttpError(404, "Расписание процесса не найдено");
        json(response, 200, schedule);
        return;
      }
      if (scheduleProcessId && request.method === "PUT") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        if (!store.getProcess(scheduleProcessId, auth.projectId)) throw new HttpError(404, "Процесс не найден");
        if (processRuntime.snapshot().mode !== "temporal") {
          throw new HttpError(503, "Расписания процессов требуют включённого Temporal runtime");
        }
        const body = await readJson<unknown>(request);
        const scheduleInput = normalizeProcessScheduleInput(body, scheduleProcessId, auth.projectId);
        const knownCollections = new Set(
          ((store.getKnowledgeSnapshot(auth.projectId).collections ?? []) as Array<{ id?: unknown }>)
            .map((collection) => typeof collection.id === "string" ? collection.id : "")
            .filter(Boolean),
        );
        const missingCollection = scheduleInput.knowledgeCollectionIds.find((id) => !knownCollections.has(id));
        if (missingCollection) throw new HttpError(400, `Knowledge collection ${missingCollection} не найдена`);
        try {
          const schedule = await processRuntime.upsertProcessSchedule(scheduleInput);
          store.recordProcessScheduleEvent(
            scheduleProcessId,
            auth.projectId,
            "process.schedule.upserted",
            scheduleInput.paused ? "Расписание процесса сохранено на паузе" : "Расписание процесса активно",
            {
              scheduleId: schedule.scheduleId,
              kind: schedule.kind,
              everySeconds: schedule.everySeconds || null,
              cronExpression: schedule.cronExpression || null,
              timezone: schedule.timezone,
              paused: schedule.paused,
            },
          );
          json(response, 200, schedule);
        } catch (error) {
          throw new HttpError(503, `Temporal не сохранил расписание: ${safeMessage(error)}`);
        }
        return;
      }
      if (scheduleProcessId && request.method === "DELETE") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        if (!store.getProcess(scheduleProcessId, auth.projectId)) throw new HttpError(404, "Процесс не найден");
        if (processRuntime.snapshot().mode !== "temporal") {
          throw new HttpError(503, "Расписания процессов требуют включённого Temporal runtime");
        }
        if (!await processRuntime.deleteProcessSchedule(scheduleProcessId, auth.projectId)) {
          throw new HttpError(404, "Расписание процесса не найдено");
        }
        store.recordProcessScheduleEvent(
          scheduleProcessId,
          auth.projectId,
          "process.schedule.deleted",
          "Расписание процесса удалено",
          {},
        );
        noContent(response);
        return;
      }

      const triggerScheduleProcessId = routeParam(pathname, /^\/api\/v1\/processes\/([^/]+)\/schedule\/trigger$/);
      if (triggerScheduleProcessId && request.method === "POST") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer", "operator"], true);
        if (!store.getProcess(triggerScheduleProcessId, auth.projectId)) throw new HttpError(404, "Процесс не найден");
        if (processRuntime.snapshot().mode !== "temporal") {
          throw new HttpError(503, "Расписания процессов требуют включённого Temporal runtime");
        }
        try {
          const schedule = await processRuntime.triggerProcessSchedule(triggerScheduleProcessId, auth.projectId);
          store.recordProcessScheduleEvent(
            triggerScheduleProcessId,
            auth.projectId,
            "process.schedule.triggered",
            "Расписание процесса запущено вручную",
            { scheduleId: schedule.scheduleId },
          );
          json(response, 202, schedule);
        } catch (error) {
          throw new HttpError(503, `Temporal не запустил расписание: ${safeMessage(error)}`);
        }
        return;
      }

      const testProcessNodeId = routeParam(pathname, /^\/api\/v1\/processes\/([^/]+)\/test-node$/);
      if (request.method === "POST" && testProcessNodeId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer", "operator"], true);
        if (!store.getProcess(testProcessNodeId, auth.projectId)) throw new HttpError(404, "Процесс не найден");
        const body = await readJson<TestProcessNodeInput>(request);
        json(response, 201, store.testProcessNode(body, auth.projectId));
        return;
      }

      const processId = routeParam(pathname, /^\/api\/v1\/processes\/([^/]+)$/);
      if (request.method === "GET" && processId) {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        const process = store.getProcess(processId, auth.projectId);
        if (!process) throw new HttpError(404, "Процесс не найден");
        json(response, 200, process);
        return;
      }
      if (request.method === "PATCH" && processId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer"], true);
        const body = await readJson<UpdateProcessInput>(request);
        const updated = store.updateProcess(processId, body, auth.projectId);
        if (!updated) throw new HttpError(404, "Процесс не найден");
        json(response, 200, updated);
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/process-instances") {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        json(response, 200, { instances: store.listProcessInstances(100, auth.projectId) });
        return;
      }

      const replayProcessInstanceId = routeParam(pathname, /^\/api\/v1\/process-instances\/([^/]+)\/replay$/);
      if (request.method === "POST" && replayProcessInstanceId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer", "operator"], true);
        const body = await readJson<ReplayProcessInstanceInput>(request);
        const instance = store.replayProcessInstance(replayProcessInstanceId, body, auth.projectId);
        if (!instance) throw new HttpError(404, "Экземпляр процесса не найден");
        const status = String(instance.status);
        if (!["completed", "failed", "cancelled"].includes(status)) {
          try {
            await processRuntime.startProcess({
              instanceId: String(instance.id),
              processId: String(instance.processId),
              projectId: auth.projectId,
              priority: typeof body.priority === "number" ? body.priority : 50,
            });
          } catch (error) {
            store.cancelProcessInstance(String(instance.id), auth.projectId);
            throw new HttpError(503, `Temporal не принял replay процесса: ${safeMessage(error)}`);
          }
        }
        json(response, 201, instance);
        return;
      }

      const cancelProcessInstanceId = routeParam(pathname, /^\/api\/v1\/process-instances\/([^/]+)\/cancel$/);
      if (request.method === "POST" && cancelProcessInstanceId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer", "operator"], true);
        if (!store.cancelProcessInstance(cancelProcessInstanceId, auth.projectId)) throw new HttpError(404, "Экземпляр процесса не найден");
        notifyProcessRuntime(processRuntime, store, cancelProcessInstanceId, "process.cancelled");
        noContent(response);
        return;
      }

      const processInstanceId = routeParam(pathname, /^\/api\/v1\/process-instances\/([^/]+)$/);
      if (request.method === "GET" && processInstanceId) {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        const instance = store.getProcessInstance(processInstanceId, auth.projectId);
        if (!instance) throw new HttpError(404, "Экземпляр процесса не найден");
        json(response, 200, instance);
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/events") {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        const afterFromQuery = Number.parseInt(url.searchParams.get("after") ?? "0", 10) || 0;
        const lastEventHeader = request.headers["last-event-id"];
        const lastEventId = Array.isArray(lastEventHeader) ? lastEventHeader[0] : lastEventHeader;
        let cursor = Math.max(afterFromQuery, Number.parseInt(lastEventId ?? "0", 10) || 0);
        response.writeHead(200, {
          "content-type": "text/event-stream; charset=utf-8",
          "cache-control": "no-cache, no-transform",
          connection: "keep-alive",
          "x-accel-buffering": "no",
        });
        response.write(": connected\n\n");

        const flush = (): void => {
          for (const event of store.listEvents(cursor, 200, auth.projectId)) {
            cursor = event.id;
            response.write(`id: ${event.id}\n`);
            response.write(`event: ${event.type}\n`);
            response.write(`data: ${JSON.stringify(event)}\n\n`);
          }
        };
        flush();
        const interval = setInterval(flush, 1_000);
        const heartbeat = setInterval(() => response.write(": heartbeat\n\n"), 15_000);
        request.on("close", () => {
          clearInterval(interval);
          clearInterval(heartbeat);
        });
        return;
      }

      const runTraceId = routeParam(pathname, /^\/api\/v1\/runs\/([^/]+)\/trace$/);
      if (request.method === "GET" && runTraceId) {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, true);
        const trace = store.getRunTrace(runTraceId, auth.projectId);
        if (!trace) throw new HttpError(404, "Запуск не найден");
        json(response, 200, trace);
        return;
      }

      const replayRunId = routeParam(pathname, /^\/api\/v1\/runs\/([^/]+)\/replay$/);
      if (request.method === "POST" && replayRunId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer", "operator"], true);
        const body = await readJson<ReplayRunInput>(request);
        json(response, 201, store.replayRun(replayRunId, body, auth.projectId));
        return;
      }

      const runId = routeParam(pathname, /^\/api\/v1\/runs\/([^/]+)$/);
      if (request.method === "GET" && runId) {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, false);
        const run = store.getRun(runId, auth.projectId);
        if (!run) throw new HttpError(404, "Запуск не найден");
        json(response, 200, run);
        return;
      }

      const artifactId = routeParam(pathname, /^\/api\/v1\/artifacts\/([^/]+)\/download$/);
      if (request.method === "GET" && artifactId) {
        const auth = await authorize(request, config, oidcVerifier, READ_ROLES, true);
        const download = store.getArtifactDownload(artifactId, auth.projectId);
        if (!download) throw new HttpError(404, "Артефакт не найден");
        const filename = String(download.artifact.name ?? "artifact");
        const status = fs.statSync(download.filePath);
        response.writeHead(200, {
          "content-type": "application/octet-stream",
          "content-length": status.size,
          "content-disposition": attachmentDisposition(filename),
          "cache-control": "private, no-store",
        });
        fs.createReadStream(download.filePath).pipe(response);
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/runs") {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "designer", "operator"], true);
        const body = await readJson<CreateRunInput>(request);
        const created = store.createRun(body, auth.projectId);
        json(response, 201, created);
        return;
      }

      if (request.method === "PATCH" && pathname === "/api/v1/settings/scheduler") {
        await authorize(request, config, oidcVerifier, ["admin"], true);
        const body = await readJson<{ mode?: unknown; globalMaxConcurrency?: number }>(request);
        const mode = parseSchedulerMode(body.mode);
        runWithPostgresSystemScope(() => store.updateScheduler(mode, body.globalMaxConcurrency));
        json(response, 200, { mode, globalMaxConcurrency: body.globalMaxConcurrency });
        return;
      }

      if (request.method === "PATCH" && pathname === "/api/v1/settings/model-router") {
        await authorize(request, config, oidcVerifier, ["admin"], true);
        const body = await readJson<Partial<ModelRouterPolicy>>(request);
        if (body.strategy !== undefined && !["balanced", "performance", "efficiency"].includes(body.strategy)) {
          throw new HttpError(400, "strategy должен быть balanced, performance или efficiency");
        }
        for (const key of ["minContextTokens", "minQualityScore", "minBatteryPercent", "maxTemperatureC"] as const) {
          if (body[key] !== undefined && (typeof body[key] !== "number" || !Number.isFinite(body[key]))) {
            throw new HttpError(400, `${key} должен быть числом`);
          }
        }
        if (body.enabled !== undefined && typeof body.enabled !== "boolean") {
          throw new HttpError(400, "enabled должен быть boolean");
        }
        if (body.allowUnknownProfiles !== undefined && typeof body.allowUnknownProfiles !== "boolean") {
          throw new HttpError(400, "allowUnknownProfiles должен быть boolean");
        }
        json(response, 200, runWithPostgresSystemScope(() => store.updateModelRouterPolicy(body)));
        return;
      }

      const approvalStageId = routeParam(pathname, /^\/api\/v1\/approvals\/([^/]+)$/);
      if (request.method === "POST" && approvalStageId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin", "operator"], true);
        const body = await readJson<{ decision?: string }>(request);
        if (body.decision !== "approve" && body.decision !== "reject") {
          throw new HttpError(400, "decision должен быть approve или reject");
        }
        const processInstanceId = store.decideApproval(approvalStageId, body.decision === "approve", auth.projectId);
        notifyProcessRuntime(processRuntime, store, processInstanceId, `approval.${body.decision}`);
        noContent(response);
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/edge/enrollment/challenges") {
        if (!config.edgeEnabled || !edgeAttestation.available) {
          throw new HttpError(503, edgeAttestation.reason ?? "Native edge enrollment выключен");
        }
        const body = await readJson<EdgeEnrollmentChallengeInput>(request);
        if (!body.enrollmentToken || !tokensEqual(body.enrollmentToken, config.enrollmentToken)) {
          throw new HttpError(403, "Токен регистрации недействителен");
        }
        if (body.platform !== "android" && body.platform !== "ios") {
          throw new HttpError(400, "platform должен быть android или ios");
        }
        const expectedApplicationId = body.platform === "android"
          ? config.edgeAndroidApplicationId
          : config.edgeIosApplicationId;
        if (!body.applicationId || body.applicationId !== expectedApplicationId) {
          throw new HttpError(403, "Application ID не разрешён edge policy");
        }
        json(response, 201, store.issueEdgeEnrollmentChallenge({
          name: body.name,
          platform: body.platform,
          applicationId: body.applicationId,
        }));
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/edge/enroll") {
        if (!config.edgeEnabled || !edgeAttestation.available) {
          throw new HttpError(503, edgeAttestation.reason ?? "Native edge enrollment выключен");
        }
        const body = await readJson<EdgeWorkerRegistration>(request);
        if (body.platform !== "android" && body.platform !== "ios") {
          throw new HttpError(400, "platform должен быть android или ios");
        }
        if (!Array.isArray(body.models)) throw new HttpError(400, "models должен быть массивом");
        if (typeof body.challengeId !== "string" || !body.challengeId
          || body.challengeId.length > 120
          || typeof body.challenge !== "string" || !body.challenge
          || body.challenge.length > 512
          || typeof body.name !== "string" || !body.name.trim()
          || body.name.length > 120) {
          throw new HttpError(400, "Challenge или имя edge-узла заданы некорректно");
        }
        if (body.modelProfiles !== undefined && !Array.isArray(body.modelProfiles)) {
          throw new HttpError(400, "modelProfiles должен быть массивом");
        }
        if (body.embeddingModels !== undefined && !Array.isArray(body.embeddingModels)) {
          throw new HttpError(400, "embeddingModels должен быть массивом");
        }
        if (!body.attestation || typeof body.attestation !== "object"
          || (body.attestation.provider !== "play_integrity" && body.attestation.provider !== "app_attest")
          || typeof body.attestation.token !== "string" || !body.attestation.token
          || body.attestation.token.length > 256_000
          || typeof body.attestation.keyId !== "string" || !body.attestation.keyId
          || body.attestation.keyId.length > 1_024
          || typeof body.attestation.applicationId !== "string") {
          throw new HttpError(400, "Attestation evidence неполна или слишком велика");
        }
        const expectedApplicationId = body.platform === "android"
          ? config.edgeAndroidApplicationId
          : config.edgeIosApplicationId;
        if (body.attestation.applicationId !== expectedApplicationId) {
          throw new HttpError(403, "Application ID не разрешён edge policy");
        }
        const challenge = store.claimEdgeEnrollmentChallenge({
          id: body.challengeId,
          challenge: body.challenge,
          name: body.name,
          platform: body.platform,
          applicationId: body.attestation.applicationId,
        });
        if (!challenge) throw new HttpError(409, "Edge enrollment challenge истёк, использован или не совпадает");
        try {
          const verdict = await edgeAttestation.verify({ challenge, registration: body });
          const registered = store.registerEdgeNode(body, verdict);
          store.finishEdgeEnrollmentChallenge(challenge.id, true);
          json(response, 201, {
            ...registered,
            trust: {
              kind: "hardware_attested",
              provider: verdict.provider,
              applicationId: verdict.applicationId,
              attestedAt: verdict.issuedAt,
              environment: verdict.environment,
            },
          });
        } catch (error) {
          store.finishEdgeEnrollmentChallenge(challenge.id, false, safeMessage(error));
          throw error;
        }
        return;
      }

      const remoteWipeNodeId = routeParam(pathname, /^\/api\/v1\/nodes\/([^/]+)\/remote-wipe$/);
      if (request.method === "POST" && remoteWipeNodeId) {
        const auth = await authorize(request, config, oidcVerifier, ["admin"], true);
        const body = await readJson<{ reason?: string }>(request);
        if (!body.reason?.trim()) throw new HttpError(400, "Причина remote wipe обязательна");
        json(response, 202, store.requestEdgeRemoteWipe(remoteWipeNodeId, body.reason, auth.subject));
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/workers/attestation/challenge") {
        const body = await readJson<WorkerRuntimeAttestationChallengeInput>(request);
        if (!body.enrollmentToken || !tokensEqual(body.enrollmentToken, config.enrollmentToken)) {
          throw new HttpError(403, "Токен регистрации недействителен");
        }
        if (!body.release || !body.name?.trim() || !body.platform?.trim()) {
          throw new HttpError(400, "Runtime attestation challenge требует worker binding и signed release identity");
        }
        json(response, 201, runWithPostgresSystemScope(() =>
          store.issueWorkerRuntimeAttestationChallenge(body)));
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/workers/attestation/refresh") {
        const node = requireWorker(request, store);
        const body = await readJson<{
          runtimeChallengeId?: string;
          runtimeAttestation?: WorkerRuntimeAttestationEnvelope;
        }>(request);
        if (!body.runtimeChallengeId || !body.runtimeAttestation) {
          throw new HttpError(400, "Runtime attestation refresh требует challenge и evidence");
        }
        try {
          json(response, 200, runWithPostgresSystemScope(() => store.refreshWorkerRuntimeAttestation(
            String(node.id),
            body.runtimeChallengeId!,
            body.runtimeAttestation!,
          )));
        } catch (error) {
          store.finishWorkerRuntimeAttestationChallenge(body.runtimeChallengeId, safeMessage(error));
          throw error;
        }
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/workers/register") {
        const body = await readJson<WorkerRegistration>(request);
        if (!body.enrollmentToken || !tokensEqual(body.enrollmentToken, config.enrollmentToken)) {
          throw new HttpError(403, "Токен регистрации недействителен");
        }
        if (!Array.isArray(body.models)) throw new HttpError(400, "models должен быть массивом");
        if (body.agentRuntimes !== undefined && !Array.isArray(body.agentRuntimes)) {
          throw new HttpError(400, "agentRuntimes должен быть массивом");
        }
        if (body.modelProfiles !== undefined && !Array.isArray(body.modelProfiles)) {
          throw new HttpError(400, "modelProfiles должен быть массивом");
        }
        if (body.embeddingModels !== undefined && !Array.isArray(body.embeddingModels)) {
          throw new HttpError(400, "embeddingModels должен быть массивом");
        }
        let registered: ReturnType<AgatStore["registerNode"]>;
        try {
          registered = store.registerNode(body);
        } catch (error) {
          if (body.runtimeChallengeId) {
            store.finishWorkerRuntimeAttestationChallenge(body.runtimeChallengeId, safeMessage(error));
          }
          throw error;
        }
        json(response, 201, registered);
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/workers/heartbeat") {
        const node = requireWorker(request, store, true);
        const body = await readJson<{ metrics?: WorkerMetrics; capabilities?: WorkerCapabilities }>(request);
        if (body.capabilities && !Array.isArray(body.capabilities.models)) {
          throw new HttpError(400, "capabilities.models должен быть массивом");
        }
        if (body.capabilities?.agentRuntimes !== undefined && !Array.isArray(body.capabilities.agentRuntimes)) {
          throw new HttpError(400, "capabilities.agentRuntimes должен быть массивом");
        }
        if (body.capabilities?.modelProfiles !== undefined && !Array.isArray(body.capabilities.modelProfiles)) {
          throw new HttpError(400, "capabilities.modelProfiles должен быть массивом");
        }
        if (body.capabilities?.embeddingModels !== undefined && !Array.isArray(body.capabilities.embeddingModels)) {
          throw new HttpError(400, "capabilities.embeddingModels должен быть массивом");
        }
        store.heartbeatNode(String(node.id), body.metrics ?? {}, body.capabilities);
        if (node.trust_kind === "hardware_attested") json(response, 200, store.edgeControl(String(node.id)));
        else noContent(response);
        return;
      }

      if (request.method === "GET" && pathname === "/api/v1/edge/control") {
        const node = requireWorker(request, store, true);
        if (node.trust_kind !== "hardware_attested") throw new HttpError(403, "Control channel доступен только edge-узлам");
        json(response, 200, store.edgeControl(String(node.id)));
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/edge/control/wipe-ack") {
        const node = requireWorker(request, store, true);
        if (node.trust_kind !== "hardware_attested") throw new HttpError(403, "Control channel доступен только edge-узлам");
        const body = await readJson<EdgeWipeAcknowledgement>(request);
        if (typeof body.credentialsDeleted !== "boolean" || typeof body.localDataDeleted !== "boolean") {
          throw new HttpError(400, "wipe acknowledgement должен содержать boolean deletion flags");
        }
        json(response, 200, store.acknowledgeEdgeRemoteWipe(String(node.id), body));
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/workers/knowledge/lease") {
        const node = requireWorker(request, store);
        const lease = store.leaseKnowledgeEmbedding(String(node.id));
        if (!lease) {
          noContent(response);
          return;
        }
        json(response, 200, lease);
        return;
      }

      const renewKnowledgeLeaseId = routeParam(pathname, /^\/api\/v1\/workers\/knowledge\/leases\/([^/]+)\/renew$/);
      if (request.method === "POST" && renewKnowledgeLeaseId) {
        const node = requireWorker(request, store);
        if (!store.renewKnowledgeEmbeddingLease(String(node.id), renewKnowledgeLeaseId)) {
          throw new HttpError(404, "Embedding-аренда не найдена");
        }
        noContent(response);
        return;
      }

      const completeKnowledgeLeaseId = routeParam(pathname, /^\/api\/v1\/workers\/knowledge\/leases\/([^/]+)\/complete$/);
      if (request.method === "POST" && completeKnowledgeLeaseId) {
        const node = requireWorker(request, store);
        const body = await readJson<{ embeddings?: KnowledgeEmbeddingResult[] }>(request, KNOWLEDGE_JSON_LIMIT_BYTES);
        if (!Array.isArray(body.embeddings)) throw new HttpError(400, "embeddings должен быть массивом");
        json(response, 200, store.completeKnowledgeEmbedding(
          String(node.id),
          completeKnowledgeLeaseId,
          body.embeddings,
        ));
        return;
      }

      const failKnowledgeLeaseId = routeParam(pathname, /^\/api\/v1\/workers\/knowledge\/leases\/([^/]+)\/fail$/);
      if (request.method === "POST" && failKnowledgeLeaseId) {
        const node = requireWorker(request, store);
        const body = await readJson<{ error?: string }>(request);
        if (!body.error?.trim()) throw new HttpError(400, "Описание ошибки обязательно");
        json(response, 200, store.failKnowledgeEmbedding(String(node.id), failKnowledgeLeaseId, body.error));
        return;
      }

      if (request.method === "POST" && pathname === "/api/v1/workers/lease") {
        const node = requireWorker(request, store);
        const body = await readJson<{ workerVersion?: string }>(request);
        const workerVersion = typeof body.workerVersion === "string" ? body.workerVersion : "unknown";
        const lease = store.leaseNext(String(node.id), workerVersion);
        if (!lease) {
          noContent(response);
          return;
        }
        json(response, 200, lease);
        return;
      }

      const renewLeaseId = routeParam(pathname, /^\/api\/v1\/leases\/([^/]+)\/renew$/);
      if (request.method === "POST" && renewLeaseId) {
        const node = requireWorker(request, store);
        if (!store.renewLease(String(node.id), renewLeaseId)) throw new HttpError(404, "Аренда не найдена");
        noContent(response);
        return;
      }

      const eventLeaseId = routeParam(pathname, /^\/api\/v1\/leases\/([^/]+)\/events$/);
      if (request.method === "POST" && eventLeaseId) {
        const node = requireWorker(request, store);
        const body = await readJson<{ level?: string; message?: string; data?: Record<string, unknown> }>(request);
        if (!body.message?.trim()) throw new HttpError(400, "Сообщение события обязательно");
        const event = store.appendLeaseEvent(
          String(node.id),
          eventLeaseId,
          body.level ?? "info",
          body.message,
          body.data ?? null,
        );
        json(response, 201, event);
        return;
      }

      const knowledgeSearchLeaseId = routeParam(pathname, /^\/api\/v1\/leases\/([^/]+)\/knowledge\/search$/);
      if (request.method === "POST" && knowledgeSearchLeaseId) {
        const node = requireWorker(request, store);
        const body = await readJson<KnowledgeSearchRequest>(request);
        json(response, 200, store.searchKnowledge(String(node.id), knowledgeSearchLeaseId, body));
        return;
      }

      const mcpLeaseCallId = routeParam(pathname, /^\/api\/v1\/leases\/([^/]+)\/mcp\/tools\/call$/);
      if (request.method === "POST" && mcpLeaseCallId) {
        const node = requireWorker(request, store);
        const body = await readJson<{
          publicName?: string;
          clientCallId?: string;
          arguments?: Record<string, unknown>;
        }>(request);
        if (typeof body.publicName !== "string" || !body.publicName) throw new HttpError(400, "publicName обязателен");
        if (typeof body.clientCallId !== "string" || !body.clientCallId) throw new HttpError(400, "clientCallId обязателен");
        const traceparentHeader = request.headers.traceparent;
        const result = await mcpGateway.callLeaseTool({
          nodeId: String(node.id),
          leaseId: mcpLeaseCallId,
          publicName: body.publicName,
          clientCallId: body.clientCallId,
          arguments: body.arguments ?? {},
          traceparent: typeof traceparentHeader === "string" ? traceparentHeader : null,
        });
        json(response, result.status === "waiting_approval" || result.status === "executing" ? 202 : 200, result);
        return;
      }

      const mcpLeaseStatusMatch = /^\/api\/v1\/leases\/([^/]+)\/mcp\/tool-calls\/([^/]+)$/.exec(pathname);
      if (request.method === "GET" && mcpLeaseStatusMatch?.[1] && mcpLeaseStatusMatch[2]) {
        const node = requireWorker(request, store);
        const result = mcpGateway.getLeaseCall(
          String(node.id),
          decodeURIComponent(mcpLeaseStatusMatch[1]),
          decodeURIComponent(mcpLeaseStatusMatch[2]),
        );
        if (!result) throw new HttpError(404, "MCP-вызов не найден");
        json(response, result.status === "waiting_approval" || result.status === "executing" ? 202 : 200, result);
        return;
      }

      const mcpLeaseCancelMatch = /^\/api\/v1\/leases\/([^/]+)\/mcp\/tool-calls\/([^/]+)\/cancel$/.exec(pathname);
      if (request.method === "POST" && mcpLeaseCancelMatch?.[1] && mcpLeaseCancelMatch[2]) {
        const node = requireWorker(request, store);
        const result = mcpGateway.cancelLeaseCall(
          String(node.id),
          decodeURIComponent(mcpLeaseCancelMatch[1]),
          decodeURIComponent(mcpLeaseCancelMatch[2]),
        );
        if (!result) throw new HttpError(404, "MCP-вызов не найден");
        json(response, result.status === "executing" ? 202 : 200, result);
        return;
      }

      const completeLeaseId = routeParam(pathname, /^\/api\/v1\/leases\/([^/]+)\/complete$/);
      if (request.method === "POST" && completeLeaseId) {
        const node = requireWorker(request, store);
        const body = await readJson<{
          output?: string;
          artifacts?: WorkerArtifactInput[];
          metrics?: WorkerExecutionMetrics;
        }>(request);
        if (typeof body.output !== "string") throw new HttpError(400, "output должен быть строкой");
        if (body.artifacts !== undefined && !Array.isArray(body.artifacts)) {
          throw new HttpError(400, "artifacts должен быть массивом");
        }
        const result = store.completeLease(
          String(node.id),
          completeLeaseId,
          body.output,
          body.artifacts ?? [],
          body.metrics ?? {},
        );
        notifyProcessRuntime(processRuntime, store, result.processInstanceId, "lease.completed");
        json(response, 200, result);
        return;
      }

      const failLeaseId = routeParam(pathname, /^\/api\/v1\/leases\/([^/]+)\/fail$/);
      if (request.method === "POST" && failLeaseId) {
        const node = requireWorker(request, store);
        const body = await readJson<{ error?: string }>(request);
        if (!body.error?.trim()) throw new HttpError(400, "Описание ошибки обязательно");
        const result = store.failLease(String(node.id), failLeaseId, body.error);
        notifyProcessRuntime(processRuntime, store, result.processInstanceId, result.retrying ? "lease.retrying" : "lease.failed");
        json(response, 200, result);
        return;
      }

      if (pathname.startsWith("/a2a/")) {
        throw new A2AProtocolError(404, "NOT_FOUND", "OPERATION_NOT_FOUND", "A2A-операция не найдена");
      }
      if (pathname.startsWith("/api/")) {
        throw new HttpError(404, "Маршрут API не найден");
      }
      if (!serveStatic(config, pathname, response)) {
        throw new HttpError(404, "Страница не найдена");
      }
    } catch (error) {
      if (response.headersSent) {
        response.end();
        return;
      }
      if (error instanceof A2AProtocolError || pathname.startsWith("/a2a/")) {
        const protocolError = error instanceof A2AProtocolError
          ? error
          : new A2AProtocolError(500, "INTERNAL", "INTERNAL_ERROR", "Внутренняя ошибка A2A adapter");
        const headers: Record<string, string> = protocolError.httpStatus === 401
          ? { "www-authenticate": 'Bearer realm="agat-a2a"' }
          : {};
        a2aJson(response, protocolError.httpStatus, a2aErrorBody(protocolError), headers);
        return;
      }
      const status = error instanceof HttpError
        || error instanceof WorkerLauncherError
        || error instanceof AuthenticationError
        || error instanceof EdgeAttestationError
        ? error.status
        : error instanceof A2ATransportError
          ? error.httpStatus
        : 400;
      json(response, status, { error: safeMessage(error) });
    }
    });
    void requestTask.catch((error) => {
      if (!response.headersSent) json(response, 500, { error: safeMessage(error) });
      else response.end();
    });
  });
  let pushPumpRunning = false;
  const pushTimer = setInterval(() => {
    if (!config.a2aEnabled || pushPumpRunning) return;
    pushPumpRunning = true;
    let deliveries: ReturnType<AgatStore["claimA2APushDeliveries"]>;
    try {
      deliveries = store.claimA2APushDeliveries(10);
    } catch (error) {
      pushPumpRunning = false;
      console.warn(`Ошибка A2A push outbox: ${safeMessage(error)}`);
      return;
    }
    void Promise.all(deliveries.map(async (delivery) => {
      try {
        await sendA2APush(delivery.url, delivery.authorization, delivery.payload, a2aOutboundPolicy);
        store.completeA2APushDelivery(delivery.deliveryId, null);
      } catch (error) {
        store.completeA2APushDelivery(delivery.deliveryId, safeMessage(error));
      }
    })).finally(() => { pushPumpRunning = false; });
  }, 500);
  pushTimer.unref();
  server.once("close", () => clearInterval(pushTimer));
  return server;
}

async function main(): Promise<void> {
  const config = loadConfig();
  validateRemoteBinding(config);
  const telemetry = new CoordinatorTelemetry({
    enabled: config.otelEnabled,
    serviceName: config.otelServiceName,
    exporterEndpoint: config.otelExporterEndpoint,
  });
  const store = new AgatStore(config.dbPath, {
    seedDemo: config.seedDemo,
    leaseTtlSeconds: config.leaseTtlSeconds,
    artifactsDir: config.artifactsDir,
    credentialsKey: config.credentialsKey,
    temporalProcesses: config.temporalEnabled,
    edgeChallengeTtlSeconds: config.edgeChallengeTtlSeconds,
    telemetry,
    stateStoreDriver: config.stateStoreDriver,
    coordinatorInstanceId: config.coordinatorInstanceId,
    region: config.region,
    residencyDomain: config.residencyDomain,
    regionLossDrActivationId: config.regionLossDrActivationId,
    regionLossDrWriteEpoch: config.regionLossDrWriteEpoch,
    workerReleasePublicKeys: config.workerReleasePublicKeys,
    requireSignedWorkerReleases: config.requireSignedWorkerReleases,
    workerProvenancePublicKeys: config.workerProvenancePublicKeys,
    requireWorkerProvenance: config.requireWorkerProvenance,
    workerRuntimeAttestationPublicKeys: config.workerRuntimeAttestationPublicKeys,
    requireWorkerRuntimeAttestation: config.requireWorkerRuntimeAttestation,
    workerRuntimeAttestationProviders: config.workerRuntimeAttestationProviders,
    workerRuntimeIdentityPrefixes: config.workerRuntimeIdentityPrefixes,
    workerRuntimeChallengeTtlSeconds: config.workerRuntimeChallengeTtlSeconds,
    workerRuntimeMaxLifetimeSeconds: config.workerRuntimeMaxLifetimeSeconds,
    siemMaxAttempts: config.siemMaxAttempts,
    siemDeliveredRetentionDays: config.siemDeliveredRetentionDays,
    siemDlqRetentionDays: config.siemDlqRetentionDays,
    artifactStoreDriver: config.artifactStoreDriver,
    artifactRetentionDays: config.artifactRetentionDays,
    ...(config.artifactStoreDriver === "s3" ? {
      artifactS3: {
        endpoint: config.artifactS3Endpoint,
        region: config.artifactS3Region,
        bucket: config.artifactS3Bucket,
        prefix: config.artifactS3Prefix,
        forcePathStyle: config.artifactS3ForcePathStyle,
        ...(config.artifactS3AccessKeyId ? {
          accessKeyId: config.artifactS3AccessKeyId,
          secretAccessKey: config.artifactS3SecretAccessKey,
          ...(config.artifactS3SessionToken ? { sessionToken: config.artifactS3SessionToken } : {}),
        } : {}),
        requestTimeoutMs: config.artifactS3RequestTimeoutMs,
        maximumObjectBytes: config.artifactS3MaximumObjectBytes,
        serverSideEncryption: config.artifactS3Sse,
        ...(config.artifactS3KmsKeyId ? { kmsKeyId: config.artifactS3KmsKeyId } : {}),
        objectLockMode: config.artifactS3ObjectLockMode,
        requireVersioning: config.artifactS3RequireVersioning,
      },
    } : {}),
    ...(config.stateStoreDriver === "postgresql" ? {
      postgresSchemaMode: "runtime" as const,
      postgres: {
        systemUrl: config.postgresUrl,
        tenantUrl: config.postgresTenantUrl,
        roleMode: "runtime" as const,
        applicationName: `agat-${config.coordinatorInstanceId}`,
        poolMax: config.postgresPoolMax,
        connectTimeoutMs: config.postgresConnectTimeoutMs,
        idleTimeoutMs: config.postgresIdleTimeoutMs,
        statementTimeoutMs: config.postgresStatementTimeoutMs,
        sslMode: config.postgresSslMode,
        sslCa: config.postgresCaCertPath ? fs.readFileSync(config.postgresCaCertPath, "utf8") : "",
        sslCert: config.postgresClientCertPath ? fs.readFileSync(config.postgresClientCertPath, "utf8") : "",
        sslKey: config.postgresClientKeyPath ? fs.readFileSync(config.postgresClientKeyPath, "utf8") : "",
      },
    } : {}),
  });
  const processRuntime = await createProcessRuntime(config);
  for (const process of store.listActiveDurableProcesses()) {
    await processRuntime.startProcess(process);
  }
  const localWorkerLauncher = createLocalWorkerLauncher(config);
  const mcpGateway = new McpGateway(store, telemetry, {
    enabled: config.mcpEnabled,
    requestTimeoutSeconds: config.mcpRequestTimeoutSeconds,
    maxResponseBytes: config.mcpMaxResponseBytes,
    approvalTtlSeconds: config.mcpApprovalTtlSeconds,
  }, undefined, createSandboxExecutor(config));
  const server = createCoordinatorServer(config, store, localWorkerLauncher, processRuntime, mcpGateway);

  void localWorkerLauncher.snapshot().then((snapshot) => {
    if (!snapshot.available) return;
    const removed = store.reconcileWorkerPoolNodes(new Set(snapshot.pools.map((pool) => pool.id)));
    if (removed > 0) console.log(`Удалены устаревшие записи управляемых workers: ${removed}`);
  }).catch((error) => {
    console.warn(`Не удалось сверить управляемые worker-пулы: ${safeMessage(error)}`);
  });

  server.listen(config.port, config.host, () => {
    console.log(`АГАТ слушает http://${config.host}:${config.port}`);
    console.log(`State store: ${config.stateStoreDriver}`);
    console.log(`HA-cell: ${config.region}/${config.residencyDomain} · ${config.coordinatorInstanceId}`);
    console.log(`Artifact store: ${config.artifactStoreDriver}${config.artifactStoreDriver === "s3" ? ` · bucket ${config.artifactS3Bucket}` : ` · cache ${config.artifactsDir}`}`);
    console.log(`Локальный worker launcher: ${config.localWorkerLauncherEnabled ? "включён" : "выключен"}`);
    console.log(`Runtime процессов: ${processRuntime.snapshot().mode}`);
    console.log(`OpenTelemetry: ${config.otelEnabled ? "включён" : "выключен"}`);
    console.log(`MCP gateway: ${config.mcpEnabled ? "включён" : "выключен"}`);
    const sandbox = mcpGateway.sandboxSnapshot();
    console.log(`Tool sandbox: ${sandbox?.available ? "доступен" : sandbox?.reason ?? "не настроен"}`);
    console.log(`A2A adapter: ${config.a2aEnabled ? `включён · ${config.a2aPublicBaseUrl}` : "выключен"}`);
    console.log(`Native edge workers: ${config.edgeEnabled ? `включены · attestation ${config.edgeAttestationMode}` : "выключены"}`);
  });

  const maintenanceTimer = setInterval(() => {
    try {
      store.maintenanceTick();
    } catch (error) {
      console.warn(`Ошибка фонового обслуживания: ${safeMessage(error)}`);
    }
  }, 1_000);
  maintenanceTimer.unref();

  const artifactLifecycleTimer = setInterval(() => {
    try {
      const result = runWithPostgresSystemScope(() => store.runArtifactLifecycle());
      if (result.delivered > 0 || result.retried > 0 || result.dead > 0) {
        console.log(`Artifact lifecycle: staged=${result.staged} delivered=${result.delivered} retried=${result.retried} dead=${result.dead}`);
      }
    } catch (error) {
      console.warn(`Ошибка artifact lifecycle: ${safeMessage(error)}`);
    }
  }, config.artifactLifecycleIntervalSeconds * 1_000);
  artifactLifecycleTimer.unref();

  let mcpRefreshRunning = false;
  const mcpRefreshTimer = setInterval(() => {
    if (mcpRefreshRunning) return;
    mcpRefreshRunning = true;
    void mcpGateway.refreshDueServers()
      .catch((error) => console.warn(`Ошибка обновления MCP-каталогов: ${safeMessage(error)}`))
      .finally(() => { mcpRefreshRunning = false; });
  }, config.mcpRefreshSeconds * 1_000);
  mcpRefreshTimer.unref();

  let siemExportRunning = false;
  const pumpSiem = async (): Promise<void> => {
    if (!config.siemEnabled || siemExportRunning) return;
    siemExportRunning = true;
    let batch: Array<Record<string, unknown>> = [];
    try {
      batch = runWithPostgresSystemScope(() => store.claimAuditExportBatch(config.siemBatchSize));
      if (batch.length === 0) return;
      const firstId = Number(batch[0]?.id);
      const lastId = Number(batch.at(-1)?.id);
      const batchAck = `${firstId}-${lastId}`;
      const response = await fetch(config.siemUrl, {
        method: "POST",
        redirect: "error",
        headers: {
          "content-type": "application/x-ndjson",
          "user-agent": "agat-coordinator/1.7.0",
          "x-agat-audit-batch": batchAck,
          ...(config.siemBearerToken ? { authorization: `Bearer ${config.siemBearerToken}` } : {}),
        },
        body: `${batch.map((event) => JSON.stringify(event)).join("\n")}\n`,
        signal: AbortSignal.timeout(config.siemTimeoutSeconds * 1_000),
      });
      await response.body?.cancel();
      const ack = response.headers.get("x-agat-audit-ack") ?? "";
      const responseSha256 = sha256Text(`${response.status}\n${ack}`);
      const decision = evaluateSiemResponse(response.status, ack, batchAck, config.siemRequireAck);
      if (!decision.delivered) {
        runWithPostgresSystemScope(() => store.completeAuditExport(
          batch.map((event) => Number(event.id)),
          decision.errorMessage,
          {
            reasonCode: decision.reasonCode ?? undefined,
            responseSha256,
            terminal: decision.terminal,
          },
        ));
        console.warn(`SIEM audit export отклонён: ${decision.errorMessage}`);
        return;
      }
      runWithPostgresSystemScope(() => store.completeAuditExport(
        batch.map((event) => Number(event.id)),
        null,
        { responseSha256 },
      ));
    } catch (error) {
      if (batch.length > 0) {
        const message = safeMessage(error);
        runWithPostgresSystemScope(() => store.completeAuditExport(
          batch.map((event) => Number(event.id)),
          message,
          { reasonCode: /timeout|timed out|abort/i.test(message) ? "timeout" : "network_error" },
        ));
      }
      console.warn(`Ошибка SIEM audit export: ${safeMessage(error)}`);
    } finally {
      siemExportRunning = false;
    }
  };
  const siemTimer = setInterval(() => void pumpSiem(), config.siemIntervalSeconds * 1_000);
  siemTimer.unref();
  if (config.siemEnabled) void pumpSiem();

  const siemRetentionTimer = setInterval(() => {
    try {
      const result = runWithPostgresSystemScope(() => store.runAuditExportRetention());
      if (result.deliveredPurged > 0 || result.deadLettersPurged > 0) {
        console.log(`SIEM retention: delivered=${result.deliveredPurged} deadLetters=${result.deadLettersPurged}`);
      }
    } catch (error) {
      console.warn(`Ошибка SIEM retention: ${safeMessage(error)}`);
    }
  }, config.siemRetentionIntervalSeconds * 1_000);
  siemRetentionTimer.unref();

  const shutdown = (): void => {
    clearInterval(maintenanceTimer);
    clearInterval(artifactLifecycleTimer);
    clearInterval(mcpRefreshTimer);
    clearInterval(siemTimer);
    clearInterval(siemRetentionTimer);
    server.close(() => void (async () => {
      store.close();
      await processRuntime.close();
      await telemetry.shutdown();
      process.exit(0);
    })());
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

const entryPoint = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (import.meta.url === entryPoint) {
  main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}
