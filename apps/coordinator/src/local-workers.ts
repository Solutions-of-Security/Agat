import fs from "node:fs";
import { request as httpsRequest } from "node:https";
import { randomUUID } from "node:crypto";

import type { CoordinatorConfig } from "./config.js";
import type {
  LaunchLocalWorkersInput,
  LocalModelInfo,
  LocalWorkerLauncherSnapshot,
  LocalWorkerPool,
} from "./types.js";

const MANAGED_LABEL = "agat.local/managed";
const POOL_LABEL = "agat.local/pool-id";
const ANNOTATION_PREFIX = "agat.local/";
const SERVICE_ACCOUNT_DIR = "/var/run/secrets/kubernetes.io/serviceaccount";
// The sandbox log endpoint may legitimately return the configured MCP maximum
// (4 MiB) plus one byte used to detect overflow.
const MAX_KUBERNETES_RESPONSE_BYTES = 4_300_000;
const MAX_MODEL_RESPONSE_BYTES = 1_000_000;

type JsonObject = Record<string, unknown>;

interface KubernetesDeployment {
  metadata?: {
    name?: string;
    labels?: Record<string, string>;
    annotations?: Record<string, string>;
  };
  spec?: {
    replicas?: number;
  };
  status?: {
    readyReplicas?: number;
    availableReplicas?: number;
    unavailableReplicas?: number;
    conditions?: Array<{ type?: string; status?: string; reason?: string; message?: string }>;
  };
}

interface KubernetesDeploymentList {
  items?: KubernetesDeployment[];
}

export interface KubernetesTransport {
  request<T>(method: string, path: string, body?: JsonObject, contentType?: string): Promise<T>;
}

export interface LocalWorkerLauncher {
  snapshot(): Promise<LocalWorkerLauncherSnapshot>;
  launch(input: LaunchLocalWorkersInput): Promise<LocalWorkerPool>;
  startPool(poolId: string): Promise<LocalWorkerPool>;
  stopPool(poolId: string): Promise<LocalWorkerPool>;
  deletePool(poolId: string): Promise<LocalWorkerPool>;
}

export class WorkerLauncherError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

interface LauncherOptions {
  namespace: string;
  workerImage: string;
  workerConfigMap: string;
  workerSecret: string;
  modelBaseUrl: string;
  modelDiscoveryUrl: string;
  embeddingModels: string;
  defaultWebEnabled: boolean;
  maxWorkersPerLaunch: number;
}

interface NormalizedLaunchInput {
  name: string;
  model: string;
  workers: number;
  concurrency: number;
  webEnabled: boolean;
}

function integer(value: unknown, fallback: number): number {
  const parsed = typeof value === "number" ? value : Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) ? Math.trunc(parsed) : fallback;
}

function safeErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Неизвестная ошибка";
}

function slug(value: string): string {
  const normalized = value
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 30);
  return normalized || "worker";
}

function validatePoolId(poolId: string): string {
  if (!/^[a-f0-9]{12}$/.test(poolId)) throw new WorkerLauncherError(400, "Некорректный идентификатор worker-пула");
  return poolId;
}

function normalizeLaunchInput(input: LaunchLocalWorkersInput, options: LauncherOptions): NormalizedLaunchInput {
  const model = typeof input.model === "string" ? input.model.trim() : "";
  if (!model) throw new WorkerLauncherError(400, "Выберите модель для worker");
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._/+@:-]{0,127}$/.test(model)) {
    throw new WorkerLauncherError(400, "Имя модели содержит неподдерживаемые символы");
  }

  const workers = integer(input.workers, 1);
  if (workers < 1 || workers > options.maxWorkersPerLaunch) {
    throw new WorkerLauncherError(400, `Количество workers должно быть от 1 до ${options.maxWorkersPerLaunch}`);
  }
  const concurrency = integer(input.concurrency, 1);
  if (concurrency < 1 || concurrency > 8) {
    throw new WorkerLauncherError(400, "Параллельность одного worker должна быть от 1 до 8");
  }

  const requestedName = typeof input.name === "string" ? input.name.trim() : "";
  const name = (requestedName || `${model} · локальные workers`).slice(0, 80);
  return {
    name,
    model,
    workers,
    concurrency,
    webEnabled: typeof input.webEnabled === "boolean" ? input.webEnabled : options.defaultWebEnabled,
  };
}

