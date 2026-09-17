import { useMemo, useState } from "react";

import type { Run, RunStatus } from "../types";
import { AccessibleTabList, TabPanel, type TabDefinition } from "./AccessibleTabs";
import { Icon } from "./Icon";

const statusCopy: Record<RunStatus, string> = {
  queued: "В очереди",
  running: "Выполняется",
  waiting_approval: "Требует решения",
  waiting_external: "Ждёт signal",
  compensating: "Компенсация",
  completed: "Завершён",
  failed: "Ошибка",
  cancelled: "Отменён",
};

function shortTime(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function Status({ status }: { status: RunStatus }) {
  return (
    <span className={`run-status run-status--${status}`}>
      <span className="run-status__dot" />
      {statusCopy[status]}
    </span>
  );
}

interface RunQueueProps {
  runs: Run[];
  selectedRunId: string | null;
  mode?: "all" | "approvals";
  approvalRunIds?: string[];
  onSelect: (runId: string) => void;
}

type QueueTab = "queue" | "active" | "history";

const queueTabs: readonly TabDefinition<QueueTab>[] = [
  { id: "queue", label: <><Icon name="list" size={18} />Все</> },
  { id: "active", label: <><Icon name="play" size={18} />Активные</> },
  { id: "history", label: <><Icon name="clock" size={18} />История</> },
];

export function RunQueue({ runs, selectedRunId, mode = "all", approvalRunIds = [], onSelect }: RunQueueProps) {
  const [tab, setTab] = useState<QueueTab>("queue");
  const approvalIds = useMemo(() => new Set(approvalRunIds), [approvalRunIds]);
  const visibleRuns = runs.filter((run) => {
    if (mode === "approvals") return run.status === "waiting_approval" || approvalIds.has(run.id);
    if (tab === "active") return run.status === "running" || run.status === "waiting_approval";
    if (tab === "history") return ["completed", "failed", "cancelled"].includes(run.status);
    return true;
  });

  return (
    <section className={`queue-panel${mode === "approvals" ? " queue-panel--approvals" : ""}`} id="queue" aria-labelledby="queue-title">
      <div className="section-header">
        <div>
          <h2 id="queue-title">{mode === "approvals" ? "Ожидают решения" : "Запуски"}</h2>
          <p>{mode === "approvals" ? "Выберите запрос" : "Выберите запуск"}</p>
        </div>
        <span className="section-header__meta"><Icon name={mode === "approvals" ? "check" : "list"} size={16} />{visibleRuns.length}</span>
      </div>
      {mode === "all" ? (
        <AccessibleTabList activeTab={tab} ariaLabel="Фильтр запусков" className="queue-tabs" idPrefix="run-queue" tabs={queueTabs} onChange={setTab} />
      ) : null}
      {mode === "all" ? queueTabs.map((queueTab) => (
        <TabPanel active={tab === queueTab.id} idPrefix="run-queue" tabId={queueTab.id} className="queue-tab-panel" key={queueTab.id}>
          {visibleRuns.length === 0 ? (
            <div className="empty-state">Очередь свободна. Создайте первый запуск.</div>
          ) : (
            <RunList runs={visibleRuns} selectedRunId={selectedRunId} onSelect={onSelect} />
          )}
        </TabPanel>
      )) : visibleRuns.length === 0 ? (
        <div className="empty-state">{mode === "approvals" ? "Нет действий, ожидающих решения." : "Очередь свободна. Создайте первый запуск."}</div>
      ) : (
        <RunList runs={visibleRuns} selectedRunId={selectedRunId} onSelect={onSelect} />
      )}
    </section>
  );
}

function RunList({ runs, selectedRunId, onSelect }: Pick<RunQueueProps, "runs" | "selectedRunId" | "onSelect">) {
  return (
    <ul className="run-list">
      {runs.map((run) => {
        const completedStages = run.stages.filter((stage) => stage.status === "completed").length;
        const activeStage = run.stages.find((stage) => ["running", "waiting_approval", "waiting_external"].includes(stage.status));
        return (
          <li key={run.id}>
            <button
              className={`run-list__item${selectedRunId === run.id ? " is-selected" : ""}`}
              type="button"
              onClick={() => onSelect(run.id)}
              aria-current={selectedRunId === run.id ? "true" : undefined}
            >
              <span className="run-list__topline">
                <strong>{run.name}</strong>
                <Icon name="chevron" size={17} />
              </span>
              <span className="run-list__statusline">
                <Status status={run.status} />
                <time dateTime={run.createdAt}>{shortTime(run.createdAt)}</time>
              </span>
              <span className="run-list__progress">
                {activeStage ? `${activeStage.agent.name} · ` : ""}{completedStages} из {run.stages.length} этапов
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
