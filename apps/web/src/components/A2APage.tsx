import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api } from "../lib/api";
import type {
  A2AEndpoint,
  A2AEndpointSecret,
  A2AInputMode,
  A2ASnapshot,
  A2ATaskState,
  Agent,
  AgatRole,
  KnowledgeCollection,
  SaveA2AEndpointRequest,
} from "../types";
import { Icon } from "./Icon";

interface A2APageProps {
  projectId: string;
  agents: Agent[];
  collections: KnowledgeCollection[];
  roles: AgatRole[];
  createRequest: number;
  onChanged: () => Promise<void>;
  onOpenRun: (runId: string) => void;
}

interface EndpointForm {
  agentId: string;
  name: string;
  description: string;
  version: string;
  skillId: string;
  skillName: string;
  skillDescription: string;
  tags: string;
  examples: string;
  inputModes: A2AInputMode[];
  knowledgeCollectionIds: string[];
  approvalRequired: boolean;
  enabled: boolean;
  priority: number;
  maxInputCharacters: number;
  maxActiveTasks: number;
}

const stateCopy: Record<A2ATaskState, string> = {
  TASK_STATE_UNSPECIFIED: "Неизвестно",
  TASK_STATE_SUBMITTED: "В очереди",
  TASK_STATE_WORKING: "Выполняется",
  TASK_STATE_COMPLETED: "Завершён",
  TASK_STATE_FAILED: "Ошибка",
  TASK_STATE_CANCELED: "Отменён",
  TASK_STATE_INPUT_REQUIRED: "Нужен ввод",
  TASK_STATE_REJECTED: "Отклонён",
  TASK_STATE_AUTH_REQUIRED: "Нужно решение",
};

function emptyForm(agent: Agent | undefined): EndpointForm {
  return {
    agentId: agent?.id ?? "",
    name: agent?.name ?? "",
    description: agent?.role ?? "",
    version: "1.0.0",
    skillId: "",
    skillName: agent?.name ?? "",
    skillDescription: agent?.role ?? "",
    tags: "agat, local-agent",
    examples: "",
    inputModes: ["text/plain"],
    knowledgeCollectionIds: [],
    approvalRequired: false,
    enabled: true,
    priority: 50,
    maxInputCharacters: 20_000,
    maxActiveTasks: 10,
  };
}

function endpointForm(endpoint: A2AEndpoint): EndpointForm {
  return {
    agentId: endpoint.agentId,
    name: endpoint.name,
    description: endpoint.description,
    version: endpoint.version,
    skillId: endpoint.skillId,
    skillName: endpoint.skillName,
    skillDescription: endpoint.skillDescription,
    tags: endpoint.tags.join(", "),
    examples: endpoint.examples.join("\n"),
    inputModes: endpoint.inputModes,
    knowledgeCollectionIds: endpoint.knowledgeCollectionIds,
    approvalRequired: endpoint.approvalRequired,
    enabled: endpoint.enabled,
    priority: endpoint.priority,
    maxInputCharacters: endpoint.maxInputCharacters,
    maxActiveTasks: endpoint.maxActiveTasks,
  };
}

function payload(form: EndpointForm): SaveA2AEndpointRequest {
  return {
    ...form,
    name: form.name.trim(),
    description: form.description.trim(),
    version: form.version.trim(),
    skillId: form.skillId.trim() || undefined,
    skillName: form.skillName.trim(),
    skillDescription: form.skillDescription.trim(),
    tags: form.tags.split(",").map((tag) => tag.trim()).filter(Boolean),
    examples: form.examples.split("\n").map((example) => example.trim()).filter(Boolean),
  };
}

function shortDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function shortId(value: string): string {
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

export function A2APage({
  projectId,
  agents,
  collections,
  roles,
  createRequest,
  onChanged,
  onOpenRun,
}: A2APageProps) {
  const [snapshot, setSnapshot] = useState<A2ASnapshot | null>(null);
  const [editor, setEditor] = useState<{ endpointId: string | null; form: EndpointForm } | null>(null);
  const [secret, setSecret] = useState<A2AEndpointSecret | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const canDesign = roles.includes("admin") || roles.includes("designer");

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await api.a2a(signal);
      setSnapshot(next);
      setError(null);
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === "AbortError") return;
      setError(requestError instanceof Error ? requestError.message : "Не удалось загрузить A2A adapter");
    }
  }, [projectId]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const interval = window.setInterval(() => void load(), 10_000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [load]);

  useEffect(() => {
    if (createRequest <= 0 || !canDesign) return;
    setEditor({ endpointId: null, form: emptyForm(agents[0]) });
  }, [agents, canDesign, createRequest]);

  async function mutate(key: string, action: () => Promise<unknown>) {
    setBusyKey(key);
    setError(null);
    try {
      await action();
      await Promise.all([load(), onChanged()]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Операция A2A завершилась ошибкой");
      throw requestError;
    } finally {
      setBusyKey(null);
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editor) return;
    try {
      if (editor.endpointId) {
        await mutate("save", () => api.updateA2AEndpoint(editor.endpointId!, payload(editor.form)));
      } else {
        let created: A2AEndpointSecret | null = null;
        await mutate("save", async () => {
          created = await api.createA2AEndpoint(payload(editor.form));
        });
        if (created) setSecret(created);
      }
      setEditor(null);
    } catch {
      // Ошибка остаётся в форме.
    }
  }

  async function copyValue(key: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(key);
      window.setTimeout(() => setCopied((current) => current === key ? null : current), 1_800);
    } catch {
      setError("Браузер не разрешил копирование; выделите значение вручную");
    }
  }

  async function rotate(endpoint: A2AEndpoint) {
    if (!window.confirm(`Заменить token endpoint «${endpoint.name}»? Старый token сразу перестанет работать.`)) return;
    try {
      let nextSecret: A2AEndpointSecret | null = null;
      await mutate(`rotate:${endpoint.id}`, async () => {
        nextSecret = await api.rotateA2AEndpointToken(endpoint.id);
      });
      if (nextSecret) setSecret(nextSecret);
    } catch {
      // Ошибка показана на странице.
    }
  }

  async function remove(endpoint: A2AEndpoint) {
    if (!window.confirm(`Удалить A2A endpoint «${endpoint.name}»? История завершённых runs останется в АГАТ.`)) return;
    try {
      await mutate(`delete:${endpoint.id}`, () => api.deleteA2AEndpoint(endpoint.id));
    } catch {
      // Ошибка показана на странице.
    }
  }

  function updateMode(mode: A2AInputMode, enabled: boolean) {
    if (!editor) return;
    const current = editor.form.inputModes;
    const next = enabled ? [...new Set([...current, mode])] : current.filter((item) => item !== mode);
    if (next.length === 0) return;
    setEditor({ ...editor, form: { ...editor.form, inputModes: next } });
  }

  function updateCollection(collectionId: string, enabled: boolean) {
    if (!editor) return;
    const current = editor.form.knowledgeCollectionIds;
    const next = enabled ? [...new Set([...current, collectionId])] : current.filter((item) => item !== collectionId);
    setEditor({ ...editor, form: { ...editor.form, knowledgeCollectionIds: next } });
  }

  const counts = snapshot?.counts;
  return (
    <main className="main-column section-page a2a-page" id="a2a">
      <div className="page-title page-title--section a2a-page__title">
        <div>
          <h1>A2A adapter</h1>
          <p>Публикация выбранных локальных агентов через A2A 1.0 HTTP+JSON без раскрытия prompts, memory и внутренних tools</p>
        </div>
        <button className="button button--primary page-title__action page-title__action--always" type="button" disabled={!canDesign || agents.length === 0} onClick={() => setEditor({ endpointId: null, form: emptyForm(agents[0]) })}>
          <Icon name="plus" size={17} />Endpoint
        </button>
      </div>

      <section className="a2a-summary" aria-label="Состояние A2A adapter">
        <div><strong>{counts?.enabledEndpoints ?? 0}/{counts?.endpoints ?? 0}</strong><span>endpoint включены</span></div>
        <div><strong>{counts?.activeTasks ?? 0}</strong><span>активных tasks</span></div>
        <div><strong>{counts?.completedTasks ?? 0}</strong><span>завершено</span></div>
        <div><strong>1.0</strong><span>HTTP+JSON · polling</span></div>
      </section>

      {!snapshot?.enabled ? <p className="a2a-banner a2a-banner--warning"><Icon name="warning" size={18} />Adapter отключён через AGAT_A2A_ENABLED. Конфигурация сохранена, но внешние маршруты недоступны.</p> : null}
      <p className="a2a-banner"><Icon name="shield" size={18} /><span><strong>Boundary policy:</strong> project-scoped bearer token · inline text/JSON · один агент на endpoint · только финальный text artifact · streaming и push выключены</span></p>
      {error ? <button className="connection-toast a2a-page__error" type="button" onClick={() => setError(null)}>{error}</button> : null}

      {!snapshot ? (
        <section className="large-empty-state large-empty-state--compact"><span className="boot-mark" /><h2>Читаем A2A registry</h2></section>
      ) : snapshot.endpoints.length === 0 ? (
        <section className="large-empty-state large-empty-state--compact">
          <Icon name="network" size={34} />
          <h2>Опубликованных агентов пока нет</h2>
          <p>Создайте project-scoped endpoint, выберите один агент и knowledge snapshots. Token будет показан ровно один раз.</p>
          {canDesign ? <button className="button button--primary" type="button" onClick={() => setEditor({ endpointId: null, form: emptyForm(agents[0]) })}><Icon name="plus" size={16} />Создать endpoint</button> : null}
        </section>
      ) : (
        <section className="a2a-endpoints" aria-label="A2A endpoints">
          {snapshot.endpoints.map((endpoint) => (
            <article className={`a2a-endpoint${endpoint.enabled ? "" : " is-disabled"}`} key={endpoint.id}>
              <header>
                <span className="a2a-endpoint__icon"><Icon name="network" size={21} /></span>
                <div>
                  <h2>{endpoint.name}<code>{endpoint.skillId}</code></h2>
                  <p>{endpoint.description}</p>
                </div>
                <span className={`a2a-live${endpoint.enabled ? " is-online" : ""}`}><i />{endpoint.enabled ? "published" : "disabled"}</span>
                <div className="a2a-endpoint__actions">
                  <button className="button button--secondary" type="button" onClick={() => void copyValue(`card:${endpoint.id}`, endpoint.agentCardUrl ?? "")} disabled={!endpoint.agentCardUrl}><Icon name={copied === `card:${endpoint.id}` ? "check" : "publish"} size={14} />{copied === `card:${endpoint.id}` ? "Скопировано" : "Agent Card"}</button>
                  {canDesign ? <button className="icon-button" type="button" aria-label="Настроить endpoint" title="Настроить" onClick={() => setEditor({ endpointId: endpoint.id, form: endpointForm(endpoint) })}><Icon name="menu" size={16} /></button> : null}
                  {canDesign ? <button className="icon-button" type="button" aria-label="Удалить endpoint" title="Удалить" disabled={busyKey === `delete:${endpoint.id}` || endpoint.activeTasks > 0} onClick={() => void remove(endpoint)}><Icon name="trash" size={15} /></button> : null}
                </div>
              </header>
              <dl className="a2a-endpoint__meta">
                <div><dt>Агент</dt><dd>{endpoint.agentName}</dd></div>
                <div><dt>Input</dt><dd>{endpoint.inputModes.join(" · ")}</dd></div>
                <div><dt>Knowledge</dt><dd>{endpoint.knowledgeCollectionIds.length} snapshots</dd></div>
                <div><dt>Policy</dt><dd>{endpoint.approvalRequired ? "operator approval" : `priority ${endpoint.priority}`}</dd></div>
                <div><dt>Tasks</dt><dd>{endpoint.activeTasks} active · {endpoint.totalTasks} total</dd></div>
                <div><dt>Token</dt><dd>••••••{endpoint.tokenSuffix} · {shortDate(endpoint.tokenRotatedAt)}</dd></div>
              </dl>
              <footer>
                <code title={endpoint.interfaceUrl}>{endpoint.interfaceUrl}</code>
                <span>max {endpoint.maxInputCharacters.toLocaleString("ru-RU")} chars · {endpoint.maxActiveTasks} concurrent</span>
                {canDesign ? <button type="button" disabled={busyKey === `rotate:${endpoint.id}`} onClick={() => void rotate(endpoint)}><Icon name="repeat" size={13} />Rotate token</button> : null}
              </footer>
            </article>
          ))}
        </section>
      )}

      <section className="a2a-tasks" aria-label="Последние A2A tasks">
        <header><div><span>External execution</span><h2>Последние tasks</h2></div><small>{snapshot?.tasks.length ?? 0} из последних 100</small></header>
        {snapshot?.tasks.length ? <div className="a2a-task-list">
          {snapshot.tasks.map((task) => (
            <article key={task.id}>
              <time>{shortDate(task.updatedAt)}</time>
              <div><strong>{task.endpointName}</strong><small>{shortId(task.clientMessageId)} · context {shortId(task.contextId)}</small></div>
              <span className={`a2a-task-state a2a-task-state--${task.state.toLowerCase().replace("task_state_", "")}`}>{stateCopy[task.state]}</span>
              <code title={task.traceId}>{shortId(task.traceId)}{task.hasExternalTraceparent ? " ↳" : ""}</code>
              <button className="button button--secondary" type="button" onClick={() => onOpenRun(task.runId)}>Run <Icon name="chevron" size={12} /></button>
            </article>
          ))}
        </div> : <p className="a2a-tasks__empty">Внешних tasks ещё не было. Для smoke test используйте URL interface и token из созданного endpoint.</p>}
      </section>

      {editor ? <div className="eval-modal" role="dialog" aria-modal="true" aria-labelledby="a2a-editor-title">
        <div className="eval-modal__surface a2a-editor">
          <header><div><h2 id="a2a-editor-title">{editor.endpointId ? "Настройка A2A endpoint" : "Новый A2A endpoint"}</h2><p>Один агент, фиксированный boundary и project-scoped token</p></div><button className="icon-button" type="button" aria-label="Закрыть" onClick={() => setEditor(null)}><Icon name="close" /></button></header>
          <form className="eval-form" onSubmit={save}>
            <div className="eval-form__grid">
              <label className="field"><span>Агент</span><select required disabled={Boolean(editor.endpointId)} value={editor.form.agentId} onChange={(event) => { const agent = agents.find((item) => item.id === event.target.value); setEditor({ ...editor, form: { ...editor.form, agentId: event.target.value, name: agent?.name ?? editor.form.name, description: agent?.role ?? editor.form.description, skillName: agent?.name ?? editor.form.skillName, skillDescription: agent?.role ?? editor.form.skillDescription } }); }}>{agents.map((agent) => <option value={agent.id} key={agent.id}>{agent.name} · {agent.runtime}</option>)}</select></label>
              <label className="field"><span>Endpoint version</span><input required maxLength={64} pattern="[0-9]+\.[0-9]+\.[0-9]+.*" value={editor.form.version} onChange={(event) => setEditor({ ...editor, form: { ...editor.form, version: event.target.value } })} /></label>
            </div>
            <label className="field"><span>Название</span><input required maxLength={120} autoFocus value={editor.form.name} onChange={(event) => setEditor({ ...editor, form: { ...editor.form, name: event.target.value } })} /></label>
            <label className="field"><span>Публичное описание</span><textarea required maxLength={2_000} rows={3} value={editor.form.description} onChange={(event) => setEditor({ ...editor, form: { ...editor.form, description: event.target.value } })} /></label>
            <section className="eval-form-section"><header><div><strong>Agent skill</strong><small>Видно внешнему A2A client в Agent Card</small></div></header>
              <div className="eval-form__grid"><label className="field"><span>Skill name</span><input required maxLength={120} value={editor.form.skillName} onChange={(event) => setEditor({ ...editor, form: { ...editor.form, skillName: event.target.value } })} /></label><label className="field"><span>Skill ID</span><input maxLength={64} pattern="[a-z0-9][a-z0-9._-]*" placeholder="создастся автоматически" value={editor.form.skillId} onChange={(event) => setEditor({ ...editor, form: { ...editor.form, skillId: event.target.value.toLowerCase() } })} /></label></div>
              <label className="field"><span>Skill description</span><textarea required maxLength={2_000} rows={2} value={editor.form.skillDescription} onChange={(event) => setEditor({ ...editor, form: { ...editor.form, skillDescription: event.target.value } })} /></label>
              <label className="field"><span>Tags через запятую</span><input required maxLength={600} value={editor.form.tags} onChange={(event) => setEditor({ ...editor, form: { ...editor.form, tags: event.target.value } })} /></label>
              <label className="field"><span>Examples, по одному на строку</span><textarea maxLength={4_000} rows={3} value={editor.form.examples} onChange={(event) => setEditor({ ...editor, form: { ...editor.form, examples: event.target.value } })} /></label>
            </section>
            <section className="eval-form-section"><header><div><strong>Boundary</strong><small>Разрешённые inline payloads и локальные knowledge snapshots</small></div></header>
              <div className="a2a-checks"><label><input type="checkbox" checked={editor.form.inputModes.includes("text/plain")} onChange={(event) => updateMode("text/plain", event.target.checked)} /><span><strong>text/plain</strong><small>Обычный текст</small></span></label><label><input type="checkbox" checked={editor.form.inputModes.includes("application/json")} onChange={(event) => updateMode("application/json", event.target.checked)} /><span><strong>application/json</strong><small>Inline data part</small></span></label></div>
              <div className="a2a-collections"><span>Knowledge collections</span>{collections.length ? collections.map((collection) => <label key={collection.id}><input type="checkbox" checked={editor.form.knowledgeCollectionIds.includes(collection.id)} onChange={(event) => updateCollection(collection.id, event.target.checked)} /><span><strong>{collection.name}</strong><small>{collection.readyDocuments}/{collection.documentCount} docs ready</small></span></label>) : <small>Коллекций в проекте нет</small>}</div>
            </section>
            <div className="a2a-policy-grid"><label className="field"><span>Priority</span><input type="number" min={0} max={100} value={editor.form.priority} onChange={(event) => setEditor({ ...editor, form: { ...editor.form, priority: Number(event.target.value) } })} /></label><label className="field"><span>Max input chars</span><input type="number" min={1_000} max={100_000} value={editor.form.maxInputCharacters} onChange={(event) => setEditor({ ...editor, form: { ...editor.form, maxInputCharacters: Number(event.target.value) } })} /></label><label className="field"><span>Max active tasks</span><input type="number" min={1} max={100} value={editor.form.maxActiveTasks} onChange={(event) => setEditor({ ...editor, form: { ...editor.form, maxActiveTasks: Number(event.target.value) } })} /></label></div>
            <div className="a2a-checks"><label><input type="checkbox" checked={editor.form.approvalRequired} onChange={(event) => setEditor({ ...editor, form: { ...editor.form, approvalRequired: event.target.checked } })} /><span><strong>Operator approval</strong><small>Task вернётся как AUTH_REQUIRED</small></span></label><label><input type="checkbox" checked={editor.form.enabled} onChange={(event) => setEditor({ ...editor, form: { ...editor.form, enabled: event.target.checked } })} /><span><strong>Endpoint enabled</strong><small>Agent Card и interface доступны</small></span></label></div>
            {error ? <p className="form-error" role="alert">{error}</p> : null}
            <div className="dialog-actions"><button className="button button--secondary" type="button" onClick={() => setEditor(null)}>Отмена</button><button className="button button--primary" type="submit" disabled={busyKey === "save" || !editor.form.agentId}>{busyKey === "save" ? "Сохраняем…" : editor.endpointId ? "Сохранить" : "Создать и выдать token"}</button></div>
          </form>
        </div>
      </div> : null}

      {secret ? <div className="eval-modal" role="dialog" aria-modal="true" aria-labelledby="a2a-secret-title">
        <div className="eval-modal__surface a2a-secret"><header><div><h2 id="a2a-secret-title">Сохраните bearer token</h2><p>После закрытия АГАТ больше не покажет его целиком</p></div><Icon name="shield" size={23} /></header><div><p>Передайте token только доверенному A2A client по защищённому каналу. В базе хранится SHA-256 hash.</p><textarea readOnly rows={3} value={secret.accessToken} aria-label="Новый A2A bearer token" /><button className="button button--primary" type="button" onClick={() => void copyValue("secret", secret.accessToken)}><Icon name={copied === "secret" ? "check" : "publish"} size={16} />{copied === "secret" ? "Token скопирован" : "Скопировать token"}</button></div><footer><button className="button button--secondary" type="button" onClick={() => setSecret(null)}>Я сохранил token</button></footer></div>
      </div> : null}
    </main>
  );
}
