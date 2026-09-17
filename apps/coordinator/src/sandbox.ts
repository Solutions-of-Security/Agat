import { createHash } from "node:crypto";
import fs from "node:fs";
import { BlockList, isIP } from "node:net";

import type { CoordinatorConfig } from "./config.js";
import {
  InClusterKubernetesTransport,
  type KubernetesTransport,
} from "./local-workers.js";
import type {
  McpCatalogTool,
  McpSandboxEgressRule,
  McpSandboxProfile,
  McpSandboxProfileInput,
  McpSandboxSummary,
  McpTransport,
} from "./types.js";

const SERVICE_ACCOUNT_DIR = "/var/run/secrets/kubernetes.io/serviceaccount";
const MAX_WASM_BYTES = 512 * 1024;
const MAX_INPUT_BYTES = 128 * 1024;
const MAX_SECRET_BYTES = 128 * 1024;
const MANAGED_LABEL = "sandbox.agat.dev/managed";
const CALL_LABEL = "sandbox.agat.dev/call";
const BLOCKED_SANDBOX_IPV4 = new BlockList();
const BLOCKED_SANDBOX_IPV6 = new BlockList();

for (const [network, prefix] of [
  ["0.0.0.0", 8], ["10.0.0.0", 8], ["100.64.0.0", 10], ["127.0.0.0", 8],
  ["169.254.0.0", 16], ["172.16.0.0", 12], ["192.0.0.0", 24], ["192.0.2.0", 24],
  ["192.168.0.0", 16], ["192.88.99.0", 24], ["198.18.0.0", 15], ["198.51.100.0", 24],
  ["203.0.113.0", 24], ["224.0.0.0", 4], ["240.0.0.0", 4],
] as const) {
  BLOCKED_SANDBOX_IPV4.addSubnet(network, prefix, "ipv4");
}
for (const [network, prefix] of [
  ["::", 128], ["::1", 128], ["::ffff:0:0", 96], ["64:ff9b::", 96], ["100::", 64],
  ["fc00::", 7], ["fe80::", 10], ["ff00::", 8], ["2001::", 23], ["2001:db8::", 32],
  ["2002::", 16], ["3ffe::", 16], ["3fff::", 20],
] as const) {
  BLOCKED_SANDBOX_IPV6.addSubnet(network, prefix, "ipv6");
}

type JsonObject = Record<string, unknown>;

export interface SandboxSnapshot {
  enabled: boolean;
  available: boolean;
  reason: string | null;
  namespace: string;
  wasiImage: string;
  runtimeClass: string | null;
  networkPolicyEnforced: boolean;
}

export interface SandboxExecution {
  callId: string;
  transport: Exclude<McpTransport, "http">;
  profile: McpSandboxProfile;
  arguments: Record<string, unknown>;
  credential: null | { type: string; data: Record<string, string> };
  maxResponseBytes: number;
  assertAllowed: () => void;
}

export interface SandboxExecutor {
  snapshot(): SandboxSnapshot;
  execute(input: SandboxExecution): Promise<unknown>;
}

export interface SandboxOptions {
  enabled: boolean;
  namespace: string;
  wasiImage: string;
  runtimeClass: string;
  networkPolicyEnforced: boolean;
}

function requiredText(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${field} обязателен`);
  const normalized = value.trim();
  if (normalized.length > maxLength || /[\r\n\0]/.test(normalized)) {
    throw new Error(`${field} содержит недопустимые символы или слишком длинное значение`);
  }
  return normalized;
}

function boundedInteger(value: unknown, min: number, max: number, fallback: number): number {
  const parsed = typeof value === "number" ? value : Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

function plainObject(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${field} должен быть JSON-объектом`);
  const encoded = JSON.stringify(value);
  if (Buffer.byteLength(encoded, "utf8") > 64 * 1024) throw new Error(`${field} превышает 64 KiB`);
  return JSON.parse(encoded) as Record<string, unknown>;
}

function sha256(value: string | Buffer): string {
  return createHash("sha256").update(value).digest("hex");
}

