import { useEffect, useRef, useState } from "react";

import type { CreateCredentialRequest, CredentialSummary, CredentialType, McpToolRisk } from "../types";
import { Icon } from "./Icon";
import { useRecoveryTarget } from "../hooks/useRecoveryTarget";

interface CredentialsDialogProps {
  open: boolean;
  credentials: CredentialSummary[];
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSave: (id: string | null, payload: CreateCredentialRequest) => void;
  onDelete: (credential: CredentialSummary) => void;
}

export function CredentialsDialog({
  open,
  credentials,
  busy,
  error,
  onClose,
  onSave,
  onDelete,
}: CredentialsDialogProps) {
  const recovery = useRecoveryTarget();
  const [editingId, setEditingId] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState("");
  const [type, setType] = useState<CredentialType>("http_header");
  const [headerName, setHeaderName] = useState("Authorization");
  const [secret, setSecret] = useState("");
  const [scopeKind, setScopeKind] = useState<"project" | "mcp">("project");
  const [scopeNamespaces, setScopeNamespaces] = useState("");
  const [scopeTools, setScopeTools] = useState("*");
  const [scopeRisks, setScopeRisks] = useState<McpToolRisk[]>(["read", "write", "destructive", "unknown"]);
  const [scopeCatalog, setScopeCatalog] = useState(true);
  const [scopeExpiresAt, setScopeExpiresAt] = useState("");

  function editCredential(credential: CredentialSummary) {
    setEditingId(credential.id);
    setName(credential.name);
    setType(credential.type);
    setHeaderName(credential.fields.includes("headerName") ? "Authorization" : "X-API-Key");
    setSecret("");
    setScopeKind(credential.scope.kind);
    setScopeNamespaces(credential.scope.serverNamespaces.join(", "));
    setScopeTools(credential.scope.toolPatterns.join(", ") || "*");
    setScopeRisks(credential.scope.risks);
    setScopeCatalog(credential.scope.allowCatalog);
    setScopeExpiresAt(credential.scope.expiresAt ? credential.scope.expiresAt.slice(0, 16) : "");
  }
  const recoveryId = recovery.get("credentials");
  useEffect(() => {
    if (!open) return;
    const credential = credentials.find((item) => item.id === recoveryId);
    if (credential) editCredential(credential);
  }, [open, recoveryId]);

  function reset() {
    setEditingId(null);
    setName("");
    setType("http_header");
    setHeaderName("Authorization");
    setSecret("");
    setScopeKind("project");
    setScopeNamespaces("");
    setScopeTools("*");
    setScopeRisks(["read", "write", "destructive", "unknown"]);
    setScopeCatalog(true);
    setScopeExpiresAt("");
  }

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) {
      dialog.close();
      reset();
    }
  }, [open]);

  const payload: CreateCredentialRequest = {
    name,
    type,
    data: type === "http_header"
      ? { headerName, headerValue: secret }
      : { apiKey: secret },
    scope: scopeKind === "mcp" ? {
      kind: "mcp",
      serverNamespaces: scopeNamespaces.split(/[\s,]+/).map((value) => value.trim()).filter(Boolean),
      toolPatterns: scopeTools.split(/[\s,]+/).map((value) => value.trim()).filter(Boolean),
      risks: scopeRisks,
      allowCatalog: scopeCatalog,
      expiresAt: scopeExpiresAt ? new Date(scopeExpiresAt).toISOString() : null,
    } : {
      kind: "project",
      serverNamespaces: [],
      toolPatterns: [],
      risks: ["read", "write", "destructive", "unknown"],
      allowCatalog: true,
      expiresAt: null,
    },
  };

  return (
    <dialog className="run-dialog credentials-dialog" ref={dialogRef} aria-labelledby="credentials-dialog-title" onCancel={onClose} onClose={onClose}>
      <section aria-label="Credentials">
        <header>
          <div><span className="eyebrow">СЕКРЕТЫ И ИНТЕГРАЦИИ</span><h2 id="credentials-dialog-title">Credentials</h2></div>
          <button className="icon-button" type="button" aria-label="Закрыть" onClick={onClose}><Icon name="close" size={18} /></button>
        </header>
        <div className="credentials-dialog__body">
          <section className="credentials-list">
            <header><strong>Сохранённые</strong><span>{credentials.length}</span></header>
            {credentials.length === 0 ? <p>Credentials пока нет. Значения шифруются до записи в SQLite.</p> : null}
            {credentials.map((credential) => (
              <article className={editingId === credential.id ? "is-selected" : ""} key={credential.id}>
                <span><Icon name="shield" size={17} /></span>
                <button type="button" onClick={() => editCredential(credential)}>
                  <strong>{credential.name}</strong>
                  <small>{credential.type === "http_header" ? "HTTP header" : "Bearer API key"} · {credential.scope.kind === "mcp" ? `MCP: ${credential.scope.serverNamespaces.join(", ")}` : "project scope"}</small>
                </button>
                <button className="icon-button" type="button" title="Удалить" disabled={busy} onClick={() => onDelete(credential)}><Icon name="trash" size={15} /></button>
              </article>
            ))}
          </section>
          <form className="credentials-form" onSubmit={(event) => {
            event.preventDefault();
            onSave(editingId, payload);
          }}>
            <h3>{editingId ? "Заменить секрет" : "Новые credentials"}</h3>
            <p>Существующее значение никогда не загружается обратно в браузер.</p>
            <label className="field"><span>Название</span><input required maxLength={100} value={name} onChange={(event) => setName(event.target.value)} /></label>
            <label className="field">
              <span>Тип</span>
              <select value={type} onChange={(event) => setType(event.target.value as CredentialType)}>
                <option value="http_header">Произвольный HTTP header</option>
                <option value="api_key">Bearer API key</option>
              </select>
            </label>
            {type === "http_header" ? (
              <label className="field"><span>Имя заголовка</span><input required maxLength={80} value={headerName} onChange={(event) => setHeaderName(event.target.value)} /></label>
            ) : null}
            <label className="field"><span>{editingId ? "Новое значение" : "Секрет"}</span><input required type="password" autoComplete="new-password" maxLength={8_000} value={secret} onChange={(event) => setSecret(event.target.value)} /></label>
            <label className="field">
              <span>Scope секрета</span>
              <select value={scopeKind} onChange={(event) => setScopeKind(event.target.value as "project" | "mcp")}>
                <option value="project">Project · HTTP и MCP проекта</option>
                <option value="mcp">MCP · ограничить namespace/tools/risk</option>
              </select>
            </label>
            {scopeKind === "mcp" ? (
              <>
                <label className="field"><span>MCP namespaces</span><input required value={scopeNamespaces} placeholder="crm, billing" onChange={(event) => setScopeNamespaces(event.target.value)} /><small className="field-hint">Точные namespaces через запятую; wildcard запрещён.</small></label>
                <label className="field"><span>Tool patterns</span><input required value={scopeTools} placeholder="find_*, update_customer" onChange={(event) => setScopeTools(event.target.value)} /></label>
                <fieldset className="credential-scope-risks">
                  <legend>Разрешённые risk labels</legend>
                  {(["read", "write", "destructive", "unknown"] as McpToolRisk[]).map((risk) => (
                    <label key={risk}><input type="checkbox" checked={scopeRisks.includes(risk)} onChange={(event) => setScopeRisks((current) => event.target.checked ? [...current, risk] : current.filter((item) => item !== risk))} />{risk}</label>
                  ))}
                </fieldset>
                <label className="field"><span>Expires at</span><input type="datetime-local" value={scopeExpiresAt} onChange={(event) => setScopeExpiresAt(event.target.value)} /><small className="field-hint">Опциональный срок действия scope.</small></label>
                <label className="credential-scope-toggle"><input type="checkbox" checked={scopeCatalog} onChange={(event) => setScopeCatalog(event.target.checked)} />Передавать secret при tools/list</label>
              </>
            ) : null}
            {error ? <div className="dialog-error"><Icon name="warning" size={16} />{error}</div> : null}
            <footer>
              {editingId ? <button className="button button--secondary" type="button" onClick={reset}>Отмена</button> : <span />}
              <button className="button button--primary" type="submit" disabled={busy || !name.trim() || !secret || (scopeKind === "mcp" && (!scopeNamespaces.trim() || !scopeTools.trim() || scopeRisks.length === 0))}>{busy ? "Сохраняем…" : editingId ? "Заменить" : "Создать"}</button>
            </footer>
          </form>
        </div>
      </section>
    </dialog>
  );
}
