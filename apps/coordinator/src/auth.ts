import { createPublicKey, createVerify, timingSafeEqual } from "node:crypto";

export type AgatRole = "admin" | "designer" | "operator" | "viewer" | "auditor";

export interface AuthContext {
  subject: string;
  username: string;
  email: string | null;
  roles: Set<AgatRole>;
  projectIds: Set<string>;
  local: boolean;
}

export interface OidcVerifierOptions {
  issuer: string;
  clientId: string;
  jwksUrl?: string;
  cacheTtlSeconds?: number;
  clockSkewSeconds?: number;
  fetcher?: typeof fetch;
}

export class AuthenticationError extends Error {
  constructor(readonly status: 401 | 403, message: string) {
    super(message);
  }
}

interface JwtHeader {
  alg?: unknown;
  kid?: unknown;
  typ?: unknown;
}

interface JwtClaims {
  iss?: unknown;
  sub?: unknown;
  exp?: unknown;
  nbf?: unknown;
  aud?: unknown;
  azp?: unknown;
  preferred_username?: unknown;
  email?: unknown;
  realm_access?: unknown;
  resource_access?: unknown;
  agat_projects?: unknown;
}

interface JwkWithKid extends JsonWebKey {
  kid?: string;
}

interface JsonWebKeySet {
  keys?: JwkWithKid[];
}

const AGAT_ROLES = new Set<AgatRole>(["admin", "designer", "operator", "viewer", "auditor"]);
const PROJECT_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/;