function environmentVariable(name: string, value: string): JsonObject {
  return { name, value };
}

export function buildWorkerDeployment(
  options: LauncherOptions,
  input: NormalizedLaunchInput,
  poolId: string,
  createdAt: string,
  index: number,
): JsonObject {
  const indexText = String(index + 1).padStart(2, "0");
  const deploymentName = `agat-local-${slug(input.name)}-${poolId.slice(0, 6)}-${indexText}`.slice(0, 63);
  const commonLabels = {
    "app.kubernetes.io/name": "agat",
    "app.kubernetes.io/component": "worker",
    "app.kubernetes.io/part-of": "agat",
    "app.kubernetes.io/managed-by": "agat-coordinator",
    [MANAGED_LABEL]: "true",
    [POOL_LABEL]: poolId,
  };
  const annotations = {
    [`${ANNOTATION_PREFIX}pool-name`]: input.name,
    [`${ANNOTATION_PREFIX}model`]: input.model,
    [`${ANNOTATION_PREFIX}concurrency`]: String(input.concurrency),
    [`${ANNOTATION_PREFIX}web-enabled`]: String(input.webEnabled),
    [`${ANNOTATION_PREFIX}created-at`]: createdAt,
    [`${ANNOTATION_PREFIX}worker-index`]: String(index + 1),
    [`${ANNOTATION_PREFIX}worker-name`]: deploymentName,
  };

  return {
    apiVersion: "apps/v1",
    kind: "Deployment",
    metadata: {
      name: deploymentName,
      namespace: options.namespace,
      labels: commonLabels,
      annotations,
    },
    spec: {
      replicas: 1,
      revisionHistoryLimit: 1,
      progressDeadlineSeconds: 180,
      strategy: { type: "Recreate" },
      selector: { matchLabels: { [POOL_LABEL]: poolId, [`${ANNOTATION_PREFIX}worker-index`]: String(index + 1) } },
      template: {
        metadata: {
          labels: {
            ...commonLabels,
            [`${ANNOTATION_PREFIX}worker-index`]: String(index + 1),
          },
          annotations,
        },
        spec: {
          automountServiceAccountToken: false,
          terminationGracePeriodSeconds: 30,
          securityContext: {
            runAsNonRoot: true,
            runAsUser: 10001,
            runAsGroup: 10001,
            fsGroup: 10001,
            fsGroupChangePolicy: "OnRootMismatch",
            seccompProfile: { type: "RuntimeDefault" },
          },
          containers: [
            {
              name: "worker",
              image: options.workerImage,
              imagePullPolicy: "Always",
              envFrom: [{ configMapRef: { name: options.workerConfigMap } }],
              env: [
                {
                  name: "AGAT_ENROLLMENT_TOKEN",
                  valueFrom: { secretKeyRef: { name: options.workerSecret, key: "enrollment-token" } },
                },
                {
                  name: "AGAT_MODEL_API_KEY",
                  valueFrom: { secretKeyRef: { name: options.workerSecret, key: "model-api-key" } },
                },
                environmentVariable("AGAT_WORKER_NAME", deploymentName),
                environmentVariable("AGAT_WORKER_MODELS", input.model),
                environmentVariable("AGAT_EMBEDDING_MODELS", options.embeddingModels),
                environmentVariable("AGAT_MODEL_BASE_URL", options.modelBaseUrl),
                environmentVariable("AGAT_WORKER_CONCURRENCY", String(input.concurrency)),
                environmentVariable("AGAT_WORKER_CREDENTIALS", "/state/worker.json"),
                environmentVariable(
                  "AGAT_WORKER_LABELS",
                  `runtime=kubernetes,cluster=docker-desktop,managed=local-launcher,pool=${poolId}`,
                ),
                environmentVariable("AGAT_WEB_ENABLED", String(input.webEnabled)),
              ],
              startupProbe: {
                exec: { command: ["/bin/sh", "-ec", "test -s /state/worker.json"] },
                periodSeconds: 2,
                timeoutSeconds: 2,
                failureThreshold: 45,
              },
              readinessProbe: {
                exec: { command: ["/bin/sh", "-ec", "test -s /state/worker.json"] },
                periodSeconds: 10,
                timeoutSeconds: 2,
                failureThreshold: 3,
              },
              resources: {
                requests: { cpu: "25m", memory: "64Mi" },
                limits: { cpu: "500m", memory: "256Mi" },
              },
              securityContext: {
                allowPrivilegeEscalation: false,
                readOnlyRootFilesystem: true,
                capabilities: { drop: ["ALL"] },
              },
              volumeMounts: [
                { name: "state", mountPath: "/state" },
                { name: "tmp", mountPath: "/tmp" },
              ],
            },
          ],
          volumes: [
            { name: "state", emptyDir: { sizeLimit: "8Mi" } },
            { name: "tmp", emptyDir: { sizeLimit: "32Mi" } },
          ],
        },
      },
    },
  };
}

