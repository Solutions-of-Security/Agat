import type { CoordinatorConfig } from "./config.js";

import { DEPLOYMENT_CONTRACT_VERSION, type DeploymentProfile, type AuthenticationMode, type DeploymentOperation, type DeploymentCapability, type DeploymentSnapshot } from "./deployment-contract.js";
export type { DeploymentProfile, AuthenticationMode, DeploymentOperation, DeploymentCapability, DeploymentSnapshot } from "./deployment-contract.js";

export interface DeploymentDependencies {
  runtime: { mode: "database" | "temporal"; connected: boolean };
  sandbox?: { available: boolean; networkPolicyEnforced: boolean } | null;
  localIdentityAvailable?: boolean;
}

export function parseDeploymentProfile(value: string | undefined): DeploymentProfile | undefined {
  if (!value?.trim()) return undefined;
  const result = value.trim().toLowerCase();
  if (result === "single-user" || result === "lite" || result === "durable" || result === "production-ha") return result;
  throw new Error("AGAT_DEPLOYMENT_PROFILE должен быть single-user, lite, durable или production-ha");
}

export function parseAuthenticationMode(value: string | undefined): AuthenticationMode | undefined {
  if (!value?.trim()) return undefined;
  const result = value.trim().toLowerCase();
  if (result === "legacy" || result === "oidc" || result === "local") return result;
  throw new Error("AGAT_AUTH_MODE должен быть legacy, oidc или local");
}

export function authenticationMode(config: CoordinatorConfig): AuthenticationMode {
  return config.authMode ?? (config.oidcEnabled ? "oidc" : "legacy");
}

