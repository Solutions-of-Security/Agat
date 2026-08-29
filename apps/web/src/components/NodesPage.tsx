import { useState } from "react";

import type { ComputeNode } from "../types";
import { Icon } from "./Icon";
import { LocalWorkerLauncherPanel } from "./LocalWorkerLauncherPanel";

const connectCommand = `AGAT_COORDINATOR_URL=http://127.0.0.1:8787 \\
AGAT_ENROLLMENT_TOKEN=PASTE_ENROLLMENT_TOKEN_HERE \\
AGAT_WORKER_MODELS=llama3.2:latest \\
AGAT_EMBEDDING_MODELS=embeddinggemma \\
python3 workers/agat_worker.py`;

const statusCopy = {
  online: "Онлайн",
  sleeping: "Нет свежего heartbeat",
  offline: "Офлайн",
};

function relativeTime(value: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1_000));
  if (seconds < 60) return `${seconds} сек назад`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} мин назад`;
  return `${Math.floor(minutes / 60)} ч назад`;
}

function memory(value: number): string {
  if (!value) return "не указана";
  return value >= 1024 ? `${Math.round(value / 1024)} ГБ` : `${value} МБ`;
}

function Meter({ label, value }: { label: string; value: number }) {
  const normalized = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  return (
    <div className="fleet-meter">
      <span><b>{label}</b><em>{Math.round(normalized)}%</em></span>
      <div><i style={{ width: `${normalized}%` }} /></div>
    </div>
  );
}

export function NodesPage({ nodes, models }: { nodes: ComputeNode[]; models: string[] }) {
  const [copied, setCopied] = useState(false);

  async function copyCommand() {
    try {
      await navigator.clipboard.writeText(connectCommand);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_800);
    } catch {
      setCopied(false);
    }
  }

  return (
    <main className="main-column section-page" id="nodes">
      <div className="page-title page-title--section">
        <div><h1>Вычислительные узлы</h1><p>Heartbeat, загрузка, модели, инструменты и фактическая доступность каждой машины</p></div>
        <button className="button button--secondary page-title__action page-title__action--always" type="button" onClick={() => document.getElementById("connect-node")?.scrollIntoView()}>
          <Icon name="plus" size={18} />Подключить узел
        </button>
      </div>

      <section className="fleet-summary" aria-label="Состояние узлов">
        <div><strong>{nodes.filter((node) => node.status === "online").length}</strong><span>онлайн</span></div>
        <div><strong>{nodes.filter((node) => node.status === "sleeping").length}</strong><span>без свежего heartbeat</span></div>
        <div><strong>{nodes.filter((node) => node.status === "offline").length}</strong><span>офлайн</span></div>
        <div><strong>{nodes.reduce((sum, node) => sum + node.maxConcurrency, 0)}</strong><span>общий лимит этапов</span></div>
      </section>

      <LocalWorkerLauncherPanel nodes={nodes} knownModels={models} />

      {nodes.length === 0 ? (
        <section className="large-empty-state large-empty-state--compact">
          <Icon name="nodes" size={31} />
          <h2>Workers ещё не подключены</h2>
          <p>Coordinator уже готов. Запустите переносимый worker на машине с локальной моделью.</p>
        </section>
      ) : (
        <section className="fleet-grid" aria-label="Список вычислительных узлов">
          {nodes.map((node) => (
            <article className={`fleet-card fleet-card--${node.status}`} key={node.id}>
              <div className="fleet-card__head">
                <div>
                  <span className={`status-dot status-dot--${node.status}`} />
                  <h2>{node.name}</h2>
                  <em>{statusCopy[node.status]}</em>
                </div>
                <span className="mono">{node.usedConcurrency} / {node.maxConcurrency}</span>
              </div>
              <div className="fleet-card__identity">
                <p>{node.platform}</p>
                <span>{node.architecture || "архитектура не указана"}</span>
                <span>{node.cpuCores} CPU · {memory(node.memoryMb)}</span>
                <span>{node.gpu || "GPU не указан"}</span>
              </div>
              <div className="fleet-card__meters">
                <Meter label="CPU" value={node.metrics.cpuPercent ?? 0} />
                <Meter label="RAM" value={node.metrics.memoryPercent ?? 0} />
                <Meter label="GPU" value={node.metrics.gpuPercent ?? 0} />
              </div>
              <div className="fleet-card__models">
                <span>Модели worker</span>
                <div>{node.models.length ? node.models.map((model) => <code key={model}>{model}</code>) : <em>любая / не указана</em>}</div>
              </div>
              <div className="fleet-card__models">
                <span>Embedding models</span>
                <div>{node.embeddingModels.length ? node.embeddingModels.map((model) => <code key={model}>{model}</code>) : <em>индексация отключена</em>}</div>
              </div>
              <div className="fleet-card__tools">
                <span>Инструменты worker</span>
                <div>
                  {node.labels.tools
                    ? node.labels.tools.split("+").filter(Boolean).map((tool) => <code key={tool}>{tool}</code>)
                    : <em>не подключены</em>}
                </div>
              </div>
              <div className="fleet-card__tools">
                <span>Agent runtimes</span>
                <div>
                  {node.agentRuntimes.map((runtime) => <code key={runtime}>{runtime}</code>)}
                </div>
              </div>
              <div className="fleet-card__tools">
                <span>LangGraph profiles</span>
                <div>
                  {node.agentRuntimeProfiles.length
                    ? node.agentRuntimeProfiles.map((profile) => <code key={profile}>{profile}</code>)
                    : <em>не поддерживаются</em>}
                </div>
              </div>
              <footer><span>Heartbeat</span><time className="mono">{relativeTime(node.lastSeen)}</time></footer>
            </article>
          ))}
        </section>
      )}

      <section className="connect-node" id="connect-node">
        <div>
          <span className="eyebrow">OUTBOUND-ONLY WORKER</span>
          <h2>Подключить ещё одну машину</h2>
          <p>Скопируйте worker, укажите адрес coordinator и список реально доступных моделей. Для Kubernetes-контура получите регистрационный токен на машине coordinator командой <code>npm run --silent k8s:enrollment-token</code> и вставьте вместо <code>PASTE_ENROLLMENT_TOKEN_HERE</code>. Это не admin token. Входящий порт на worker не требуется.</p>
        </div>
        <pre><code>{connectCommand}</code></pre>
        <button className="button button--secondary" type="button" onClick={() => void copyCommand()}>
          <Icon name={copied ? "check" : "terminal"} size={16} />{copied ? "Скопировано" : "Скопировать команду"}
        </button>
      </section>
    </main>
  );
}
