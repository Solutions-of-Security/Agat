import { useDeferredValue, useEffect, useMemo, useState } from "react";
import { useRecoveryTarget } from "../hooks/useRecoveryTarget";
import type { AgentMarketplaceTemplate } from "../agentMarketplace";
import type { Agent, ComputeNode } from "../types";
import { AccessibleTabList, TabPanel } from "./AccessibleTabs";
import { AgentMarketplace } from "./AgentMarketplace";
import { Icon } from "./Icon";

type AgentFilter = "all" | "ready" | "active" | "blocked";
type AgentTab = "configured" | "marketplace";
const tabs: readonly { id: AgentTab; label: string }[] = [{ id: "configured", label: "Мои агенты" }, { id: "marketplace", label: "Шаблоны агентов" }];

function availableNodes(agent: Agent, nodes: ComputeNode[], agentsById: Map<string, Agent>) {
  const memberIds = agent.runtimeConfig.profile === "specialist_team_v1" ? agent.runtimeConfig.specialistAgentIds : [];
  const models = [agent.model, ...memberIds.map((id) => agentsById.get(id)?.model)].filter((model): model is string => Boolean(model));
  return nodes.filter((node) => node.status === "online" && node.agentRuntimes.includes(agent.runtime)
    && (agent.runtime !== "langgraph" || node.agentRuntimeProfiles.includes(agent.runtimeConfig.profile))
    && (!node.models.length || models.every((model) => node.models.includes(model))));
}

interface Props {
  agents: Agent[];
  nodes: ComputeNode[];
  onCreate: () => void;
  onEdit: (agent: Agent) => void;
  onRun: (id: string) => void;
  onInstallTemplate: (template: AgentMarketplaceTemplate) => void;
}