function normalizeModule(value: unknown, current: McpSandboxProfile | undefined): { base64: string; sha256: string } {
  if ((value === undefined || value === null || value === "") && current?.moduleBase64 && current.moduleSha256) {
    return { base64: current.moduleBase64, sha256: current.moduleSha256 };
  }
  if (typeof value !== "string" || !value || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) {
    throw new Error("WASI moduleBase64 должен содержать корректный base64");
  }
  const bytes = Buffer.from(value, "base64");
  if (bytes.length < 8 || bytes.length > MAX_WASM_BYTES) {
    throw new Error(`WASI module должен иметь размер от 8 байт до ${MAX_WASM_BYTES} байт`);
  }
  if (!bytes.subarray(0, 8).equals(Buffer.from([0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00]))) {
    throw new Error("WASI module не содержит WebAssembly magic/version 1");
  }
  const canonical = bytes.toString("base64");
  return { base64: canonical, sha256: sha256(bytes) };
}

export function isPublicSandboxIp(ip: string): boolean {
  const family = isIP(ip);
  if (family === 4) return !BLOCKED_SANDBOX_IPV4.check(ip, "ipv4");
  if (family === 6) {
    const firstHextet = Number.parseInt(ip.split(":", 1)[0] || "0", 16);
    const currentGlobalUnicast = firstHextet >= 0x2000 && firstHextet <= 0x3fff;
    return currentGlobalUnicast && !BLOCKED_SANDBOX_IPV6.check(ip, "ipv6");
  }
  return false;
}

function normalizeEgress(value: unknown): McpSandboxEgressRule[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 16) throw new Error("egress должен содержать не более 16 exact IP rules");
  const seen = new Set<string>();
  return value.map((raw, index) => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error(`egress[${index}] некорректен`);
    const entry = raw as Record<string, unknown>;
    const ip = requiredText(entry.ip, `egress[${index}].ip`, 45);
    if (!isPublicSandboxIp(ip)) throw new Error(`egress[${index}].ip должен быть публичным exact IP`);
    const port = typeof entry.port === "number" ? entry.port : Number(entry.port);
    if (!Number.isInteger(port) || port < 1 || port > 65_535) {
      throw new Error(`egress[${index}].port должен быть целым числом от 1 до 65535`);
    }
    const key = `${ip}:${port}`;
    if (seen.has(key)) throw new Error(`Дублирующий egress rule ${key}`);
    seen.add(key);
    return { ip, port };
  }).sort((left, right) => left.ip.localeCompare(right.ip) || left.port - right.port);
}

function normalizedTool(raw: unknown): McpSandboxProfile["tool"] {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("sandbox.tool обязателен");
  const value = raw as Record<string, unknown>;
  const name = requiredText(value.name, "sandbox.tool.name", 128);
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(name)) {
    throw new Error("sandbox.tool.name должен содержать только A-Z, a-z, 0-9, _ и -");
  }
  const description = typeof value.description === "string" ? value.description.trim().slice(0, 8_000) : "";
  const title = value.title === null || value.title === undefined || value.title === ""
    ? null
    : requiredText(value.title, "sandbox.tool.title", 200);
  const outputSchema = value.outputSchema === null || value.outputSchema === undefined
    ? null
    : plainObject(value.outputSchema, "sandbox.tool.outputSchema");
  const annotations = value.annotations === null || value.annotations === undefined
    ? null
    : plainObject(value.annotations, "sandbox.tool.annotations");
  return {
    name,
    title,
    description,
    inputSchema: plainObject(value.inputSchema, "sandbox.tool.inputSchema"),
    outputSchema,
    annotations,
  };
}

