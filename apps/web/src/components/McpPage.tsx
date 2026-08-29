import { useEffect, useRef, useState, type FormEvent } from "react";

import { api } from "../lib/api";
import type {
  AgatRole,
  CredentialSummary,
  McpOverview,
  McpPolicyDocument,
  McpPolicyPreview,
  McpServer,
  McpToolPolicy,
  SaveMcpServerRequest,
} from "../types";
import { Icon } from "./Icon";

const emptyServer: SaveMcpServerRequest = {
  name: "",
  namespace: "",
  endpoint: "https://",
  credentialId: null,
  enabled: true,
  trustAnnotations: false,
  allowInsecureHttp: false,
  defaultPolicy: "deny",
  catalogTtlSeconds: 300,
};

const riskCopy = {
  read: "Чтение",
  write: "Запись",
  destructive: "Опасный",
  unknown: "Неизвестно",
};

const policyCopy = {
  allow: "Разрешить",
  approval: "Подтверждение",
  deny: "Запретить",
};

const statusCopy = {
  waiting_approval: "Ждёт решения",
  executing: "Выполняется",
  completed: "Завершён",
  failed: "Ошибка",
  rejected: "Отклонён",
  expired: "Истёк",
};

function serverForm(server: McpServer | null): SaveMcpServerRequest {
  return server ? {
    name: server.name,
    namespace: server.namespace,
    endpoint: server.endpoint,
    credentialId: server.credentialId,
    enabled: server.enabled,
    trustAnnotations: server.trustAnnotations,
    allowInsecureHttp: server.allowInsecureHttp,
    defaultPolicy: server.defaultPolicy,
    catalogTtlSeconds: server.catalogTtlSeconds,
  } : emptyServer;
}

function displayEndpoint(value: string): string {
  try {
    const url = new URL(value);
    return `${url.host}${url.pathname}`;
  } catch {
    return value;
  }
}

