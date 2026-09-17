import fs from "node:fs";
import os from "node:os";

import type { NativeConnectionOptions, TLSConfig, WorkerOptions } from "@temporalio/worker";

export type TemporalTarget = "local" | "cloud" | "self-hosted";
export type TemporalDeploymentRing = "stable" | "canary";
export type TemporalVersioningBehavior = "PINNED" | "AUTO_UPGRADE";

export interface TemporalWorkerConfig {
  address: string;
  namespace: string;
  taskQueue: string;
  target: TemporalTarget;
  tls: boolean;
  apiKey: string;
  caCertPath: string;
  clientCertPath: string;
  clientKeyPath: string;
  serverNameOverride: string;
  coordinatorUrl: string;
  internalToken: string;
  metricsAddress: string;
  versioningEnabled: boolean;
  deploymentName: string;
  buildId: string;
  deploymentRing: TemporalDeploymentRing;
  defaultVersioningBehavior: TemporalVersioningBehavior;
  identity: string;
  maxConcurrentWorkflowTasks: number;
  maxConcurrentActivityTasks: number;
  shutdownGraceSeconds: number;
}

function booleanFromEnv(value: string | undefined, fallback: boolean): boolean {
  if (value === undefined || value.trim() === "") return fallback;
  const normalized = value.trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  throw new Error(`Некорректное boolean-значение: ${value}`);
}

function integerFromEnv(value: string | undefined, fallback: number, field: string, min: number, max: number): number {
  if (value === undefined || value.trim() === "") return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
    throw new Error(`${field} должен быть целым числом от ${min} до ${max}`);
  }
  return parsed;
}

function boundedText(value: string | undefined, fallback: string, field: string, maxLength = 200): string {
  const normalized = (value ?? fallback).trim();
  if (!normalized || normalized.length > maxLength || /[\r\n\0]/.test(normalized)) {
    throw new Error(`${field} должен содержать от 1 до ${maxLength} безопасных символов`);
  }
  return normalized;
}

function temporalAddress(value: string | undefined): string {
  const address = boundedText(value, "agat-temporal:7233", "AGAT_TEMPORAL_ADDRESS", 300);
  if (address.includes("://") || /[/?#@\s]/.test(address)) {
    throw new Error("AGAT_TEMPORAL_ADDRESS должен иметь формат host:port без URL-схемы, пути и credentials");
  }
  return address;
}

function temporalTarget(value: string | undefined): TemporalTarget {
  const normalized = (value ?? "local").trim().toLowerCase();
  if (normalized === "local" || normalized === "cloud" || normalized === "self-hosted") return normalized;
  throw new Error("AGAT_TEMPORAL_TARGET должен быть local, cloud или self-hosted");
}

function deploymentRing(value: string | undefined): TemporalDeploymentRing {
  const normalized = (value ?? "stable").trim().toLowerCase();
  if (normalized === "stable" || normalized === "canary") return normalized;
  throw new Error("AGAT_TEMPORAL_DEPLOYMENT_RING должен быть stable или canary");
}

function versioningBehavior(value: string | undefined): TemporalVersioningBehavior {
  const normalized = (value ?? "PINNED").trim().toUpperCase();
  if (normalized === "PINNED" || normalized === "AUTO_UPGRADE") return normalized;
  throw new Error("AGAT_TEMPORAL_VERSIONING_BEHAVIOR должен быть PINNED или AUTO_UPGRADE");
}

function optionalSafeText(value: string | undefined, field: string, maxLength: number): string {
  const normalized = (value ?? "").trim();
  if (normalized.length > maxLength || /[\r\n\0]/.test(normalized)) {
    throw new Error(`${field} содержит недопустимые управляющие символы или слишком длинное значение`);
  }
  return normalized;
}

function optionalPath(value: string | undefined, field: string): string {
  return optionalSafeText(value, field, 4_096);
}

function validateTls(config: TemporalWorkerConfig): void {
  const hasClientCert = Boolean(config.clientCertPath);
  const hasClientKey = Boolean(config.clientKeyPath);
  if (hasClientCert !== hasClientKey) {
    throw new Error("AGAT_TEMPORAL_CLIENT_CERT_PATH и AGAT_TEMPORAL_CLIENT_KEY_PATH должны задаваться вместе");
  }
  if ((config.caCertPath || hasClientCert || config.serverNameOverride) && !config.tls) {
    throw new Error("TLS-файлы и server-name override нельзя использовать при AGAT_TEMPORAL_TLS=false");
  }
  if (config.apiKey && !config.tls) {
    throw new Error("AGAT_TEMPORAL_API_KEY разрешён только при включённом TLS");
  }
  if (config.target === "cloud") {
    if (!config.tls) throw new Error("Temporal Cloud требует AGAT_TEMPORAL_TLS=true");
    if (!config.apiKey) throw new Error("Temporal Cloud требует AGAT_TEMPORAL_API_KEY");
  }
  if (config.target === "self-hosted") {
    if (!config.tls) throw new Error("Production self-hosted Temporal требует AGAT_TEMPORAL_TLS=true");
    if (!config.apiKey && !hasClientCert) {
      throw new Error("Self-hosted Temporal требует API key либо пару клиентского сертификата и ключа");
    }
  }
}

function validateVersioning(config: TemporalWorkerConfig): void {
  if (config.target !== "local" && !config.versioningEnabled) {
    throw new Error("Production Temporal worker должен включать AGAT_TEMPORAL_VERSIONING_ENABLED=true");
  }
  if (!config.versioningEnabled) return;
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,126}$/.test(config.deploymentName)) {
    throw new Error("AGAT_TEMPORAL_DEPLOYMENT_NAME содержит недопустимые символы");
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,254}$/.test(config.buildId)) {
    throw new Error("AGAT_TEMPORAL_BUILD_ID обязателен и должен однозначно идентифицировать immutable build");
  }
  if (config.target !== "local" && config.buildId === "local-dev") {
    throw new Error("Production Temporal worker требует явный immutable AGAT_TEMPORAL_BUILD_ID");
  }
}

