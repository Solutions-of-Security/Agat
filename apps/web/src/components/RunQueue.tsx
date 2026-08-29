import { useState } from "react";

import type { Run, RunStatus } from "../types";
import { Icon } from "./Icon";

const statusCopy: Record<RunStatus, string> = {
  queued: "В очереди",
  running: "Выполняется",
  waiting_approval: "Нужно подтверждение",
  waiting_external: "Ждёт signal",
  compensating: "Компенсация",
  completed: "Завершён",
  failed: "Ошибка",
  cancelled: "Отклонён",
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
  onSelect: (runId: string) => void;
}

export function RunQueue({ runs, selectedRunId, onSelect }: RunQueueProps) {
  const [tab, setTab] = useState<"queue" | "active" | "history">("queue");
  const visibleRuns = runs.filter((run) => {
    if (tab === "active") return run.status === "running" || run.status === "waiting_approval";
    if (tab === "history") return ["completed", "failed", "cancelled"].includes(run.status);
    return true;
  });

  return (
    <section className="queue-panel" id="queue" aria-labelledby="queue-title">
      <div className="section-header">
        <h2 id="queue-title">Очередь запусков</h2>
        <div className="queue-filter-buttons" aria-label="Фильтр запусков">
          <button className={tab === "queue" ? "is-active" : ""} type="button" onClick={() => setTab("queue")}>Все</button>
          <button className={tab === "active" ? "is-active" : ""} type="button" onClick={() => setTab("active")}>Активные</button>
          <button className={tab === "history" ? "is-active" : ""} type="button" onClick={() => setTab("history")}>История</button>
          <span className="section-header__meta"><Icon name="list" size={16} />{visibleRuns.length}</span>
        </div>
      </div>
      <div className="queue-tabs" role="tablist" aria-label="Фильтр запусков">
        <button className={tab === "queue" ? "is-active" : ""} type="button" role="tab" aria-selected={tab === "queue"} onClick={() => setTab("queue")}>
          <Icon name="list" size={18} />Все
        </button>
        <button className={tab === "active" ? "is-active" : ""} type="button" role="tab" aria-selected={tab === "active"} onClick={() => setTab("active")}>
          <Icon name="play" size={18} />Активные
        </button>
        <button className={tab === "history" ? "is-active" : ""} type="button" role="tab" aria-selected={tab === "history"} onClick={() => setTab("history")}>
          <Icon name="clock" size={18} />История
        </button>
      </div>
      {visibleRuns.length === 0 ? (
        <div className="empty-state">Очередь свободна. Создайте первый запуск.</div>
      ) : (
        <div className="run-table-wrap">
          <table className="run-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Запуск</th>
                <th>Статус</th>
                <th>Узел</th>
                <th>Агент(ы)</th>
                <th>Создан</th>
                <th><span className="sr-only">Открыть</span></th>
              </tr>
            </thead>
            <tbody>
              {visibleRuns.map((run, index) => {
                const activeStage = run.stages.find((stage) => stage.status === "running");
                const node = activeStage?.nodeName ?? run.stages.find((stage) => stage.nodeName)?.nodeName ?? "—";
                const chain = run.stages.map((stage) => stage.agent.name).join(" → ");
                return (
                  <tr className={selectedRunId === run.id ? "is-selected" : ""} key={run.id}>
                    <td className="run-table__index">
                      <span className="desktop-row-index">{run.status === "running" ? <Icon name="play" size={13} /> : index + 1}</span>
                      <span className="mobile-row-icon"><Icon name={run.status === "running" ? "list" : run.status === "waiting_approval" ? "warning" : "box"} size={20} /></span>
                    </td>
                    <td>
                      <button className="run-name" type="button" onClick={() => onSelect(run.id)}>
                        {run.name}
                      </button>
                    </td>
                    <td><Status status={run.status} /></td>
                    <td className="mono muted">{node}</td>
                    <td className="chain-cell">{chain}</td>
                    <td className="mono muted">{shortTime(run.createdAt)}</td>
                    <td>
                      <button className="row-open" type="button" onClick={() => onSelect(run.id)} aria-label={`Открыть ${run.name}`}>
                        <Icon name="chevron" size={17} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
