import { useEffect, useRef, useState, type FormEvent, type MouseEvent } from "react";

import type { Agent, AgatRole, CreateProcessRequest, KnowledgeCollection, ProcessDefinition, ProcessTemplateCatalog, ResultDestination, StartProcessRequest } from "../types";
import { api } from "../lib/api";
import { catalogProcessRequest, suggestedProcessName, type CatalogProcessTemplate } from "../processTemplates";
import { Icon } from "./Icon";
import { KnowledgeCollectionPicker } from "./KnowledgeCollectionPicker";
import { ResultStorageFields } from "./ResultStorageFields";
import { ProcessTemplatePicker } from "./ProcessTemplatePicker";
import { ProcessInputField } from "./ProcessInputField";
import { ProcessPackInstaller } from "./ProcessPackInstaller";
import { ScenarioPreflightPanel } from "./ScenarioPreflightPanel";
import { useScenarioPreflight } from "../hooks/useScenarioPreflight";
import { recoveryTarget, scenarioStartAllowed } from "../scenarioPreflight";

interface DialogStateProps {
  roles: AgatRole[];
  open: boolean;
  busy: boolean;
  error: string | null;
  onClose: () => void;
}

interface NewProcessDialogProps extends DialogStateProps {
  processes: ProcessDefinition[];
  agents: Agent[];
  onSubmit: (payload: CreateProcessRequest) => void;
  onPackInstalled: () => void;
  onPackRun: (runId: string) => void;
}