export function normalizeSandboxProfile(
  transport: Exclude<McpTransport, "http">,
  input: McpSandboxProfileInput | null | undefined,
  current?: McpSandboxProfile,
): McpSandboxProfile {
  if (!input && current) return current;
  if (!input || typeof input !== "object") throw new Error("sandbox profile обязателен для изолированного MCP tool");
  const tool = normalizedTool(input.tool ?? current?.tool);
  const timeoutSeconds = boundedInteger(input.timeoutSeconds ?? current?.timeoutSeconds, 1, 120, 30);
  const cpuMillis = boundedInteger(input.cpuMillis ?? current?.cpuMillis, 25, 4_000, 500);
  const memoryMiB = boundedInteger(input.memoryMiB ?? current?.memoryMiB, 32, 2_048, 128);
  const egress = normalizeEgress(input.egress ?? current?.egress);
  let moduleBase64: string | null = null;
  let moduleSha256: string | null = null;
  let image: string | null = null;
  let command: string[] = [];
  if (transport === "wasi") {
    if (egress.length > 0) throw new Error("WASI profile не поддерживает сетевой egress");
    const module = normalizeModule(input.moduleBase64, current);
    moduleBase64 = module.base64;
    moduleSha256 = module.sha256;
  } else {
    image = requiredText(input.image ?? current?.image, "sandbox.image", 512);
    if (!/^[a-zA-Z0-9][a-zA-Z0-9._/:-]*@sha256:[a-f0-9]{64}$/.test(image)) {
      throw new Error("Container image должен быть закреплён полным @sha256 digest");
    }
    const rawCommand = input.command ?? current?.command;
    if (!Array.isArray(rawCommand) || rawCommand.length < 1 || rawCommand.length > 16) {
      throw new Error("sandbox.command должен содержать от 1 до 16 exec-аргументов без shell");
    }
    command = rawCommand.map((part, index) => requiredText(part, `sandbox.command[${index}]`, 512));
  }
  const hashInput = JSON.stringify({
    transport,
    tool,
    moduleSha256,
    image,
    command,
    timeoutSeconds,
    cpuMillis,
    memoryMiB,
    egress,
  });
  return {
    tool,
    moduleBase64,
    moduleSha256,
    image,
    command,
    timeoutSeconds,
    cpuMillis,
    memoryMiB,
    egress,
    profileSha256: sha256(hashInput),
  };
}

export function sandboxSummary(profile: McpSandboxProfile): McpSandboxSummary {
  const { moduleBase64: _moduleBase64, ...summary } = profile;
  return summary;
}

function resourceName(callId: string): string {
  const id = callId.toLowerCase().replace(/[^a-z0-9]+/g, "").slice(0, 20);
  if (!id) throw new Error("Некорректный sandbox call id");
  return `agat-tool-${id}`;
}

function callLabels(callId: string): Record<string, string> {
  return {
    "app.kubernetes.io/name": "agat",
    "app.kubernetes.io/component": "tool-sandbox",
    "app.kubernetes.io/managed-by": "agat-coordinator",
    [MANAGED_LABEL]: "true",
    [CALL_LABEL]: resourceName(callId).slice(-20),
  };
}

export function buildSandboxSecret(input: SandboxExecution, namespace: string): JsonObject {
  const inputJson = JSON.stringify(input.arguments);
  const credentialJson = JSON.stringify(input.credential ?? {});
  if (Buffer.byteLength(inputJson, "utf8") > MAX_INPUT_BYTES) throw new Error(`Sandbox arguments превышают ${MAX_INPUT_BYTES} байт`);
  if (Buffer.byteLength(credentialJson, "utf8") > MAX_SECRET_BYTES) throw new Error(`Scoped credentials превышают ${MAX_SECRET_BYTES} байт`);
  const data = {
    "input.json": Buffer.from(inputJson, "utf8").toString("base64"),
    "credential.json": Buffer.from(credentialJson, "utf8").toString("base64"),
    ...(input.transport === "wasi" && input.profile.moduleBase64
      ? { "module.wasm": input.profile.moduleBase64 }
      : {}),
  };
  if (Object.values(data).reduce((sum, value) => sum + Buffer.byteLength(value, "utf8"), 0) > 900 * 1024) {
    throw new Error("Sandbox invocation не помещается в безопасный лимит Kubernetes Secret");
  }
  return {
    apiVersion: "v1",
    kind: "Secret",
    metadata: { name: resourceName(input.callId), namespace, labels: callLabels(input.callId) },
    immutable: true,
    type: "Opaque",
    data,
  };
}