function deploymentPool(deployments: KubernetesDeployment[]): LocalWorkerPool {
  const sorted = [...deployments].sort((left, right) => {
    const leftIndex = integer(left.metadata?.annotations?.[`${ANNOTATION_PREFIX}worker-index`], 0);
    const rightIndex = integer(right.metadata?.annotations?.[`${ANNOTATION_PREFIX}worker-index`], 0);
    return leftIndex - rightIndex;
  });
  const first = sorted[0];
  if (!first) throw new WorkerLauncherError(500, "Пустой worker-пул");
  const annotations = first.metadata?.annotations ?? {};
  const id = first.metadata?.labels?.[POOL_LABEL] ?? "";
  const desiredWorkers = sorted.reduce((sum, deployment) => sum + Math.max(0, integer(deployment.spec?.replicas, 0)), 0);
  const readyWorkers = sorted.reduce((sum, deployment) => sum + Math.max(0, integer(deployment.status?.readyReplicas, 0)), 0);
  const failed = sorted.some((deployment) => deployment.status?.conditions?.some((condition) =>
    condition.type === "ReplicaFailure" && condition.status === "True"));
  const status = desiredWorkers === 0
    ? "stopped"
    : readyWorkers === sorted.length && desiredWorkers === sorted.length
      ? "ready"
      : failed || readyWorkers > 0
        ? "degraded"
        : "starting";

  return {
    id,
    name: annotations[`${ANNOTATION_PREFIX}pool-name`] ?? "Локальные workers",
    model: annotations[`${ANNOTATION_PREFIX}model`] ?? "",
    workers: sorted.length,
    desiredWorkers,
    readyWorkers,
    concurrency: integer(annotations[`${ANNOTATION_PREFIX}concurrency`], 1),
    webEnabled: annotations[`${ANNOTATION_PREFIX}web-enabled`] === "true",
    status,
    createdAt: annotations[`${ANNOTATION_PREFIX}created-at`] ?? "",
    workerNames: sorted.map((deployment) => deployment.metadata?.name ?? "").filter(Boolean),
  };
}

function groupDeployments(deployments: KubernetesDeployment[]): LocalWorkerPool[] {
  const groups = new Map<string, KubernetesDeployment[]>();
  for (const deployment of deployments) {
    const poolId = deployment.metadata?.labels?.[POOL_LABEL];
    if (!poolId || deployment.metadata?.labels?.[MANAGED_LABEL] !== "true") continue;
    const group = groups.get(poolId) ?? [];
    group.push(deployment);
    groups.set(poolId, group);
  }
  return [...groups.values()]
    .map(deploymentPool)
    .sort((left, right) => right.createdAt.localeCompare(left.createdAt));
}