export function deploymentSnapshot(config: CoordinatorConfig, dependencies: DeploymentDependencies): DeploymentSnapshot {
  const mode = authenticationMode(config);
  const profile = config.deploymentProfile ?? (mode === "legacy" ? "single-user" : config.temporalEnabled ? "durable" : "lite");
  const blockers: DeploymentSnapshot["blockers"] = [];
  const identityReady = mode === "oidc" ? Boolean(config.oidcIssuer && config.oidcClientId)
    : mode === "local" && dependencies.localIdentityAvailable === true;
  if (mode === "local" && !dependencies.localIdentityAvailable) blockers.push({ code: "local_identity_unavailable", message: "Локальная аутентификация недоступна. Настройте поддерживаемый identity provider." });
  if (mode === "oidc" && !identityReady) blockers.push({ code: "oidc_configuration_missing", message: "Для OIDC нужны issuer и client ID. Переход к local-admin запрещён." });
  if (mode !== "oidc" && config.oidcEnabled) blockers.push({ code: "identity_configuration_conflict", message: "Выберите один источник входа: local, legacy или OIDC." });
  if (profile === "single-user" && mode !== "legacy") blockers.push({ code: "identity_profile_conflict", message: "Профиль single-user предназначен для legacy оператора. Для индивидуального входа выберите lite или durable." });
  if (profile !== "single-user" && mode === "legacy") blockers.push({ code: "individual_identity_required", message: "Этот профиль требует индивидуальной аутентификации. Общий admin-token представляет одного local-admin." });
  if ((profile === "durable" || profile === "production-ha") && (!config.temporalEnabled || dependencies.runtime.mode !== "temporal" || !dependencies.runtime.connected)) blockers.push({ code: "temporal_required", message: "Профиль требует подключённого Temporal runtime." });
  if (profile === "production-ha" && config.stateStoreDriver !== "postgresql") blockers.push({ code: "postgresql_required", message: "Production HA профиль требует PostgreSQL state store." });

  const ready = blockers.length === 0;
  // A Temporal outage must not disable identity, diagnostics or independent agent runs.
  const accessBlocker = blockers.find((issue) => issue.code !== "temporal_required");
  const controlPlaneAvailable = !accessBlocker;
  const capabilities: DeploymentCapability[] = [];
  const capability = (operation: DeploymentOperation, label: string, available: boolean, code: string, message: string) => {
    const blocked = accessBlocker;
    capabilities.push({ operation, label, available: controlPlaneAvailable && available,
      code: blocked?.code ?? (available ? "available" : code), message: blocked?.message ?? (available ? "Доступно в текущей конфигурации. Готовность конкретного сценария проверяется отдельно." : message) });
  };
  capability("runs", "Обычные запуски", true, "", "");
  const requiresTemporal = profile === "durable" || profile === "production-ha";
  capability("processes", "Процессы", dependencies.runtime.connected && (!requiresTemporal || config.temporalEnabled && dependencies.runtime.mode === "temporal"), "runtime_disconnected", "Нужный этому профилю runtime процессов недоступен.");
  capability("schedules", "Durable процессы и расписания", config.temporalEnabled && dependencies.runtime.mode === "temporal" && dependencies.runtime.connected, "temporal_unavailable", "Нужен подключённый Temporal runtime. Обычные запуски не требуют Temporal.");
  capability("multiuser", "Индивидуальные пользователи", identityReady, "individual_identity_required", "Single-user режим использует одного local-admin. Для нескольких пользователей настройте отдельные субъекты.");
  capability("mcpHttp", "HTTP MCP tools", config.mcpEnabled, "mcp_disabled", "MCP gateway выключен.");
  capability("wasi", "Изолированные WASI tools", config.mcpEnabled && dependencies.sandbox?.available === true, "sandbox_unavailable", "Нужны MCP gateway и доступный sandbox executor.");
  capability("container", "Изолированные OCI tools", config.mcpEnabled && dependencies.sandbox?.available === true && dependencies.sandbox.networkPolicyEnforced,
    dependencies.sandbox?.available ? "network_policy_unverified" : "sandbox_unavailable", "Нужны sandbox executor и подтверждённое исполнение NetworkPolicy.");
  capability("workspace", "Workspace и выполнение команд", false, "workspace_not_implemented", "Выполнение команд в рабочем каталоге агента пока недоступно. Изолированный OCI tool не предоставляет такой каталог.");
  capability("stdio", "MCP STDIO", false, "stdio_not_implemented", "Подключение MCP-серверов через STDIO пока недоступно.");
  capability("chat", "Диалоговый бот", false, "chat_not_implemented", "Диалоговый бот и сохраняемая история переписки пока недоступны.");
  capability("callbacks", "Result callbacks", false, "callbacks_not_implemented", "Автоматическая доставка результата на HTTPS callback пока недоступна. Статус запуска доступен в панели; A2A push настраивается отдельно.");
  capability("cloudModels", "Облачные подключения моделей", false, "cloud_executor_not_implemented", "Облачного провайдера пока нельзя подключить через интерфейс. Ручная настройка endpoint на worker остаётся доступна.");
  capability("productionHa", "Production HA qualification", false, "qualification_required", "Конфигурация не доказывает HA/DR. Нужны evidence и OPS gates выбранного контура.");
  return { contractVersion: DEPLOYMENT_CONTRACT_VERSION, profile,
    label: { "single-user": "Однопользовательский", lite: "Лёгкий многопользовательский", durable: "Durable", "production-ha": "Production HA" }[profile],
    ready, controlPlaneAvailable, authentication: { mode, individualSubjects: identityReady, label: mode === "legacy" ? "Один субъект local-admin" : mode === "oidc" ? "OIDC" : "Локальные идентичности" },
    stateStore: config.stateStoreDriver, runtime: dependencies.runtime.mode, ingress: config.deploymentIngress ?? "direct", productionQualified: false, blockers, capabilities };
}

export function deploymentPreflight(snapshot: DeploymentSnapshot, operations: unknown): { allowed: boolean; deployment: DeploymentSnapshot; capabilities: DeploymentCapability[] } {
  if (!Array.isArray(operations) || operations.length < 1 || operations.length > snapshot.capabilities.length || operations.some((value) => typeof value !== "string")) throw new Error("operations должен содержать поддерживаемые операции deployment contract");
  const selected = [...new Set(operations as string[])].map((name) => {
    const entry = snapshot.capabilities.find((item) => item.operation === name);
    if (!entry) throw new Error("Неизвестная операция deployment contract");
    return entry;
  });
  return { allowed: selected.every((entry) => entry.available), deployment: snapshot, capabilities: selected };
}
