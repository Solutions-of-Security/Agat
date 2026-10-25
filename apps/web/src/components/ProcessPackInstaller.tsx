import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type { AgatRole, KnowledgeCollection } from "../types";
import { reportPackRequest, type ProcessPackCatalog, type ProcessPackHistoryEntry, type ProcessPackPreview } from "../processPacks";
import { scenarioStartAllowed } from "../scenarioPreflight";
import { ScenarioPreflightPanel } from "./ScenarioPreflightPanel";

const changeNames: Record<string, string> = { roles: "Роли и инструкции", knowledge: "Требования к источникам", tools: "Инструменты",
  schemas: "Правила входа и результата", process: "Шаги процесса", policies: "Ограничения и согласование", profile: "Профиль выполнения",
  evaluation: "Проверка качества", model: "Модель", knowledgeCollectionIds: "Выбранные коллекции" };

export function ProcessPackInstaller({ roles, onClose, onInstalled, onRun }: {
  roles: AgatRole[]; onClose: () => void; onInstalled: () => void; onRun: (runId: string) => void | Promise<void>;
}) {
  const [catalog, setCatalog] = useState<ProcessPackCatalog | null>(null);
  const [history, setHistory] = useState<ProcessPackHistoryEntry[]>([]);
  const [collections, setCollections] = useState<KnowledgeCollection[]>([]);
  const [version, setVersion] = useState<number | null>(null);
  const [model, setModel] = useState("");
  const [collectionIds, setCollectionIds] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [period, setPeriod] = useState("");
  const [requirements, setRequirements] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const [response, setResponse] = useState<{ key: string; value: ProcessPackPreview } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [startMode, setStartMode] = useState<"now" | "queue">("now");
  const releases = catalog?.releases ?? [];
  const pack = releases.find(release => release.version === version) ?? null;
  const ownDocuments = pack?.knowledge.mode === "project_collections";
  const canPreview = Boolean(pack && model.trim() && (!ownDocuments || (collectionIds.length > 0 && collectionIds.length <= 20)));
  const key = JSON.stringify([pack?.id, pack?.version, pack?.manifestSha256, model, collectionIds, attempt]);
  const preview = response?.key === key ? response.value : null;
  const installation = preview?.installation ?? pack?.installation;
  const active = preview?.activeInstallation ?? catalog?.activeInstallation;
  const selectedIsActive = Boolean(installation && active?.manifestSha256 === installation.manifestSha256);
  const canManage = roles.some(role => role === "admin" || role === "designer");
  const request = useMemo(() => reportPackRequest(question, period, requirements), [question, period, requirements]);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    void Promise.all([api.processPacks(controller.signal), api.knowledge(controller.signal), api.processPackHistory("internal-report", controller.signal)]).then(([value, knowledge, records]) => {
      if (controller.signal.aborted) return;
      const selected = value.releases.find(release => release.version === (version ?? value.activeInstallation?.version ?? 2)) ?? value.releases[0];
      if (!selected) throw new Error("Каталог пакетов пуст");
      setCatalog(value); setCollections(knowledge.collections); setHistory(records.history); setVersion(selected.version);
      setAttempt(current => current + 1);
      if (!catalog || selected.installation) {
        setModel(selected.installation?.model ?? selected.defaults.model);
        setCollectionIds(selected.version === 2 ? selected.installation?.knowledgeCollectionIds ?? [] : []);
      }
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Не удалось загрузить пакет и источники"); });
    return () => controller.abort();
  }, [catalogAttempt]);

  useEffect(() => {
    const controller = new AbortController();
    setResponse(null); setError(null);
    if (!pack || !canPreview) return () => controller.abort();
    void api.previewProcessPack(pack.id, { version: pack.version, manifestSha256: pack.manifestSha256, model,
      ...(ownDocuments ? { knowledgeCollectionIds: collectionIds } : {}) }, controller.signal).then(value => {
      if (!controller.signal.aborted) {
        setResponse({ key, value });
        setCatalog(current => current ? { ...current, activeInstallation: value.activeInstallation } : current);
      }
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Проверка пакета недоступна"); });
    return () => controller.abort();
  }, [key]);

  function selectVersion(nextVersion: number) {
    const next = releases.find(release => release.version === nextVersion);
    if (!next) return;
    setVersion(next.version); setModel(next.installation?.model ?? next.defaults.model);
    setCollectionIds(next.version === 2 ? next.installation?.knowledgeCollectionIds ?? [] : []);
    setResponse(null); setError(null);
  }

  function applyPreview(value: ProcessPackPreview) {
    setCatalog(current => current ? { ...current, activeInstallation: value.activeInstallation,
      releases: current.releases.map(release => release.version === value.manifest.version ? { ...release, installation: value.installation } : release) } : current);
    setResponse({ key, value });
    if (value.installation) setHistory(current => current.some(entry => entry.installation.version === value.installation!.version) ? current
      : [...current, { manifest: value.manifest, installation: value.installation! }].sort((a, b) => b.installation.version - a.installation.version));
    onInstalled();
  }

  async function install() {
    if (!pack || !preview || busy) return;
    setBusy(true); setError(null);
    try { applyPreview(await api.installProcessPack(pack.id, { version: pack.version, manifestSha256: pack.manifestSha256, model,
      ...(ownDocuments ? { knowledgeCollectionIds: collectionIds } : {}) })); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Установка не завершена. Обновите список и повторите."); }
    finally { setBusy(false); }
  }

  async function activate() {
    if (!pack || !installation || !preview || busy) return;
    setBusy(true); setError(null);
    try { applyPreview(await api.activateProcessPack(pack.id, { version: pack.version, manifestSha256: pack.manifestSha256,
      expectedActiveManifestSha256: preview.activeInstallation?.manifestSha256 ?? null })); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Версия не переключена. Повторите проверку различий."); setResponse(null); }
    finally { setBusy(false); }
  }

  async function start() {
    if (!pack || !installation || !scenarioStartAllowed(preview?.preflight ?? null, startMode) || busy || (ownDocuments && !request.input)) return;
    setBusy(true); setError(null);
    try {
      const instance = await api.startProcess(installation.processId, { input: ownDocuments ? request.input! : pack.sample.input, version: installation.processVersion,
        knowledgeCollectionIds: installation.knowledgeCollectionIds, startMode, resultDestination: "history", priority: 50, artifactPath: "" });
      await onRun(instance.runId);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось запустить отчёт"); setResponse(null); }
    finally { setBusy(false); }
  }

  if (!pack) return <section aria-label="Установка пакета">{error ? <><p role="alert" className="form-error">{error}</p><button type="button" className="button" onClick={() => setCatalogAttempt(value => value + 1)}>Повторить загрузку пакета</button></> : <p role="status">Загружаем пакет и источники…</p>}</section>;
  return <section className="process-pack" aria-label="Установка пакета">
    <header><h3>{pack.name} <small>v{pack.version}</small></h3><p>{ownDocuments ? "Ваши документы → отчёт с источниками → проверка человеком → файл отчёта." : pack.description}</p><p className="muted">{ownDocuments ? "Локальное выполнение · три роли" : pack.execution}</p></header>
    <label className="field"><span>Версия пакета</span><select aria-label="Версия пакета" value={pack.version} disabled={busy} onChange={event => selectVersion(Number(event.target.value))}>
      {releases.map(release => <option key={release.version} value={release.version}>v{release.version} — {release.name}{release.installation ? " · установлена" : ""}</option>)}
    </select></label>
    {active ? <p role="status">Активная версия пакета: v{active.version}.{selectedIsActive ? " Выбрана для новых задач." : ` Сейчас просматривается v${pack.version}.`}</p> : null}
    {ownDocuments ? <p className="muted">Качество этого профиля ещё требует проверки на ваших задачах. Готовность инфраструктуры и проверка чисел не заменяют решение владельца отчёта.</p> : null}
    <div className="process-pack__columns">
      <section><h4>Роли в пакете</h4><ul>{pack.roles.map(role => <li key={role.id}><strong>{role.name}</strong><p>{role.responsibility}</p></li>)}</ul></section>
      <section><h4>Знания и инструменты</h4><p>{pack.knowledge.description}</p><ul>{pack.knowledge.documents.map(document => <li key={document.sourceUri}>{document.name}</li>)}</ul><ul>{pack.tools.map(tool => <li key={tool.id}><strong>{tool.name}.</strong> {tool.requirement}</li>)}</ul></section>
    </div>
    {ownDocuments ? <fieldset className="process-pack__sources" disabled={busy || Boolean(installation)}><legend>Документы для отчёта</legend>
      <p>Выберите от 1 до 20 коллекций текущего проекта.</p>
      {collections.length ? collections.map(collection => <label key={collection.id} className="process-pack__collection">
        <input type="checkbox" checked={collectionIds.includes(collection.id)} onChange={event => setCollectionIds(current => event.target.checked ? [...current, collection.id].sort() : current.filter(id => id !== collection.id))} />
        <span><strong>{collection.name}</strong><small>{collection.documentCount} документов · готово {collection.readyDocuments} · поисковый индекс {collection.embeddedChunks}/{collection.chunkCount}</small></span>
      </label>) : <p>Коллекций пока нет. Добавьте документы в разделе «Знания», затем вернитесь к установке.</p>}
      {installation?.knowledgeCollectionIds.filter(id => !collections.some(collection => collection.id === id)).map(id => <p className="form-error" key={id}>Коллекция {id} недоступна. Восстановите источник или опубликуйте исправленный процесс.</p>)}
    </fieldset> : null}
    {ownDocuments ? <div className="process-pack__source-actions"><a className="button button--secondary" href="#knowledge" onClick={onClose}>Добавить документы</a><button type="button" className="button button--secondary" disabled={busy} onClick={() => setCatalogAttempt(value => value + 1)}>Обновить список источников</button></div> : null}
    <details><summary>Правила и границы пакета</summary><ul>{pack.policies.map(policy => <li key={policy}>{policy}</li>)}</ul><p>{pack.limitations}</p></details>
    {!ownDocuments ? <details><summary>Учебные данные и ожидаемые расчёты</summary><p>{pack.sample.input}</p>{pack.knowledge.documents.map(document => <div key={document.sourceUri}><strong>{document.name}</strong><pre>{document.content}</pre></div>)}<p>{pack.sample.expected}</p></details> : null}
    <label className="field"><span>Локальная модель для трёх ролей</span><input aria-label="Локальная модель для трёх ролей" value={model} maxLength={100} disabled={busy || Boolean(installation)} onChange={event => setModel(event.target.value)} /><small>Укажите точное имя модели на worker.{!ownDocuments ? ` Embeddings: ${pack.defaults.embeddingModel}.` : " Модели embeddings зависят от выбранных коллекций."}</small></label>
    {installation ? <p role="status">Пакет установлен в текущем проекте. Версия процесса: v{installation.processVersion}.</p> : <p>{ownDocuments ? "Установка создаст 3 агента и опубликованный процесс с выбранными источниками." : "Установка создаст 3 агента, коллекцию с 2 документами и опубликованный процесс."} Запуск выполняется отдельно.</p>}
    {preview?.changes.length ? <section className="process-pack__changes" aria-label="Различия версий"><h4>Изменения относительно активной v{active?.version}</h4>
      <ul>{preview.changes.map(change => <li key={change.section}>{changeNames[change.section] ?? change.section}</li>)}</ul>
      <p>Обновление создаёт отдельный процесс. Работающие задачи и ваши черновики сохраняются.</p>
      <details><summary>Точные значения до и после</summary>{preview.changes.map(change => <section key={change.section}><h5>{changeNames[change.section] ?? change.section}</h5><p>Было</p><pre>{JSON.stringify(change.before, null, 2)}</pre><p>Будет</p><pre>{change.after === null && change.section === "knowledgeCollectionIds" ? "Учебная коллекция будет создана при установке" : JSON.stringify(change.after, null, 2)}</pre></section>)}</details>
    </section> : null}
    {canPreview ? <ScenarioPreflightPanel result={preview?.preflight ?? null} error={error} roles={roles} onRefresh={() => setAttempt(value => value + 1)} onRecover={(event, href) => {
      if (href.includes("packId=")) { event.preventDefault(); document.getElementById("install-report-pack")?.focus(); }
      else onClose();
    }} /> : <p role={error ? "alert" : "status"} className={error ? "form-error" : "muted"}>{error ?? (!model.trim() ? "Укажите модель для проверки готовности." : "Выберите от 1 до 20 коллекций для проверки готовности.")}</p>}
    <div className="dialog-actions">
      {!installation ? <button id="install-report-pack" className="button button--primary" type="button" disabled={busy || !preview || !canManage} onClick={() => void install()}>{busy ? "Устанавливаем…" : "Установить пакет"}</button> : <>
        <a className="button button--secondary" href={`#processes?processId=${encodeURIComponent(installation.processId)}&tab=readiness`} onClick={onClose}>Открыть процесс</a>
        {!selectedIsActive ? <button type="button" className="button button--secondary" disabled={busy || !canManage || !preview?.preflight.queueable} onClick={() => void activate()}>{active && pack.version < active.version ? `Вернуться к v${pack.version}` : `Переключить на v${pack.version}`}</button> : null}
      </>}
    </div>
    {installation && roles.some(role => ["admin", "designer", "operator"].includes(role)) ? <section className="process-pack__request" aria-label="Задание для отчёта">
      <h4>{ownDocuments ? "Задача по своим документам" : "Учебный пример"}</h4>
      {ownDocuments ? <><label className="field"><span>Вопрос для отчёта</span><textarea value={question} maxLength={8000} rows={3} disabled={busy} onChange={event => setQuestion(event.target.value)} /></label>
        <label className="field"><span>Период документов</span><input value={period} maxLength={160} disabled={busy} onChange={event => setPeriod(event.target.value)} /></label>
        <label className="field"><span>Требования к отчёту</span><textarea aria-label="Требования к отчёту" value={requirements} rows={3} disabled={busy} onChange={event => setRequirements(event.target.value)} /><small>Необязательно. До 20 требований, каждое с новой строки.</small></label>
        {request.error ? <p className="muted" role="status">{request.error}</p> : null}</> : null}
      <div className="dialog-actions"><label className="field"><span>{ownDocuments ? "Режим запуска отчёта" : "Режим запуска примера"}</span><select value={startMode} disabled={busy} onChange={event => setStartMode(event.target.value as "now" | "queue")}><option value="now">Выполнить сейчас</option><option value="queue">Ожидать готовности в очереди</option></select></label>
        <button type="button" className="button button--primary" disabled={busy || !scenarioStartAllowed(preview?.preflight ?? null, startMode) || (ownDocuments && !request.input)} onClick={() => void start()}>{ownDocuments ? startMode === "queue" ? "Поставить отчёт в очередь" : "Запустить отчёт" : startMode === "queue" ? "Поставить пример в очередь" : "Запустить пример"}</button>
      </div>
    </section> : null}
    {history.length ? <details className="process-pack__history"><summary>История установок ({history.length})</summary><ul>{history.map(entry => <li key={entry.installation.version}>
      <strong>v{entry.installation.version} · {entry.manifest.name}</strong><p>{entry.installation.model} · {new Date(entry.installation.installedAt).toLocaleString("ru-RU")}</p>
      <small>Manifest SHA-256: <code>{entry.installation.manifestSha256}</code></small><p><a href={`#processes?processId=${encodeURIComponent(entry.installation.processId)}&tab=versions`} onClick={onClose}>Версия процесса v{entry.installation.processVersion}</a></p>
    </li>)}</ul></details> : null}
  </section>;
}