function decodeJsonPart<T>(value: string, label: string): T {
  if (value.length > 32_000) throw new AuthenticationError(401, `${label} JWT слишком большой`);
  try {
    return JSON.parse(Buffer.from(value, "base64url").toString("utf8")) as T;
  } catch {
    throw new AuthenticationError(401, `Некорректный ${label} JWT`);
  }
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function tokenRoles(claims: JwtClaims, clientId: string): Set<AgatRole> {
  const realm = claims.realm_access && typeof claims.realm_access === "object"
    ? stringArray((claims.realm_access as Record<string, unknown>).roles)
    : [];
  const resources = claims.resource_access && typeof claims.resource_access === "object"
    ? claims.resource_access as Record<string, unknown>
    : {};
  const client = resources[clientId] && typeof resources[clientId] === "object"
    ? stringArray((resources[clientId] as Record<string, unknown>).roles)
    : [];
  return new Set([...realm, ...client].filter((role): role is AgatRole => AGAT_ROLES.has(role as AgatRole)));
}

function tokenProjects(claims: JwtClaims): Set<string> {
  const values = stringArray(claims.agat_projects).map((value) => value.trim().toLowerCase()).filter((value) => PROJECT_ID.test(value));
  return new Set(values.length ? values : ["default"]);
}

export class OidcVerifier {
  private readonly issuer: string;
  private readonly clientId: string;
  private readonly jwksUrl: string;
  private readonly cacheTtlMs: number;
  private readonly clockSkewSeconds: number;
  private readonly fetcher: typeof fetch;
  private cachedKeys: JwkWithKid[] = [];
  private keysExpireAt = 0;

  constructor(options: OidcVerifierOptions) {
    this.issuer = options.issuer.replace(/\/+$/, "");
    this.clientId = options.clientId;
    this.jwksUrl = options.jwksUrl || `${this.issuer}/protocol/openid-connect/certs`;
    this.cacheTtlMs = Math.max(10, Math.min(3_600, options.cacheTtlSeconds ?? 300)) * 1_000;
    this.clockSkewSeconds = Math.max(0, Math.min(120, options.clockSkewSeconds ?? 15));
    this.fetcher = options.fetcher ?? fetch;
  }

  async verify(token: string): Promise<AuthContext> {
    if (token.length > 32_000) throw new AuthenticationError(401, "Access token слишком большой");
    const parts = token.split(".");
    if (parts.length !== 3 || parts.some((part) => !part)) throw new AuthenticationError(401, "Некорректный access token");
    const [encodedHeader, encodedPayload, encodedSignature] = parts as [string, string, string];
    const header = decodeJsonPart<JwtHeader>(encodedHeader, "header");
    const claims = decodeJsonPart<JwtClaims>(encodedPayload, "payload");
    if (header.alg !== "RS256" || typeof header.kid !== "string" || !header.kid) {
      throw new AuthenticationError(401, "Поддерживаются только подписанные RS256 access tokens");
    }
    const key = await this.key(header.kid);
    const verifier = createVerify("RSA-SHA256");
    verifier.update(`${encodedHeader}.${encodedPayload}`);
    verifier.end();
    let signatureValid = false;
    try {
      signatureValid = verifier.verify(createPublicKey({ key, format: "jwk" }), Buffer.from(encodedSignature, "base64url"));
    } catch {
      signatureValid = false;
    }
    if (!signatureValid) throw new AuthenticationError(401, "Подпись access token недействительна");

    const now = Math.floor(Date.now() / 1_000);
    if (claims.iss !== this.issuer) throw new AuthenticationError(401, "Issuer access token не совпадает");
    if (typeof claims.sub !== "string" || !claims.sub) throw new AuthenticationError(401, "В access token отсутствует subject");
    if (typeof claims.exp !== "number" || claims.exp < now - this.clockSkewSeconds) {
      throw new AuthenticationError(401, "Access token истёк");
    }
    if (typeof claims.nbf === "number" && claims.nbf > now + this.clockSkewSeconds) {
      throw new AuthenticationError(401, "Access token ещё не действует");
    }
    const audiences = typeof claims.aud === "string" ? [claims.aud] : stringArray(claims.aud);
    if (!audiences.includes(this.clientId) && claims.azp !== this.clientId) {
      throw new AuthenticationError(401, "Access token выпущен для другого client");
    }
    const roles = tokenRoles(claims, this.clientId);
    if (roles.size === 0) throw new AuthenticationError(403, "Пользователю не назначена роль АГАТ");
    return {
      subject: claims.sub,
      username: typeof claims.preferred_username === "string" ? claims.preferred_username : claims.sub,
      email: typeof claims.email === "string" ? claims.email : null,
      roles,
      projectIds: tokenProjects(claims),
      local: false,
    };
  }

  private async key(kid: string): Promise<JwkWithKid> {
    let keys = await this.keys(false);
    let key = keys.find((candidate) => candidate.kid === kid && candidate.kty === "RSA");
    if (!key) {
      keys = await this.keys(true);
      key = keys.find((candidate) => candidate.kid === kid && candidate.kty === "RSA");
    }
    if (!key) throw new AuthenticationError(401, "Ключ подписи access token не найден");
    return key;
  }

  private async keys(force: boolean): Promise<JwkWithKid[]> {
    if (!force && this.cachedKeys.length && Date.now() < this.keysExpireAt) return this.cachedKeys;
    let response: Response;
    try {
      response = await this.fetcher(this.jwksUrl, {
        headers: { accept: "application/json" },
        signal: AbortSignal.timeout(5_000),
      });
    } catch {
      throw new AuthenticationError(401, "Keycloak JWKS недоступен");
    }
    if (!response.ok) throw new AuthenticationError(401, `Keycloak JWKS вернул HTTP ${response.status}`);
    const payload = await response.json() as JsonWebKeySet;
    if (!Array.isArray(payload.keys) || payload.keys.length > 20) {
      throw new AuthenticationError(401, "Keycloak вернул некорректный JWKS");
    }
    this.cachedKeys = payload.keys;
    this.keysExpireAt = Date.now() + this.cacheTtlMs;
    return this.cachedKeys;
  }
}

export function localAdminContext(): AuthContext {
  return {
    subject: "local-admin",
    username: "local-admin",
    email: null,
    roles: new Set<AgatRole>(["admin"]),
    projectIds: new Set(["default"]),
    local: true,
  };
}

export function selectProject(context: AuthContext, requested: string | undefined): string {
  const projectId = (requested || "default").trim().toLowerCase();
  if (!PROJECT_ID.test(projectId)) throw new AuthenticationError(403, "Некорректный проект");
  if (!context.roles.has("admin") && !context.projectIds.has(projectId)) {
    throw new AuthenticationError(403, "Нет доступа к выбранному проекту");
  }
  return projectId;
}

export function tokenEquals(left: string, right: string): boolean {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
}
