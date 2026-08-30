import path from "node:path";
import { fileURLToPath } from "node:url";

const currentDir = path.dirname(fileURLToPath(import.meta.url));

function booleanFromEnv(value: string | undefined, fallback: boolean): boolean {
  if (value === undefined || value.trim() === "") return fallback;
  const normalized = value.trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  throw new Error(`Некорректное boolean-значение: ${value}`);
}

function integerFromEnv(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export type TemporalTarget = "local" | "cloud" | "self-hosted";
export type StateStoreDriver = "sqlite" | "postgresql";
export type EdgeAttestationMode = "disabled" | "broker";
export type PostgresSslMode = "disable" | "require" | "verify-full";

function temporalTargetFromEnv(value: string | undefined): TemporalTarget {
  const normalized = (value ?? "local").trim().toLowerCase();
  if (normalized === "local" || normalized === "cloud" || normalized === "self-hosted") return normalized;
  throw new Error("AGAT_TEMPORAL_TARGET должен быть local, cloud или self-hosted");
}

function stateStoreDriverFromEnv(value: string | undefined): StateStoreDriver {
  const normalized = (value ?? "sqlite").trim().toLowerCase();
  if (normalized === "sqlite" || normalized === "postgresql") return normalized;
  throw new Error("AGAT_STATE_STORE_DRIVER должен быть sqlite или postgresql");
}

function edgeAttestationModeFromEnv(value: string | undefined): EdgeAttestationMode {
  const normalized = (value ?? "disabled").trim().toLowerCase();
  if (normalized === "disabled" || normalized === "broker") return normalized;
  throw new Error("AGAT_EDGE_ATTESTATION_MODE должен быть disabled или broker");
}

function postgresSslModeFromEnv(value: string | undefined): PostgresSslMode {
  const normalized = (value ?? "disable").trim().toLowerCase();
  if (normalized === "disable" || normalized === "require" || normalized === "verify-full") return normalized;
  throw new Error("AGAT_POSTGRES_SSL_MODE должен быть disable, require или verify-full");
}

function safeJsonStringMap(value: string | undefined, field: string): Record<string, string> {
  if (!value?.trim()) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error(`${field} должен быть JSON object`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(`${field} должен быть JSON object`);
  const entries = Object.entries(parsed as Record<string, unknown>);
  if (entries.length > 32) throw new Error(`${field} содержит больше 32 ключей`);
  return Object.fromEntries(entries.map(([key, item]) => {
    if (!/^[a-z0-9][a-z0-9._-]{0,62}$/.test(key) || typeof item !== "string" || item.length > 16_384) {
      throw new Error(`${field} содержит некорректный key или public key`);
    }
    return [key, item];
  }));
}

function safeListFromEnv(value: string | undefined, fallback: string[], field: string): string[] {
  if (value === undefined || value.trim() === "") return fallback;
  const items = [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))];
  if (items.length === 0 || items.length > 16 || items.some((item) => item.length > 120 || /[\r\n\0]/.test(item))) {
    throw new Error(`${field} содержит недопустимый список`);
  }
  return items;
}

function optionalSafeText(value: string | undefined, field: string, maxLength: number): string {
  const normalized = (value ?? "").trim();
  if (normalized.length > maxLength || /[\r\n\0]/.test(normalized)) {
    throw new Error(`${field} содержит недопустимые управляющие символы или слишком длинное значение`);
  }
  return normalized;
}

function optionalResolvedPath(value: string | undefined, field: string): string {
  const normalized = optionalSafeText(value, field, 4_096);
  return normalized ? path.resolve(normalized) : "";
}

