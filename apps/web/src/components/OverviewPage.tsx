import type { Agent, ComputeNode, Overview, Run, RunStatus, ViewId } from "../types";
import { Icon } from "./Icon";

const statusCopy: Record<RunStatus, string> = {
  queued: "В очереди",
  running: "Выполняется",
  waiting_approval: "Ждёт решения",
  waiting_external: "Ждёт signal",
  compensating: "Компенсация",
  completed: "Завершён",
  failed: "Ошибка",
  cancelled: "Отклонён",
};

function time(value: string): string {
  return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
}

function isAgentReady(agent: Agent, nodes: ComputeNode[]): boolean {
  return nodes.some((node) => {
    const modelCompatible = agent.model === null || node.models.length === 0 || node.models.includes(agent.model);
    return node.status === "online" && modelCompatible && node.agentRuntimes.includes(agent.runtime);
  });
}

function RunSnapshot({ run, onOpen }: { run: Run; onOpen: (runId: string) => void }) {
  const completed = run.stages.filter((stage) => stage.status === "completed").length;
  const percent = run.stages.length === 0 ? 0 : Math.round((completed / run.stages.length) * 100);
  return (
    <button className="snapshot-row" type="button" onClick={() => onOpen(run.id)}>
      <span className={`snapshot-row__marker snapshot-row__marker--${run.status}`} />
      <span className="snapshot-row__main">
        <strong>{run.name}</strong>
        <small>{run.stages.map((stage) => stage.agent.name).join(" → ")}</small>
      </span>
      <span className={`run-status run-status--${run.status}`}>{statusCopy[run.status]}</span>
      <span className="snapshot-row__progress"><i style={{ width: `${percent}%` }} /></span>
      <Icon name="chevron" size={16} />
    </button>
  );
}

interface OverviewPageProps {
  overview: Overview;
  onCreateRun: () => void;
  onNavigate: (view: ViewId) => void;
  onOpenRun: (runId: string) => void;
}

export function OverviewPage({ overview, onCreateRun, onNavigate, onOpenRun }: OverviewPageProps) {
  const activeRuns = overview.runs
    .filter((run) => ["queued", "running", "waiting_approval"].includes(run.status))
    .slice(0, 6);
  const recentEvents = overview.events.slice(-7).reverse();

  return (
    <main className="main-column section-page" id="overview">
      <div className="page-title page-title--section">
        <div><h1>Центр управления</h1><p>Живое состояние очереди, агентов и локальных вычислительных узлов</p></div>
        <button className="button button--primary page-title__action" type="button" onClick={onCreateRun}>
          <Icon name="play" size={17} />Новый запуск
        </button>
      </div>

      <div className="live-source">
        <span><i />LIVE</span>
        <p>Данные из coordinator и heartbeat воркеров</p>
        <time className="mono">снимок {time(overview.generatedAt)}</time>
      </div>

      <section className="overview-kpis" aria-label="Ключевые показатели">
        <article><span>Выполняются</span><strong>{overview.counts.activeRuns}</strong><small>{overview.scheduler.activeLeases} активных этапов</small></article>
        <article><span>В очереди</span><strong>{overview.counts.queued}</strong><small>{overview.counts.waitingApprovals} ждут решения</small></article>
        <article><span>Агенты готовы</span><strong>{overview.counts.readyAgents}<em>/{overview.counts.agents}</em></strong><small>есть совместимый online-узел</small></article>
        <article><span>Вычислительный контур</span><strong>{overview.counts.onlineNodes}<em>/{overview.counts.nodes}</em></strong><small>{overview.counts.models} моделей онлайн</small></article>
      </section>

      <div className="overview-board">
        <section className="overview-panel overview-panel--wide">
          <header><div><h2>Активная очередь</h2><p>Реальные запуски в SQLite coordinator</p></div><button type="button" onClick={() => onNavigate("runs")}>Все запуски <Icon name="chevron" size={15} /></button></header>
          <div className="snapshot-list">
            {activeRuns.length > 0
              ? activeRuns.map((run) => <RunSnapshot run={run} onOpen={onOpenRun} key={run.id} />)
              : <div className="panel-empty"><Icon name="check" size={22} /><span>Очередь свободна</span><button type="button" onClick={onCreateRun}>Создать запуск</button></div>}
          </div>
        </section>

        <section className="overview-panel">
          <header><div><h2>Готовность агентов</h2><p>Модель и runtime сопоставлены с online-узлом</p></div><button type="button" onClick={() => onNavigate("agents")}>Управлять <Icon name="chevron" size={15} /></button></header>
          <div className="readiness-list">
            {overview.agents.slice(0, 6).map((agent) => {
              const ready = isAgentReady(agent, overview.nodes);
              return (
                <div key={agent.id}>
                  <span className={`status-dot status-dot--${ready ? "online" : "waiting"}`} />
                  <span><strong>{agent.name}</strong><small className="mono">{agent.model ?? "Автовыбор"} · {agent.runtime}</small></span>
                  <em>{ready ? "Готов" : "Нет узла"}</em>
                </div>
              );
            })}
          </div>
        </section>

        <section className="overview-panel overview-panel--events">
          <header><div><h2>Последние события</h2><p>SSE-журнал без имитации состояния</p></div><span className="live-indicator"><i />SSE</span></header>
          <div className="overview-events mono">
            {recentEvents.length > 0 ? recentEvents.map((event) => (
              <div key={event.id}>
                <time>{time(event.createdAt)}</time>
                <span className={`event-level event-level--${event.level}`}>{event.level.toUpperCase()}</span>
                <p>{event.message}</p>
              </div>
            )) : <p className="panel-empty-copy">Событий пока нет — они появятся после подключения узла или создания запуска.</p>}
          </div>
        </section>

        <section className="overview-panel overview-panel--infra">
          <header><div><h2>Инфраструктура</h2><p>Что coordinator видит прямо сейчас</p></div><button type="button" onClick={() => onNavigate("nodes")}>Узлы <Icon name="chevron" size={15} /></button></header>
          <dl className="infra-list">
            <div><dt>Online / всего узлов</dt><dd>{overview.counts.onlineNodes} / {overview.counts.nodes}</dd></div>
            <div><dt>Модели online / всего</dt><dd>{overview.counts.models} / {overview.counts.totalModels}</dd></div>
            <div><dt>Политика очереди</dt><dd>{overview.scheduler.mode}</dd></div>
            <div><dt>Всего запусков</dt><dd>{overview.counts.totalRuns}</dd></div>
          </dl>
          {overview.counts.nodes === 0 ? <p className="infra-warning"><Icon name="warning" size={17} />Нет подключённых workers. Запуски останутся в очереди.</p> : null}
        </section>
      </div>
    </main>
  );
}
