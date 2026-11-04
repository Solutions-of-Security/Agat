import { createHash, createHmac, randomBytes, randomUUID } from "node:crypto";
import type { IncomingMessage } from "node:http";
import { AuthenticationError, selectProject, tokenEquals, type AgatRole, type AuthContext } from "./auth.js";
import { currentLocalIdentity, identityAudit } from "./local-identity-context.js";
import { LOCAL_ROLES, LOCAL_SCOPES, scopesForRoles, type ChannelBinding, type CreateLocalPrincipal, type IssueLocalCredential,
  type LocalCredential, type LocalPrincipal, type LocalScope } from "./local-identity-contract.js";
import type { SyncDatabase } from "./sync-database.js";

type Row = Record<string, unknown>;
// A random lookup handle for a hashed session row, never a personal/API credential.
type SessionHandle = string & { readonly sessionHandle: unique symbol };
type Audit = (type: string, data: Record<string, unknown>) => void;
const MAX_LIFETIME = 90 * 24 * 60 * 60 * 1000;
const SESSION_LIFETIME = 8 * 60 * 60 * 1000;
const PROJECT = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const hash = (value: string) => createHash("sha256").update(value).digest("hex");
const readArray = <T extends string>(value: unknown): T[] => JSON.parse(String(value)) as T[];
const deny = (): never => { throw new AuthenticationError(401, "Индивидуальный ключ или сессия недействительны"); };
function text(value: unknown, label: string, max = 120): string {
  if (typeof value !== "string" || !value.trim() || value.trim().length > max || /[\x00-\x1f\x7f]/.test(value)) throw new Error(`Некорректное поле ${label}`);
  return value.trim();
}
function values<T extends string>(value: unknown, valid: readonly T[], label: string): T[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > valid.length || value.some(v => typeof v !== "string" || !valid.includes(v as T))) throw new Error(`Некорректное поле ${label}`);
  return [...new Set(value)] as T[];
}
function subset(requested: string[], granted: string[]): boolean {
  return requested.every(value => granted.includes(value) || value !== "*" && granted.includes("*"));
}
function intersection(requested: string[], granted: string[]): string[] {
  return requested.includes("*") ? [...granted] : requested.filter(value => granted.includes("*") || granted.includes(value));
}
function publicOrigin(value: string | undefined): string | null {
  if (!value) return null;
  const url = new URL(value);
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.username || url.password || url.pathname !== "/" || url.search || url.hash || url.origin !== value.replace(/\/$/, "")
    || url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) throw new Error("AGAT_LOCAL_AUTH_ORIGIN должен быть HTTPS origin; HTTP разрешён только на loopback");
  return url.origin;
}

