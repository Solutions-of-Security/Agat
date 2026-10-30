import Keycloak from "keycloak-js";

export interface AuthConfig {
  mode?: "legacy" | "oidc" | "local";
  deployment?: { controlPlaneAvailable: boolean; blockers: Array<{ code: string; message: string }> };
  enabled: boolean;
  url: string;
  realm: string;
  clientId: string;
}

const PROJECT_KEY = "agat.project-id.v1";
let config: AuthConfig = { enabled: false, url: "", realm: "", clientId: "agat-web" };
let keycloak: Keycloak | null = null;
let localAuthenticated = false;
let localCsrf: string | null = null;
const sessionListeners = new Set<() => void>();
export function localAuthEnabled(): boolean { return config.mode === "local"; }
export function localSessionAuthenticated(): boolean { return localAuthenticated; }
export function csrfToken(): string | null { return localCsrf; }
export function subscribeLocalSession(listener: () => void): () => void { sessionListeners.add(listener); return () => sessionListeners.delete(listener); }
export function expireLocalSession(): void { localAuthenticated = false; localCsrf = null; for (const listener of sessionListeners) listener(); }
export function acceptLocalSession(value: { csrfToken: string }): void {
  if (typeof value.csrfToken !== "string" || !value.csrfToken) throw new Error("Некорректная сессия входа");
  localCsrf = value.csrfToken;
  localAuthenticated = true;
  for (const listener of sessionListeners) listener();
}
export async function loginLocal(token: string): Promise<void> {
  const response = await fetch("/api/v1/auth/local/login", { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" }, body: JSON.stringify({ token }) });
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { error?: string };
    throw new Error(body.error ?? "Не удалось выполнить вход");
  }
  acceptLocalSession(await response.json() as { csrfToken: string });
}

export async function initializeAuth(): Promise<void> {
  const response = await fetch("/api/v1/auth/config", { headers: { accept: "application/json" } });
  if (!response.ok) throw new Error("Не удалось загрузить конфигурацию входа");
  config = await response.json() as AuthConfig;
  if (config.deployment?.controlPlaneAvailable === false) throw new Error(config.deployment.blockers.find((issue) => issue.code !== "temporal_required")?.message ?? "Профиль развёртывания недоступен");
  if (localAuthEnabled()) {
    window.localStorage.removeItem("agat.admin-token.v1");
    const session = await fetch("/api/v1/auth/local/session", { credentials: "same-origin", cache: "no-store" });
    if (session.ok) acceptLocalSession(await session.json() as { csrfToken: string });
    else if (session.status !== 401) throw new Error("Не удалось проверить локальную сессию");
    return;
  }
  if (!config.enabled) return;
  if (!config.url || !config.realm || !config.clientId) throw new Error("OIDC настроен неполностью");
  keycloak = new Keycloak({ url: config.url, realm: config.realm, clientId: config.clientId });
  const authenticated = await keycloak.init({
    onLoad: "login-required",
    flow: "standard",
    pkceMethod: "S256",
    checkLoginIframe: false,
  });
  if (!authenticated) await keycloak.login();
  keycloak.onTokenExpired = () => {
    void keycloak?.updateToken(30).catch(() => keycloak?.login());
  };
}

export function oidcEnabled(): boolean {
  return config.enabled;
}

export async function accessToken(): Promise<string | null> {
  if (!keycloak) return null;
  try {
    await keycloak.updateToken(30);
  } catch {
    await keycloak.login();
    return null;
  }
  return keycloak.token ?? null;
}

export function activeProjectId(): string {
  return window.localStorage.getItem(PROJECT_KEY)?.trim().toLowerCase() || "default";
}

export function setActiveProjectId(projectId: string): void {
  window.localStorage.setItem(PROJECT_KEY, projectId);
}

export async function logout(): Promise<void> {
  if (localAuthEnabled()) {
    const response = await fetch("/api/v1/auth/local/logout", { method: "POST", credentials: "same-origin", headers: { "x-agat-csrf-token": localCsrf ?? "" } });
    if (!response.ok && response.status !== 401) throw new Error("Не удалось закрыть сессию. Повторите выход.");
    expireLocalSession();
    return;
  }
  if (keycloak) {
    await keycloak.logout({ redirectUri: window.location.origin });
    return;
  }
  window.location.reload();
}
