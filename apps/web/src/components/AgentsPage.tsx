import { useDeferredValue, useMemo, useState } from "react";

import type { Agent, ComputeNode } from "../types";
import { Icon } from "./Icon";

function formatDate(value: string | null): string {
  if (!value) return "Ещё не запускался";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function compatibleNodes(agent: Agent, nodes: ComputeNode[]): ComputeNode[] {
  return nodes.filter((node) => {
    if (node.status !== "online") return false;
    const modelCompatible = agent.model === null || node.models.length === 0 || node.models.includes(agent.model);
    return modelCompatible && node.agentRuntimes.includes(agent.runtime);
  });
}

interface AgentsPageProps {
  agents: Agent[];
  nodes: ComputeNode[];
  onCreate: () => void;
  onEdit: (agent: Agent) => void;
  onRun: (agentId: string) => void;
}

export function AgentsPage({ agents, nodes, onCreate, onEdit, onRun }: AgentsPageProps) {
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query.trim().toLocaleLowerCase("ru"));
  const visibleAgents = useMemo(() => {
    if (!deferredQuery) return agents;
    return agents.filter((agent) =>
      [agent.name, agent.role, agent.model ?? "автовыбор", agent.runtime]
        .join(" ")
        .toLocaleLowerCase("ru")
        .includes(deferredQuery),
    );
  }, [agents, deferredQuery]);
  const readyCount = agents.filter((agent) => compatibleNodes(agent, nodes).length > 0).length;

  return (
    <main className="main-column section-page" id="agents">
      <div className="page-title page-title--section">
        <div><h1>Агенты</h1><p>Реальные конфигурации, промпты, модели и активность в очереди</p></div>
        <button className="button button--primary page-title__action" type="button" onClick={onCreate}>
          <Icon name="plus" size={18} />Новый агент
        </button>
      </div>

      <section className="section-toolbar" aria-label="Сводка по агентам">
        <div className="toolbar-stat"><strong>{readyCount}</strong><span>готовы к запуску</span></div>
        <div className="toolbar-stat"><strong>{agents.length - readyCount}</strong><span>ждут подходящий узел</span></div>
        <div className="toolbar-stat"><strong>{agents.reduce((sum, agent) => sum + agent.activeRuns, 0)}</strong><span>участий в активных цепочках</span></div>
        <label className="search-field">
          <Icon name="agents" size={17} />
          <span className="sr-only">Найти агента</span>
          <input value={query} placeholder="Найти агента" onChange={(event) => setQuery(event.target.value)} />
        </label>
      </section>

      {visibleAgents.length === 0 ? (
        <section className="large-empty-state">
          <Icon name="agents" size={30} />
          <h2>{agents.length === 0 ? "Создайте первого агента" : "Ничего не найдено"}</h2>
          <p>{agents.length === 0 ? "Задайте роль, системный промпт и модель — агент сразу появится в конструкторе запусков." : "Измените поисковый запрос."}</p>
          {agents.length === 0 ? <button className="button button--primary" type="button" onClick={onCreate}>Новый агент</button> : null}
        </section>
      ) : (
        <section className="agent-grid" aria-label="Список агентов">
          {visibleAgents.map((agent) => {
            const compatible = compatibleNodes(agent, nodes);
            const ready = compatible.length > 0;
            return (
              <article className="agent-card" key={agent.id}>
                <div className="agent-card__head">
                  <div className="agent-avatar"><Icon name="agents" size={20} /></div>
                  <div>
                    <div className="agent-card__title">
                      <h2>{agent.name}</h2>
                      {agent.isBuiltIn ? <span className="tag">Встроенный</span> : <span className="tag tag--custom">Пользовательский</span>}
                      <span className={`tag tag--runtime tag--runtime-${agent.runtime}`}>
                        {agent.runtime === "langgraph" ? "LangGraph" : "Single"}
                      </span>
                    </div>
                    <p>{agent.role}</p>
                  </div>
                  <span className={`readiness readiness--${ready ? "ready" : "blocked"}`}>
                    <i />{ready ? "Готов" : "Нет узла"}
                  </span>
                </div>

                <div className="prompt-preview">
                  <span>Системный промпт</span>
                  <p>{agent.systemPrompt}</p>
                </div>

                <dl className="agent-card__metrics">
                  <div><dt>Модель</dt><dd className="mono">{agent.model ?? "Автовыбор"}</dd></div>
                  <div><dt>Совместимые узлы</dt><dd>{compatible.length}</dd></div>
                  <div><dt>Запуски</dt><dd>{agent.totalRuns}</dd></div>
                  <div><dt>Последний</dt><dd>{formatDate(agent.lastRunAt)}</dd></div>
                </dl>

                <div className="agent-card__actions">
                  <button className="button button--secondary" type="button" onClick={() => onEdit(agent)}>Настроить</button>
                  <button className="button button--primary" type="button" onClick={() => onRun(agent.id)}>
                    <Icon name="play" size={15} />{ready ? "Запустить" : "В очередь"}
                  </button>
                </div>
              </article>
            );
          })}
        </section>
      )}
    </main>
  );
}
