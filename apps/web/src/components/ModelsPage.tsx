import { useEffect, useMemo, useState, type FormEvent } from "react";

import type {
  Agent,
  ComputeNode,
  ModelRouterPolicy,
  NodeModelProfile,
} from "../types";
import { Icon } from "./Icon";
import { useRecoveryTarget, useRecoveryFocus } from "../hooks/useRecoveryTarget";

interface ModelProfileLocation {
  node: ComputeNode;
  profile: NodeModelProfile;
}

interface ModelInventory {
  name: string;
  nodes: ComputeNode[];
  onlineNodes: ComputeNode[];
  agents: Agent[];
  profiles: ModelProfileLocation[];
  capacity: number;
  maxContext: number | null;
  maxQuality: number | null;
  maxTokensPerSecond: number | null;
  minJoulesPer1kTokens: number | null;
  capabilities: string[];
}

interface ModelsPageProps {
  models: string[];
  nodes: ComputeNode[];
  agents: Agent[];
  modelRouter: {
    policy: ModelRouterPolicy;
    profiledModels: number;
    benchmarkedModels: number;
    lastBenchmarkAt: string | null;
  };
  busy: boolean;
  onSavePolicy: (policy: ModelRouterPolicy) => Promise<void>;
}

const strategyCopy = {
  balanced: "Минимальная модель после SLA-фильтров",
  performance: "Максимальный наблюдаемый throughput",
  efficiency: "Минимальная энергия и footprint",
} as const;

function maxKnown(values: Array<number | undefined>): number | null {
  const known = values.filter((value): value is number => value !== undefined);
  return known.length ? Math.max(...known) : null;
}

function minKnown(values: Array<number | null | undefined>): number | null {
  const known = values.filter((value): value is number => value !== null && value !== undefined);
  return known.length ? Math.min(...known) : null;
}