export function buildSandboxNetworkPolicy(input: SandboxExecution, namespace: string): JsonObject {
  return {
    apiVersion: "networking.k8s.io/v1",
    kind: "NetworkPolicy",
    metadata: { name: resourceName(input.callId), namespace, labels: callLabels(input.callId) },
    spec: {
      podSelector: { matchLabels: { [CALL_LABEL]: resourceName(input.callId).slice(-20) } },
      policyTypes: ["Ingress", "Egress"],
      ingress: [],
      egress: input.profile.egress.map((rule) => ({
        to: [{ ipBlock: { cidr: `${rule.ip}/${isIP(rule.ip) === 4 ? 32 : 128}` } }],
        ports: [{ protocol: "TCP", port: rule.port }],
      })),
    },
  };
}

export function buildSandboxJob(input: SandboxExecution, options: SandboxOptions): JsonObject {
  const name = resourceName(input.callId);
  const labels = callLabels(input.callId);
  const container = input.transport === "wasi"
    ? {
        name: "tool",
        image: options.wasiImage,
        imagePullPolicy: "IfNotPresent",
        env: [
          { name: "AGAT_WASM_MODULE", value: "/run/agat/module.wasm" },
          { name: "AGAT_TOOL_INPUT_FILE", value: "/run/agat/input.json" },
          { name: "AGAT_TOOL_CREDENTIAL_FILE", value: "/run/agat/credential.json" },
          { name: "AGAT_WASM_FUEL", value: String(Math.max(1_000_000, input.profile.cpuMillis * input.profile.timeoutSeconds * 10_000)) },
          { name: "AGAT_MAX_OUTPUT_BYTES", value: String(input.maxResponseBytes) },
        ],
      }
    : {
        name: "tool",
        image: input.profile.image,
        imagePullPolicy: "IfNotPresent",
        command: input.profile.command,
        env: [
          { name: "AGAT_TOOL_INPUT_FILE", value: "/run/agat/input.json" },
          { name: "AGAT_TOOL_CREDENTIAL_FILE", value: "/run/agat/credential.json" },
          { name: "AGAT_MAX_OUTPUT_BYTES", value: String(input.maxResponseBytes) },
        ],
      };
  return {
    apiVersion: "batch/v1",
    kind: "Job",
    metadata: { name, namespace: options.namespace, labels },
    spec: {
      backoffLimit: 0,
      activeDeadlineSeconds: input.profile.timeoutSeconds,
      ttlSecondsAfterFinished: 60,
      template: {
        metadata: { labels },
        spec: {
          automountServiceAccountToken: false,
          enableServiceLinks: false,
          restartPolicy: "Never",
          terminationGracePeriodSeconds: 1,
          ...(options.runtimeClass ? { runtimeClassName: options.runtimeClass } : {}),
          securityContext: {
            runAsNonRoot: true,
            runAsUser: 65_532,
            runAsGroup: 65_532,
            fsGroup: 65_532,
            seccompProfile: { type: "RuntimeDefault" },
          },
          containers: [{
            ...container,
            resources: {
              requests: { cpu: `${Math.min(100, input.profile.cpuMillis)}m`, memory: `${Math.min(64, input.profile.memoryMiB)}Mi` },
              limits: { cpu: `${input.profile.cpuMillis}m`, memory: `${input.profile.memoryMiB}Mi` },
            },
            securityContext: {
              allowPrivilegeEscalation: false,
              readOnlyRootFilesystem: true,
              capabilities: { drop: ["ALL"] },
            },
            volumeMounts: [
              { name: "invocation", mountPath: "/run/agat", readOnly: true },
              { name: "tmp", mountPath: "/tmp" },
            ],
          }],
          volumes: [
            { name: "invocation", secret: { secretName: name, defaultMode: 0o440 } },
            { name: "tmp", emptyDir: { medium: "Memory", sizeLimit: "16Mi" } },
          ],
        },
      },
    },
  };
}

