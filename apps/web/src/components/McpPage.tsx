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
import { useActionDialog } from "./ActionDialog";
import { Icon } from "./Icon";

const emptyServer: SaveMcpServerRequest = {
  name: "",
  namespace: "",
  transport: "http",
  endpoint: "https://",
  credentialId: null,
  enabled: true,
  trustAnnotations: false,
  allowInsecureHttp: false,
  defaultPolicy: "deny",
  catalogTtlSeconds: 300,
};
const MAX_WASM_UPLOAD_BYTES = 512 * 1024;

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
    transport: server.transport,
    endpoint: server.transport === "http" ? server.endpoint : "",
    sandbox: server.sandbox ? { ...server.sandbox } : undefined,
    credentialId: server.credentialId,
    enabled: server.enabled,
    trustAnnotations: server.trustAnnotations,
    allowInsecureHttp: server.allowInsecureHttp,
    defaultPolicy: server.defaultPolicy,
    catalogTtlSeconds: server.catalogTtlSeconds,
  } : emptyServer;
}

function isolatedRisk(server: SaveMcpServerRequest): keyof typeof riskCopy {
  const annotations = server.sandbox?.tool.annotations;
  if (annotations?.destructiveHint === true) return "destructive";
  if (annotations?.readOnlyHint === true) return "read";
  if (annotations?.idempotentHint === true) return "write";
  return "unknown";
}

function riskAnnotations(
  risk: keyof typeof riskCopy,
  current: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  const next = { ...(current ?? {}) };
  delete next.destructiveHint;
  delete next.readOnlyHint;
  delete next.idempotentHint;
  if (risk === "destructive") next.destructiveHint = true;
  if (risk === "read") next.readOnlyHint = true;
  if (risk === "write") next.idempotentHint = true;
  return next;
}

function fileBase64(file: File): Promise<string> {
  if (file.size < 8 || file.size > MAX_WASM_UPLOAD_BYTES) {
    return Promise.reject(new Error("WASI module должен иметь размер от 8 байт до 512 KiB"));
  }
  return file.arrayBuffer().then((buffer) => {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 32_768) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 32_768));
    }
    return btoa(binary);
  });
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
  canManageSandbox: boolean;
  onClose: () => void;
  onSubmit: (payload: SaveMcpServerRequest) => void;
}

