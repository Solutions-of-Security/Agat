import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import type { Agent, CreateAgentRequest } from "../types";
import { Icon } from "./Icon";

interface AgentDialogProps {
  open: boolean;
  agent: Agent | null;
  agents: Agent[];
  models: string[];
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (payload: CreateAgentRequest) => void;
}

const emptyForm: CreateAgentRequest = {
  name: "",
  role: "",
  systemPrompt: "",
  model: null,
  runtime: "single",
  runtimeConfig: {
    profile: "tool_loop_v1",
    maxIterations: 6,
  },
};

export function AgentDialog({ open, agent, agents, models, busy, error, onClose, onSubmit }: AgentDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [form, setForm] = useState<CreateAgentRequest>(emptyForm);
  const specialistCandidates = useMemo(
    () => agents.filter((candidate) =>
      candidate.id !== agent?.id && candidate.runtimeConfig.profile !== "specialist_team_v1"),
    [agent?.id, agents],
  );
  const teamConfig = form.runtimeConfig.profile === "specialist_team_v1" ? form.runtimeConfig : null;
  const teamMemberError = teamConfig && (
    teamConfig.specialistAgentIds.length < 2 || teamConfig.specialistAgentIds.length > 8
  ) ? "Выберите от 2 до 8 специалистов." : null;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setForm(agent ? {
        name: agent.name,
        role: agent.role,
        systemPrompt: agent.systemPrompt,
        model: agent.model,
        runtime: agent.runtime,
        runtimeConfig: agent.runtimeConfig,
      } : emptyForm);
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [agent, open]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit({ ...form, model: form.model?.trim() || null });
  }

  return (
    <dialog className="run-dialog agent-dialog" ref={dialogRef} onCancel={onClose} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="dialog-head">
          <div>
            <h2>{agent ? "Настройка агента" : "Новый агент"}</h2>
            <p>{agent ? "Роль и runtime редактируются здесь; prompt/model продвигаются через Golden eval" : "Опишите ответственность, инструкции и предпочтительную локальную модель"}</p>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" />
          </button>
        </div>

        <label className="field">
          <span>Имя агента</span>
          <input
            value={form.name}
            required
            maxLength={80}
            autoFocus
            placeholder="Например, Юрист по договорам"
            onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
          />
        </label>
        <label className="field">
          <span>Роль</span>
          <input
            value={form.role}
            required
            maxLength={280}
            placeholder="Что агент делает и какой результат передаёт дальше"
            onChange={(event) => setForm((current) => ({ ...current, role: event.target.value }))}
          />
        </label>
        <label className="field">
          <span>Системный промпт</span>
          <textarea
            value={form.systemPrompt}
            required
            maxLength={20_000}
            rows={7}
            placeholder="Инструкции, ограничения, формат ответа и критерии качества"
            disabled={Boolean(agent)}
            onChange={(event) => setForm((current) => ({ ...current, systemPrompt: event.target.value }))}
          />
          {agent ? <small className="field-hint">Активная версия неизменяема. Создайте candidate в Golden eval → Prompt registry.</small> : null}
        </label>
        <label className="field">
          <span>Модель</span>
          <input
            value={form.model ?? ""}
            list="agat-model-options"
            maxLength={200}
            placeholder="Автовыбор по доступному узлу"
            disabled={Boolean(agent)}
            onChange={(event) => setForm((current) => ({ ...current, model: event.target.value || null }))}
          />
          <small className="field-hint">{agent ? "Смена model pin требует прошедшего golden experiment." : "Оставьте пустым для модели по умолчанию или укажите имя, которое публикует worker."}</small>
          <datalist id="agat-model-options">
            {models.map((model) => <option value={model} key={model} />)}
          </datalist>
        </label>
        <label className="field">
          <span>Runtime агента</span>
          <select
            value={form.runtime}
            onChange={(event) => setForm((current) => ({
              ...current,
              runtime: event.target.value === "langgraph" ? "langgraph" : "single",
              runtimeConfig: event.target.value === "langgraph" || current.runtimeConfig.profile === "tool_loop_v1"
                ? current.runtimeConfig
                : { profile: "tool_loop_v1", maxIterations: current.runtimeConfig.maxIterations },
            }))}
          >
            <option value="single">Single · прямой model/tool loop</option>
            <option value="langgraph">LangGraph · управляемый StateGraph</option>
          </select>
          <small className="field-hint">
            LangGraph выполняется внутри одного lease. Temporal продолжает управлять всем бизнес-процессом.
          </small>
        </label>
        {form.runtime === "langgraph" ? (
          <>
            <label className="field">
              <span>Профиль графа</span>
              <select
                value={form.runtimeConfig.profile}
                onChange={(event) => setForm((current) => ({
                  ...current,
                  runtimeConfig: event.target.value === "specialist_team_v1"
                    ? {
                      profile: "specialist_team_v1",
                      maxIterations: current.runtimeConfig.maxIterations,
                      maxHandoffs: 4,
                      stateSchema: "specialist_team_state_v1",
                      specialistAgentIds: [],
                    }
                    : { profile: "tool_loop_v1", maxIterations: current.runtimeConfig.maxIterations },
                }))}
              >
                <option value="tool_loop_v1">Tool loop v1 · один агент</option>
                <option value="specialist_team_v1">Specialist team v1 · supervisor + handoff</option>
              </select>
            </label>
            <label className="field">
              <span>Максимум итераций subgraph</span>
              <input
                type="number"
                min={1}
                max={12}
                step={1}
                value={form.runtimeConfig.maxIterations}
                onChange={(event) => setForm((current) => ({
                  ...current,
                  runtimeConfig: {
                    ...current.runtimeConfig,
                    maxIterations: Number(event.target.value),
                  },
                }))}
              />
              <small className="field-hint">
                Жёсткий предел 1–12 дополнительно ограничивается настройкой worker.
              </small>
            </label>
            {teamConfig ? (
              <div className="agent-team-config">
                <label className="field">
                  <span>Максимум handoff</span>
                  <input
                    type="number"
                    min={1}
                    max={8}
                    step={1}
                    value={teamConfig.maxHandoffs}
                    onChange={(event) => setForm((current) => current.runtimeConfig.profile === "specialist_team_v1" ? ({
                      ...current,
                      runtimeConfig: {
                        ...current.runtimeConfig,
                        maxHandoffs: Number(event.target.value),
                      },
                    }) : current)}
                  />
                </label>
                <fieldset className="agent-team-picker">
                  <legend>Specialists · {teamConfig.specialistAgentIds.length}/8</legend>
                  <p>Участники фиксируются immutable snapshots при создании run.</p>
                  <div>
                    {specialistCandidates.map((candidate) => {
                      const selected = teamConfig.specialistAgentIds.includes(candidate.id);
                      return (
                        <label key={candidate.id}>
                          <input
                            type="checkbox"
                            checked={selected}
                            disabled={!selected && teamConfig.specialistAgentIds.length >= 8}
                            onChange={() => setForm((current) => {
                              if (current.runtimeConfig.profile !== "specialist_team_v1") return current;
                              const ids = current.runtimeConfig.specialistAgentIds;
                              return {
                                ...current,
                                runtimeConfig: {
                                  ...current.runtimeConfig,
                                  specialistAgentIds: ids.includes(candidate.id)
                                    ? ids.filter((id) => id !== candidate.id)
                                    : [...ids, candidate.id],
                                },
                              };
                            })}
                          />
                          <span><strong>{candidate.name}</strong><small>{candidate.role} · {candidate.model ?? "автовыбор"}</small></span>
                        </label>
                      );
                    })}
                  </div>
                  {specialistCandidates.length === 0 ? <small className="field-hint">Сначала создайте как минимум двух обычных агентов.</small> : null}
                  {teamMemberError ? <small className="form-error">{teamMemberError}</small> : null}
                </fieldset>
                <p className="agent-team-schema">State schema <code>{teamConfig.stateSchema}</code> · произвольные schemas и вложенные teams запрещены.</p>
              </div>
            ) : null}
          </>
        ) : null}

        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="dialog-actions">
          <button className="button button--secondary" type="button" onClick={onClose}>Отмена</button>
          <button className="button button--primary" type="submit" disabled={busy || Boolean(teamMemberError)}>
            <Icon name={agent ? "check" : "plus"} size={17} />
            {busy ? "Сохраняем…" : agent ? "Сохранить" : "Создать агента"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
