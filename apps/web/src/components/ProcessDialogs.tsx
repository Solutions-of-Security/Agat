import { useEffect, useRef, useState, type FormEvent } from "react";

import type { CreateProcessRequest, KnowledgeCollection, ProcessDefinition, ResultDestination, StartProcessRequest } from "../types";
import { Icon } from "./Icon";
import { KnowledgeCollectionPicker } from "./KnowledgeCollectionPicker";
import { ResultStorageFields } from "./ResultStorageFields";

interface DialogStateProps {
  open: boolean;
  busy: boolean;
  error: string | null;
  onClose: () => void;
}

interface NewProcessDialogProps extends DialogStateProps {
  processes: ProcessDefinition[];
  onSubmit: (payload: CreateProcessRequest) => void;
}

export function NewProcessDialog({ open, busy, error, processes, onClose, onSubmit }: NewProcessDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      formRef.current?.reset();
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    onSubmit({
      name: String(data.get("name") ?? ""),
      description: String(data.get("description") ?? ""),
      templateId: String(data.get("templateId") ?? "") || undefined,
      isTemplate: data.get("isTemplate") === "on",
    });
  }

  return (
    <dialog className="run-dialog process-dialog" ref={dialogRef} onCancel={onClose} onClose={onClose}>
      <form ref={formRef} onSubmit={submit}>
        <div className="dialog-head">
          <div><h2>Новый процесс</h2><p>Начните с готового безопасного графа и настройте шаги на полотне</p></div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" />
          </button>
        </div>
        <label className="field">
          <span>Название</span>
          <input name="name" required maxLength={100} placeholder="Например, контент-конвейер" autoFocus />
        </label>
        <label className="field">
          <span>Описание</span>
          <textarea name="description" rows={4} maxLength={1_000} placeholder="Что автоматизирует процесс и какой результат считается готовым" />
        </label>
        <label className="field">
          <span>Начать из шаблона</span>
          <select name="templateId" defaultValue="">
            <option value="">Базовый безопасный граф</option>
            {processes.filter((process) => process.isTemplate).map((process) => (
              <option value={process.id} key={process.id}>{process.name} · v{process.publishedVersion}</option>
            ))}
          </select>
        </label>
        <label className="approval-toggle process-approval-toggle">
          <input type="checkbox" name="isTemplate" />
          <span><strong>Сохранить как шаблон</strong><small>Процесс появится в каталоге переиспользуемых заготовок.</small></span>
        </label>
        <div className="process-dialog__hint">
          <Icon name="shield" size={20} />
          <span><strong>Безопасный стартовый шаблон</strong><small>Старт, агенты, условие, цикл с лимитом и завершение уже связаны.</small></span>
        </div>
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="dialog-actions">
          <button className="button button--secondary" type="button" onClick={onClose}>Отмена</button>
          <button className="button button--primary" type="submit" disabled={busy}>
            <Icon name="plus" size={17} />{busy ? "Создаём…" : "Создать процесс"}
          </button>
        </div>
      </form>
    </dialog>
  );
}

interface StartProcessDialogProps extends DialogStateProps {
  process: ProcessDefinition | null;
  collections: KnowledgeCollection[];
  onSubmit: (payload: StartProcessRequest) => void;
}

export function StartProcessDialog({ process, collections, open, busy, error, onClose, onSubmit }: StartProcessDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const [priority, setPriority] = useState(50);
  const [resultDestination, setResultDestination] = useState<ResultDestination>("artifacts");
  const [artifactPath, setArtifactPath] = useState("");
  const [selectedCollections, setSelectedCollections] = useState<string[]>([]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      formRef.current?.reset();
      setPriority(50);
      setResultDestination("artifacts");
      setArtifactPath("");
      setSelectedCollections([]);
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    onSubmit({
      input: String(data.get("input") ?? ""),
      priority,
      resultDestination,
      artifactPath: resultDestination === "artifacts" ? artifactPath : "",
      knowledgeCollectionIds: selectedCollections,
    });
  }

  return (
    <dialog className="run-dialog process-dialog" ref={dialogRef} onCancel={onClose} onClose={onClose}>
      <form ref={formRef} onSubmit={submit}>
        <div className="dialog-head">
          <div>
            <h2>Запустить процесс</h2>
            <p>{process ? `${process.name} · версия ${process.publishedVersion}` : "Опубликованный процесс"}</p>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" />
          </button>
        </div>
        <label className="field">
          <span>Входные данные</span>
          <textarea
            name="input"
            required
            rows={7}
            maxLength={100_000}
            placeholder="Задача, исходные данные, ограничения и критерии результата"
            autoFocus
          />
        </label>
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
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="dialog-actions">
          <button className="button button--secondary" type="button" onClick={onClose}>Отмена</button>
          <button className="button button--primary" type="submit" disabled={busy || !process}>
            <Icon name="play" size={17} />{busy ? "Запускаем…" : "Запустить"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
