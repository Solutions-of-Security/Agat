import type { ProcessInstance, RunStatus } from "../types";
import { Icon } from "./Icon";

const statusLabels: Record<RunStatus, string> = {
  queued: "В очереди",
  running: "Выполняется",
  waiting_approval: "Ждёт подтверждения",
  waiting_external: "Ждёт signal",
  compensating: "Компенсация",
  completed: "Завершён",
  failed: "Ошибка",
  cancelled: "Остановлен",
};

function shortInstanceId(instance: ProcessInstance): string {
  const date = new Date(instance.createdAt);
  const timestamp = Number.isNaN(date.getTime())
    ? instance.id.slice(0, 8)
    : date.toLocaleString("ru-RU", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).replace(/[.,: ]/g, "");
  return `${instance.processName.slice(0, 4).toLocaleLowerCase("ru")}-${timestamp}`;
}

function duration(instance: ProcessInstance): string {
  const start = new Date(instance.startedAt ?? instance.createdAt).getTime();
  const end = new Date(instance.completedAt ?? instance.updatedAt).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return "—";
  const seconds = Math.max(0, Math.floor((end - start) / 1_000));
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function temporalWorkflowUrl(instance: ProcessInstance): string | null {
  if (instance.runtime !== "temporal" || !instance.workflowId || typeof window === "undefined") return null;
  const hostname = window.location.hostname || "127.0.0.1";
  return `http://${hostname}:8233/namespaces/agat/workflows/${encodeURIComponent(instance.workflowId)}`;
}

interface ProcessInstancesProps {
  instances: ProcessInstance[];
  busy: boolean;
  selectedInstanceId?: string | null;
  onSelect?: (instance: ProcessInstance) => void;
  onOpenRun: (runId: string) => void;
  onCancel: (instanceId: string) => void;
  onReplay: (instanceId: string, mode: "safe" | "live") => void;
}

export function ProcessInstances({
  instances,
  busy,
  selectedInstanceId = null,
  onSelect,
  onOpenRun,
  onCancel,
  onReplay,
}: ProcessInstancesProps) {
  return (
    <section className="process-instances">
      <header>
        <div><h2>Последние экземпляры процессов</h2><span>{instances.length}</span></div>
        <small>Версия закрепляется при запуске</small>
      </header>
      {instances.length === 0 ? (
        <div className="process-instances__empty">
          <Icon name="play" size={22} />
          <span><strong>Запусков процессов пока нет</strong><small>Опубликуйте граф и запустите первый экземпляр.</small></span>
        </div>
      ) : (
        <div className="process-instances__table-wrap">
          <table className="process-instances__table">
            <thead>
              <tr><th>Экземпляр</th><th>Версия</th><th>Runtime</th><th>Статус</th><th>Текущий шаг</th><th>Итерация</th><th>Длительность</th><th /></tr>
            </thead>
            <tbody>
              {instances.map((instance) => {
                const active = ["queued", "running", "waiting_approval", "waiting_external", "compensating"].includes(instance.status);
                const workflowUrl = temporalWorkflowUrl(instance);
                return (
                  <tr className={instance.id === selectedInstanceId ? "is-selected" : ""} key={instance.id}>
                    <td>
                      <button type="button" onClick={() => onSelect ? onSelect(instance) : onOpenRun(instance.runId)}>{shortInstanceId(instance)}</button>
                    </td>
                    <td className="mono">v{instance.processVersion}</td>
                    <td>
                      {workflowUrl ? (
                        <a
                          className="process-runtime process-runtime--temporal"
                          href={workflowUrl}
                          target="_blank"
                          rel="noreferrer"
                          title={`Открыть ${instance.workflowId} в Temporal UI`}
                        >
                          Temporal
                        </a>
                      ) : (
                        <span className="process-runtime">БД</span>
                      )}
                    </td>
                    <td><span className={`run-status run-status--${instance.status}`}><i className="run-status__dot" />{statusLabels[instance.status]}</span></td>
                    <td>
                      {instance.activeNodes?.length > 1
                        ? `${instance.activeNodes.length} параллельных шага`
                        : instance.pendingSignals?.[0]
                          ? `Signal: ${instance.pendingSignals[0].name}`
                          : instance.currentNode?.name ?? "—"}
                    </td>
                    <td className="mono">{instance.maxIterations ? `${instance.currentIteration} / ${instance.maxIterations}` : "—"}</td>
                    <td className="mono">{duration(instance)}</td>
                    <td>
                      {active ? (
                        <button
                          className="row-open"
                          type="button"
                          disabled={busy}
                          onClick={() => onCancel(instance.id)}
                          aria-label={`Остановить ${shortInstanceId(instance)}`}
                        >
                          <Icon name="close" size={16} />
                        </button>
                      ) : (
                        <span className="process-instance-actions">
                          <button className="row-open" type="button" disabled={busy} onClick={() => onReplay(instance.id, "safe")} aria-label="Безопасно повторить экземпляр" title="Safe replay">
                            <Icon name="repeat" size={15} />
                          </button>
                          <button className="row-open" type="button" onClick={() => onOpenRun(instance.runId)} aria-label="Открыть запуск">
                            <Icon name="chevron" size={16} />
                          </button>
                        </span>
                      )}
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