/** SQLite authority: no public signup, passwords, shared admin secret, or cached grants. */
export class LocalIdentityProvider {
  readonly origin: string | null;
  readonly cookieName: string;
  constructor(readonly db: SyncDatabase, origin?: string, private readonly audit: Audit = () => {}, private readonly clock = Date.now) {
    if (db.dialect !== "sqlite") throw new Error("Локальные идентичности поддерживают только SQLite; миграция требует отдельного identity mapping");
    this.origin = publicOrigin(origin);
    this.cookieName = this.origin?.startsWith("https:") ? "__Host-agat-session" : "agat-local-session";
    this.transaction(() => {
      db.exec(`CREATE TABLE IF NOT EXISTS local_identity_schema (id INTEGER PRIMARY KEY CHECK(id = 1), version INTEGER NOT NULL)`);
      const version = db.prepare("SELECT version FROM local_identity_schema WHERE id = 1").get();
      if (version && version.version !== 1) throw new Error("Неподдерживаемая версия local identity schema; fallback запрещён");
      db.exec(`
        CREATE TABLE IF NOT EXISTS local_principals (
          subject TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK(kind IN ('user','service')),
          roles_json TEXT NOT NULL, projects_json TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS local_identity_credentials (
          id TEXT PRIMARY KEY, subject TEXT NOT NULL REFERENCES local_principals(subject), token_hash TEXT NOT NULL UNIQUE,
          label TEXT NOT NULL, purpose TEXT NOT NULL CHECK(purpose IN ('personal','api')), application TEXT NOT NULL,
          roles_json TEXT NOT NULL, projects_json TEXT NOT NULL, scopes_json TEXT NOT NULL, expires_at INTEGER NOT NULL,
          revoked INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_local_credentials_subject ON local_identity_credentials(subject);
        CREATE TABLE IF NOT EXISTS local_identity_sessions (
          session_hash TEXT PRIMARY KEY, credential_id TEXT NOT NULL REFERENCES local_identity_credentials(id), expires_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_local_sessions_credential ON local_identity_sessions(credential_id);
        CREATE TABLE IF NOT EXISTS local_run_identities (
          run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE, project_id TEXT NOT NULL,
          subject TEXT NOT NULL REFERENCES local_principals(subject), credential_id TEXT NOT NULL REFERENCES local_identity_credentials(id),
          source TEXT NOT NULL, application TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS local_channel_bindings (
          id TEXT PRIMARY KEY, provider TEXT NOT NULL, installation_id TEXT NOT NULL, external_user_id TEXT NOT NULL,
          project_id TEXT NOT NULL REFERENCES projects(id), subject TEXT NOT NULL REFERENCES local_principals(subject),
          UNIQUE(provider, installation_id, external_user_id, project_id)
        );
      `);
      if (!version) db.prepare("INSERT INTO local_identity_schema(id, version) VALUES (1, 1)").run();
    });
  }
  get initialized(): boolean { return Boolean(this.db.prepare("SELECT subject FROM local_principals LIMIT 1").get()); }
  get available(): boolean { return this.initialized && Boolean(this.origin); }
  private transaction<T>(callback: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try { const result = callback(); this.db.exec("COMMIT"); return result; }
    catch (error) { this.db.exec("ROLLBACK"); throw error; }
  }
  private projects(input: unknown, roles: string[]): string[] {
    if (!Array.isArray(input) || !input.length || input.length > 100 || input.some(id => typeof id !== "string" || id !== "*" && !PROJECT.test(id))) throw new Error("Нужны корректные projectIds");
    const ids = [...new Set(input)] as string[];
    if (ids.includes("*") && (!roles.includes("admin") || ids.length !== 1)) throw new Error("Доступ ко всем проектам требует явной роли admin");
    for (const id of ids) if (id !== "*" && !this.db.prepare("SELECT id FROM projects WHERE id = ?").get(id)) throw new Error("Проект не найден");
    return ids;
  }
  private principal(row: Row): LocalPrincipal {
    return { subject: String(row.subject), username: String(row.username), kind: row.kind as LocalPrincipal["kind"],
      roles: readArray<AgatRole>(row.roles_json), projectIds: readArray(row.projects_json), disabled: Boolean(row.disabled), createdAt: String(row.created_at) };
  }
  private credential(row: Row): LocalCredential {
    return { id: String(row.id), subject: String(row.subject), label: String(row.label), purpose: row.purpose as LocalCredential["purpose"],
      application: String(row.application), roles: readArray<AgatRole>(row.roles_json), projectIds: readArray(row.projects_json),
      scopes: readArray<LocalScope>(row.scopes_json), expiresAt: new Date(Number(row.expires_at)).toISOString(), revoked: Boolean(row.revoked), createdAt: String(row.created_at) };
  }
  listPrincipals(): LocalPrincipal[] { return this.db.prepare("SELECT * FROM local_principals ORDER BY created_at, subject").all().map(row => this.principal(row)); }
  listCredentials(subject: string): LocalCredential[] {
    return this.db.prepare("SELECT * FROM local_identity_credentials WHERE subject = ? ORDER BY created_at DESC LIMIT 1000").all(subject).map(row => this.credential(row));
  }
  private insertPrincipal(input: CreateLocalPrincipal): LocalPrincipal {
    const username = text(input.username, "username", 80);
    if (input.kind !== "user" && input.kind !== "service") throw new Error("kind должен быть user или service");
    const roles = values(input.roles, LOCAL_ROLES, "roles");
    if (input.kind === "service" && roles.includes("admin")) throw new Error("Service principal не получает роль admin");
    const projects = this.projects(input.projectIds, roles);
    if (Number(this.db.prepare("SELECT COUNT(*) AS count FROM local_principals").get()?.count) >= 1000) throw new Error("Достигнут предел локальных субъектов");
    const subject = `local-${input.kind}:${randomUUID()}`;
    this.db.prepare("INSERT INTO local_principals(subject, username, kind, roles_json, projects_json, created_at) VALUES (?, ?, ?, ?, ?, ?)")
      .run(subject, username, input.kind, JSON.stringify(roles), JSON.stringify(projects), new Date(this.clock()).toISOString());
    this.audit("identity.principal.created", { subject, kind: input.kind, roles, projectIds: projects });
    return this.principal(this.db.prepare("SELECT * FROM local_principals WHERE subject = ?").get(subject)!);
  }
  createPrincipal(input: CreateLocalPrincipal): LocalPrincipal { return this.transaction(() => this.insertPrincipal(input)); }
  updatePrincipal(subject: string, input: Partial<CreateLocalPrincipal> & { disabled?: boolean }): LocalPrincipal {
    return this.transaction(() => {
      const row = this.db.prepare("SELECT * FROM local_principals WHERE subject = ?").get(subject);
      if (!row) throw new Error("Субъект не найден");
      const previous = this.principal(row);
      if (input.kind !== undefined && input.kind !== previous.kind) throw new Error("Тип субъекта неизменяем");
      if (input.disabled !== undefined && typeof input.disabled !== "boolean") throw new Error("disabled должен быть boolean");
      const roles = input.roles === undefined ? previous.roles : values(input.roles, LOCAL_ROLES, "roles");
      if (previous.kind === "service" && roles.includes("admin")) throw new Error("Service principal не получает роль admin");
      const projects = this.projects(input.projectIds ?? previous.projectIds, roles);
      const disabled = input.disabled ?? previous.disabled;
      const remainsAdmin = !disabled && roles.includes("admin") && projects.includes("*");
      if (!remainsAdmin && previous.roles.includes("admin") && previous.projectIds.includes("*") && !previous.disabled
        && !this.listPrincipals().some(p => p.subject !== subject && !p.disabled && p.roles.includes("admin") && p.projectIds.includes("*"))) throw new Error("Нельзя отключить последнего администратора; используйте offline recovery");
      this.db.prepare("UPDATE local_principals SET username = ?, roles_json = ?, projects_json = ?, disabled = ? WHERE subject = ?")
        .run(input.username === undefined ? previous.username : text(input.username, "username", 80), JSON.stringify(roles), JSON.stringify(projects), disabled ? 1 : 0, subject);
      if (disabled) this.db.prepare("DELETE FROM local_identity_sessions WHERE credential_id IN (SELECT id FROM local_identity_credentials WHERE subject = ?)").run(subject);
      this.audit("identity.principal.updated", { subject, roles, projectIds: projects, disabled });
      return this.principal(this.db.prepare("SELECT * FROM local_principals WHERE subject = ?").get(subject)!);
    });
  }
  private issue(subject: string, input: IssueLocalCredential): { credential: LocalCredential; token: string } {
    const row = this.db.prepare("SELECT * FROM local_principals WHERE subject = ? AND disabled = 0").get(subject);
    if (!row) throw new Error("Активный субъект не найден");
    const principal = this.principal(row);
    const roles = values(input.roles, LOCAL_ROLES, "roles");
    const projects = this.projects(input.projectIds, roles);
    const scopes = values(input.scopes, LOCAL_SCOPES, "scopes");
    if (!subset(roles, principal.roles) || !subset(projects, principal.projectIds) || !subset(scopes, scopesForRoles(roles))) throw new AuthenticationError(403, "Ключ не может расширять права субъекта");
    if (input.purpose !== "personal" && input.purpose !== "api" || principal.kind === "service" && input.purpose !== "api") throw new Error("Персональный вход разрешён только пользователям");
    if (principal.kind === "service" && scopes.some(scope => scope === "approve" || scope === "review")) throw new Error("Service principal не участвует в human approvals/reviews");
    const expires = typeof input.expiresAt === "string" ? Date.parse(input.expiresAt) : NaN;
    if (!Number.isFinite(expires) || expires <= this.clock() || expires > this.clock() + MAX_LIFETIME) throw new Error("Срок ключа должен составлять от 1 секунды до 90 дней");
    if (Number(this.db.prepare("SELECT COUNT(*) AS count FROM local_identity_credentials WHERE subject = ? AND revoked = 0 AND expires_at > ?").get(subject, this.clock())?.count) >= 128) throw new Error("Достигнут предел активных ключей субъекта");
    const id = randomUUID();
    const token = `agat_${input.purpose}_${randomBytes(32).toString("base64url")}`;
    this.db.prepare(`INSERT INTO local_identity_credentials(id, subject, token_hash, label, purpose, application, roles_json, projects_json, scopes_json, expires_at, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
      .run(id, subject, hash(token), text(input.label, "label"), input.purpose, text(input.application, "application", 80), JSON.stringify(roles), JSON.stringify(projects), JSON.stringify(scopes), expires, new Date(this.clock()).toISOString());
    this.audit("identity.credential.created", { subject, credentialId: id, purpose: input.purpose, application: input.application, roles, projectIds: projects, scopes, expiresAt: new Date(expires).toISOString() });
    return { credential: this.credential(this.db.prepare("SELECT * FROM local_identity_credentials WHERE id = ?").get(id)!), token };
  }
  issueCredential(subject: string, input: IssueLocalCredential): { credential: LocalCredential; token: string } { return this.transaction(() => this.issue(subject, input)); }
  bootstrap(username: string): { principal: LocalPrincipal; credential: LocalCredential; token: string } {
    return this.transaction(() => {
      if (this.initialized) throw new Error("Bootstrap уже выполнен; публичного повторного bootstrap нет");
      const principal = this.insertPrincipal({ username, kind: "user", roles: ["admin"], projectIds: ["*"] });
      const issued = this.issue(principal.subject, { label: "Первичный вход", purpose: "personal", application: "agat-web", roles: ["admin"], projectIds: ["*"], scopes: [...LOCAL_SCOPES], expiresAt: new Date(this.clock() + 30 * 86400000).toISOString() });
      this.audit("identity.bootstrap", { actor: { subject: principal.subject, kind: "user", source: "local-cli", application: "agat-local-auth" } });
      return { principal, ...issued };
    });
  }
  revokeCredential(id: string): void {
    this.transaction(() => this.revoke(id));
  }
  private revoke(id: string): void {
    const credential = this.db.prepare("SELECT subject FROM local_identity_credentials WHERE id = ?").get(id);
    if (!credential) throw new Error("Ключ не найден");
    this.db.prepare("UPDATE local_identity_credentials SET revoked = 1 WHERE id = ?").run(id);
    this.db.prepare("DELETE FROM local_identity_sessions WHERE credential_id = ?").run(id);
    this.audit("identity.credential.revoked", { subject: credential.subject, credentialId: id });
  }
  rotateCredential(id: string): { credential: LocalCredential; token: string } {
    return this.transaction(() => {
      const row = this.db.prepare("SELECT * FROM local_identity_credentials WHERE id = ? AND revoked = 0").get(id);
      if (!row) throw new Error("Активный ключ не найден");
      const credential = this.credential(row);
      const issued = this.issue(credential.subject, credential);
      this.revoke(id);
      return issued;
    });
  }
  recover(subject: string): { credential: LocalCredential; token: string } {
    return this.transaction(() => {
      const row = this.db.prepare("SELECT * FROM local_principals WHERE subject = ? AND disabled = 0").get(subject);
      if (!row) throw new Error("Субъект не найден");
      const principal = this.principal(row);
      if (principal.kind !== "user" || !principal.roles.includes("admin") || !principal.projectIds.includes("*")) throw new Error("Offline recovery требует существующий global admin subject");
      for (const key of this.db.prepare("SELECT id FROM local_identity_credentials WHERE subject = ? AND revoked = 0").all(subject)) this.revoke(String(key.id));
      const issued = this.issue(subject, { label: "Offline recovery", purpose: "personal", application: "agat-web", roles: ["admin"], projectIds: ["*"], scopes: [...LOCAL_SCOPES], expiresAt: new Date(this.clock() + 30 * 86400000).toISOString() });
      this.audit("identity.offline_recovery", { actor: { subject, kind: "user", source: "local-cli", application: "agat-local-auth" } });
      return issued;
    });
  }
  private verifyId(id: string, source = "local-bearer"): AuthContext {
    const key = this.db.prepare("SELECT * FROM local_identity_credentials WHERE id = ? AND revoked = 0 AND expires_at > ?").get(id, this.clock());
    if (!key) return deny();
    const row = this.db.prepare("SELECT * FROM local_principals WHERE subject = ? AND disabled = 0").get(String(key.subject));
    if (!row) return deny();
    const principal = this.principal(row);
    const roles = intersection(readArray(key.roles_json), principal.roles) as AgatRole[];
    const projects = intersection(readArray(key.projects_json), principal.projectIds);
    const scopes = intersection(readArray(key.scopes_json), scopesForRoles(roles)) as LocalScope[];
    if (!roles.length || !projects.length || !scopes.length) return deny();
    return { subject: principal.subject, username: principal.username, email: null, local: true,
      roles: new Set(roles), projectIds: new Set(projects), scopes: new Set(scopes), credentialId: id,
      principalKind: principal.kind, identitySource: source, application: String(key.application) };
  }
  verify(token: string): AuthContext {
    if (!/^agat_(?:personal|api)_[A-Za-z0-9_-]{43}$/.test(token)) return deny();
    const row = this.db.prepare("SELECT id FROM local_identity_credentials WHERE token_hash = ?").get(hash(token));
    return row ? this.verifyId(String(row.id)) : deny();
  }
  assertCurrent(context: AuthContext, projectId: string, scope: LocalScope): AuthContext {
    if (!context.credentialId) return deny();
    const current = this.verifyId(context.credentialId, context.identitySource);
    if (current.subject !== context.subject) return deny();
    selectProject(current, projectId);
    if (!current.scopes?.has(scope)) throw new AuthenticationError(403, "Ключ не разрешает эту операцию");
    return current;
  }
  assertOrigin(request: IncomingMessage): void {
    if (!this.origin || request.headers.origin !== this.origin) throw new AuthenticationError(403, "Origin локального входа не разрешён");
  }
  private sessionHandle(request: IncomingMessage): SessionHandle | null {
    const cookies = (request.headers.cookie ?? "").split(";").map(value => value.trim()).filter(value => value.startsWith(`${this.cookieName}=`));
    if (cookies.length !== 1) return null;
    const value = cookies[0]!.slice(this.cookieName.length + 1);
    return /^[A-Za-z0-9_-]{43}$/.test(value) ? value as SessionHandle : null;
  }
  private csrf(handle: SessionHandle): string { return createHmac("sha256", handle).update("agat-local-csrf-v1").digest("base64url"); }
  private sessionCookie(handle: SessionHandle | "", maxAge = SESSION_LIFETIME / 1000): string {
    if (handle !== "" && !/^[A-Za-z0-9_-]{43}$/.test(handle)) throw new Error("Некорректный session handle");
    return `${this.cookieName}=${handle}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAge}${this.origin?.startsWith("https:") ? "; Secure" : ""}`;
  }
  login(request: IncomingMessage, token: string): { cookie: string; csrfToken: string; auth: AuthContext } {
    this.assertOrigin(request);
    const auth = this.verify(token);
    const key = this.db.prepare("SELECT purpose, expires_at FROM local_identity_credentials WHERE id = ?").get(auth.credentialId!);
    if (auth.principalKind !== "user" || key?.purpose !== "personal") throw new AuthenticationError(403, "API/service ключ не подходит для входа в интерфейс");
    const handle = randomBytes(32).toString("base64url") as SessionHandle;
    const expires = Math.min(this.clock() + SESSION_LIFETIME, Number(key.expires_at));
    this.transaction(() => {
      this.db.prepare("DELETE FROM local_identity_sessions WHERE expires_at <= ?").run(this.clock());
      this.db.prepare("INSERT INTO local_identity_sessions(session_hash, credential_id, expires_at) VALUES (?, ?, ?)").run(hash(handle), auth.credentialId!, expires);
      this.db.prepare(`DELETE FROM local_identity_sessions WHERE session_hash IN
        (SELECT session_hash FROM local_identity_sessions WHERE credential_id = ? ORDER BY expires_at DESC, rowid DESC LIMIT -1 OFFSET 20)`).run(auth.credentialId!);
      this.audit("identity.session.created", { actor: { ...identityAudit(auth), source: "local-session" } });
    });
    return { cookie: this.sessionCookie(handle, Math.floor((expires - this.clock()) / 1000)), csrfToken: this.csrf(handle), auth };
  }
  session(request: IncomingMessage): { auth: AuthContext; csrfToken: string } {
    const handle = this.sessionHandle(request);
    if (!handle) return deny();
    const row = this.db.prepare("SELECT credential_id FROM local_identity_sessions WHERE session_hash = ? AND expires_at > ?").get(hash(handle), this.clock());
    if (!row) return deny();
    const auth = this.verifyId(String(row.credential_id), "local-session");
    if (auth.principalKind !== "user") return deny();
    return { auth, csrfToken: this.csrf(handle) };
  }
  authenticate(request: IncomingMessage): AuthContext {
    if (request.headers["x-agat-admin-token"] !== undefined) return deny();
    if (request.headers.authorization !== undefined) {
      const match = /^Bearer ([^ ]+)$/.exec(request.headers.authorization);
      return match ? this.verify(match[1]!) : deny();
    }
    const session = this.session(request);
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method ?? "")) {
      this.assertOrigin(request);
      const supplied = request.headers["x-agat-csrf-token"];
      if (typeof supplied !== "string" || !tokenEquals(supplied, session.csrfToken)) throw new AuthenticationError(403, "Проверка CSRF не пройдена");
    }
    return session.auth;
  }
  authorizeOperation(request: IncomingMessage, context: AuthContext, allowedRoles: AgatRole[]): void {
    if (![...context.roles].some(role => allowedRoles.includes(role))) throw new AuthenticationError(403, "Недостаточно прав для операции");
    const path = new URL(request.url ?? "/", "http://local.invalid").pathname;
    const write = !["GET", "HEAD"].includes(request.method ?? "");
    const review = /\/evals\/items\/[^/]+\/reviews$|\/review-extraction$/.test(path);
    const humanDecision = write && (review || /\/approvals\/|\/mcp\/tool-calls\/[^/]+\/decision$|\/artifacts\/[^/]+\/decisions$/.test(path));
    if (humanDecision && context.principalKind !== "user") throw new AuthenticationError(403, "Service principal не участвует в human decisions");
    const globalOperation = /^\/api\/v1\/(?:local-workers|fleet\/(?:releases|siem)|auth\/(?:principals|credentials|audit))/.test(path)
      || path === "/api/v1/projects" && write;
    if (globalOperation && (!context.roles.has("admin") || !context.projectIds.has("*"))) throw new AuthenticationError(403, "Операция требует администратора всех проектов");
    let scope: LocalScope = "read";
    if (globalOperation && write) scope = "admin";
    else if (write && humanDecision) scope = review ? "review" : "approve";
    else if (write && path.startsWith("/api/v1/credentials")) scope = "credentials";
    else if (write && /\/preflight$|\/preview$|\/deployment\/preflight$/.test(path)) scope = "read";
    else if (write && (/^\/api\/v1\/runs(?:\/|$)/.test(path) || /\/(?:start|replay|test|invoke)$/.test(path) || /\/eval\//.test(path))) scope = "run";
    else if (write && allowedRoles.includes("designer")) scope = "design";
    else if (write) scope = allowedRoles.length === 1 && allowedRoles[0] === "admin" ? "admin" : "run";
    if (!context.scopes?.has(scope)) throw new AuthenticationError(403, `Ключ не разрешает scope ${scope}`);
  }
  logout(request: IncomingMessage): string {
    const auth = this.authenticate(request);
    const handle = this.sessionHandle(request);
    if (handle) this.db.prepare("DELETE FROM local_identity_sessions WHERE session_hash = ?").run(hash(handle));
    this.audit("identity.session.closed", { actor: identityAudit(auth) });
    return this.sessionCookie("", 0);
  }
  bindRun(runId: string, projectId: string, parentRunId?: string): void {
    const inherited = parentRunId ? this.db.prepare("SELECT * FROM local_run_identities WHERE run_id = ?").get(parentRunId) : undefined;
    const actor = inherited ? this.verifyId(String(inherited.credential_id), String(inherited.source)) : currentLocalIdentity();
    if (!actor) throw new AuthenticationError(403, "Запуск требует индивидуальный user/service principal; legacy external ingress не назначает владельца");
    this.assertCurrent(actor, projectId, "run");
    this.db.prepare("INSERT INTO local_run_identities(run_id, project_id, subject, credential_id, source, application) VALUES (?, ?, ?, ?, ?, ?)")
      .run(runId, projectId, actor.subject, actor.credentialId!, actor.identitySource!, actor.application!);
  }
  assertRunAllowed(runId: string, projectId?: string): void {
    const binding = this.db.prepare("SELECT i.*, r.project_id AS actual_project FROM local_run_identities i JOIN runs r ON r.id = i.run_id WHERE i.run_id = ?").get(runId);
    if (!binding || binding.project_id !== binding.actual_project || projectId && projectId !== binding.project_id) throw new AuthenticationError(403, "У запуска нет действующей индивидуальной привязки");
    const context = this.verifyId(String(binding.credential_id), String(binding.source));
    if (context.subject !== binding.subject) return deny();
    this.assertCurrent(context, String(binding.project_id), "run");
  }
  runAudit(runId: string): Record<string, unknown> | undefined {
    const row = this.db.prepare("SELECT * FROM local_run_identities WHERE run_id = ?").get(runId);
    return row ? { subject: row.subject, source: row.source, application: row.application, credentialId: row.credential_id } : undefined;
  }
  listBindings(projectId: string): ChannelBinding[] {
    return this.db.prepare("SELECT * FROM local_channel_bindings WHERE project_id = ? ORDER BY id").all(projectId).map(row => ({
      id: String(row.id), provider: String(row.provider), installationId: String(row.installation_id), externalUserId: String(row.external_user_id), projectId: String(row.project_id), subject: String(row.subject) }));
  }
  createBinding(input: Omit<ChannelBinding, "id">): ChannelBinding {
    return this.transaction(() => {
      const principal = this.db.prepare("SELECT * FROM local_principals WHERE subject = ? AND disabled = 0").get(input.subject);
      if (!principal || principal.kind !== "user" || !subset([input.projectId], readArray(principal.projects_json))) throw new AuthenticationError(403, "Для привязки нужен действующий пользователь выбранного проекта");
      this.projects([input.projectId], []);
      const binding = { id: randomUUID(), provider: text(input.provider, "provider", 40), installationId: text(input.installationId, "installationId", 128), externalUserId: text(input.externalUserId, "externalUserId", 128), projectId: input.projectId, subject: input.subject };
      this.db.prepare("INSERT INTO local_channel_bindings(id, provider, installation_id, external_user_id, project_id, subject) VALUES (?, ?, ?, ?, ?, ?)")
        .run(binding.id, binding.provider, binding.installationId, binding.externalUserId, binding.projectId, binding.subject);
      this.audit("identity.binding.created", { bindingId: binding.id, subject: binding.subject, projectId: binding.projectId, provider: binding.provider });
      return binding;
    });
  }
  deleteBinding(id: string, projectId: string): boolean {
    return this.transaction(() => {
      const result = this.db.prepare("DELETE FROM local_channel_bindings WHERE id = ? AND project_id = ?").run(id, projectId);
      if (result.changes) this.audit("identity.binding.deleted", { bindingId: id, projectId });
      return Boolean(result.changes);
    });
  }
  resolveBinding(provider: string, installationId: string, externalUserId: string, projectId: string): string | null {
    const row = this.db.prepare(`SELECT b.subject, p.projects_json FROM local_channel_bindings b JOIN local_principals p ON p.subject = b.subject
      WHERE b.provider = ? AND b.installation_id = ? AND b.external_user_id = ? AND b.project_id = ? AND p.disabled = 0 AND p.kind = 'user'`).get(provider, installationId, externalUserId, projectId);
    return row && subset([projectId], readArray(row.projects_json)) ? String(row.subject) : null;
  }
}

export function localIdentitiesExist(db: SyncDatabase): boolean {
  return db.dialect === "sqlite" && Boolean(db.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'local_principals'").get())
    && Boolean(db.prepare("SELECT subject FROM local_principals LIMIT 1").get());
}