interface JobStatus {
  status?: {
    succeeded?: number;
    failed?: number;
    conditions?: Array<{ type?: string; status?: string; reason?: string; message?: string }>;
  };
}

interface PodList {
  items?: Array<{ metadata?: { name?: string } }>;
}

export class KubernetesSandboxExecutor implements SandboxExecutor {
  constructor(
    private readonly options: SandboxOptions,
    private readonly transport: KubernetesTransport,
    private readonly wait: (milliseconds: number) => Promise<void> = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
  ) {}

  snapshot(): SandboxSnapshot {
    return {
      enabled: this.options.enabled,
      available: this.options.enabled,
      reason: this.options.enabled ? null : "Изолированное выполнение выключено",
      namespace: this.options.namespace,
      wasiImage: this.options.wasiImage,
      runtimeClass: this.options.runtimeClass || null,
      networkPolicyEnforced: this.options.networkPolicyEnforced,
    };
  }

  async execute(input: SandboxExecution): Promise<unknown> {
    if (!this.options.enabled) throw new Error("Изолированное выполнение tools выключено");
    if (input.transport === "container" && !this.options.networkPolicyEnforced) {
      throw new Error("Container sandbox заблокирован: CNI enforcement NetworkPolicy не подтверждён оператором");
    }
    input.assertAllowed();
    const name = resourceName(input.callId);
    const namespace = encodeURIComponent(this.options.namespace);
    const secretPath = `/api/v1/namespaces/${namespace}/secrets`;
    const networkPolicyPath = `/apis/networking.k8s.io/v1/namespaces/${namespace}/networkpolicies`;
    const jobPath = `/apis/batch/v1/namespaces/${namespace}/jobs`;
    let secretMayExist = false;
    let policyMayExist = false;
    let jobMayExist = false;
    let jobTerminal = false;
    try {
      const secret = buildSandboxSecret(input, this.options.namespace);
      secretMayExist = true;
      await this.transport.request("POST", secretPath, secret);
      const networkPolicy = buildSandboxNetworkPolicy(input, this.options.namespace);
      policyMayExist = true;
      await this.transport.request("POST", networkPolicyPath, networkPolicy);
      input.assertAllowed();
      const jobSpec = buildSandboxJob(input, this.options);
      jobMayExist = true;
      await this.transport.request("POST", jobPath, jobSpec);
      const deadline = Date.now() + (input.profile.timeoutSeconds + 5) * 1_000;
      let succeeded = false;
      while (Date.now() < deadline) {
        input.assertAllowed();
        const job = await this.transport.request<JobStatus>("GET", `${jobPath}/${encodeURIComponent(name)}`);
        if ((job.status?.succeeded ?? 0) > 0) {
          jobTerminal = true;
          succeeded = true;
          break;
        }
        if ((job.status?.failed ?? 0) > 0) {
          jobTerminal = true;
          const condition = job.status?.conditions?.find((entry) => entry.status === "True");
          throw new Error(`Sandbox Job завершился с ошибкой${condition?.reason ? `: ${condition.reason}` : ""}`);
        }
        await this.wait(200);
      }
      if (!succeeded) throw new Error("Sandbox Job не завершился в установленный срок");
      input.assertAllowed();
      const pods = await this.transport.request<PodList>(
        "GET",
        `/api/v1/namespaces/${namespace}/pods?labelSelector=${encodeURIComponent(`${CALL_LABEL}=${name.slice(-20)}`)}`,
      );
      const podName = pods.items?.[0]?.metadata?.name;
      if (!podName) throw new Error("Sandbox Job завершён, но Pod не найден");
      const result = await this.transport.request<unknown>(
        "GET",
        `/api/v1/namespaces/${namespace}/pods/${encodeURIComponent(podName)}/log?container=tool&limitBytes=${input.maxResponseBytes + 1}`,
      );
      const encoded = JSON.stringify(result);
      if (encoded === undefined || Buffer.byteLength(encoded, "utf8") > input.maxResponseBytes) {
        throw new Error(`Sandbox output превышает ${input.maxResponseBytes} байт`);
      }
      return result;
    } finally {
      let podTerminationConfirmed = !jobMayExist || jobTerminal;
      if (jobMayExist) {
        await this.transport.request("DELETE", `${jobPath}/${encodeURIComponent(name)}`, {
          apiVersion: "v1",
          kind: "DeleteOptions",
          propagationPolicy: "Foreground",
          gracePeriodSeconds: 0,
        }).catch(() => undefined);
        if (!podTerminationConfirmed) {
          const podPath = `/api/v1/namespaces/${namespace}/pods?labelSelector=${encodeURIComponent(`${CALL_LABEL}=${name.slice(-20)}`)}`;
          for (let attempt = 0; attempt < 25; attempt += 1) {
            let pods: PodList;
            try {
              pods = await this.transport.request<PodList>("GET", podPath);
            } catch {
              break;
            }
            if ((pods.items?.length ?? 0) === 0) {
              podTerminationConfirmed = true;
              break;
            }
            await this.wait(200);
          }
        }
      }
      if (secretMayExist) {
        await this.transport.request("DELETE", `${secretPath}/${encodeURIComponent(name)}`).catch(() => undefined);
      }
      // Never remove the egress boundary while an aborted Pod may still be alive.
      if (policyMayExist && podTerminationConfirmed) {
        await this.transport.request("DELETE", `${networkPolicyPath}/${encodeURIComponent(name)}`).catch(() => undefined);
      }
    }
  }
}

