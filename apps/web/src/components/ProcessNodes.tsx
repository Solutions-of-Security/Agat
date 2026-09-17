import { memo, useEffect } from "react";
import { Handle, Position, useUpdateNodeInternals, type Node, type NodeProps } from "@xyflow/react";
import type { ProcessGraphNode, ProcessNodeType, StageStatus } from "../types";
import { Icon, type IconName } from "./Icon";

export interface ProcessNodeData extends Record<string, unknown> {
  kind: ProcessNodeType;
  mobile?: boolean;
  name: string;
  subtitle: string;
  config: ProcessGraphNode["config"];
  storedPosition: ProcessGraphNode["position"];
  onSelect: (nodeId: string) => void;
  execution?: { status: StageStatus | "visited"; label: string; attempt: number | null };
}
export type ProcessFlowNode = Node<ProcessNodeData, ProcessNodeType>;
const nodePresentation: Record<ProcessNodeType, { icon: IconName; label: string }> = {
  start: { icon: "play", label: "Входные данные" },
  end: { icon: "check", label: "Завершение" },
  agent: { icon: "agents", label: "ИИ-агент" },
  http: { icon: "plug", label: "HTTP-запрос" },
  transform: { icon: "layers", label: "Преобразование" },
  wait: { icon: "clock", label: "Ожидание" },
  approval: { icon: "shield", label: "Согласование" },
  artifact: { icon: "box", label: "Сохранение результата" },
  condition: { icon: "diamond", label: "Условие" },
  loop: { icon: "repeat", label: "Цикл" },
  parallel_fork: { icon: "network", label: "Параллельные ветки" },
  parallel_join: { icon: "layers", label: "Объединение веток" },
  signal: { icon: "bell", label: "Внешнее событие" },
  subprocess: { icon: "workflow", label: "Вложенный процесс" },
};

function ProcessNodeComponent({ id, data, selected }: NodeProps<ProcessFlowNode>) {
  const presentation = nodePresentation[data.kind];
  const updateNodeInternals = useUpdateNodeInternals();
  useEffect(() => { updateNodeInternals(id); }, [id, data.mobile, updateNodeInternals]);
  return (
    <div className={`process-node process-node--tile process-node--${data.kind}${selected ? " is-selected" : ""}${data.execution ? ` has-execution process-node--status-${data.execution.status}` : ""}`}
      role="button" tabIndex={0} aria-label={`${presentation.label}: ${data.name}`}
      onClick={(event) => { event.stopPropagation(); data.onSelect(id); }}
      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); event.stopPropagation(); data.onSelect(id); } }}>
      {data.kind !== "start" ? <Handle type="target" position={data.mobile ? Position.Top : Position.Left} /> : null}
      <div className="process-node__heading"><span className="process-node__icon"><Icon name={presentation.icon} size={21} /></span><strong>{data.name}</strong></div>
      <small title={data.subtitle || presentation.label}>{data.subtitle || presentation.label}</small>
      {data.execution ? <span className={`process-node__execution process-node__execution--${data.execution.status}`}><i />{data.execution.label}</span> : null}
      {data.kind === "condition" ? <>
        <Handle className="process-handle--true" type="source" id="true" position={Position.Bottom} aria-label="Ветка да" />
        <Handle className="process-handle--false" type="source" id="false" position={Position.Right} aria-label="Ветка нет" />
      </> : data.kind === "loop" ? <>
        <Handle className="process-handle--repeat" type="source" id="repeat" position={data.mobile ? Position.Left : Position.Top} aria-label="Повтор" />
        <Handle className="process-handle--exit" type="source" id="exit" position={data.mobile ? Position.Bottom : Position.Right} aria-label="Выход из цикла" />
      </> : data.kind !== "end" ? <Handle type="source" id="default" position={data.mobile ? Position.Bottom : Position.Right} /> : null}
    </div>
  );
}
const ProcessNode = memo(ProcessNodeComponent);
export const processNodeTypes = Object.fromEntries(Object.keys(nodePresentation).map((kind) => [kind, ProcessNode])) as Record<ProcessNodeType, typeof ProcessNode>;