export function loadTemporalWorkerConfig(env: NodeJS.ProcessEnv = process.env): TemporalWorkerConfig {
  const target = temporalTarget(env.AGAT_TEMPORAL_TARGET);
  const tls = booleanFromEnv(env.AGAT_TEMPORAL_TLS, target !== "local");
  const versioningEnabled = booleanFromEnv(env.AGAT_TEMPORAL_VERSIONING_ENABLED, target !== "local");
  const deploymentName = boundedText(
    env.AGAT_TEMPORAL_DEPLOYMENT_NAME,
    "agat-processes",
    "AGAT_TEMPORAL_DEPLOYMENT_NAME",
    127,
  );
  const buildId = boundedText(
    env.AGAT_TEMPORAL_BUILD_ID ?? env.AGAT_BUILD_ID,
    "local-dev",
    "AGAT_TEMPORAL_BUILD_ID",
    255,
  );
  const config: TemporalWorkerConfig = {
    address: temporalAddress(env.AGAT_TEMPORAL_ADDRESS),
    namespace: boundedText(env.AGAT_TEMPORAL_NAMESPACE, "agat", "AGAT_TEMPORAL_NAMESPACE", 255),
    taskQueue: boundedText(env.AGAT_TEMPORAL_TASK_QUEUE, "agat-processes-v1", "AGAT_TEMPORAL_TASK_QUEUE", 255),
    target,
    tls,
    apiKey: optionalSafeText(env.AGAT_TEMPORAL_API_KEY, "AGAT_TEMPORAL_API_KEY", 8_192),
    caCertPath: optionalPath(env.AGAT_TEMPORAL_CA_CERT_PATH, "AGAT_TEMPORAL_CA_CERT_PATH"),
    clientCertPath: optionalPath(env.AGAT_TEMPORAL_CLIENT_CERT_PATH, "AGAT_TEMPORAL_CLIENT_CERT_PATH"),
    clientKeyPath: optionalPath(env.AGAT_TEMPORAL_CLIENT_KEY_PATH, "AGAT_TEMPORAL_CLIENT_KEY_PATH"),
    serverNameOverride: optionalSafeText(
      env.AGAT_TEMPORAL_SERVER_NAME_OVERRIDE,
      "AGAT_TEMPORAL_SERVER_NAME_OVERRIDE",
      253,
    ),
    coordinatorUrl: boundedText(
      env.AGAT_COORDINATOR_INTERNAL_URL,
      "http://agat-coordinator:8787",
      "AGAT_COORDINATOR_INTERNAL_URL",
      500,
    ).replace(/\/+$/, ""),
    internalToken: optionalSafeText(env.AGAT_TEMPORAL_INTERNAL_TOKEN, "AGAT_TEMPORAL_INTERNAL_TOKEN", 8_192),
    metricsAddress: boundedText(
      env.AGAT_TEMPORAL_METRICS_ADDRESS,
      "0.0.0.0:9091",
      "AGAT_TEMPORAL_METRICS_ADDRESS",
      200,
    ),
    versioningEnabled,
    deploymentName,
    buildId,
    deploymentRing: deploymentRing(env.AGAT_TEMPORAL_DEPLOYMENT_RING),
    defaultVersioningBehavior: versioningBehavior(env.AGAT_TEMPORAL_VERSIONING_BEHAVIOR),
    identity: (env.AGAT_TEMPORAL_WORKER_ID ?? "").trim()
      || `${deploymentName}/${buildId}/${os.hostname()}-${process.pid}`,
    maxConcurrentWorkflowTasks: integerFromEnv(
      env.AGAT_TEMPORAL_MAX_WORKFLOW_TASKS,
      20,
      "AGAT_TEMPORAL_MAX_WORKFLOW_TASKS",
      2,
      1_000,
    ),
    maxConcurrentActivityTasks: integerFromEnv(
      env.AGAT_TEMPORAL_MAX_ACTIVITY_TASKS,
      8,
      "AGAT_TEMPORAL_MAX_ACTIVITY_TASKS",
      1,
      1_000,
    ),
    shutdownGraceSeconds: integerFromEnv(
      env.AGAT_TEMPORAL_SHUTDOWN_GRACE_SECONDS,
      30,
      "AGAT_TEMPORAL_SHUTDOWN_GRACE_SECONDS",
      1,
      600,
    ),
  };
  if (!/^https?:\/\//.test(config.coordinatorUrl)) {
    throw new Error("AGAT_COORDINATOR_INTERNAL_URL должен быть абсолютным HTTP(S) URL");
  }
  if (!config.internalToken) throw new Error("AGAT_TEMPORAL_INTERNAL_TOKEN обязателен для Temporal worker");
  validateTls(config);
  validateVersioning(config);
  return config;
}