export class InClusterKubernetesTransport implements KubernetesTransport {
  private readonly baseUrl: string;
  private readonly tokenPath: string;
  private readonly caPath: string;

  constructor() {
    const host = process.env.KUBERNETES_SERVICE_HOST ?? "kubernetes.default.svc";
    const port = process.env.KUBERNETES_SERVICE_PORT_HTTPS ?? "443";
    this.baseUrl = `https://${host}:${port}`;
    this.tokenPath = `${SERVICE_ACCOUNT_DIR}/token`;
    this.caPath = `${SERVICE_ACCOUNT_DIR}/ca.crt`;
  }

  async request<T>(method: string, requestPath: string, body?: JsonObject, contentType = "application/json"): Promise<T> {
    const token = fs.readFileSync(this.tokenPath, "utf8").trim();
    const ca = fs.readFileSync(this.caPath);
    const payload = body === undefined ? undefined : Buffer.from(JSON.stringify(body), "utf8");

    return new Promise<T>((resolve, reject) => {
      const request = httpsRequest(new URL(requestPath, this.baseUrl), {
        method,
        ca,
        headers: {
          accept: "application/json",
          authorization: `Bearer ${token}`,
          ...(payload ? { "content-type": contentType, "content-length": String(payload.length) } : {}),
        },
      }, (response) => {
        const chunks: Buffer[] = [];
        let size = 0;
        response.on("data", (chunk: Buffer) => {
          size += chunk.length;
          if (size > MAX_KUBERNETES_RESPONSE_BYTES) {
            response.destroy(new Error("Ответ Kubernetes API слишком большой"));
            return;
          }
          chunks.push(chunk);
        });
        response.on("error", reject);
        response.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          const status = response.statusCode ?? 500;
          let parsed: unknown = undefined;
          if (raw) {
            try {
              parsed = JSON.parse(raw);
            } catch {
              if (/\/log(?:\?|$)/.test(requestPath)) {
                reject(new WorkerLauncherError(502, "Sandbox tool должен вывести ровно одно JSON-значение"));
                return;
              }
              parsed = { message: raw };
            }
          }
          if (status < 200 || status >= 300) {
            const message = parsed && typeof parsed === "object" && "message" in parsed
              ? String((parsed as { message?: unknown }).message)
              : `Kubernetes API вернул HTTP ${status}`;
            reject(new WorkerLauncherError(status === 403 ? 503 : 502, message));
            return;
          }
          resolve(parsed as T);
        });
      });
      request.setTimeout(8_000, () => request.destroy(new Error("Kubernetes API не ответил за 8 секунд")));
      request.on("error", (error) => reject(new WorkerLauncherError(502, safeErrorMessage(error))));
      if (payload) request.write(payload);
      request.end();
    });
  }
}

export class KubernetesLocalWorkerLauncher implements LocalWorkerLauncher {
  private readonly collectionPath: string;

  constructor(
    private readonly options: LauncherOptions,
    private readonly transport: KubernetesTransport,
  ) {
    this.collectionPath = `/apis/apps/v1/namespaces/${encodeURIComponent(options.namespace)}/deployments`;
  }

  async snapshot(): Promise<LocalWorkerLauncherSnapshot> {
    const [deployments, discovery] = await Promise.all([
      this.listDeployments(),
      this.discoverModels(),
    ]);
    return {
      available: true,
      reason: null,
      runtime: "docker-desktop-kubernetes",
      workerImage: this.options.workerImage,
      modelBaseUrl: this.options.modelBaseUrl,
      maxWorkersPerLaunch: this.options.maxWorkersPerLaunch,
      defaultWebEnabled: this.options.defaultWebEnabled,
      models: discovery.models,
      modelDiscoveryError: discovery.error,
      pools: groupDeployments(deployments),
    };
  }