function shortDate(value: string | null): string {
  if (!value) return "ещё не синхронизирован";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

interface McpServerDialogProps {
  open: boolean;
  server: McpServer | null;
  credentials: CredentialSummary[];
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (payload: SaveMcpServerRequest) => void;
}

function McpServerDialog({ open, server, credentials, busy, error, onClose, onSubmit }: McpServerDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [form, setForm] = useState<SaveMcpServerRequest>(emptyServer);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setForm(serverForm(server));
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open, server]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit({
      ...form,
      name: form.name.trim(),
      namespace: form.namespace.trim().toLowerCase(),
      endpoint: form.endpoint.trim(),
    });
  }

  return (
    <dialog className="run-dialog mcp-dialog" ref={dialogRef} onCancel={onClose} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="dialog-head">
          <div>
            <h2>{server ? "Настройка MCP-сервера" : "Новый MCP-сервер"}</h2>
            <p>Streamable HTTP · протокол 2026-07-28 · policy закрыта по умолчанию</p>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть"><Icon name="close" /></button>
        </div>
        <div className="mcp-dialog__grid">
          <label className="field">
            <span>Название</span>
            <input required maxLength={100} autoFocus value={form.name} placeholder="Внутренний CRM" onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} />
          </label>
          <label className="field">
            <span>Namespace</span>
            <input required maxLength={24} pattern="[a-z][a-z0-9_]{0,23}" value={form.namespace} placeholder="crm" onChange={(event) => setForm((current) => ({ ...current, namespace: event.target.value }))} />
            <small className="field-hint">Префикс защищает имена tools от коллизий.</small>
          </label>
        </div>
        <label className="field">
          <span>Streamable HTTP endpoint</span>
          <input required type="url" maxLength={2_000} value={form.endpoint} placeholder="https://mcp.example.com/mcp" onChange={(event) => setForm((current) => ({ ...current, endpoint: event.target.value }))} />
        </label>
        <div className="mcp-dialog__grid">
          <label className="field">
            <span>Credentials</span>
            <select value={form.credentialId ?? ""} onChange={(event) => setForm((current) => ({ ...current, credentialId: event.target.value || null }))}>
              <option value="">Без credentials</option>
              {credentials.filter((credential) => credential.scope.kind === "project" || credential.scope.serverNamespaces.includes(form.namespace)).map((credential) => <option value={credential.id} key={credential.id}>{credential.name} · {credential.type} · {credential.scope.kind}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Policy по умолчанию</span>
            <select value={form.defaultPolicy} onChange={(event) => setForm((current) => ({ ...current, defaultPolicy: event.target.value as SaveMcpServerRequest["defaultPolicy"] }))}>
              <option value="deny">Deny · ничего не выдавать</option>
              <option value="approval">Approval · решение оператора</option>
              <option value="auto">Auto · только доверенное чтение</option>
            </select>
          </label>
        </div>
        <label className="field">
          <span>TTL каталога, секунд</span>
          <input type="number" min={30} max={86_400} value={form.catalogTtlSeconds} onChange={(event) => setForm((current) => ({ ...current, catalogTtlSeconds: Number(event.target.value) }))} />
        </label>
        <div className="mcp-dialog__checks">
          <label><input type="checkbox" checked={form.enabled} onChange={(event) => setForm((current) => ({ ...current, enabled: event.target.checked }))} /><span><strong>Сервер включён</strong><small>Каталог участвует в lease</small></span></label>
          <label><input type="checkbox" checked={form.trustAnnotations} onChange={(event) => setForm((current) => ({ ...current, trustAnnotations: event.target.checked }))} /><span><strong>Доверять annotations</strong><small>Разрешает risk-классификацию сервера</small></span></label>
          <label><input type="checkbox" checked={form.allowInsecureHttp} onChange={(event) => setForm((current) => ({ ...current, allowInsecureHttp: event.target.checked }))} /><span><strong>Разрешить HTTP</strong><small>Только для контролируемой локальной сети</small></span></label>
        </div>
        {form.trustAnnotations ? <p className="mcp-trust-warning"><Icon name="warning" size={17} /> Аннотации задаёт MCP-сервер. Включайте доверие только после проверки владельца и транспорта.</p> : null}
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="dialog-actions">
          <button className="button button--secondary" type="button" onClick={onClose}>Отмена</button>
          <button className="button button--primary" type="submit" disabled={busy}><Icon name={server ? "check" : "plus"} size={17} />{busy ? "Сохраняем…" : "Сохранить"}</button>
        </div>
      </form>
    </dialog>
  );
}

interface McpPageProps {
  mcp: McpOverview;
  credentials: CredentialSummary[];
  onChanged: () => Promise<void>;
  onManageCredentials: () => void;
  createRequest: number;
  roles: AgatRole[];
}