function readTlsFile(filePath: string, field: string): Buffer {
  try {
    const content = fs.readFileSync(filePath);
    if (content.length === 0) throw new Error("файл пуст");
    return content;
  } catch (error) {
    throw new Error(`${field}: не удалось прочитать TLS-файл ${filePath}`, { cause: error });
  }
}

export function temporalWorkerConnectionOptions(config: TemporalWorkerConfig): NativeConnectionOptions {
  let tls: boolean | TLSConfig = config.tls;
  if (config.tls && (config.caCertPath || config.clientCertPath || config.serverNameOverride)) {
    tls = {
      ...(config.serverNameOverride ? { serverNameOverride: config.serverNameOverride } : {}),
      ...(config.caCertPath
        ? { serverRootCACertificate: readTlsFile(config.caCertPath, "AGAT_TEMPORAL_CA_CERT_PATH") }
        : {}),
      ...(config.clientCertPath
        ? {
            clientCertPair: {
              crt: readTlsFile(config.clientCertPath, "AGAT_TEMPORAL_CLIENT_CERT_PATH"),
              key: readTlsFile(config.clientKeyPath, "AGAT_TEMPORAL_CLIENT_KEY_PATH"),
            },
          }
        : {}),
    };
  }
  return {
    address: config.address,
    tls,
    ...(config.apiKey ? { apiKey: config.apiKey } : {}),
  };
}

export function temporalWorkerDeploymentOptions(
  config: TemporalWorkerConfig,
): WorkerOptions["workerDeploymentOptions"] {
  if (!config.versioningEnabled) return undefined;
  return {
    useWorkerVersioning: true,
    version: {
      deploymentName: config.deploymentName,
      buildId: config.buildId,
    },
    defaultVersioningBehavior: config.defaultVersioningBehavior,
  };
}
