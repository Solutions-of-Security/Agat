import Keycloak from "keycloak-js";

export interface AuthConfig {
  enabled: boolean;
  url: string;
  realm: string;
  clientId: string;
}

const PROJECT_KEY = "agat.project-id.v1";
let config: AuthConfig = { enabled: false, url: "", realm: "", clientId: "agat-web" };
let keycloak: Keycloak | null = null;

export async function initializeAuth(): Promise<void> {
  const response = await fetch("/api/v1/auth/config", { headers: { accept: "application/json" } });
  if (!response.ok) throw new Error("Не удалось загрузить конфигурацию входа");
  config = await response.json() as AuthConfig;
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
  if (keycloak) {
    await keycloak.logout({ redirectUri: window.location.origin });
    return;
  }
  window.location.reload();
}