export function NewProcessDialog({ open, busy, error, processes, agents, onClose, onSubmit, roles, onPackInstalled, onPackRun }: NewProcessDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const [source, setSource] = useState("pack");
  const [catalog, setCatalog] = useState<ProcessTemplateCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [selected, setSelected] = useState<CatalogProcessTemplate | null>(null);
  const [bindings, setBindings] = useState<Record<string, string>>({});
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const preflight = useScenarioPreflight("process-templates", selected?.id ?? null, {
    catalogTemplateVersion: selected?.version, templateBindings: bindings,
  }, open && source === "catalog");

  function recoverTemplate(event: MouseEvent<HTMLAnchorElement>, href: string) {
    if (href.startsWith("#processes")) {
      event.preventDefault();
      if (recoveryTarget(href).has("templateId")) { setSelected(null); setLoadAttempt((value) => value + 1); return; }
      const nodeId = recoveryTarget(href).get("nodeId");
      const roleId = selected?.stages.find((stage) => stage.id === nodeId)?.roleId;
      const control = roleId ? document.getElementById(`template-role-${roleId}`) : formRef.current?.querySelector<HTMLButtonElement>('button[type="submit"]');
      control?.focus();
      control?.scrollIntoView({ block: "center" });
    } else onClose();
  }

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setCatalog(null);
    setCatalogError(null);
    void api.processTemplates().then((result) => {
      if (!cancelled) setCatalog(result);
    }).catch((requestError: unknown) => {
      if (!cancelled) setCatalogError(requestError instanceof Error ? requestError.message : "Не удалось загрузить каталог");
    });
    return () => { cancelled = true; };
  }, [open, loadAttempt]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      formRef.current?.reset();
      setSource("pack");
      setSelected(null);
      setBindings({});
      setName("");
      setDescription("");
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (source === "pack") return;
    const data = new FormData(event.currentTarget);
    if (source === "catalog") {
      if (selected) onSubmit(catalogProcessRequest(selected, name, description, bindings, data.get("isTemplate") === "on"));
      return;
    }
    onSubmit({
      name,
      description,
      templateId: String(data.get("templateId") ?? "") || undefined,
      isTemplate: data.get("isTemplate") === "on",
    });
  }

  function selectTemplate(template: CatalogProcessTemplate) {
    if (template.id === selected?.id) return;
    setSelected(template);
    setBindings({});
    setName(suggestedProcessName(template.name, processes.map((process) => process.name)));
    setDescription(template.description);
  }

  return (
    <dialog className="run-dialog process-dialog process-catalog-dialog" ref={dialogRef} aria-labelledby="new-process-dialog-title" onCancel={onClose} onClose={onClose}>
      <form ref={formRef} onSubmit={submit}>
        <div className="dialog-head">
          <div><h2 id="new-process-dialog-title">Новый процесс</h2><p>Установите готовый пакет или выберите схему для настройки</p></div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" />
          </button>
        </div>
        <label className="field">
          <span>Источник процесса</span>
          <select value={source} onChange={(event) => setSource(event.target.value)} disabled={busy}>
            <option value="pack">Готовый пакет: внутренний отчёт</option>
            <option value="catalog">Каталог типовых процессов</option>
            <option value="project">Шаблон проекта</option>
            <option value="basic">Базовый граф</option>
          </select>
        </label>
        {source === "pack" && open ? <ProcessPackInstaller roles={roles} onClose={onClose} onInstalled={onPackInstalled} onRun={onPackRun} /> : null}
        {source === "catalog" ? (
          catalog ? <ProcessTemplatePicker catalog={catalog} selected={selected} agents={agents} bindings={bindings} disabled={busy}
            onSelect={selectTemplate} onBindingChange={(roleId, agentId) => setBindings((current) => ({ ...current, [roleId]: agentId }))} />
            : catalogError ? <div><p className="form-error" role="alert">{catalogError}</p><button type="button" className="button button--secondary" onClick={() => setLoadAttempt((value) => value + 1)}>Повторить загрузку каталога</button></div>
              : <p role="status">Загружаем каталог процессов…</p>
        ) : null}
        {source === "catalog" && selected ? <ScenarioPreflightPanel {...preflight} roles={roles} onRefresh={preflight.refresh} onRecover={recoverTemplate} /> : null}
        {source !== "pack" ? <>
        <label className="field">
          <span>Название</span>
          <input name="name" required maxLength={100} placeholder="Например, еженедельный отчёт" value={name} onChange={(event) => setName(event.target.value)} disabled={busy} />
        </label>
        <label className="field">
          <span>Описание</span>
          <textarea name="description" rows={2} maxLength={1_000} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Что автоматизирует процесс и какой результат считается готовым" disabled={busy} />
        </label>
        {source === "project" ? <label className="field">
          <span>Начать из шаблона</span>
          <select name="templateId" defaultValue="" required disabled={busy}>
            <option value="" disabled>Выберите шаблон проекта</option>
            {processes.filter((process) => process.isTemplate).map((process) => (
              <option value={process.id} key={process.id}>{process.name} · {process.publishedVersion ? `v${process.publishedVersion}` : "черновик"}</option>
            ))}
          </select>
          {processes.every((process) => !process.isTemplate) ? <small>В проекте пока нет шаблонов. Создайте процесс из каталога и отметьте «Сохранить как шаблон».</small> : null}
        </label> : null}
        <label className="approval-toggle process-approval-toggle">
          <input type="checkbox" name="isTemplate" disabled={busy} />
          <span><strong>Сохранить как шаблон</strong><small>Процесс появится в каталоге переиспользуемых заготовок.</small></span>
        </label>
        {source === "basic" ? <div className="process-dialog__hint">
          <Icon name="shield" size={20} />
          <span><strong>Безопасный стартовый шаблон</strong><small>Старт, агенты, условие, цикл с лимитом и завершение уже связаны.</small></span>
        </div> : null}
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="dialog-actions">
          <button className="button button--secondary" type="button" onClick={onClose}>Отмена</button>
          <button className="button button--primary" type="submit" disabled={busy || (source === "catalog" && (!catalog || !selected))}>
            <Icon name="plus" size={17} />{busy ? "Создаём…" : "Создать процесс"}
          </button>
        </div>
        </> : null}
      </form>
    </dialog>
  );
}

interface StartProcessDialogProps extends DialogStateProps {
  process: ProcessDefinition | null;
  collections: KnowledgeCollection[];
  onSubmit: (payload: StartProcessRequest) => void;
}