export function AgentsDirectory({ agents, nodes, onCreate, onEdit, onRun, onInstallTemplate }: Props) {
  const recovery = useRecoveryTarget();
  const recoveryAgent = recovery.get("agentId");
  useEffect(() => {
    const agent = agents.find((item) => item.id === recoveryAgent);
    if (agent) onEdit(agent);
  }, [recoveryAgent]);
  const [tab, setTab] = useState<AgentTab>("configured");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<AgentFilter>("all");
  const search = useDeferredValue(query.trim().toLocaleLowerCase("ru"));
  const agentsById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent])), [agents]);
  const availability = useMemo(() => new Map(agents.map((agent) => [agent.id, availableNodes(agent, nodes, agentsById).length])), [agents, nodes, agentsById]);
  const readyCount = agents.filter((agent) => availability.get(agent.id) && !agent.activeRuns).length;
  const blockedCount = agents.filter((agent) => !availability.get(agent.id) && !agent.activeRuns).length;
  const activeCount = agents.filter((agent) => agent.activeRuns > 0).length;
  const filters: { id: AgentFilter; label: string; count: number }[] = [
    { id: "all", label: "Все", count: agents.length }, { id: "ready", label: "Готовы", count: readyCount },
    { id: "active", label: "В работе", count: activeCount }, { id: "blocked", label: "Требуют внимания", count: blockedCount },
  ];
  const visible = agents.filter((agent) => {
    const ready = Boolean(availability.get(agent.id));
    if ((filter === "ready" && (!ready || agent.activeRuns > 0)) || (filter === "blocked" && (ready || agent.activeRuns > 0)) || (filter === "active" && !agent.activeRuns)) return false;
    return !search || [agent.name, agent.role, agent.model ?? "Автовыбор", agent.runtime, agent.runtimeConfig.profile].join(" ").toLocaleLowerCase("ru").includes(search);
  });

  return <main className="main-column section-page agent-workspace" id="agents">
    <div className="page-title page-title--section"><div><h1>Агенты</h1><p>Настраивайте агентов, объединяйте их в процессы и следите за готовностью к работе.</p></div><button className="button button--primary page-title__action" onClick={onCreate}><Icon name="plus" size={18} />Новый агент</button></div>
    <AccessibleTabList activeTab={tab} ariaLabel="Разделы агентов" className="agents-tabs" idPrefix="agents" tabs={tabs} onChange={setTab} />
    <TabPanel active={tab === "configured"} idPrefix="agents" tabId="configured">
      <section className="agent-directory-toolbar" aria-label="Фильтры агентов">
        <label className="search-field"><Icon name="search" size={17} /><span className="sr-only">Найти агента</span><input placeholder="Поиск по имени, задаче или модели" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
        <div className="agent-status-filters" aria-label="Состояние агента">{filters.map((item) => <button key={item.id} className={filter === item.id ? "is-active" : ""} aria-pressed={filter === item.id} onClick={() => setFilter(item.id)}>{item.label}<span>{item.count}</span></button>)}</div>
      </section>
      <p className="agent-results-count" role="status">{visible.length} из {agents.length} агентов</p>
      {visible.length ? <section className="agent-directory" aria-label="Список агентов">
        <div className="agent-directory-heading" aria-hidden="true"><span>Агент и задача</span><span>Модель</span><span>Состояние</span><span>Запуски</span><span>Действия</span></div>
        {visible.map((agent) => {
          const count = availability.get(agent.id) ?? 0;
          const members = agent.runtimeConfig.profile === "specialist_team_v1" ? agent.runtimeConfig.specialistAgentIds.flatMap((id) => { const member = agentsById.get(id); return member ? [member] : []; }) : [];
          const lastRun = agent.lastRunAt ? new Date(agent.lastRunAt).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) : "Ещё не запускался";
          return <article className="agent-directory-row" key={agent.id}>
            <div className="agent-directory-row__main">
              <div className="agent-directory-row__identity"><div className="agent-avatar"><Icon name="agents" size={20} /></div><div><div className="agent-card__title"><h2>{agent.name}</h2></div><p>{agent.role}</p></div></div>
              <div className="agent-directory-row__model"><strong>{agent.model ?? "Автовыбор"}</strong><small>{members.length ? `Команда · ${members.length} агентов` : agent.runtime === "langgraph" ? "LangGraph" : "Один агент"}</small></div>
              <div className="agent-directory-row__status"><span className={`readiness readiness--${count ? "ready" : "blocked"}`}><i />{agent.activeRuns ? "В работе" : count ? "Готов" : "Ожидает узел"}</span><small>{agent.activeRuns ? `${agent.activeRuns} активных запусков` : count ? `${count} совместимых узлов` : "Нет доступного исполнителя"}</small></div>
              <div className="agent-directory-row__activity"><strong>{agent.totalRuns}</strong><small>{lastRun}</small></div>
              <div className="agent-card__actions"><button className="button button--secondary" aria-label={`Настроить агента ${agent.name}`} onClick={() => onEdit(agent)}>Настроить</button><button className="button button--secondary agent-run-action" aria-label={`Запустить агента ${agent.name}`} title={count ? "Запустить агента" : "Добавить в очередь до появления подходящего узла"} onClick={() => onRun(agent.id)}><Icon name="play" size={16} /></button></div>
            </div>
            <details className="agent-directory-row__details"><summary>Инструкция и состав{agent.isBuiltIn ? " · встроенный агент" : ""}</summary><p>{agent.systemPrompt}</p>{members.length ? <p><strong>Участники: </strong>{members.map((member) => member.name).join(", ")}</p> : null}<small>Запусков: {agent.totalRuns} · Последний: {lastRun}</small></details>
          </article>;
        })}
      </section> : <section className="large-empty-state"><Icon name="agents" size={32} /><h2>{agents.length ? "Агенты не найдены" : "Создайте первого агента"}</h2><p>{agents.length ? "Попробуйте другой запрос или сбросьте фильтры." : "Задайте роль, инструкцию и модель или выберите готовый шаблон."}</p><button className="button button--secondary" onClick={() => { if (!agents.length) setTab("marketplace"); else { setQuery(""); setFilter("all"); } }}>{agents.length ? "Сбросить фильтры" : "Выбрать шаблон"}</button></section>}
    </TabPanel>
    <TabPanel active={tab === "marketplace"} idPrefix="agents" tabId="marketplace"><AgentMarketplace agents={agents} onInstall={onInstallTemplate} onOpenAgent={onEdit} /></TabPanel>
  </main>;
}