export function McpPage({ mcp, credentials, onChanged, onManageCredentials, createRequest, roles }: McpPageProps) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<McpServer | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [policyText, setPolicyText] = useState(() => JSON.stringify(mcp.policy.document, null, 2));
  const [policyPreview, setPolicyPreview] = useState<McpPolicyPreview | null>(null);
  const [previewSource, setPreviewSource] = useState("");

  const canManagePolicy = roles.includes("admin") || roles.includes("designer");
  const canManageMcp = canManagePolicy;
  const canEmergencyDeny = roles.includes("admin");

  useEffect(() => {
    setPolicyText(JSON.stringify(mcp.policy.document, null, 2));
    setPolicyPreview(null);
    setPreviewSource("");
  }, [mcp.policy.sha256]);

  useEffect(() => {
    if (createRequest <= 0 || !canManageMcp) return;
    setEditing(null);
    setError(null);
    setDialogOpen(true);
  }, [canManageMcp, createRequest]);

  async function run(key: string, action: () => Promise<unknown>) {
    setBusyKey(key);
    setError(null);
    try {
      await action();
      await onChanged();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Операция MCP завершилась ошибкой");
      throw requestError;
    } finally {
      setBusyKey(null);
    }
  }

  async function save(payload: SaveMcpServerRequest) {
    try {
      await run("save", () => editing ? api.updateMcpServer(editing.id, payload) : api.createMcpServer(payload));
      setDialogOpen(false);
      setEditing(null);
    } catch {
      // Error remains visible in the dialog.
    }
  }

  async function remove(server: McpServer) {
    if (!window.confirm(`Удалить MCP-сервер «${server.name}»? История вызовов останется в audit.`)) return;
    try {
      await run(`delete:${server.id}`, () => api.deleteMcpServer(server.id));
    } catch {
      // Page-level error is already set.
    }
  }

  function parsedPolicy(): McpPolicyDocument {
    const parsed = JSON.parse(policyText) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Policy должна быть JSON-объектом");
    return parsed as McpPolicyDocument;
  }

  async function previewPolicy() {
    try {
      const source = policyText;
      const preview = await api.previewMcpPolicy(parsedPolicy());
      setPolicyPreview(preview);
      setPreviewSource(source);
      setError(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось проверить MCP policy");
    }
  }

  async function activatePolicy() {
    if (!policyPreview || previewSource !== policyText) {
      setError("Policy изменилась после preview. Выполните preview повторно.");
      return;
    }
    try {
      await run("policy:activate", () => api.activateMcpPolicy(parsedPolicy(), policyPreview.baseSha256));
      setPolicyPreview(null);
    } catch {
      // Page-level error is already set.
    }
  }

  async function toggleEmergencyDeny() {
    const enabling = !mcp.policy.emergencyDeny.enabled;
    const reason = window.prompt(
      enabling ? "Причина включения emergency deny" : "Причина снятия emergency deny",
      enabling ? "Incident response" : "Incident resolved",
    );
    if (!reason?.trim()) return;
    try {
      await run("emergency-deny", () => api.setMcpEmergencyDeny(enabling, reason.trim()));
    } catch {
      // Page-level error is already set.
    }
  }

  const tools = mcp.servers.reduce((sum, server) => sum + server.tools.length, 0);
  const allowed = mcp.servers.reduce((sum, server) => sum + server.tools.filter((tool) => tool.policy !== "deny").length, 0);

  return (
    <main className="main-column section-page mcp-page" id="tools">
      <div className="page-title page-title--section">
        <div><h1>MCP gateway</h1><p>Единый каталог tools, project policy, approval и защищённый proxy без передачи secrets worker-узлам</p></div>
        <button className="button button--primary page-title__action page-title__action--always" type="button" disabled={!mcp.enabled || !canManageMcp} onClick={() => { setEditing(null); setError(null); setDialogOpen(true); }}><Icon name="plus" size={17} />MCP-сервер</button>
      </div>

      <section className="mcp-summary" aria-label="Состояние MCP gateway">
        <div><strong>{mcp.servers.length}</strong><span>серверов</span></div>
        <div><strong>{tools}</strong><span>tools в каталогах</span></div>
        <div><strong>{allowed}</strong><span>выдаются в lease</span></div>
        <div><strong>{mcp.servers.reduce((sum, server) => sum + server.waitingCalls, 0)}</strong><span>ждут решения</span></div>
      </section>

      {!mcp.enabled ? <p className="mcp-banner mcp-banner--error"><Icon name="warning" size={18} />Gateway отключён через AGAT_MCP_ENABLED.</p> : null}
      {mcp.policy.emergencyDeny.enabled ? <p className="mcp-banner mcp-banner--error"><Icon name="warning" size={18} />Emergency deny включён: {mcp.policy.emergencyDeny.reason}. Новые вызовы запрещены; выполняются: {mcp.policy.emergencyDeny.executingCalls}.</p> : null}
      {error && !dialogOpen ? <button className="connection-toast mcp-page__error" type="button" onClick={() => setError(null)}>{error}</button> : null}

      <section className="mcp-policy-card" aria-label="MCP policy as code">
        <header>
          <div><span className="eyebrow">POLICY AS CODE</span><h2>{mcp.policy.document.name} · v{mcp.policy.version}</h2><p><code>{mcp.policy.sha256.slice(0, 16)}</code> · {mcp.policy.actor}</p></div>
          {canEmergencyDeny ? <button className={`button ${mcp.policy.emergencyDeny.enabled ? "button--secondary" : "button--danger"}`} type="button" disabled={busyKey !== null} onClick={() => void toggleEmergencyDeny()}><Icon name="warning" size={15} />{mcp.policy.emergencyDeny.enabled ? "Снять emergency deny" : "Emergency deny"}</button> : null}
        </header>
        <div className="mcp-policy-editor">
          <textarea aria-label="MCP policy JSON" spellCheck={false} value={policyText} readOnly={!canManagePolicy} onChange={(event) => { setPolicyText(event.target.value); setPolicyPreview(null); }} />
          <aside>
            <h3>Risk tiers</h3>
            {(Object.entries(mcp.policy.document.defaults) as Array<[string, { tier: string; effect: string; approvals: number }]>).map(([risk, decision]) => <div key={risk}><code>{risk}</code><span>{decision.tier} · {decision.effect} · {decision.approvals} eyes</span></div>)}
            <p>Critical и destructive всегда требуют два разных OIDC subject либо deny.</p>
          </aside>
        </div>
        {policyPreview ? (
          <div className="mcp-policy-preview">
            <strong>Preview diff · {policyPreview.summary.toolsChanged}/{policyPreview.summary.toolsEvaluated} tools</strong>
            <span>+allow {policyPreview.summary.newlyAllowed} · +deny {policyPreview.summary.newlyDenied} · approvals ↑{policyPreview.summary.approvalsIncreased} ↓{policyPreview.summary.approvalsDecreased}</span>
            {policyPreview.changes.slice(0, 20).map((change) => <div key={`${change.serverId}:${change.toolName}`}><code>{change.publicName}</code><span>{change.before.tier}/{change.before.effect}/{change.before.approvals} → {change.after.tier}/{change.after.effect}/{change.after.approvals}</span></div>)}
          </div>
        ) : null}
        {canManagePolicy ? <footer><button className="button button--secondary" type="button" disabled={busyKey !== null} onClick={() => void previewPolicy()}>Preview diff</button><button className="button button--primary" type="button" disabled={busyKey !== null || !policyPreview || previewSource !== policyText || !policyPreview.changed} onClick={() => void activatePolicy()}>Активировать immutable version</button></footer> : null}
      </section>

      {mcp.servers.length === 0 ? (
        <section className="large-empty-state large-empty-state--compact">
          <Icon name="plug" size={32} />
          <h2>Подключённых MCP-серверов пока нет</h2>
          <p>Добавьте Streamable HTTP endpoint. Новый сервер создаётся с deny policy; каталог не станет доступен агентам до явного решения.</p>
          {canManageMcp ? <button className="button button--primary" type="button" onClick={() => setDialogOpen(true)}><Icon name="plus" size={16} />Добавить сервер</button> : null}
        </section>
      ) : (
        <section className="mcp-server-list" aria-label="MCP-серверы">
          {mcp.servers.map((server) => (
            <article className={`mcp-server${server.enabled ? "" : " is-disabled"}`} key={server.id}>
              <header className="mcp-server__head">
                <span className="mcp-server__icon"><Icon name="plug" /></span>
                <div>
                  <h2>{server.name}<code>{server.namespace}</code></h2>
                  <p title={server.endpoint}>{displayEndpoint(server.endpoint)}</p>
                </div>
                <span className={`mcp-health ${server.lastError ? "mcp-health--error" : server.lastSyncAt ? "mcp-health--ok" : ""}`}><i />{!server.enabled ? "выключен" : server.lastError ? "ошибка" : server.lastSyncAt ? "готов" : "не синхронизирован"}</span>
                <div className="mcp-server__actions">
                  <button className="button button--secondary" type="button" disabled={!canManageMcp || busyKey !== null || !server.enabled} onClick={() => void run(`sync:${server.id}`, () => api.syncMcpServer(server.id)).catch(() => undefined)}><Icon name="repeat" size={14} />Синхронизировать</button>
                  <button className="icon-button" type="button" aria-label="Настроить сервер" disabled={!canManageMcp} onClick={() => { setEditing(server); setError(null); setDialogOpen(true); }}><Icon name="dots" /></button>
                  <button className="icon-button icon-button--danger" type="button" aria-label="Удалить сервер" disabled={!canManageMcp || busyKey !== null} onClick={() => void remove(server)}><Icon name="trash" size={17} /></button>
                </div>
              </header>
              <dl className="mcp-server__meta">
                <div><dt>Протокол</dt><dd>{server.protocolVersion}</dd></div>
                <div><dt>Default policy</dt><dd>{server.defaultPolicy}</dd></div>
                <div><dt>Annotations</dt><dd>{server.trustAnnotations ? "trusted" : "untrusted"}</dd></div>
                <div><dt>Каталог</dt><dd>{shortDate(server.lastSyncAt)}</dd></div>
              </dl>
              {server.lastError ? <p className="mcp-server__error"><Icon name="warning" size={15} />{server.lastError}</p> : null}
              <div className="mcp-tools">
                <div className="mcp-tools__head"><strong>Tools · {server.tools.length}</strong><span>Effective policy применяется до передачи схемы worker</span></div>
                {server.tools.length === 0 ? <p className="mcp-tools__empty">Каталог пуст. Проверьте endpoint, credentials и синхронизацию.</p> : server.tools.map((tool) => (
                  <div className="mcp-tool" key={tool.name}>
                    <div><code>{tool.publicName}</code><p>{tool.title || tool.description || tool.name}</p></div>
                    <span className={`mcp-risk mcp-risk--${tool.risk}`}>{riskCopy[tool.risk]} · {tool.riskTier}</span>
                    <span className="mcp-tool__decision">{tool.policy} · {tool.requiredApprovals} eyes</span>
                    <select aria-label={`Legacy policy ${tool.publicName}`} value={tool.policyOverride ?? tool.legacyPolicy} disabled={!canManageMcp || busyKey !== null} onChange={(event) => void run(`policy:${server.id}:${tool.name}`, () => api.setMcpToolPolicy(server.id, tool.name, event.target.value as McpToolPolicy)).catch(() => undefined)}>
                      {(Object.keys(policyCopy) as McpToolPolicy[]).map((policy) => <option value={policy} key={policy}>{policyCopy[policy]}</option>)}
                    </select>
                  </div>
                ))}
              </div>
            </article>
          ))}
        </section>
      )}

      <section className="mcp-audit">
        <div className="mcp-audit__head"><div><span className="eyebrow">AUDIT</span><h2>Последние MCP-вызовы</h2></div>{canManageMcp ? <button className="button button--secondary" type="button" onClick={onManageCredentials}><Icon name="shield" size={15} />Credentials</button> : null}</div>
        {mcp.recentCalls.length === 0 ? <p className="mcp-audit__empty">Вызовов пока нет.</p> : (
          <div className="mcp-call-list">
            {mcp.recentCalls.slice(0, 30).map((call) => (
              <div className="mcp-call" key={call.callId}>
                <time>{shortDate(call.createdAt)}</time>
                <div><code>{call.publicName}</code><small>{call.serverName} · {call.riskTier} · {call.policy} · approvals {call.approvalCount}/{call.requiredApprovals}</small></div>
                <span className={`mcp-call__status mcp-call__status--${call.status}`}>{statusCopy[call.status]}</span>
                <p>{call.error || JSON.stringify(call.arguments)}</p>
              </div>
            ))}
          </div>
        )}
      </section>

      <McpServerDialog open={dialogOpen} server={editing} credentials={credentials} busy={busyKey === "save"} error={dialogOpen ? error : null} onClose={() => { setDialogOpen(false); setEditing(null); setError(null); }} onSubmit={(payload) => void save(payload)} />
    </main>
  );
}
