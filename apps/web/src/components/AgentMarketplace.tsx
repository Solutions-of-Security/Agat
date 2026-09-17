import { useDeferredValue, useMemo, useState } from "react";

import {
  AGENT_MARKETPLACE_TEMPLATES,
  normalizeAgentTemplateName,
  type AgentMarketplaceTemplate,
  type AgentTemplatePriority,
} from "../agentMarketplace";
import type { Agent } from "../types";
import { Icon } from "./Icon";

type PriorityFilter = "all" | AgentTemplatePriority;

const PRIORITY_FILTERS: readonly { id: PriorityFilter; label: string }[] = [
  { id: "all", label: "Все" },
  { id: "P0", label: "P0 · сейчас" },
  { id: "P1", label: "P1 · 3–6 мес." },
  { id: "P2", label: "P2 · 6–12 мес." },
];

interface AgentMarketplaceProps {
  agents: Agent[];
  onInstall: (template: AgentMarketplaceTemplate) => void;
  onOpenAgent: (agent: Agent) => void;
}

function runtimeLabel(template: AgentMarketplaceTemplate): string {
  if (template.request.runtimeConfig.profile === "specialist_team_v1") return "Specialist team";
  return template.request.runtime === "langgraph" ? "LangGraph" : "Single";
}

export function AgentMarketplace({ agents, onInstall, onOpenAgent }: AgentMarketplaceProps) {
  const [query, setQuery] = useState("");
  const [priority, setPriority] = useState<PriorityFilter>("all");
  const deferredQuery = useDeferredValue(query.trim().toLocaleLowerCase("ru"));
  const agentsByName = useMemo(
    () => new Map(agents.map((agent) => [normalizeAgentTemplateName(agent.name), agent])),
    [agents],
  );
  const installedCount = useMemo(
    () => AGENT_MARKETPLACE_TEMPLATES.filter((template) => agentsByName.has(normalizeAgentTemplateName(template.name))).length,
    [agentsByName],
  );
  const visibleTemplates = useMemo(() => AGENT_MARKETPLACE_TEMPLATES.filter((template) => {
    if (priority !== "all" && template.priority !== priority) return false;
    if (!deferredQuery) return true;
    return [
      template.name,
      template.summary,
      template.category,
      template.outcome,
      template.recommendedProcess,
      ...template.capabilities,
      ...template.requirements,
    ].join(" ").toLocaleLowerCase("ru").includes(deferredQuery);
  }), [deferredQuery, priority]);

  return (
    <div className="agent-marketplace">
      <section className="section-toolbar marketplace-summary" aria-label="Сводка маркетплейса агентов">
        <div className="toolbar-stat"><strong>{AGENT_MARKETPLACE_TEMPLATES.length}</strong><span>готовых шаблонов</span></div>
        <div className="toolbar-stat"><strong>6</strong><span>P0 для первого запуска</span></div>
        <div className="toolbar-stat"><strong>{installedCount}</strong><span>уже установлено</span></div>
        <label className="search-field">
          <Icon name="search" size={17} />
          <span className="sr-only">Найти шаблон агента</span>
          <input value={query} placeholder="Найти шаблон или задачу" onChange={(event) => setQuery(event.target.value)} />
        </label>
      </section>

      <section className="marketplace-intro" aria-labelledby="marketplace-title">
        <div>
          <span className="marketplace-eyebrow">Agat curated · v1</span>
          <h2 id="marketplace-title">Готовые агенты для контролируемых процессов</h2>
          <p>Выберите роль, проверьте системный промпт и модель, затем установите отдельную копию в текущий проект.</p>
        </div>
        <div className="marketplace-priority-filters" role="group" aria-label="Фильтр по приоритету">
          {PRIORITY_FILTERS.map((filter) => (
            <button
              className={priority === filter.id ? "is-active" : ""}
              type="button"
              aria-pressed={priority === filter.id}
              onClick={() => setPriority(filter.id)}
              key={filter.id}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </section>

      <p className="marketplace-result-count" role="status">
        Показано {visibleTemplates.length} из {AGENT_MARKETPLACE_TEMPLATES.length}
      </p>

      {visibleTemplates.length === 0 ? (
        <section className="large-empty-state large-empty-state--compact">
          <Icon name="search" size={30} />
          <h2>Шаблоны не найдены</h2>
          <p>Измените запрос или выберите другой горизонт.</p>
          <button className="button button--secondary" type="button" onClick={() => { setQuery(""); setPriority("all"); }}>
            Сбросить фильтры
          </button>
        </section>
      ) : (
        <section className="marketplace-grid" aria-label="Шаблоны агентов">
          {visibleTemplates.map((template) => {
            const installedAgent = agentsByName.get(normalizeAgentTemplateName(template.name)) ?? null;
            const teamTemplate = template.request.runtimeConfig.profile === "specialist_team_v1";
            return (
              <article className={`marketplace-card marketplace-card--${template.priority.toLocaleLowerCase("en")}`} key={template.id}>
                <header className="marketplace-card__header">
                  <div className="marketplace-card__icon"><Icon name={teamTemplate ? "network" : "agents"} size={21} /></div>
                  <div>
                    <div className="marketplace-card__badges">
                      <span className={`marketplace-priority marketplace-priority--${template.priority.toLocaleLowerCase("en")}`}>{template.priority}</span>
                      <span>{template.category}</span>
                      <span>v{template.version}</span>
                    </div>
                    <h3>{template.name}</h3>
                  </div>
                  {installedAgent ? <span className="marketplace-installed"><Icon name="check" size={13} />Установлен</span> : null}
                </header>

                <p className="marketplace-card__summary">{template.summary}</p>

                <div className="marketplace-card__outcome">
                  <span>Результат</span>
                  <p>{template.outcome}</p>
                </div>

                <div className="marketplace-card__capabilities" aria-label="Возможности">
                  {template.capabilities.map((capability) => <span key={capability}>{capability}</span>)}
                </div>

                <dl className="marketplace-card__details">
                  <div><dt>Горизонт</dt><dd>{template.horizon}</dd></div>
                  <div><dt>Runtime</dt><dd>{runtimeLabel(template)}</dd></div>
                  <div><dt>Процесс</dt><dd>{template.recommendedProcess}</dd></div>
                  <div><dt>Нужно</dt><dd>{template.requirements.join(" · ")}</dd></div>
                </dl>

                <footer>
                  <small>Шаблон <code>{template.id}</code> · системный промпт можно проверить до установки</small>
                  {installedAgent ? (
                    <button className="button button--secondary" type="button" onClick={() => onOpenAgent(installedAgent)}>
                      Открыть агента
                    </button>
                  ) : (
                    <button className="button button--primary" type="button" onClick={() => onInstall(template)}>
                      <Icon name="plus" size={16} />{teamTemplate ? "Настроить команду" : "Настроить и установить"}
                    </button>
                  )}
                </footer>
              </article>
            );
          })}
        </section>
      )}
    </div>
  );
}