function McpServerDialog({ open, server, credentials, busy, error, canManageSandbox, onClose, onSubmit }: McpServerDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [form, setForm] = useState<SaveMcpServerRequest>(emptyServer);
  const [schemaText, setSchemaText] = useState('{"type":"object","properties":{}}');
  const [commandText, setCommandText] = useState('["/opt/tool","run"]');
  const [egressText, setEgressText] = useState("[]");
  const [moduleName, setModuleName] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      const next = serverForm(server);
      setForm(next);
      setSchemaText(JSON.stringify(next.sandbox?.tool.inputSchema ?? { type: "object", properties: {} }, null, 2));
      setCommandText(JSON.stringify(next.sandbox?.command ?? ["/opt/tool", "run"], null, 2));
      setEgressText(JSON.stringify(next.sandbox?.egress ?? [], null, 2));
      setModuleName(next.sandbox?.moduleSha256 ? `Сохранён · ${next.sandbox.moduleSha256.slice(0, 16)}` : "");
      setValidationError(null);
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open, server]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const payload: SaveMcpServerRequest = {
        ...form,
        name: form.name.trim(),
        namespace: form.namespace.trim().toLowerCase(),
        endpoint: form.transport === "http" ? form.endpoint?.trim() : undefined,
      };
      if (form.transport !== "http") {
        const inputSchema = JSON.parse(schemaText) as unknown;
        const command = form.transport === "container" ? JSON.parse(commandText) as unknown : [];
        const egress = form.transport === "container" ? JSON.parse(egressText) as unknown : [];
        if (!inputSchema || typeof inputSchema !== "object" || Array.isArray(inputSchema)) throw new Error("Input schema должна быть JSON-объектом");
        if (!Array.isArray(command) || !command.every((part) => typeof part === "string") || !Array.isArray(egress)) {
          throw new Error("Command должен быть массивом строк, egress — JSON-массивом");
        }
        payload.sandbox = {
          ...form.sandbox!,
          tool: { ...form.sandbox!.tool, inputSchema: inputSchema as Record<string, unknown> },
          command: command.map(String),
          egress: egress as Array<{ ip: string; port: number }>,
        };
      }
      setValidationError(null);
      onSubmit(payload);
    } catch (submitError) {
      setValidationError(submitError instanceof Error ? submitError.message : "Некорректная sandbox-конфигурация");
    }
  }

  function selectTransport(transport: SaveMcpServerRequest["transport"]) {
    setForm((current) => ({
      ...current,
      transport,
      endpoint: transport === "http" ? current.endpoint || "https://" : "",
      trustAnnotations: transport === "http" ? current.trustAnnotations : true,
      allowInsecureHttp: transport === "http" ? current.allowInsecureHttp : false,
      sandbox: transport === "http" ? undefined : current.sandbox ?? {
        tool: {
          name: "",
          description: "",
          inputSchema: { type: "object", properties: {} },
          annotations: {},
        },
        command: transport === "container" ? ["/opt/tool", "run"] : [],
        timeoutSeconds: 30,
        cpuMillis: 500,
        memoryMiB: 128,
        egress: [],
      },
    }));
  }

  function updateSandbox(patch: Partial<NonNullable<SaveMcpServerRequest["sandbox"]>>) {
    setForm((current) => ({ ...current, sandbox: { ...current.sandbox!, ...patch } }));
  }

  function updateSandboxTool(patch: Partial<NonNullable<SaveMcpServerRequest["sandbox"]>["tool"]>) {
    setForm((current) => ({
      ...current,
      sandbox: { ...current.sandbox!, tool: { ...current.sandbox!.tool, ...patch } },
    }));
  }

  return (
    <dialog className="run-dialog mcp-dialog" ref={dialogRef} aria-labelledby="mcp-dialog-title" onCancel={onClose} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="dialog-head">
          <div>
            <h2 id="mcp-dialog-title">{server ? "Настройка MCP-сервера" : "Новый MCP-сервер"}</h2>
            <p>Streamable HTTP, WASI или digest-pinned OCI · единый policy boundary</p>
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
          <span>Transport</span>
          <select value={form.transport} disabled={Boolean(server && server.transport !== "http" && !canManageSandbox)} onChange={(event) => selectTransport(event.target.value as SaveMcpServerRequest["transport"])}>
            <option value="http">Streamable HTTP</option>
            {canManageSandbox ? <option value="wasi">WASM / WASI Preview 1</option> : null}
            {canManageSandbox ? <option value="container">OCI container by digest</option> : null}
          </select>
          <small className="field-hint">WASI и OCI profiles доступны только admin и всегда проходят MCP approvals.</small>
        </label>
        {form.transport === "http" ? (
          <label className="field">
            <span>Streamable HTTP endpoint</span>
            <input required type="url" maxLength={2_000} value={form.endpoint ?? ""} placeholder="https://mcp.example.com/mcp" onChange={(event) => setForm((current) => ({ ...current, endpoint: event.target.value }))} />
          </label>
        ) : (
          <section className="mcp-sandbox-form" aria-label="Профиль изолированного tool">
            <div className="mcp-dialog__grid">
              <label className="field"><span>Tool name</span><input required maxLength={128} pattern="[A-Za-z0-9][A-Za-z0-9_-]*" value={form.sandbox?.tool.name ?? ""} placeholder="render_report" onChange={(event) => updateSandboxTool({ name: event.target.value })} /></label>
              <label className="field"><span>Risk annotation</span><select value={isolatedRisk(form)} onChange={(event) => updateSandboxTool({ annotations: riskAnnotations(event.target.value as keyof typeof riskCopy, form.sandbox?.tool.annotations) })}><option value="read">Read only</option><option value="write">Write / idempotent</option><option value="destructive">Destructive · four-eyes</option><option value="unknown">Unknown</option></select></label>
            </div>
            <label className="field"><span>Описание</span><input maxLength={8_000} value={form.sandbox?.tool.description ?? ""} onChange={(event) => updateSandboxTool({ description: event.target.value })} /></label>
            <label className="field"><span>Input schema · JSON</span><textarea className="mcp-sandbox-form__json" spellCheck={false} value={schemaText} onChange={(event) => setSchemaText(event.target.value)} /></label>
            {form.transport === "wasi" ? (
              <label className="field"><span>WASI module</span><input required={!server?.sandbox?.moduleSha256} type="file" accept=".wasm,application/wasm" onChange={(event) => { const file = event.target.files?.[0]; if (!file) return; setValidationError(null); void fileBase64(file).then((moduleBase64) => { updateSandbox({ moduleBase64 }); setModuleName(file.name); }).catch((uploadError: unknown) => setValidationError(uploadError instanceof Error ? uploadError.message : "Не удалось прочитать WASI module")); }} /><small className="field-hint">{moduleName || "До 512 KiB; filesystem и network capabilities не выдаются."}</small></label>
            ) : (
              <>
                <label className="field"><span>OCI image digest</span><input required maxLength={512} value={form.sandbox?.image ?? ""} placeholder={`registry.example/tool@sha256:${"a".repeat(64)}`} onChange={(event) => updateSandbox({ image: event.target.value })} /></label>
                <div className="mcp-dialog__grid">
                  <label className="field"><span>Exec command · JSON array</span><textarea className="mcp-sandbox-form__json" spellCheck={false} value={commandText} onChange={(event) => setCommandText(event.target.value)} /></label>
                  <label className="field"><span>Egress · exact IP/port JSON</span><textarea className="mcp-sandbox-form__json" spellCheck={false} value={egressText} onChange={(event) => setEgressText(event.target.value)} /></label>
                </div>
              </>
            )}
            <div className="mcp-dialog__grid mcp-sandbox-form__limits">
              <label className="field"><span>Timeout, сек.</span><input type="number" min={1} max={120} value={form.sandbox?.timeoutSeconds ?? 30} onChange={(event) => updateSandbox({ timeoutSeconds: Number(event.target.value) })} /></label>
              <label className="field"><span>CPU, millicores</span><input type="number" min={25} max={4_000} value={form.sandbox?.cpuMillis ?? 500} onChange={(event) => updateSandbox({ cpuMillis: Number(event.target.value) })} /></label>
              <label className="field"><span>Memory, MiB</span><input type="number" min={32} max={2_048} value={form.sandbox?.memoryMiB ?? 128} onChange={(event) => updateSandbox({ memoryMiB: Number(event.target.value) })} /></label>
            </div>
          </section>
        )}
        <div className="mcp-dialog__grid">
          <label className="field">
            <span>Credentials</span>
            <select value={form.credentialId ?? ""} onChange={(event) => setForm((current) => ({ ...current, credentialId: event.target.value || null }))}>
              <option value="">Без credentials</option>
              {credentials.filter((credential) => (form.transport === "http" || credential.scope.kind === "mcp") && (credential.scope.kind === "project" || credential.scope.serverNamespaces.includes(form.namespace))).map((credential) => <option value={credential.id} key={credential.id}>{credential.name} · {credential.type} · {credential.scope.kind}</option>)}
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
        {form.transport === "http" ? <label className="field">
          <span>TTL каталога, секунд</span>
          <input type="number" min={30} max={86_400} value={form.catalogTtlSeconds} onChange={(event) => setForm((current) => ({ ...current, catalogTtlSeconds: Number(event.target.value) }))} />
        </label> : null}
        <div className="mcp-dialog__checks">
          <label><input type="checkbox" checked={form.enabled} onChange={(event) => setForm((current) => ({ ...current, enabled: event.target.checked }))} /><span><strong>Сервер включён</strong><small>Каталог участвует в lease</small></span></label>
          <label><input type="checkbox" checked={form.trustAnnotations} disabled={form.transport !== "http"} onChange={(event) => setForm((current) => ({ ...current, trustAnnotations: event.target.checked }))} /><span><strong>Доверять annotations</strong><small>{form.transport === "http" ? "Разрешает risk-классификацию сервера" : "Isolated manifest утверждает admin"}</small></span></label>
          {form.transport === "http" ? <label><input type="checkbox" checked={form.allowInsecureHttp} onChange={(event) => setForm((current) => ({ ...current, allowInsecureHttp: event.target.checked }))} /><span><strong>Разрешить HTTP</strong><small>Только для контролируемой локальной сети</small></span></label> : <label><input type="checkbox" checked readOnly /><span><strong>Read-only root</strong><small>Non-root, seccomp и drop ALL capabilities</small></span></label>}
        </div>
        {form.trustAnnotations ? <p className="mcp-trust-warning"><Icon name="warning" size={17} /> {form.transport === "http" ? "Аннотации задаёт MCP-сервер. Включайте доверие только после проверки владельца и транспорта." : "Risk annotation входит в исполняемый profile и фиксируется его SHA-256; изменение требует нового approval."}</p> : null}
        {validationError || error ? <p className="form-error" role="alert">{validationError || error}</p> : null}
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
  const requestAction = useActionDialog();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<McpServer | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [policyText, setPolicyText] = useState(() => JSON.stringify(mcp.policy.document, null, 2));
  const [policyPreview, setPolicyPreview] = useState<McpPolicyPreview | null>(null);
  const [previewSource, setPreviewSource] = useState("");

  const canManagePolicy = roles.includes("admin") || roles.includes("designer");
  const canManageMcp = canManagePolicy;
  const canManageSandbox = roles.includes("admin");
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
    const decision = await requestAction({
      title: "Удалить MCP-сервер?",
      description: "Tools этого сервера исчезнут из каталога и перестанут выдаваться новым lease.",
      subject: server.name,
      subjectLabel: "MCP-сервер",
      impact: "Подключение, catalog cache и tool policy сервера будут удалены. История вызовов останется в audit.",
      recovery: "Добавьте сервер заново, привяжите credential и повторно проверьте tool policy.",
      confirmLabel: "Удалить сервер",
      tone: "danger",
    });
    if (!decision.confirmed) return;
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
    const decision = await requestAction({
      title: enabling ? "Включить emergency deny?" : "Снять emergency deny?",
      description: enabling
        ? "Это глобальный circuit breaker для MCP-вызовов текущего проекта."
        : "MCP-вызовы снова будут оцениваться и выполняться по активной policy.",
      subject: "MCP policy текущего проекта",
      subjectLabel: "Контур",
      impact: enabling
        ? "Ожидающие и новые MCP-вызовы будут заблокированы; активные процессы могут завершиться ошибкой."
        : "Новые MCP-вызовы снова смогут проходить по правилам allow/approval активной policy.",
      recovery: enabling
        ? "Emergency deny можно снять отдельным аудируемым действием после завершения incident response."
        : "Circuit breaker можно включить повторно, но уже разрешённые side effects автоматически не откатываются.",
      confirmLabel: enabling ? "Включить emergency deny" : "Снять emergency deny",
      tone: "danger",
      input: {
        label: "Причина изменения",
        placeholder: enabling ? "Например: подозрение на утечку credential" : "Например: инцидент устранён, policy проверена",
        defaultValue: enabling ? "Incident response" : "Incident resolved",
        hint: "Причина попадёт в audit trail и поможет восстановить контекст решения.",
        required: true,
        requiredMessage: "Укажите причину изменения emergency deny",
        maxLength: 1_000,
      },
    });
    const reason = decision.value;
    if (!decision.confirmed || !reason) return;
    try {
      await run("emergency-deny", () => api.setMcpEmergencyDeny(enabling, reason));
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
      {mcp.sandbox && !mcp.sandbox.available ? <p className="mcp-banner"><Icon name="shield" size={18} />Sandbox недоступен: {mcp.sandbox.reason}. HTTP MCP продолжает работать.</p> : null}
      {mcp.sandbox?.available && !mcp.sandbox.networkPolicyEnforced ? <p className="mcp-banner"><Icon name="warning" size={18} />WASI доступен; OCI tools fail-closed до подтверждения enforcement NetworkPolicy.</p> : null}
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
          <p>Добавьте Streamable HTTP endpoint или admin-managed isolated tool. Новый источник создаётся с deny policy.</p>
          {canManageMcp ? <button className="button button--primary" type="button" onClick={() => setDialogOpen(true)}><Icon name="plus" size={16} />Добавить сервер</button> : null}
        </section>
      ) : (
        <section className="mcp-server-list" aria-label="MCP-серверы">
          {mcp.servers.map((server) => (
            <article className={`mcp-server${server.enabled ? "" : " is-disabled"}`} key={server.id}>
              <header className="mcp-server__head">
                <span className="mcp-server__icon"><Icon name={server.transport === "http" ? "plug" : "shield"} /></span>
                <div>
                  <h2>{server.name}<code>{server.namespace}</code></h2>
                  <p title={server.endpoint}>{server.transport === "http" ? displayEndpoint(server.endpoint) : `${server.transport.toUpperCase()} · ${server.sandbox?.profileSha256?.slice(0, 20) ?? "profile"}`}</p>
                </div>
                <span className={`mcp-health ${server.lastError ? "mcp-health--error" : server.lastSyncAt ? "mcp-health--ok" : ""}`}><i />{!server.enabled ? "выключен" : server.lastError ? "ошибка" : server.lastSyncAt ? "готов" : "не синхронизирован"}</span>
                <div className="mcp-server__actions">
                  {server.transport === "http" ? <button className="button button--secondary" type="button" disabled={!canManageMcp || busyKey !== null || !server.enabled} onClick={() => void run(`sync:${server.id}`, () => api.syncMcpServer(server.id)).catch(() => undefined)}><Icon name="repeat" size={14} />Синхронизировать</button> : null}
                  <button className="icon-button" type="button" aria-label="Настроить сервер" disabled={!canManageMcp || (server.transport !== "http" && !canManageSandbox)} onClick={() => { setEditing(server); setError(null); setDialogOpen(true); }}><Icon name="dots" /></button>
                  <button className="icon-button icon-button--danger" type="button" aria-label="Удалить сервер" disabled={!canManageMcp || (server.transport !== "http" && !canManageSandbox) || busyKey !== null} onClick={() => void remove(server)}><Icon name="trash" size={17} /></button>
                </div>
              </header>
              <dl className="mcp-server__meta">
                <div><dt>Transport</dt><dd>{server.transport === "http" ? server.protocolVersion : server.transport.toUpperCase()}</dd></div>
                <div><dt>Default policy</dt><dd>{server.defaultPolicy}</dd></div>
                <div><dt>Annotations</dt><dd>{server.trustAnnotations ? "trusted" : "untrusted"}</dd></div>
                <div><dt>{server.transport === "http" ? "Каталог" : "Profile SHA-256"}</dt><dd>{server.transport === "http" ? shortDate(server.lastSyncAt) : server.sandbox?.profileSha256?.slice(0, 16)}</dd></div>
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
                <div><code>{call.publicName}</code><small>{call.serverName} · {call.transport ?? "http"}{call.sandboxProfileSha256 ? ` · profile ${call.sandboxProfileSha256.slice(0, 16)}` : ""} · {call.riskTier} · {call.policy} · approvals {call.approvalCount}/{call.requiredApprovals}</small></div>
                <span className={`mcp-call__status mcp-call__status--${call.status}`}>{statusCopy[call.status]}</span>
                <p>{call.error || JSON.stringify(call.arguments)}</p>
              </div>
            ))}
          </div>
        )}
      </section>

      <McpServerDialog open={dialogOpen} server={editing} credentials={credentials} busy={busyKey === "save"} error={dialogOpen ? error : null} canManageSandbox={canManageSandbox} onClose={() => { setDialogOpen(false); setEditing(null); setError(null); }} onSubmit={(payload) => void save(payload)} />
    </main>
  );
}