function compactNumber(value: number | null, suffix = ""): string {
  if (value === null) return "—";
  return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1, notation: "compact" }).format(value)}${suffix}`;
}

function ModelRouterPolicyCard({
  value,
  stats,
  busy,
  onSave,
}: {
  value: ModelRouterPolicy;
  stats: Omit<ModelsPageProps["modelRouter"], "policy">;
  busy: boolean;
  onSave: (policy: ModelRouterPolicy) => Promise<void>;
}) {
  const [draft, setDraft] = useState(value);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!dirty) setDraft(value);
  }, [dirty, value]);

  const update = <Key extends keyof ModelRouterPolicy>(key: Key, next: ModelRouterPolicy[Key]) => {
    setDraft((current) => ({ ...current, [key]: next }));
    setDirty(true);
  };

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await onSave(draft);
      setDirty(false);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось сохранить policy");
    }
  }

  return (
    <form className="model-router-card" onSubmit={(event) => void submit(event)}>
      <div className="model-router-card__head">
        <span className="model-router-card__icon"><Icon name="layers" size={20} /></span>
        <div>
          <h2>Model Router</h2>
          <p>{strategyCopy[draft.strategy]}. Решение и сигналы сохраняются в trace каждого stage.</p>
        </div>
        <label className="router-switch">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) => update("enabled", event.target.checked)}
          />
          <span>{draft.enabled ? "Активен" : "Выключен"}</span>
        </label>
      </div>

      <div className="model-router-fields">
        <label>
          <span>Стратегия</span>
          <select value={draft.strategy} onChange={(event) => update("strategy", event.target.value as ModelRouterPolicy["strategy"])}>
            <option value="balanced">Balanced</option>
            <option value="performance">Performance</option>
            <option value="efficiency">Efficiency</option>
          </select>
        </label>
        <label>
          <span>Мин. контекст</span>
          <input type="number" min="0" max="2000000" step="1024" value={draft.minContextTokens} onChange={(event) => update("minContextTokens", Number(event.target.value))} />
        </label>
        <label>
          <span>Мин. quality, 0–100</span>
          <input type="number" min="0" max="100" value={draft.minQualityScore} onChange={(event) => update("minQualityScore", Number(event.target.value))} />
        </label>
        <label>
          <span>Батарея не ниже, %</span>
          <input type="number" min="0" max="100" value={draft.minBatteryPercent} onChange={(event) => update("minBatteryPercent", Number(event.target.value))} />
        </label>
        <label>
          <span>Температура не выше, °C</span>
          <input type="number" min="0" max="150" value={draft.maxTemperatureC} onChange={(event) => update("maxTemperatureC", Number(event.target.value))} />
        </label>
        <label className="router-unknown-policy">
          <input type="checkbox" checked={draft.allowUnknownProfiles} onChange={(event) => update("allowUnknownProfiles", event.target.checked)} />
          <span>Разрешать legacy workers с неполным профилем</span>
        </label>
      </div>

      <footer>
        <div>
          <span>{stats.profiledModels} профилей</span>
          <span>{stats.benchmarkedModels} с benchmark</span>
          <span>{stats.lastBenchmarkAt ? `последний ${new Date(stats.lastBenchmarkAt).toLocaleString("ru-RU")}` : "наблюдений пока нет"}</span>
        </div>
        {error ? <p>{error}</p> : null}
        <button className="button button--primary" type="submit" disabled={busy || !dirty}>
          {busy ? "Сохраняем…" : "Сохранить policy"}
        </button>
      </footer>
    </form>
  );
}

export function ModelsPage({ models, nodes, agents, modelRouter, busy, onSavePolicy }: ModelsPageProps) {
  const recovery = useRecoveryTarget();
  useRecoveryFocus(recovery.get("model") ? `model-${recovery.get("model")}` : recovery.get("section") === "policy" ? "model-router-policy" : null, nodes);
  const recoveryNode = nodes.find((node) => node.id === recovery.get("nodeId"));
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
        const profiles = onlineNodes.flatMap((node) => node.modelProfiles
          .filter((profile) => profile.name === name)
          .map((profile) => ({ node, profile })));
        return {
          name,
          nodes: modelNodes,
          onlineNodes,
          agents: agents.filter((agent) => agent.model === name),
          profiles,
          capacity: onlineNodes.reduce((sum, node) => sum + Math.max(0, node.maxConcurrency - node.usedConcurrency), 0),
          maxContext: maxKnown(profiles.map(({ profile }) => profile.contextWindow)),
          maxQuality: maxKnown(profiles.map(({ profile }) => profile.qualityScore)),
          maxTokensPerSecond: maxKnown(profiles.map(({ profile }) => profile.benchmark?.tokensPerSecond)),
          minJoulesPer1kTokens: minKnown(profiles.map(({ profile }) => profile.benchmark?.joulesPer1kTokens)),
          capabilities: [...new Set(profiles.flatMap(({ profile }) => profile.capabilities ?? []))].sort(),
        };
      });
  }, [agents, models, nodes]);
  const autoAgents = agents.filter((agent) => agent.model === null);

  return (
    <main className="main-column section-page" id="models">
      <div className="page-title page-title--section">
        <div><h1>Локальные модели</h1><p>Hardware profiles, пассивные benchmarks и explainable routing по реальным workers</p></div>
      </div>

      <div className="live-source live-source--models">
        <span><i />ROUTER</span>
        <p>Worker capabilities обновляются heartbeat, throughput — только после реальных model calls</p>
        <strong>{inventory.filter((model) => model.onlineNodes.length > 0).length} online</strong>
      </div>

      {recoveryNode && recovery.get("model") ? <p>Для сценария добавьте модель <strong>{recovery.get("model")}</strong> на worker <strong>{recoveryNode.name}</strong>, сохранив остальные модели команды.</p> : null}
      <section id="model-router-policy" tabIndex={-1}><ModelRouterPolicyCard
        value={modelRouter.policy}
        stats={modelRouter}
        busy={busy}
        onSave={onSavePolicy}
      /></section>

      {inventory.length === 0 ? (
        <section className="large-empty-state">
          <Icon name="models" size={31} />
          <h2>Модели пока не обнаружены</h2>
          <p>Подключите worker — router получит hardware/model profile и начнёт накапливать benchmark по реальным запускам.</p>
        </section>
      ) : (
        <section className="model-grid" aria-label="Инвентарь моделей">
          {inventory.map((model) => {
            const available = model.onlineNodes.length > 0;
            return (
              <article id={`model-${model.name}`} tabIndex={-1} className={`model-card${available ? "" : " model-card--unavailable"}`} key={model.name}>
                <header>
                  <div className="model-icon"><Icon name="models" size={21} /></div>
                  <div><h2 className="mono">{model.name}</h2><p>{available ? "Участвует в policy routing" : "Нет online-узла с этой моделью"}</p></div>
                  <span className={`readiness readiness--${available ? "ready" : "blocked"}`}><i />{available ? "Online" : "Недоступна"}</span>
                </header>
                <dl className="model-card__metrics">
                  <div><dt>Throughput</dt><dd>{compactNumber(model.maxTokensPerSecond, " ток/с")}</dd></div>
                  <div><dt>Контекст</dt><dd>{compactNumber(model.maxContext)}</dd></div>
                  <div><dt>Quality</dt><dd>{model.maxQuality ?? "—"}</dd></div>
                  <div><dt>Энергия</dt><dd>{compactNumber(model.minJoulesPer1kTokens, " J/1k")}</dd></div>
                </dl>
                <div className="model-capabilities">
                  {model.capabilities.length
                    ? model.capabilities.map((capability) => <code key={capability}>{capability}</code>)
                    : <em>capabilities неизвестны</em>}
                </div>
                <div className="model-card__links">
                  <span>Узлы</span>
                  <div className="model-locations">
                    {model.profiles.length ? model.profiles.map(({ node, profile }) => (
                      <p key={node.id}>
                        <strong>{node.name}</strong>
                        <small>
                          RAM {compactNumber(node.memoryMb, " MiB")} · VRAM {node.vramMb ? compactNumber(node.vramMb, " MiB") : "—"}
                          {profile.parameterSize ? ` · ${profile.parameterSize}` : ""}
                          {profile.quantization ? ` · ${profile.quantization}` : ""}
                          {profile.benchmark ? ` · ${profile.benchmark.samples} sample(s)` : " · без benchmark"}
                        </small>
                      </p>
                    )) : <p>{model.nodes.length ? model.nodes.map((node) => node.name).join(", ") : "Worker не объявляет модель"}</p>}
                  </div>
                </div>
                <div className="model-card__links">
                  <span>Агенты</span>
                  <p>{model.agents.length ? model.agents.map((agent) => agent.name).join(", ") : "Не закреплена — доступна для автовыбора"}</p>
                </div>
                <footer className="model-card__footer">
                  <span>{model.onlineNodes.length}/{model.nodes.length} узлов online</span>
                  <span>{model.capacity} свободных слотов</span>
                </footer>
              </article>
            );
          })}
        </section>
      )}

      <section className="auto-model-band">
        <Icon name="layers" size={22} />
        <div><h2>Автовыбор модели</h2><p>{autoAgents.length} агентов проходят SLA-фильтры и затем ранжируются по стратегии {modelRouter.policy.strategy}.</p></div>
        <div>{autoAgents.slice(0, 8).map((agent) => <span key={agent.id}>{agent.name}</span>)}</div>
      </section>
    </main>
  );
}
