import { useEffect, useRef, useState, type FormEvent } from "react";

import type { Agent, CreateRunRequest, KnowledgeCollection, ResultDestination, SchedulerMode } from "../types";
import { Icon } from "./Icon";
import { KnowledgeCollectionPicker } from "./KnowledgeCollectionPicker";
import { ResultStorageFields } from "./ResultStorageFields";

interface NewRunDialogProps {
  open: boolean;
  agents: Agent[];
  collections: KnowledgeCollection[];
  initialAgentIds?: string[] | null;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (payload: CreateRunRequest) => void;
}

export function NewRunDialog({ open, agents, collections, initialAgentIds, busy, error, onClose, onSubmit }: NewRunDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const [mode, setMode] = useState<SchedulerMode>("sequential");
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [selectedCollections, setSelectedCollections] = useState<string[]>([]);
  const [resultDestination, setResultDestination] = useState<ResultDestination>("artifacts");
  const [artifactPath, setArtifactPath] = useState("");
  const finalAgentName = agents.find((agent) => agent.id === selectedAgents.at(-1))?.name ?? "Финальный агент";

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      formRef.current?.reset();
      setMode("sequential");
      setResultDestination("artifacts");
      setArtifactPath("");
      setSelectedCollections([]);
      const builtIns = agents.filter((agent) => agent.isBuiltIn).map((agent) => agent.id);
      setSelectedAgents(initialAgentIds?.length ? initialAgentIds : builtIns.length ? builtIns : agents.slice(0, 1).map((agent) => agent.id));
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [agents, initialAgentIds, open]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    onSubmit({
      name: String(data.get("name") ?? ""),
      input: String(data.get("input") ?? ""),
      executionMode: mode,
      priority: Number(data.get("priority") ?? 50),
      approvalRequired: data.get("approvalRequired") === "on",
      agentIds: selectedAgents,
      resultDestination,
      artifactPath: resultDestination === "artifacts" ? artifactPath : "",
      knowledgeCollectionIds: selectedCollections,
    });
  }

  function toggleAgent(agentId: string) {
    setSelectedAgents((current) =>
      current.includes(agentId) ? current.filter((id) => id !== agentId) : [...current, agentId],
    );
  }

  function toggleCollection(collectionId: string) {
    setSelectedCollections((current) =>
      current.includes(collectionId)
        ? current.filter((id) => id !== collectionId)
        : [...current, collectionId],
    );
  }

  return (
    <dialog className="run-dialog" ref={dialogRef} onCancel={onClose} onClose={onClose}>
      <form ref={formRef} onSubmit={submit}>
        <div className="dialog-head">
          <div><h2>Новый запуск</h2><p>Соберите цепочку и задайте политику выполнения</p></div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" />
          </button>
        </div>

        <label className="field">
          <span>Название</span>
          <input name="name" required maxLength={120} placeholder="Например, проверка релиза" autoFocus />
        </label>
        <label className="field">
          <span>Задача и входные данные</span>
          <textarea name="input" required rows={5} placeholder="Что должны сделать агенты, какие данные и ограничения учесть" />
        </label>

        <fieldset className="agent-picker">
          <legend>Цепочка агентов</legend>
          <div>
            {agents.map((agent) => (
              <label className={selectedAgents.includes(agent.id) ? "is-selected" : ""} key={agent.id}>
                <input type="checkbox" checked={selectedAgents.includes(agent.id)} onChange={() => toggleAgent(agent.id)} />
                <span>{selectedAgents.includes(agent.id) ? selectedAgents.indexOf(agent.id) + 1 : "+"}</span>
                <strong>{agent.name}</strong>
              </label>
            ))}
          </div>
        </fieldset>

        <KnowledgeCollectionPicker
          collections={collections}
          selectedIds={selectedCollections}
          onToggle={toggleCollection}
        />

        <div className="form-row">
          <label className="field">
            <span>Режим</span>
            <select value={mode} onChange={(event) => setMode(event.target.value as SchedulerMode)}>
              <option value="sequential">Последовательно</option>
              <option value="auto">Авто</option>
              <option value="parallel">Параллельно</option>
            </select>
          </label>
          <label className="field">
            <span>Приоритет</span>
            <input type="number" name="priority" min="0" max="100" defaultValue="50" />
          </label>
        </div>

        <label className="approval-toggle">
          <input type="checkbox" name="approvalRequired" defaultChecked />
          <span><strong>Подтверждать финальный этап</strong><small>{finalAgentName} не начнёт работу без решения оператора</small></span>
        </label>
        <ResultStorageFields
          destination={resultDestination}
          artifactPath={artifactPath}
          onDestinationChange={setResultDestination}
          onArtifactPathChange={setArtifactPath}
        />
        {error ? <p className="form-error" role="alert">{error}</p> : null}

        <div className="dialog-actions">
          <button className="button button--secondary" type="button" onClick={onClose}>Отмена</button>
          <button className="button button--primary" type="submit" disabled={busy || selectedAgents.length === 0}>
            <Icon name="play" size={17} />{busy ? "Создаём…" : "Запустить"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