export interface CoordinatorConfig {
  host: string;
  port: number;
  dbPath: string;
  artifactsDir: string;
  enrollmentToken: string;
  adminToken: string;
  credentialsKey: string;
  oidcEnabled: boolean;
  oidcIssuer: string;
  oidcClientId: string;
  oidcJwksUrl: string;
  temporalEnabled: boolean;
  temporalAddress: string;
  temporalNamespace: string;
  temporalTaskQueue: string;
  temporalInternalToken: string;
  temporalTarget: TemporalTarget;
  temporalTls: boolean;
  temporalApiKey: string;
  temporalCaCertPath: string;
  temporalClientCertPath: string;
  temporalClientKeyPath: string;
  temporalServerNameOverride: string;
  stateStoreDriver: StateStoreDriver;
  postgresUrl: string;
  postgresTenantUrl: string;
  postgresPoolMax: number;
  postgresConnectTimeoutMs: number;
  postgresIdleTimeoutMs: number;
  postgresStatementTimeoutMs: number;
  postgresSslMode: PostgresSslMode;
  postgresCaCertPath: string;
  postgresClientCertPath: string;
  postgresClientKeyPath: string;
  coordinatorInstanceId: string;
  region: string;
  residencyDomain: string;
  workerReleasePublicKeys: Record<string, string>;
  requireSignedWorkerReleases: boolean;
  siemEnabled: boolean;
  siemUrl: string;
  siemBearerToken: string;
  siemBatchSize: number;
  siemIntervalSeconds: number;
  siemTimeoutSeconds: number;
  seedDemo: boolean;
  serveWeb: boolean;
  webDistPath: string;
  leaseTtlSeconds: number;
  allowedOrigins: string[];
  localWorkerLauncherEnabled: boolean;
  localWorkerNamespace: string;
  localWorkerImage: string;
  localWorkerConfigMap: string;
  localWorkerSecret: string;
  localWorkerModelBaseUrl: string;
  localWorkerModelDiscoveryUrl: string;
  localWorkerEmbeddingModels: string;
  localWorkerDefaultWebEnabled: boolean;
  localWorkerMaxPerLaunch: number;
  edgeEnabled: boolean;
  edgeAttestationMode: EdgeAttestationMode;
  edgeAttestationBrokerUrl: string;
  edgeAttestationBrokerToken: string;
  edgeAttestationTimeoutSeconds: number;
  edgeChallengeTtlSeconds: number;
  edgeAndroidApplicationId: string;
  edgeIosApplicationId: string;
  edgeAllowDevelopmentAttestation: boolean;
  edgeAndroidRequiredVerdicts: string[];
  edgeIosRequiredVerdicts: string[];
  otelEnabled: boolean;
  otelServiceName: string;
  otelExporterEndpoint: string;
  mcpEnabled: boolean;
  mcpRefreshSeconds: number;
  mcpRequestTimeoutSeconds: number;
  mcpMaxResponseBytes: number;
  mcpApprovalTtlSeconds: number;
  sandboxEnabled: boolean;
  sandboxNamespace: string;
  sandboxWasiImage: string;
  sandboxRuntimeClass: string;
  sandboxNetworkPolicyEnforced: boolean;
  a2aEnabled: boolean;
  a2aPublicBaseUrl: string;
  a2aOutboundEnabled: boolean;
  a2aAllowLoopbackOutbound: boolean;
  a2aOutboundTimeoutSeconds: number;
  a2aMaxResponseBytes: number;
}