  async launch(rawInput: LaunchLocalWorkersInput): Promise<LocalWorkerPool> {
    const input = normalizeLaunchInput(rawInput, this.options);
    const poolId = randomUUID().replaceAll("-", "").slice(0, 12);
    const createdAt = new Date().toISOString();
    const createdNames: string[] = [];

    try {
      for (let index = 0; index < input.workers; index += 1) {
        const deployment = buildWorkerDeployment(this.options, input, poolId, createdAt, index);
        const created = await this.transport.request<KubernetesDeployment>("POST", this.collectionPath, deployment);
        const name = created.metadata?.name;
        if (name) createdNames.push(name);
      }
    } catch (error) {
      await Promise.allSettled(createdNames.map((name) => this.transport.request(
        "DELETE",
        `${this.collectionPath}/${encodeURIComponent(name)}`,
        { propagationPolicy: "Background" },
      )));
      throw error;
    }

    const pool = (await this.poolsById(poolId))[0];
    if (!pool) throw new WorkerLauncherError(502, "Kubernetes создал workers, но пул не найден");
    return pool;
  }

  async startPool(poolId: string): Promise<LocalWorkerPool> {
    return this.setPoolRunning(poolId, true);
  }

  async stopPool(poolId: string): Promise<LocalWorkerPool> {
    return this.setPoolRunning(poolId, false);
  }

  async deletePool(rawPoolId: string): Promise<LocalWorkerPool> {
    const poolId = validatePoolId(rawPoolId);
    const deployments = await this.poolDeployments(poolId);
    if (deployments.length === 0) throw new WorkerLauncherError(404, "Worker-пул не найден");
    const pool = deploymentPool(deployments);
    for (const deployment of deployments) {
      const name = deployment.metadata?.name;
      if (!name) continue;
      await this.transport.request(
        "DELETE",
        `${this.collectionPath}/${encodeURIComponent(name)}`,
        { propagationPolicy: "Foreground" },
      );
    }
    return pool;
  }

  private async setPoolRunning(rawPoolId: string, running: boolean): Promise<LocalWorkerPool> {
    const poolId = validatePoolId(rawPoolId);
    const deployments = await this.poolDeployments(poolId);
    if (deployments.length === 0) throw new WorkerLauncherError(404, "Worker-пул не найден");
    for (const deployment of deployments) {
      const name = deployment.metadata?.name;
      if (!name) continue;
      await this.transport.request(
        "PATCH",
        `${this.collectionPath}/${encodeURIComponent(name)}`,
        { spec: { replicas: running ? 1 : 0 } },
        "application/merge-patch+json",
      );
    }
    const pool = (await this.poolsById(poolId))[0];
    if (!pool) throw new WorkerLauncherError(502, "Не удалось прочитать worker-пул после изменения");
    return pool;
  }

  private async listDeployments(labelSelector = `${MANAGED_LABEL}=true`): Promise<KubernetesDeployment[]> {
    const response = await this.transport.request<KubernetesDeploymentList>(
      "GET",
      `${this.collectionPath}?labelSelector=${encodeURIComponent(labelSelector)}`,
    );
    return Array.isArray(response.items) ? response.items : [];
  }

  private poolDeployments(poolId: string): Promise<KubernetesDeployment[]> {
    return this.listDeployments(`${MANAGED_LABEL}=true,${POOL_LABEL}=${poolId}`);
  }

  private async poolsById(poolId: string): Promise<LocalWorkerPool[]> {
    return groupDeployments(await this.poolDeployments(poolId));
  }