export function StartProcessDialog({ process, collections, open, busy, error, onClose, onSubmit, roles }: StartProcessDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const [priority, setPriority] = useState(50);
  const [resultDestination, setResultDestination] = useState<ResultDestination>("artifacts");
  const [artifactPath, setArtifactPath] = useState("");
  const [selectedCollections, setSelectedCollections] = useState<string[]>([]);
  const [startMode, setStartMode] = useState<"queue" | "now">("now");
  const [pinnedVersion, setPinnedVersion] = useState<number | undefined>();
  const [inputReady, setInputReady] = useState(false);
  const draftOnly = !process?.publishedVersion;
  const preflight = useScenarioPreflight("processes", process?.id ?? null, { version: pinnedVersion, knowledgeCollectionIds: selectedCollections }, open && !draftOnly);
  useEffect(() => { if (error) preflight.refresh(); }, [error]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      formRef.current?.reset();
      setPriority(50);
      setResultDestination("artifacts");
      setArtifactPath("");
      setSelectedCollections(process?.draftGraph.requiredKnowledgeCollectionIds ?? []);
      setStartMode("now");
      setPinnedVersion(process?.publishedVersion);
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (draftOnly || !inputReady || !scenarioStartAllowed(preflight.result, startMode)) return;
    const data = new FormData(event.currentTarget);
    onSubmit({
      input: String(data.get("input") ?? ""),
      priority,
      resultDestination,
      artifactPath: resultDestination === "artifacts" ? artifactPath : "",
      knowledgeCollectionIds: selectedCollections,
      version: pinnedVersion,
      startMode,
    });
  }

  return (
    <dialog className="run-dialog process-dialog" ref={dialogRef} aria-labelledby="start-process-dialog-title" onCancel={onClose} onClose={onClose}>
      <form ref={formRef} onSubmit={submit}>
        <div className="dialog-head">
          <div>
            <h2 id="start-process-dialog-title">{draftOnly ? "Входные данные процесса" : "Запустить процесс"}</h2>
            <p>{process ? `${process.name} · ${draftOnly ? "черновик" : `версия ${pinnedVersion ?? process.publishedVersion}`}` : "Опубликованный процесс"}</p>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" />
          </button>
        </div>
        <ProcessInputField processId={process?.id ?? null} version={pinnedVersion || process?.publishedVersion || "draft"}
          open={open} busy={busy} onReadyChange={setInputReady} />
        {draftOnly ? <p className="process-field-help">Шаблон сохранён в стартовом шаге. Запуск станет доступен после подготовки и публикации процесса.</p> : null}
        <label className="field">
          <span>Приоритет · {priority}</span>
          <input type="range" min="0" max="100" value={priority} onChange={(event) => setPriority(Number(event.target.value))} />
        </label>
        <KnowledgeCollectionPicker
          collections={collections}
          selectedIds={selectedCollections}
          onToggle={(collectionId) => setSelectedCollections((current) =>
            current.includes(collectionId)
              ? current.filter((id) => id !== collectionId)
              : [...current, collectionId]
          )}
        />
        <ResultStorageFields
          destination={resultDestination}
          artifactPath={artifactPath}
          onDestinationChange={setResultDestination}
          onArtifactPathChange={setArtifactPath}
        />
        {!draftOnly ? <ScenarioPreflightPanel {...preflight} roles={roles} onRefresh={preflight.refresh} onRecover={() => onClose()} /> : null}
        <label className="field"><span>Способ запуска</span><select value={startMode} onChange={(event) => setStartMode(event.target.value as "queue" | "now")} disabled={busy}>
          <option value="now">Выполнить сейчас</option><option value="queue">Поставить в очередь и ждать готовности</option>
        </select></label>
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="dialog-actions">
          <button className="button button--secondary" type="button" onClick={onClose}>Отмена</button>
          <button className="button button--primary" type="submit" disabled={busy || !process || draftOnly || !inputReady || !scenarioStartAllowed(preflight.result, startMode)}>
            <Icon name="play" size={17} />{busy ? "Запускаем…" : startMode === "queue" ? "Поставить в очередь" : "Выполнить сейчас"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
