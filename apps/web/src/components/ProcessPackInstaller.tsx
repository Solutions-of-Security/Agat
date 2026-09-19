import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { AgatRole } from "../types";
import type { ProcessPackManifest, ProcessPackInstallation, ProcessPackPreview } from "../processPacks";
import { scenarioStartAllowed } from "../scenarioPreflight";
import { ScenarioPreflightPanel } from "./ScenarioPreflightPanel";

export function ProcessPackInstaller({ roles, onClose, onInstalled, onRun }: {
  roles: AgatRole[]; onClose: () => void; onInstalled: () => void; onRun: (runId: string) => void;
}) {
  const [pack, setPack] = useState<(ProcessPackManifest & { installation: ProcessPackInstallation | null }) | null>(null);
  const [model, setModel] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const [response, setResponse] = useState<{ key: string; value: ProcessPackPreview } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [startMode, setStartMode] = useState<"now" | "queue">("now");
  const key = JSON.stringify([pack?.id, pack?.version, pack?.manifestSha256, model, attempt]);
  const preview = response?.key === key ? response.value : null;
  const installation = preview?.installation ?? pack?.installation;

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    void api.processPacks(controller.signal).then(({ packs }) => {
      if (controller.signal.aborted) return;
      const first = packs[0];
      if (!first) throw new Error("Каталог пакетов пуст");
      setPack(first); setModel(first.installation?.model ?? first.defaults.model);
    }).catch((error: unknown) => { if (!controller.signal.aborted) setError(error instanceof Error ? error.message : "Не удалось загрузить пакет"); });
    return () => controller.abort();
  }, [catalogAttempt]);

  useEffect(() => {
    if (!pack) return;
    const controller = new AbortController();
    setError(null);
    setResponse(null);
    void api.previewProcessPack(pack.id, { version: pack.version, manifestSha256: pack.manifestSha256, model }, controller.signal).then((value) => {
      if (!controller.signal.aborted) setResponse({ key, value });
    }).catch((error: unknown) => { if (!controller.signal.aborted) setError(error instanceof Error ? error.message : "Проверка пакета недоступна"); });
    return () => controller.abort();
  }, [key]);

  async function install() {
    if (!pack || !preview || busy) return;
    setBusy(true); setError(null);
    try {
      const value = await api.installProcessPack(pack.id, { version: pack.version, manifestSha256: pack.manifestSha256, model });
      setPack({ ...pack, installation: value.installation }); setResponse({ key, value }); onInstalled();
    } catch (error) { setError(error instanceof Error ? error.message : "Установка не завершена. Повторите проверку и установку."); }
    finally { setBusy(false); }
  }

  async function startSample() {
    if (!pack || !installation || !scenarioStartAllowed(preview?.preflight ?? null, startMode) || busy) return;
    setBusy(true); setError(null);
    try {
      const instance = await api.startProcess(installation.processId, { input: pack.sample.input, version: installation.processVersion,
        knowledgeCollectionIds: installation.knowledgeCollectionIds, startMode, resultDestination: "history", priority: 50, artifactPath: "" });
      onRun(instance.runId);
    } catch (error) { setError(error instanceof Error ? error.message : "Не удалось запустить пример"); setResponse(null); }
    finally { setBusy(false); }
  }

  if (!pack) return <section aria-label="Установка пакета">{error ? <><p role="alert" className="form-error">{error}</p><button type="button" className="button" onClick={() => setCatalogAttempt((value) => value + 1)}>Повторить загрузку пакета</button></> : <p role="status">Загружаем пакет…</p>}</section>;
  return <section className="process-pack" aria-label="Установка пакета">
    <header><h3>{pack.name} <small>v{pack.version}</small></h3><p>{pack.description}</p><p className="muted">{pack.execution}</p></header>
    <div className="process-pack__columns">
      <section><h4>Роли в пакете</h4><ul>{pack.roles.map((role) => <li key={role.id}><strong>{role.name}</strong><p>{role.responsibility}</p></li>)}</ul></section>
      <section><h4>Знания и инструменты</h4><p>{pack.knowledge.description}</p><ul>{pack.knowledge.documents.map((document) => <li key={document.sourceUri}>{document.name}</li>)}</ul><ul>{pack.tools.map((tool) => <li key={tool.id}><strong>{tool.name}.</strong> {tool.requirement}</li>)}</ul></section>
    </div>
    <details><summary>Правила и границы пакета</summary><ul>{pack.policies.map((policy) => <li key={policy}>{policy}</li>)}</ul><p>{pack.limitations}</p></details>
    <details><summary>Учебные данные и ожидаемые расчёты</summary><p>{pack.sample.input}</p>{pack.knowledge.documents.map((document) => <div key={document.sourceUri}><strong>{document.name}</strong><pre>{document.content}</pre></div>)}<p>{pack.sample.expected}</p></details>
    <label className="field"><span>Локальная модель для трёх ролей</span><input value={model} maxLength={100} disabled={busy || Boolean(installation)} onChange={(event) => setModel(event.target.value)} /><small>Укажите точное имя модели на worker. Embeddings: {pack.defaults.embeddingModel}.</small></label>
    {installation ? <p role="status">Пакет установлен в текущем проекте. Версия процесса: v{installation.processVersion}.</p> : <p>Установка создаст 3 агента, коллекцию с 2 документами и опубликованный процесс. Запуск выполняется отдельно.</p>}
    <ScenarioPreflightPanel result={preview?.preflight ?? null} error={error} roles={roles} onRefresh={() => setAttempt((value) => value + 1)} onRecover={(event, href) => {
      if (href.includes("packId=")) { event.preventDefault(); document.getElementById("install-report-pack")?.focus(); }
      else onClose();
    }} />
    <div className="dialog-actions">
      {!installation ? <button id="install-report-pack" className="button button--primary" type="button" disabled={busy || !preview || !roles.some((role) => role === "admin" || role === "designer")} onClick={() => void install()}>{busy ? "Устанавливаем…" : "Установить пакет"}</button> : <>
        <a className="button button--secondary" href={`#processes?processId=${encodeURIComponent(installation.processId)}&tab=readiness`} onClick={onClose}>Открыть процесс</a>
        {roles.some((role) => ["admin", "designer", "operator"].includes(role)) ? <><label className="field"><span>Режим запуска примера</span><select value={startMode} disabled={busy} onChange={(event) => setStartMode(event.target.value as "now" | "queue")}><option value="now">Выполнить сейчас</option><option value="queue">Ожидать готовности в очереди</option></select></label><button type="button" className="button button--primary" disabled={busy || !scenarioStartAllowed(preview?.preflight ?? null, startMode)} onClick={() => void startSample()}>{startMode === "queue" ? "Поставить пример в очередь" : "Запустить пример"}</button></> : null}
      </>}
    </div>
  </section>;
}
