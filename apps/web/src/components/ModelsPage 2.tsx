import { useMemo } from "react";

import type { Agent, ComputeNode } from "../types";
import { Icon } from "./Icon";

interface ModelInventory {
  name: string;
  nodes: ComputeNode[];
  onlineNodes: ComputeNode[];
  agents: Agent[];
  capacity: number;
}

export function ModelsPage({ models, nodes, agents }: { models: string[]; nodes: ComputeNode[]; agents: Agent[] }) {
  const inventory = useMemo<ModelInventory[]>(() => {
    const names = new Set(models);
    agents.forEach((agent) => {
      if (agent.model) names.add(agent.model);
    });
    return [...names]
      .sort((left, right) => left.localeCompare(right))
      .map((name) => {
        const modelNodes = nodes.filter((node) => node.models.includes(name));
        const onlineNodes = modelNodes.filter((node) => node.status === "online");
        return {
          name,
          nodes: modelNodes,
          onlineNodes,
          agents: agents.filter((agent) => agent.model === name),
          capacity: onlineNodes.reduce((sum, node) => sum + Math.max(0, node.maxConcurrency - node.usedConcurrency), 0),
        };
      });
  }, [agents, models, nodes]);
  const autoAgents = agents.filter((agent) => agent.model === null);

  return (
    <main className="main-column section-page" id="models">
      <div className="page-title page-title--section">
        <div><h1>Локальные модели</h1><p>Инвентарь строится из объявлений реальных workers и настроек агентов</p></div>
      </div>

      <div className="live-source live-source--models">
        <span><i />DISCOVERY</span>
        <p>Модель считается доступной только при свежем heartbeat совместимого узла</p>
        <strong>{inventory.filter((model) => model.onlineNodes.length > 0).length} online</strong>
      </div>

      {inventory.length === 0 ? (
        <section className="large-empty-state">
          <Icon name="models" size={31} />
          <h2>Модели пока не обнаружены</h2>
          <p>Подключите worker или укажите модель в настройках агента — здесь появится её фактическая доступность.</p>
        </section>
      ) : (
        <section className="model-grid" aria-label="Инвентарь моделей">
          {inventory.map((model) => {
            const available = model.onlineNodes.length > 0;
            return (
              <article className={`model-card${available ? "" : " model-card--unavailable"}`} key={model.name}>
                <header>
                  <div className="model-icon"><Icon name="models" size={21} /></div>
                  <div><h2 className="mono">{model.name}</h2><p>{available ? "Готова принимать этапы" : "Нет online-узла с этой моделью"}</p></div>
                  <span className={`readiness readiness--${available ? "ready" : "blocked"}`}><i />{available ? "Online" : "Недоступна"}</span>
                </header>
                <dl>
                  <div><dt>Узлы online</dt><dd>{model.onlineNodes.length} / {model.nodes.length}</dd></div>
                  <div><dt>Свободных слотов</dt><dd>{model.capacity}</dd></div>
                  <div><dt>Закреплено агентов</dt><dd>{model.agents.length}</dd></div>
                </dl>
                <div className="model-card__links">
                  <span>Узлы</span>
                  <p>{model.nodes.length ? model.nodes.map((node) => node.name).join(", ") : "Модель указана агентом, но worker её не объявляет"}</p>
                </div>
                <div className="model-card__links">
                  <span>Агенты</span>
                  <p>{model.agents.length ? model.agents.map((agent) => agent.name).join(", ") : "Не закреплена — доступна для автовыбора"}</p>
                </div>
              </article>
            );
          })}
        </section>
      )}

      <section className="auto-model-band">
        <Icon name="layers" size={22} />
        <div><h2>Автовыбор модели</h2><p>{autoAgents.length} агентов не закреплены за конкретной моделью и могут выполняться на любом online-worker.</p></div>
        <div>{autoAgents.slice(0, 8).map((agent) => <span key={agent.id}>{agent.name}</span>)}</div>
      </section>
    </main>
  );
}
