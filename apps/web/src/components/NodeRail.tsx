import type { ComputeNode } from "../types";
import { Icon } from "./Icon";

const statusCopy = {
  online: "Онлайн",
  sleeping: "Режим сна",
  offline: "Офлайн",
};

function Usage({ label, value }: { label: string; value: number }) {
  const normalized = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  return (
    <div className="usage">
      <span>{label}</span>
      <div className="usage__track"><i style={{ width: `${normalized}%` }} /></div>
    </div>
  );
}

export function NodeRail({ nodes, onManage }: { nodes: ComputeNode[]; onManage: () => void }) {
  return (
    <aside className="node-rail" id="nodes" aria-labelledby="nodes-title">
      <div className="node-rail__header">
        <h2 id="nodes-title">Узлы</h2>
        <span>{nodes.length} узла</span>
      </div>
      <div className="node-list">
        {nodes.length === 0 ? (
          <div className="node-list__empty"><Icon name="nodes" size={25} /><strong>Нет workers</strong><p>Подключите машину с локальной моделью</p></div>
        ) : nodes.map((node) => (
          <article className={`node node--${node.status}`} key={node.id}>
            <div className="node__head">
              <strong><span className={`status-dot status-dot--${node.status}`} />{node.name}</strong>
              <span>{statusCopy[node.status]}</span>
            </div>
            <p>{node.platform}</p>
            <p className="mono node__model">{node.models[0] ?? "Модель не указана"}</p>
            <div className="node__telemetry">
              <Usage label="CPU" value={node.metrics.cpuPercent ?? 0} />
              <Usage label="GPU" value={node.metrics.gpuPercent ?? 0} />
              <Usage label="RAM" value={node.metrics.memoryPercent ?? 0} />
              <div className="node__capacity"><span>Параллельность</span><strong>{node.usedConcurrency} / {node.maxConcurrency}</strong></div>
            </div>
          </article>
        ))}
      </div>
      <button className="node-rail__manage" type="button" onClick={onManage}><Icon name="nodes" size={17} />Управление узлами<Icon name="chevron" size={16} /></button>
    </aside>
  );
}
