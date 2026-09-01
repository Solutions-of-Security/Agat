import { useDeferredValue, useMemo, useState } from "react";

import type { AgentMarketplaceTemplate } from "../agentMarketplace";
import type { Agent, ComputeNode } from "../types";
import { AccessibleTabList, TabPanel } from "./AccessibleTabs";
import { AgentMarketplace } from "./AgentMarketplace";
import { Icon } from "./Icon";

type AgentsTab = "configured" | "marketplace";

const AGENT_TABS: readonly { id: AgentsTab; label: string }[] = [
  { id: "configured", label: "Настроенные" },
  { id: "marketplace", label: "Маркетплейс" },
];

function formatDate(value: string | null): string {
  if (!value) return "Ещё не запускался";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function compatibleNodes(
  agent: Agent,
  nodes: ComputeNode[],
  agentsById: Map<string, Agent>,
): ComputeNode[] {
  const memberIds = agent.runtimeConfig.profile === "specialist_team_v1"
    ? agent.runtimeConfig.specialistAgentIds
    : [];
  const requiredModels = new Set([
    agent.model,
    ...memberIds.map((id) => agentsById.get(id)?.model ?? null),
  ].filter((model): model is string => Boolean(model)));
  return nodes.filter((node) => {
    if (node.status !== "online") return false;
    const modelCompatible = node.models.length === 0
      || [...requiredModels].every((model) => node.models.includes(model));
    const profileCompatible = agent.runtime !== "langgraph"
      || node.agentRuntimeProfiles.includes(agent.runtimeConfig.profile);
    return modelCompatible && profileCompatible && node.agentRuntimes.includes(agent.runtime);
  });
}

interface AgentsPageProps {
  agents: Agent[];
  nodes: ComputeNode[];
  onCreate: () => void;
  onEdit: (agent: Agent) => void;
  onRun: (agentId: string) => void;
  onInstallTemplate: (template: AgentMarketplaceTemplate) => void;
}

export function AgentsPage({ agents, nodes, onCreate, onEdit, onRun, onInstallTemplate }: AgentsPageProps) {
  const [activeTab, setActiveTab] = useState<AgentsTab>("configured");
  const [query, setQuery] = useState("");
  const agentsById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent])), [agents]);
  const deferredQuery = useDeferredValue(query.trim().toLocaleLowerCase("ru"));
  const visibleAgents = useMemo(() => {
    if (!deferredQuery) return agents;
    return agents.filter((agent) =>
      [agent.name, agent.role, agent.model ?? "автовыбор", agent.runtime, agent.runtimeConfig.profile]
        .join(" ")
        .toLocaleLowerCase("ru")
        .includes(deferredQuery),
    );
  }, [agents, deferredQuery]);
  const readyCount = agents.filter((agent) => compatibleNodes(agent, nodes, agentsById).length > 0).length;

  return (
    <main className="main-column section-page" id="agents">
      <div className="page-title page-title--section">
        <div>
          <h1>Агенты</h1>
          <p>{activeTab === "configured"
            ? "Реальные конфигурации, промпты, модели и активность в очереди"
            : "Проверенные шаблоны ролей из продуктового отчёта Agat"}</p>
        </div>
        <button className="button button--primary page-title__action" type="button" onClick={onCreate}>
          <Icon name="plus" size={18} />Новый агент
        </button>
      </div>

      <AccessibleTabList
        activeTab={activeTab}
        ariaLabel="Разделы агентов"
        className="agents-tabs"
        idPrefix="agents"
        tabs={AGENT_TABS}
        onChange={setActiveTab}
      />

      <TabPanel active={activeTab === "configured"} className="agents-tab-panel" idPrefix="agents" tabId="configured">
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
            <p>{agents.length === 0 ? "Создайте агента вручную или установите проверенный шаблон из маркетплейса." : "Измените поисковый запрос."}</p>
            {agents.length === 0 ? (
              <div className="large-empty-state__actions">
                <button className="button button--primary" type="button" onClick={() => setActiveTab("marketplace")}>Открыть маркетплейс</button>
                <button className="button button--secondary" type="button" onClick={onCreate}>Новый агент</button>
              </div>
            ) : null}
          </section>
        ) : (
          <section className="agent-grid" aria-label="Список агентов">
            {visibleAgents.map((agent) => {
              const compatible = compatibleNodes(agent, nodes, agentsById);
              const ready = compatible.length > 0;
              const teamMembers = agent.runtimeConfig.profile === "specialist_team_v1"
                ? agent.runtimeConfig.specialistAgentIds.flatMap((id) => {
                  const member = agentsById.get(id);
                  return member ? [member] : [];
                })
                : [];
              return (
                <article className="agent-card" key={agent.id}>
                  <div className="agent-card__head">
                    <div className="agent-avatar"><Icon name="agents" size={20} /></div>
                    <div>
                      <div className="agent-card__title">
                        <h2>{agent.name}</h2>
                        {agent.isBuiltIn ? <span className="tag">Встроенный</span> : <span className="tag tag--custom">Пользовательский</span>}
                        <span className={`tag tag--runtime tag--runtime-${agent.runtime}`}>
                          {agent.runtimeConfig.profile === "specialist_team_v1"
                            ? `Team · ${teamMembers.length}`
                            : agent.runtime === "langgraph" ? "LangGraph" : "Single"}
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

                  {teamMembers.length > 0 ? (
                    <div className="agent-team-preview">
                      <span>Specialist subgraphs</span>
                      <div>{teamMembers.map((member) => <code key={member.id}>{member.name}</code>)}</div>
                      <small>{agent.runtimeConfig.profile === "specialist_team_v1" ? `${agent.runtimeConfig.maxHandoffs} handoff · ${agent.runtimeConfig.stateSchema}` : ""}</small>
                    </div>
                  ) : null}

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
      </TabPanel>

      <TabPanel active={activeTab === "marketplace"} className="agents-tab-panel" idPrefix="agents" tabId="marketplace">
        <AgentMarketplace agents={agents} onInstall={onInstallTemplate} onOpenAgent={onEdit} />
      </TabPanel>
    </main>
  );
}