class DisabledSandboxExecutor implements SandboxExecutor {
  constructor(private readonly options: SandboxOptions, private readonly reason: string) {}

  snapshot(): SandboxSnapshot {
    return {
      enabled: this.options.enabled,
      available: false,
      reason: this.reason,
      namespace: this.options.namespace,
      wasiImage: this.options.wasiImage,
      runtimeClass: this.options.runtimeClass || null,
      networkPolicyEnforced: this.options.networkPolicyEnforced,
    };
  }

  async execute(_input: SandboxExecution): Promise<unknown> {
    throw new Error(this.reason);
  }
}

export function createSandboxExecutor(config: CoordinatorConfig): SandboxExecutor {
  const options: SandboxOptions = {
    enabled: config.sandboxEnabled,
    namespace: config.sandboxNamespace,
    wasiImage: config.sandboxWasiImage,
    runtimeClass: config.sandboxRuntimeClass,
    networkPolicyEnforced: config.sandboxNetworkPolicyEnforced,
  };
  if (!config.sandboxEnabled) return new DisabledSandboxExecutor(options, "Изолированное выполнение tools выключено");
  if (!/^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$/.test(options.namespace)) {
    return new DisabledSandboxExecutor(options, "AGAT_SANDBOX_NAMESPACE не является Kubernetes namespace");
  }
  if (!options.wasiImage) return new DisabledSandboxExecutor(options, "AGAT_SANDBOX_WASI_IMAGE не настроен");
  if (options.runtimeClass && !/^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$/.test(options.runtimeClass)) {
    return new DisabledSandboxExecutor(options, "AGAT_SANDBOX_RUNTIME_CLASS некорректен");
  }
  if (!fs.existsSync(`${SERVICE_ACCOUNT_DIR}/token`) || !fs.existsSync(`${SERVICE_ACCOUNT_DIR}/ca.crt`)) {
    return new DisabledSandboxExecutor(options, "Coordinator не запущен с Kubernetes service account для sandbox");
  }
  return new KubernetesSandboxExecutor(options, new InClusterKubernetesTransport());
}

export function sandboxCatalogTool(profile: McpSandboxProfile, publicName: string): McpCatalogTool {
  return {
    name: profile.tool.name,
    publicName,
    title: profile.tool.title ?? null,
    description: profile.tool.description ?? "",
    inputSchema: profile.tool.inputSchema,
    outputSchema: profile.tool.outputSchema ?? null,
    annotations: profile.tool.annotations ?? null,
  };
}
