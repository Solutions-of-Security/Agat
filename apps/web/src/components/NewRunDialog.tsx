import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import {
  RUN_PRIORITY_VALUES,
  buildCreateRunRequest,
  initialRunAgentIds,
  moveRunAgent,
  normalizeRunPriority,
  priorityPresetForValue,
  type RunPriorityPreset,
} from "../newRunFlow";
import type { Agent, CreateRunRequest, KnowledgeCollection, ResultDestination, SchedulerMode } from "../types";
import { Icon } from "./Icon";
import { KnowledgeCollectionPicker } from "./KnowledgeCollectionPicker";

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

type WizardStep = 1 | 2 | 3;

interface FieldErrors {
  name?: string;
  input?: string;
  agents?: string;
}

const wizardSteps: Array<{ id: WizardStep; label: string }> = [
  { id: 1, label: "Задача" },
  { id: 2, label: "Цепочка" },
  { id: 3, label: "Проверка" },
];

const modeOptions: Array<{ id: SchedulerMode; label: string }> = [
  { id: "sequential", label: "Последовательно" },
  { id: "auto", label: "Авто" },
  { id: "parallel", label: "Параллельно" },
];

const priorityOptions: Array<{ id: RunPriorityPreset; label: string }> = [
  { id: "normal", label: "Обычный" },
  { id: "high", label: "Высокий" },
  { id: "urgent", label: "Срочный" },
];