export function loadConfig(): CoordinatorConfig {
  const otelTracesEndpoint = process.env.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT?.trim() ?? "";
  const otelBaseEndpoint = process.env.OTEL_EXPORTER_OTLP_ENDPOINT?.trim().replace(/\/+$/, "") ?? "";
  const otelExporterEndpoint = otelTracesEndpoint || (otelBaseEndpoint ? `${otelBaseEndpoint}/v1/traces` : "");
  const port = integerFromEnv(process.env.AGAT_PORT, 8787);
  const temporalTarget = temporalTargetFromEnv(process.env.AGAT_TEMPORAL_TARGET);
  const stateStoreDriver = stateStoreDriverFromEnv(process.env.AGAT_STATE_STORE_DRIVER);
  const region = optionalSafeText(process.env.AGAT_REGION ?? "local", "AGAT_REGION", 63).toLowerCase();
  return {
    host: process.env.AGAT_HOST ?? "127.0.0.1",
    port,
    dbPath: path.resolve(process.env.AGAT_DB_PATH ?? "./data/agat.db"),
    artifactsDir: path.resolve(process.env.AGAT_ARTIFACTS_DIR ?? "./data/artifacts"),
    enrollmentToken: process.env.AGAT_ENROLLMENT_TOKEN ?? "agat-local-enrollment",
    adminToken: process.env.AGAT_ADMIN_TOKEN ?? "",
    credentialsKey: process.env.AGAT_CREDENTIALS_KEY ?? process.env.AGAT_ADMIN_TOKEN ?? "agat-local-credentials-key",
    oidcEnabled: booleanFromEnv(process.env.AGAT_OIDC_ENABLED, false),
    oidcIssuer: (process.env.AGAT_OIDC_ISSUER ?? "").replace(/\/+$/, ""),
    oidcClientId: process.env.AGAT_OIDC_CLIENT_ID ?? "agat-web",
    oidcJwksUrl: process.env.AGAT_OIDC_JWKS_URL ?? "",
    temporalEnabled: booleanFromEnv(process.env.AGAT_TEMPORAL_ENABLED, false),
    temporalAddress: process.env.AGAT_TEMPORAL_ADDRESS ?? "127.0.0.1:7233",
    temporalNamespace: process.env.AGAT_TEMPORAL_NAMESPACE ?? "agat",
    temporalTaskQueue: process.env.AGAT_TEMPORAL_TASK_QUEUE ?? "agat-processes-v1",
    temporalInternalToken: optionalSafeText(
      process.env.AGAT_TEMPORAL_INTERNAL_TOKEN,
      "AGAT_TEMPORAL_INTERNAL_TOKEN",
      8_192,
    ),
    temporalTarget,
    temporalTls: booleanFromEnv(process.env.AGAT_TEMPORAL_TLS, temporalTarget !== "local"),
    temporalApiKey: optionalSafeText(process.env.AGAT_TEMPORAL_API_KEY, "AGAT_TEMPORAL_API_KEY", 8_192),
    temporalCaCertPath: optionalResolvedPath(process.env.AGAT_TEMPORAL_CA_CERT_PATH, "AGAT_TEMPORAL_CA_CERT_PATH"),
    temporalClientCertPath: optionalResolvedPath(
      process.env.AGAT_TEMPORAL_CLIENT_CERT_PATH,
      "AGAT_TEMPORAL_CLIENT_CERT_PATH",
    ),
    temporalClientKeyPath: optionalResolvedPath(
      process.env.AGAT_TEMPORAL_CLIENT_KEY_PATH,
      "AGAT_TEMPORAL_CLIENT_KEY_PATH",
    ),
    temporalServerNameOverride: optionalSafeText(
      process.env.AGAT_TEMPORAL_SERVER_NAME_OVERRIDE,
      "AGAT_TEMPORAL_SERVER_NAME_OVERRIDE",
      253,
    ),
    stateStoreDriver,
    postgresUrl: optionalSafeText(process.env.AGAT_POSTGRES_URL, "AGAT_POSTGRES_URL", 8_192),
    postgresTenantUrl: optionalSafeText(process.env.AGAT_POSTGRES_TENANT_URL, "AGAT_POSTGRES_TENANT_URL", 8_192),
    postgresPoolMax: Math.max(1, Math.min(32, integerFromEnv(process.env.AGAT_POSTGRES_POOL_MAX, 4))),
    postgresConnectTimeoutMs: Math.max(500, Math.min(60_000, integerFromEnv(process.env.AGAT_POSTGRES_CONNECT_TIMEOUT_MS, 5_000))),
    postgresIdleTimeoutMs: Math.max(1_000, Math.min(600_000, integerFromEnv(process.env.AGAT_POSTGRES_IDLE_TIMEOUT_MS, 30_000))),
    postgresStatementTimeoutMs: Math.max(1_000, Math.min(600_000, integerFromEnv(process.env.AGAT_POSTGRES_STATEMENT_TIMEOUT_MS, 30_000))),
    postgresSslMode: postgresSslModeFromEnv(process.env.AGAT_POSTGRES_SSL_MODE),
    postgresCaCertPath: optionalResolvedPath(process.env.AGAT_POSTGRES_CA_CERT_PATH, "AGAT_POSTGRES_CA_CERT_PATH"),
    postgresClientCertPath: optionalResolvedPath(process.env.AGAT_POSTGRES_CLIENT_CERT_PATH, "AGAT_POSTGRES_CLIENT_CERT_PATH"),
    postgresClientKeyPath: optionalResolvedPath(process.env.AGAT_POSTGRES_CLIENT_KEY_PATH, "AGAT_POSTGRES_CLIENT_KEY_PATH"),
    coordinatorInstanceId: optionalSafeText(
      process.env.AGAT_COORDINATOR_INSTANCE_ID ?? process.env.HOSTNAME ?? `coordinator-${process.pid}`,
      "AGAT_COORDINATOR_INSTANCE_ID",
      120,
    ),
    region,
    residencyDomain: optionalSafeText(
      process.env.AGAT_RESIDENCY_DOMAIN ?? region,
      "AGAT_RESIDENCY_DOMAIN",
      63,
    ).toLowerCase(),
    workerReleasePublicKeys: safeJsonStringMap(
      process.env.AGAT_WORKER_RELEASE_PUBLIC_KEYS,
      "AGAT_WORKER_RELEASE_PUBLIC_KEYS",
    ),
    requireSignedWorkerReleases: booleanFromEnv(
      process.env.AGAT_REQUIRE_SIGNED_WORKER_RELEASES,
      stateStoreDriver === "postgresql",
    ),
    siemEnabled: booleanFromEnv(process.env.AGAT_SIEM_ENABLED, false),
    siemUrl: optionalSafeText(process.env.AGAT_SIEM_URL, "AGAT_SIEM_URL", 2_048),
    siemBearerToken: optionalSafeText(process.env.AGAT_SIEM_BEARER_TOKEN, "AGAT_SIEM_BEARER_TOKEN", 8_192),
    siemBatchSize: Math.max(1, Math.min(500, integerFromEnv(process.env.AGAT_SIEM_BATCH_SIZE, 100))),
    siemIntervalSeconds: Math.max(1, Math.min(300, integerFromEnv(process.env.AGAT_SIEM_INTERVAL_SECONDS, 5))),
    siemTimeoutSeconds: Math.max(1, Math.min(60, integerFromEnv(process.env.AGAT_SIEM_TIMEOUT_SECONDS, 10))),
    seedDemo: booleanFromEnv(process.env.AGAT_SEED_DEMO, false),
    serveWeb: booleanFromEnv(process.env.AGAT_SERVE_WEB, true),
    webDistPath: path.resolve(currentDir, "../../web/dist"),
    leaseTtlSeconds: integerFromEnv(process.env.AGAT_LEASE_TTL_SECONDS, 180),
    allowedOrigins: (process.env.AGAT_ALLOWED_ORIGINS ?? "http://127.0.0.1:5173,http://localhost:5173")
      .split(",")
      .map((origin) => origin.trim())
      .filter(Boolean),
    localWorkerLauncherEnabled: booleanFromEnv(process.env.AGAT_LOCAL_WORKER_LAUNCHER, false),
    localWorkerNamespace: process.env.AGAT_LOCAL_WORKER_NAMESPACE ?? "agat",
    localWorkerImage: process.env.AGAT_LOCAL_WORKER_IMAGE ?? "agat-local/worker:1.7.0",
    localWorkerConfigMap: process.env.AGAT_LOCAL_WORKER_CONFIG_MAP ?? "agat-worker-config",
    localWorkerSecret: process.env.AGAT_LOCAL_WORKER_SECRET ?? "agat-secrets",
    localWorkerModelBaseUrl: process.env.AGAT_LOCAL_MODEL_BASE_URL ?? "http://host.docker.internal:11434/v1",
    localWorkerModelDiscoveryUrl: process.env.AGAT_LOCAL_MODEL_DISCOVERY_URL ?? "http://host.docker.internal:11434/api/tags",
    localWorkerEmbeddingModels: process.env.AGAT_LOCAL_WORKER_EMBEDDING_MODELS ?? "",
    localWorkerDefaultWebEnabled: booleanFromEnv(process.env.AGAT_LOCAL_WORKER_WEB_ENABLED, true),
    localWorkerMaxPerLaunch: Math.max(1, Math.min(16, integerFromEnv(process.env.AGAT_LOCAL_WORKER_MAX_PER_LAUNCH, 8))),
    edgeEnabled: booleanFromEnv(process.env.AGAT_EDGE_ENABLED, false),
    edgeAttestationMode: edgeAttestationModeFromEnv(process.env.AGAT_EDGE_ATTESTATION_MODE),
    edgeAttestationBrokerUrl: optionalSafeText(
      process.env.AGAT_EDGE_ATTESTATION_BROKER_URL,
      "AGAT_EDGE_ATTESTATION_BROKER_URL",
      2_048,
    ),
    edgeAttestationBrokerToken: optionalSafeText(
      process.env.AGAT_EDGE_ATTESTATION_BROKER_TOKEN,
      "AGAT_EDGE_ATTESTATION_BROKER_TOKEN",
      8_192,
    ),
    edgeAttestationTimeoutSeconds: Math.max(
      1,
      Math.min(60, integerFromEnv(process.env.AGAT_EDGE_ATTESTATION_TIMEOUT_SECONDS, 10)),
    ),
    edgeChallengeTtlSeconds: Math.max(30, Math.min(600, integerFromEnv(process.env.AGAT_EDGE_CHALLENGE_TTL_SECONDS, 180))),
    edgeAndroidApplicationId: optionalSafeText(
      process.env.AGAT_EDGE_ANDROID_APPLICATION_ID,
      "AGAT_EDGE_ANDROID_APPLICATION_ID",
      255,
    ),
    edgeIosApplicationId: optionalSafeText(
      process.env.AGAT_EDGE_IOS_APPLICATION_ID,
      "AGAT_EDGE_IOS_APPLICATION_ID",
      255,
    ),
    edgeAllowDevelopmentAttestation: booleanFromEnv(process.env.AGAT_EDGE_ALLOW_DEVELOPMENT_ATTESTATION, false),
    edgeAndroidRequiredVerdicts: safeListFromEnv(
      process.env.AGAT_EDGE_ANDROID_REQUIRED_VERDICTS,
      ["MEETS_DEVICE_INTEGRITY", "PLAY_RECOGNIZED"],
      "AGAT_EDGE_ANDROID_REQUIRED_VERDICTS",
    ),
    edgeIosRequiredVerdicts: safeListFromEnv(
      process.env.AGAT_EDGE_IOS_REQUIRED_VERDICTS,
      ["APP_ATTEST_VALID"],
      "AGAT_EDGE_IOS_REQUIRED_VERDICTS",
    ),
    otelEnabled: booleanFromEnv(process.env.AGAT_OTEL_ENABLED, Boolean(otelExporterEndpoint)),
    otelServiceName: process.env.OTEL_SERVICE_NAME ?? "agat-coordinator",
    otelExporterEndpoint,
    mcpEnabled: booleanFromEnv(process.env.AGAT_MCP_ENABLED, true),
    mcpRefreshSeconds: Math.max(10, integerFromEnv(process.env.AGAT_MCP_REFRESH_SECONDS, 30)),
    mcpRequestTimeoutSeconds: Math.max(1, Math.min(300, integerFromEnv(process.env.AGAT_MCP_REQUEST_TIMEOUT_SECONDS, 60))),
    mcpMaxResponseBytes: Math.max(65_536, integerFromEnv(process.env.AGAT_MCP_MAX_RESPONSE_BYTES, 4_194_304)),
    mcpApprovalTtlSeconds: Math.max(30, Math.min(86_400, integerFromEnv(process.env.AGAT_MCP_APPROVAL_TTL_SECONDS, 600))),
    sandboxEnabled: booleanFromEnv(process.env.AGAT_SANDBOX_ENABLED, false),
    sandboxNamespace: optionalSafeText(process.env.AGAT_SANDBOX_NAMESPACE ?? "agat", "AGAT_SANDBOX_NAMESPACE", 63),
    sandboxWasiImage: optionalSafeText(
      process.env.AGAT_SANDBOX_WASI_IMAGE ?? "agat-local/sandbox-wasi:1.7.0",
      "AGAT_SANDBOX_WASI_IMAGE",
      512,
    ),
    sandboxRuntimeClass: optionalSafeText(process.env.AGAT_SANDBOX_RUNTIME_CLASS, "AGAT_SANDBOX_RUNTIME_CLASS", 63),
    sandboxNetworkPolicyEnforced: booleanFromEnv(process.env.AGAT_SANDBOX_NETWORK_POLICY_ENFORCED, false),
    a2aEnabled: booleanFromEnv(process.env.AGAT_A2A_ENABLED, true),
    a2aPublicBaseUrl: (process.env.AGAT_A2A_PUBLIC_BASE_URL ?? `http://127.0.0.1:${port}`).replace(/\/+$/, ""),
    a2aOutboundEnabled: booleanFromEnv(process.env.AGAT_A2A_OUTBOUND_ENABLED, true),
    a2aAllowLoopbackOutbound: booleanFromEnv(process.env.AGAT_A2A_ALLOW_LOOPBACK_OUTBOUND, false),
    a2aOutboundTimeoutSeconds: Math.max(1, Math.min(120, integerFromEnv(process.env.AGAT_A2A_OUTBOUND_TIMEOUT_SECONDS, 15))),
    a2aMaxResponseBytes: Math.max(65_536, Math.min(4_194_304, integerFromEnv(process.env.AGAT_A2A_MAX_RESPONSE_BYTES, 1_048_576))),
  };
}