  private async discoverModels(): Promise<{ models: LocalModelInfo[]; error: string | null }> {
    if (!this.options.modelDiscoveryUrl) return { models: [], error: "Discovery локальных моделей отключён" };
    try {
      const response = await fetch(this.options.modelDiscoveryUrl, {
        headers: { accept: "application/json" },
        signal: AbortSignal.timeout(4_000),
      });
      if (!response.ok) throw new Error(`model server вернул HTTP ${response.status}`);
      const raw = await response.text();
      if (Buffer.byteLength(raw, "utf8") > MAX_MODEL_RESPONSE_BYTES) throw new Error("список моделей слишком большой");
      const parsed = JSON.parse(raw) as { models?: Array<Record<string, unknown>> };
      const models = (Array.isArray(parsed.models) ? parsed.models : [])
        .flatMap((model): LocalModelInfo[] => {
          const name = typeof model.name === "string" ? model.name : typeof model.model === "string" ? model.model : "";
          const capabilities = Array.isArray(model.capabilities) ? model.capabilities.map(String) : [];
          if (!name || /embed/i.test(name) || (capabilities.length > 0 && !capabilities.includes("completion"))) return [];
          return [{
            name,
            sizeBytes: typeof model.size === "number" && Number.isFinite(model.size) ? model.size : null,
            modifiedAt: typeof model.modified_at === "string" ? model.modified_at : null,
          }];
        })
        .sort((left, right) => (left.sizeBytes ?? Number.MAX_SAFE_INTEGER) - (right.sizeBytes ?? Number.MAX_SAFE_INTEGER)
          || left.name.localeCompare(right.name));
      return { models, error: null };
    } catch (error) {
      return { models: [], error: `Не удалось получить модели: ${safeErrorMessage(error)}` };
    }
  }
}

class DisabledLocalWorkerLauncher implements LocalWorkerLauncher {
  constructor(
    private readonly options: LauncherOptions,
    private readonly reason: string,
  ) {}

  async snapshot(): Promise<LocalWorkerLauncherSnapshot> {
    return {
      available: false,
      reason: this.reason,
      runtime: "docker-desktop-kubernetes",
      workerImage: this.options.workerImage,
      modelBaseUrl: this.options.modelBaseUrl,
      maxWorkersPerLaunch: this.options.maxWorkersPerLaunch,
      defaultWebEnabled: this.options.defaultWebEnabled,
      models: [],
      modelDiscoveryError: null,
      pools: [],
    };
  }

  async launch(_input: LaunchLocalWorkersInput): Promise<LocalWorkerPool> {
    throw new WorkerLauncherError(503, this.reason);
  }

  async startPool(_poolId: string): Promise<LocalWorkerPool> {
    throw new WorkerLauncherError(503, this.reason);
  }

  async stopPool(_poolId: string): Promise<LocalWorkerPool> {
    throw new WorkerLauncherError(503, this.reason);
  }

  async deletePool(_poolId: string): Promise<LocalWorkerPool> {
    throw new WorkerLauncherError(503, this.reason);
  }
}

export function createLocalWorkerLauncher(config: CoordinatorConfig): LocalWorkerLauncher {
  const options: LauncherOptions = {
    namespace: config.localWorkerNamespace,
    workerImage: config.localWorkerImage,
    workerConfigMap: config.localWorkerConfigMap,
    workerSecret: config.localWorkerSecret,
    modelBaseUrl: config.localWorkerModelBaseUrl,
    modelDiscoveryUrl: config.localWorkerModelDiscoveryUrl,
    embeddingModels: config.localWorkerEmbeddingModels,
    defaultWebEnabled: config.localWorkerDefaultWebEnabled,
    maxWorkersPerLaunch: config.localWorkerMaxPerLaunch,
  };
  if (!config.localWorkerLauncherEnabled) {
    return new DisabledLocalWorkerLauncher(options, "Локальный launcher выключен в конфигурации coordinator");
  }
  if (!fs.existsSync(`${SERVICE_ACCOUNT_DIR}/token`) || !fs.existsSync(`${SERVICE_ACCOUNT_DIR}/ca.crt`)) {
    return new DisabledLocalWorkerLauncher(options, "Coordinator не запущен с Kubernetes service account для worker launcher");
  }
  return new KubernetesLocalWorkerLauncher(options, new InClusterKubernetesTransport());
}