function collectionCountLabel(count: number) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return `${count} коллекция`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${count} коллекции`;
  return `${count} коллекций`;
}

export function NewRunDialog({ open, agents, collections, initialAgentIds, busy, error, onClose, onSubmit }: NewRunDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const stepHeadingRef = useRef<HTMLHeadingElement>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const taskInputRef = useRef<HTMLTextAreaElement>(null);
  const [step, setStep] = useState<WizardStep>(1);
  const [name, setName] = useState("");
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<SchedulerMode>("sequential");
  const [priority, setPriority] = useState(RUN_PRIORITY_VALUES.normal);
  const [approvalRequired, setApprovalRequired] = useState(true);
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [selectedCollections, setSelectedCollections] = useState<string[]>([]);
  const [resultDestination, setResultDestination] = useState<ResultDestination>("history");
  const [artifactPath, setArtifactPath] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});

  const agentsById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent])), [agents]);
  const collectionsById = useMemo(() => new Map(collections.map((collection) => [collection.id, collection])), [collections]);
  const selectedAgentRecords = selectedAgents.flatMap((id) => {
    const agent = agentsById.get(id);
    return agent ? [agent] : [];
  });
  const availableAgents = agents.filter((agent) => !selectedAgents.includes(agent.id));
  const selectedCollectionRecords = selectedCollections.flatMap((id) => {
    const collection = collectionsById.get(id);
    return collection ? [collection] : [];
  });
  const agentDefinitionsReady = selectedAgentRecords.length === selectedAgents.length && selectedAgents.length > 0;
  const knowledgeReady = selectedCollectionRecords.length === selectedCollections.length
    && selectedCollectionRecords.every((collection) => collection.chunkCount > 0 && collection.embeddedChunks === collection.chunkCount);
  const canLaunch = Boolean(name.trim() && input.trim() && agentDefinitionsReady && knowledgeReady);
  const priorityPreset = priorityPresetForValue(priority);
  const finalAgentName = selectedAgentRecords.at(-1)?.name ?? "Финальный агент";
  const chainSummary = selectedAgentRecords.map((agent) => agent.name).join(" → ") || "Цепочка не собрана";
  const knowledgeSummary = selectedCollectionRecords.length > 0
    ? selectedCollectionRecords.map((collection) => collection.name).join(", ")
    : "Без дополнительных знаний";

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setStep(1);
      setName("");
      setInput("");
      setMode("sequential");
      setPriority(RUN_PRIORITY_VALUES.normal);
      setApprovalRequired(true);
      setSelectedAgents(initialRunAgentIds(agents, initialAgentIds));
      setSelectedCollections([]);
      setResultDestination("history");
      setArtifactPath("");
      setFieldErrors({});
      dialog.showModal();
      requestAnimationFrame(() => nameInputRef.current?.focus());
    }
    if (!open && dialog.open) dialog.close();
  }, [agents, initialAgentIds, open]);

  useEffect(() => {
    if (!open || step === 1) return;
    requestAnimationFrame(() => stepHeadingRef.current?.focus());
  }, [open, step]);

  function goToStep(nextStep: WizardStep) {
    setFieldErrors({});
    setStep(nextStep);
  }

  function continueFromTask() {
    const nextErrors: FieldErrors = {};
    if (!name.trim()) nextErrors.name = "Укажите название запуска.";
    if (!input.trim()) nextErrors.input = "Опишите задачу и ожидаемый результат.";
    setFieldErrors(nextErrors);
    if (nextErrors.name) {
      nameInputRef.current?.focus();
      return;
    }
    if (nextErrors.input) {
      taskInputRef.current?.focus();
      return;
    }
    setStep(2);
  }

  function continueFromChain() {
    if (selectedAgents.length === 0) {
      setFieldErrors({ agents: "Добавьте хотя бы одного агента в цепочку." });
      return;
    }
    setFieldErrors({});
    setStep(3);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (step === 1) {
      continueFromTask();
      return;
    }
    if (step === 2) {
      continueFromChain();
      return;
    }
    if (!canLaunch) return;

    onSubmit(buildCreateRunRequest({
      name,
      input,
      executionMode: mode,
      priority,
      approvalRequired,
      agentIds: selectedAgents,
      resultDestination,
      artifactPath,
      knowledgeCollectionIds: selectedCollections,
    }));
  }

  function addAgent(agentId: string) {
    setSelectedAgents((current) => current.includes(agentId) ? current : [...current, agentId]);
    setFieldErrors((current) => ({ ...current, agents: undefined }));
  }

  function removeAgent(agentId: string) {
    setSelectedAgents((current) => current.filter((id) => id !== agentId));
  }

  function toggleCollection(collectionId: string) {
    setSelectedCollections((current) =>
      current.includes(collectionId)
        ? current.filter((id) => id !== collectionId)
        : [...current, collectionId],
    );
  }

  function closeDialog() {
    if (!busy) onClose();
  }

  return (
    <dialog
      className="run-dialog run-wizard-dialog"
      ref={dialogRef}
      aria-labelledby="new-run-dialog-title"
      aria-describedby="new-run-dialog-description"
      onCancel={(event) => {
        if (busy) event.preventDefault();
        else onClose();
      }}
      onClose={onClose}
    >
      <form className="run-wizard" onSubmit={submit} noValidate>
        <div className="dialog-head run-wizard__head">
          <div>
            <h2 id="new-run-dialog-title">Новый запуск</h2>
            <p id="new-run-dialog-description">
              <span className="run-wizard__desktop-copy">Три шага до запуска</span>
              <span className="run-wizard__mobile-copy">Шаг {step} из 3</span>
            </p>
          </div>
          <button className="icon-button" type="button" onClick={closeDialog} disabled={busy} aria-label="Закрыть">
            <Icon name="close" />
          </button>
        </div>

        <ol className="run-wizard__steps" aria-label="Шаги создания запуска">
          {wizardSteps.map((item) => {
            const completed = step > item.id;
            const current = step === item.id;
            return (
              <li className={completed ? "is-completed" : current ? "is-current" : ""} aria-current={current ? "step" : undefined} key={item.id}>
                <span>{completed ? <Icon name="check" size={16} /> : item.id}</span>
                <strong>{item.label}</strong>
              </li>
            );
          })}
        </ol>

        <div className="run-wizard__content">
          {step === 1 ? (
            <section className="run-wizard__step run-wizard__step--task" aria-labelledby="run-wizard-step-title">
              <header>
                <h3 id="run-wizard-step-title" ref={stepHeadingRef} tabIndex={-1}>Что нужно сделать?</h3>
              </header>
              <label className="field">
                <span>Название запуска</span>
                <input
                  ref={nameInputRef}
                  value={name}
                  required
                  maxLength={120}
                  placeholder="Например, проверка квартального отчёта"
                  aria-invalid={Boolean(fieldErrors.name)}
                  aria-describedby={fieldErrors.name ? "run-name-error" : undefined}
                  onChange={(event) => {
                    setName(event.target.value);
                    if (fieldErrors.name) setFieldErrors((current) => ({ ...current, name: undefined }));
                  }}
                />
                {fieldErrors.name ? <small className="field-error" id="run-name-error" role="alert">{fieldErrors.name}</small> : null}
              </label>
              <label className="field">
                <span>Задача и входные данные</span>
                <textarea
                  ref={taskInputRef}
                  value={input}
                  required
                  rows={6}
                  placeholder="Опишите ожидаемый результат, источники данных и ограничения"
                  aria-invalid={Boolean(fieldErrors.input)}
                  aria-describedby={fieldErrors.input ? "run-input-error" : "run-input-hint"}
                  onChange={(event) => {
                    setInput(event.target.value);
                    if (fieldErrors.input) setFieldErrors((current) => ({ ...current, input: undefined }));
                  }}
                />
                {fieldErrors.input ? (
                  <small className="field-error" id="run-input-error" role="alert">{fieldErrors.input}</small>
                ) : (
                  <small className="field-hint" id="run-input-hint">Опишите результат, источники и ограничения — детали можно уточнить позже.</small>
                )}
              </label>
            </section>
          ) : null}

          {step === 2 ? (
            <section className="run-wizard__step run-wizard__step--chain" aria-labelledby="run-wizard-step-title">
              <header>
                <h3 id="run-wizard-step-title" ref={stepHeadingRef} tabIndex={-1}>Кто выполнит задачу?</h3>
                <p>Порядок выполнения задаётся сверху вниз.</p>
              </header>

              <ol className="run-wizard__chain" aria-label="Выбранная цепочка агентов">
                {selectedAgentRecords.map((agent, index) => (
                  <li key={agent.id}>
                    <span className="run-wizard__chain-number">{index + 1}</span>
                    <span className="run-wizard__agent-status" aria-hidden="true" />
                    <span className="run-wizard__agent-copy">
                      <strong>{agent.name}</strong>
                      <small>{agent.role} · {agent.model ?? "Автовыбор модели"}</small>
                    </span>
                    <span className="run-wizard__reorder">
                      <button type="button" disabled={index === 0} aria-label={`Переместить ${agent.name} выше`} onClick={() => setSelectedAgents((current) => moveRunAgent(current, agent.id, -1))}>
                        <Icon name="up" size={17} />
                      </button>
                      <button type="button" disabled={index === selectedAgentRecords.length - 1} aria-label={`Переместить ${agent.name} ниже`} onClick={() => setSelectedAgents((current) => moveRunAgent(current, agent.id, 1))}>
                        <Icon name="down" size={17} />
                      </button>
                      <button type="button" aria-label={`Убрать ${agent.name} из цепочки`} onClick={() => removeAgent(agent.id)}>
                        <Icon name="trash" size={17} />
                      </button>
                    </span>
                  </li>
                ))}
              </ol>

              {selectedAgents.length === 0 ? <p className="run-wizard__empty">Цепочка пока пуста.</p> : null}
              {fieldErrors.agents ? <p className="field-error" role="alert">{fieldErrors.agents}</p> : null}

              <section className="run-wizard__available" aria-labelledby="run-wizard-available-title">
                <h4 id="run-wizard-available-title">Добавить агента</h4>
                {availableAgents.length > 0 ? (
                  <ul>
                    {availableAgents.map((agent) => (
                      <li key={agent.id}>
                        <span className="run-wizard__agent-status" aria-hidden="true" />
                        <span className="run-wizard__agent-copy">
                          <strong>{agent.name}</strong>
                          <small>{agent.role} · {agent.model ?? "Автовыбор модели"}</small>
                        </span>
                        <button type="button" onClick={() => addAgent(agent.id)}>Добавить</button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="run-wizard__empty">Все доступные агенты уже в цепочке.</p>
                )}
              </section>

              <details className="run-wizard__knowledge">
                <summary>
                  <Icon name="knowledge" size={19} />
                  <span><strong>Подключить знания <small>· необязательно</small></strong><small>{collectionCountLabel(selectedCollections.length)}</small></span>
                  <Icon name="down" size={17} />
                </summary>
                <KnowledgeCollectionPicker collections={collections} selectedIds={selectedCollections} onToggle={toggleCollection} />
              </details>
            </section>
          ) : null}

          {step === 3 ? (
            <section className="run-wizard__step run-wizard__step--review" aria-labelledby="run-wizard-step-title">
              <header>
                <h3 id="run-wizard-step-title" ref={stepHeadingRef} tabIndex={-1}>Проверьте перед запуском</h3>
              </header>

              <div className="run-wizard__summary" aria-label="Сводка запуска">
                <button type="button" onClick={() => goToStep(1)}>
                  <Icon name="list" size={19} />
                  <span><small>Задача</small><strong>{name.trim()}</strong></span>
                  <Icon name="chevron" size={17} />
                </button>
                <button type="button" onClick={() => goToStep(2)}>
                  <Icon name="workflow" size={19} />
                  <span><small>Цепочка</small><strong>{chainSummary}</strong></span>
                  <Icon name="chevron" size={17} />
                </button>
                <button type="button" onClick={() => goToStep(2)}>
                  <Icon name="knowledge" size={19} />
                  <span><small>Знания</small><strong>{knowledgeSummary}</strong></span>
                  <Icon name="chevron" size={17} />
                </button>
              </div>

              <fieldset className="run-wizard__choice">
                <legend>Как выполнить</legend>
                <div className="run-wizard__choice-grid run-wizard__choice-grid--three">
                  {modeOptions.map((option) => (
                    <label className={mode === option.id ? "is-selected" : ""} key={option.id}>
                      <input type="radio" name="executionMode" value={option.id} checked={mode === option.id} onChange={() => setMode(option.id)} />
                      <span>{option.label}</span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <fieldset className="run-wizard__choice">
                <legend>Приоритет</legend>
                <div className="run-wizard__choice-grid run-wizard__choice-grid--three">
                  {priorityOptions.map((option) => (
                    <label className={priorityPreset === option.id ? "is-selected" : ""} key={option.id}>
                      <input type="radio" name="priorityPreset" value={option.id} checked={priorityPreset === option.id} onChange={() => setPriority(RUN_PRIORITY_VALUES[option.id])} />
                      <span>{option.label}</span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <fieldset className="run-wizard__choice">
                <legend>Результат</legend>
                <div className="run-wizard__choice-grid run-wizard__choice-grid--two">
                  <label className={resultDestination === "history" ? "is-selected" : ""}>
                    <input type="radio" name="resultDestination" value="history" checked={resultDestination === "history"} onChange={() => setResultDestination("history")} />
                    <Icon name="list" size={18} />
                    <span>Только в журнале</span>
                  </label>
                  <label className={resultDestination === "artifacts" ? "is-selected" : ""}>
                    <input type="radio" name="resultDestination" value="artifacts" checked={resultDestination === "artifacts"} onChange={() => setResultDestination("artifacts")} />
                    <Icon name="save" size={18} />
                    <span>Журнал + Artifact Store</span>
                  </label>
                </div>
                <p>{resultDestination === "history"
                  ? "Результат останется доступен в запуске."
                  : "Каждый этап и финальный результат будут сохранены файлами."}</p>
              </fieldset>

              <details className="run-wizard__advanced">
                <summary>
                  <Icon name="layers" size={19} />
                  <span><strong>Расширенные настройки</strong><small>Приоритет {priority} · подтверждение {approvalRequired ? "включено" : "выключено"}</small></span>
                  <Icon name="down" size={17} />
                </summary>
                <div className="run-wizard__advanced-content">
                  <label className="field">
                    <span>Точный приоритет · 0–100</span>
                    <input type="number" min="0" max="100" value={priority} onChange={(event) => setPriority(normalizeRunPriority(Number(event.target.value)))} />
                    <small className="field-hint">Presets используют значения 50, 75 и 100.</small>
                  </label>
                  <label className="approval-toggle">
                    <input type="checkbox" checked={approvalRequired} onChange={(event) => setApprovalRequired(event.target.checked)} />
                    <span><strong>Подтверждать финальный этап</strong><small>{finalAgentName} не начнёт работу без решения оператора.</small></span>
                  </label>
                  {resultDestination === "artifacts" ? (
                    <label className="field">
                      <span>Каталог внутри Artifact Store · необязательно</span>
                      <input value={artifactPath} maxLength={180} placeholder="Например, reports/releases" onChange={(event) => setArtifactPath(event.target.value)} />
                      <small className="field-hint">К относительному пути автоматически добавится ID запуска.</small>
                    </label>
                  ) : null}
                </div>
              </details>

              <div className={`run-wizard__readiness${canLaunch ? " is-ready" : ""}`} role="status">
                <Icon name={canLaunch ? "check" : "warning"} size={20} />
                <span>
                  <strong>{canLaunch ? "Готово к запуску" : "Нужно исправить настройки"}</strong>
                  <small>{!agentDefinitionsReady
                    ? "Выбранный агент больше недоступен — вернитесь к цепочке."
                    : !knowledgeReady
                      ? "Выбранная коллекция ещё не готова — дождитесь индексации или отключите её."
                      : "Проверьте настройки и запустите цепочку."}</small>
                </span>
              </div>
              {error ? <p className="form-error" role="alert">{error}</p> : null}
            </section>
          ) : null}
        </div>

        <div className="dialog-actions run-wizard__actions">
          {step === 1 ? (
            <button className="button button--secondary" type="button" onClick={closeDialog} disabled={busy}>Отмена</button>
          ) : (
            <button className="button button--secondary" type="button" onClick={() => goToStep((step - 1) as WizardStep)} disabled={busy}>
              <Icon name="back" size={17} />Назад
            </button>
          )}
          <button className="button button--primary" type="submit" disabled={busy || (step === 3 && !canLaunch)}>
            {step === 3 ? <Icon name="play" size={17} /> : null}
            {busy ? "Создаём…" : step === 1 ? "Далее: цепочка" : step === 2 ? "Далее: проверка" : "Запустить"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