export function validateTemporalCoordinatorConfig(config: CoordinatorConfig): void {
  if (!/^[a-z0-9][a-z0-9._-]{0,62}$/.test(config.region)) {
    throw new Error("AGAT_REGION должен содержать 1..63 символа [a-z0-9._-]");
  }
  if (!/^[a-z0-9][a-z0-9._-]{0,62}$/.test(config.residencyDomain)) {
    throw new Error("AGAT_RESIDENCY_DOMAIN должен содержать 1..63 символа [a-z0-9._-]");
  }
  if (config.stateStoreDriver === "postgresql") {
    if (!config.postgresUrl || !config.postgresTenantUrl) {
      throw new Error("PostgreSQL state store требует AGAT_POSTGRES_URL и отдельный AGAT_POSTGRES_TENANT_URL");
    }
    let systemUrl: URL;
    let tenantUrl: URL;
    try {
      systemUrl = new URL(config.postgresUrl);
      tenantUrl = new URL(config.postgresTenantUrl);
    } catch {
      throw new Error("PostgreSQL connection URL некорректен");
    }
    if (!["postgres:", "postgresql:"].includes(systemUrl.protocol)
      || !["postgres:", "postgresql:"].includes(tenantUrl.protocol)) {
      throw new Error("PostgreSQL connection URL должен использовать postgresql://");
    }
    if (systemUrl.username === tenantUrl.username) {
      throw new Error("System и tenant PostgreSQL URL должны использовать разные least-privilege roles");
    }
    const rolePattern = /^[a-z_][a-z0-9_]{0,62}$/;
    if (!rolePattern.test(decodeURIComponent(systemUrl.username)) || !rolePattern.test(decodeURIComponent(tenantUrl.username))) {
      throw new Error("PostgreSQL URLs должны использовать lowercase role identifiers");
    }
    if (!systemUrl.password || !tenantUrl.password) {
      throw new Error("PostgreSQL system и tenant roles должны аутентифицироваться отдельными credentials");
    }
    if (systemUrl.hostname !== tenantUrl.hostname
      || systemUrl.port !== tenantUrl.port
      || systemUrl.pathname !== tenantUrl.pathname) {
      throw new Error("PostgreSQL system и tenant URLs должны указывать на одну HA-cell database");
    }
    const localPostgres = config.region === "local"
      || ["localhost", "127.0.0.1", "::1", "postgres", "agat-coordinator-postgres"].includes(systemUrl.hostname);
    if (config.postgresSslMode === "disable" && !localPostgres) {
      throw new Error("Удалённый PostgreSQL требует AGAT_POSTGRES_SSL_MODE=require или verify-full");
    }
    if (config.requireSignedWorkerReleases && Object.keys(config.workerReleasePublicKeys).length === 0) {
      throw new Error("Signed worker releases требуют хотя бы один AGAT_WORKER_RELEASE_PUBLIC_KEYS trust root");
    }
  }
  const hasPostgresClientCert = Boolean(config.postgresClientCertPath);
  const hasPostgresClientKey = Boolean(config.postgresClientKeyPath);
  if (hasPostgresClientCert !== hasPostgresClientKey) {
    throw new Error("AGAT_POSTGRES_CLIENT_CERT_PATH и AGAT_POSTGRES_CLIENT_KEY_PATH должны задаваться вместе");
  }
  if (config.postgresSslMode === "disable" && (config.postgresCaCertPath || hasPostgresClientCert)) {
    throw new Error("PostgreSQL TLS-файлы нельзя использовать при AGAT_POSTGRES_SSL_MODE=disable");
  }
  if (config.siemEnabled) {
    if (!config.siemUrl) throw new Error("AGAT_SIEM_URL обязателен при AGAT_SIEM_ENABLED=true");
    let siem: URL;
    try {
      siem = new URL(config.siemUrl);
    } catch {
      throw new Error("AGAT_SIEM_URL должен быть корректным абсолютным URL");
    }
    const loopbackSiem = ["localhost", "127.0.0.1", "::1"].includes(siem.hostname);
    if (siem.protocol !== "https:" && !(loopbackSiem && siem.protocol === "http:")) {
      throw new Error("SIEM exporter требует HTTPS (HTTP разрешён только для loopback test sink)");
    }
  }
  if (!config.temporalEnabled) return;
  if (!config.temporalInternalToken) {
    throw new Error("AGAT_TEMPORAL_INTERNAL_TOKEN обязателен при включённом Temporal");
  }
  if (config.temporalAddress.includes("://") || /[/?#@\s]/.test(config.temporalAddress)) {
    throw new Error("AGAT_TEMPORAL_ADDRESS должен иметь формат host:port без URL-схемы, пути и credentials");
  }
  const hasClientCert = Boolean(config.temporalClientCertPath);
  const hasClientKey = Boolean(config.temporalClientKeyPath);
  if (hasClientCert !== hasClientKey) {
    throw new Error("AGAT_TEMPORAL_CLIENT_CERT_PATH и AGAT_TEMPORAL_CLIENT_KEY_PATH должны задаваться вместе");
  }
  if (
    (config.temporalCaCertPath || hasClientCert || config.temporalServerNameOverride)
    && !config.temporalTls
  ) {
    throw new Error("TLS-файлы и server-name override нельзя использовать при AGAT_TEMPORAL_TLS=false");
  }
  if (config.temporalApiKey && !config.temporalTls) {
    throw new Error("AGAT_TEMPORAL_API_KEY разрешён только при включённом TLS");
  }
  if (config.temporalTarget === "cloud") {
    if (!config.temporalTls) throw new Error("Temporal Cloud требует AGAT_TEMPORAL_TLS=true");
    if (!config.temporalApiKey) throw new Error("Temporal Cloud требует AGAT_TEMPORAL_API_KEY");
  }
  if (config.temporalTarget === "self-hosted") {
    if (!config.temporalTls) throw new Error("Production self-hosted Temporal требует AGAT_TEMPORAL_TLS=true");
    if (!config.temporalApiKey && !hasClientCert) {
      throw new Error("Self-hosted Temporal требует API key либо пару клиентского сертификата и ключа");
    }
  }
}
