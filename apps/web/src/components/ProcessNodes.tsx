import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";

import type { ProcessGraphNode, ProcessNodeType, StageStatus } from "../types";
import { Icon, type IconName } from "./Icon";

export interface ProcessNodeData extends Record<string, unknown> {
  kind: ProcessNodeType;
  name: string;
  subtitle: string;
  config: ProcessGraphNode["config"];
  storedPosition: ProcessGraphNode["position"];
  onSelect: (nodeId: string) => void;
  execution?: {
    status: StageStatus | "visited";
    label: string;
    attempt: number | null;
  };
}

export type ProcessFlowNode = Node<ProcessNodeData, ProcessNodeType>;

function executionClass(data: ProcessNodeData): string {
  return data.execution ? ` has-execution process-node--status-${data.execution.status}` : "";
}

function ExecutionIndicator({ data }: { data: ProcessNodeData }) {
  if (!data.execution) return null;
  return (
    <span className={`process-node__execution process-node__execution--${data.execution.status}`} title={data.execution.label}>
      <i />{data.execution.label}
    </span>
  );
}

function StartNodeComponent({ id, data, selected }: NodeProps<ProcessFlowNode>) {
  return (
    <div className={`process-node process-node--event process-node--start${selected ? " is-selected" : ""}${executionClass(data)}`} onClick={(event) => { event.stopPropagation(); data.onSelect(id); }}>
      <span className="process-node__event-shape" />
      <strong>{data.name}</strong>
      <ExecutionIndicator data={data} />
      <Handle type="source" id="default" position={Position.Right} />
    </div>
  );
}

function EndNodeComponent({ id, data, selected }: NodeProps<ProcessFlowNode>) {
  return (
    <div className={`process-node process-node--event process-node--end${selected ? " is-selected" : ""}${executionClass(data)}`} onClick={(event) => { event.stopPropagation(); data.onSelect(id); }}>
      <Handle type="target" position={Position.Left} />
      <span className="process-node__event-shape" />
      <strong>{data.name}</strong>
      <ExecutionIndicator data={data} />
    </div>
  );
}

function AgentNodeComponent({ id, data, selected }: NodeProps<ProcessFlowNode>) {
  return (
    <div className={`process-node process-node--task${selected ? " is-selected" : ""}${executionClass(data)}`} onClick={(event) => { event.stopPropagation(); data.onSelect(id); }}>
      <Handle type="target" position={Position.Left} />
      <Icon name="agents" size={23} />
      <span>
        <strong>{data.name}</strong>
        <small>{data.subtitle}</small>
      </span>
      <ExecutionIndicator data={data} />
      <Handle type="source" id="default" position={Position.Right} />
    </div>
  );
}

const activityIcons: Partial<Record<ProcessNodeType, IconName>> = {
  http: "terminal",
  transform: "layers",
  wait: "clock",
  approval: "shield",
  artifact: "box",
  signal: "network",
  subprocess: "workflow",
};

function ActivityNodeComponent({ id, data, selected }: NodeProps<ProcessFlowNode>) {
  return (
    <div className={`process-node process-node--task process-node--${data.kind}${selected ? " is-selected" : ""}${executionClass(data)}`} onClick={(event) => { event.stopPropagation(); data.onSelect(id); }}>
      <Handle type="target" position={Position.Left} />
      <Icon name={activityIcons[data.kind] ?? "workflow"} size={23} />
      <span>
        <strong>{data.name}</strong>
        <small>{data.subtitle}</small>
      </span>
      <ExecutionIndicator data={data} />
      <Handle type="source" id="default" position={Position.Right} />
    </div>
  );
}

function ConditionNodeComponent({ id, data, selected }: NodeProps<ProcessFlowNode>) {
  return (
    <div className={`process-node process-node--condition${selected ? " is-selected" : ""}${executionClass(data)}`} onClick={(event) => { event.stopPropagation(); data.onSelect(id); }}>
      <Handle type="target" position={Position.Left} />
      <div className="process-node__diamond">
        <span>
          <strong>{data.name}</strong>
          <small>{data.subtitle}</small>
        </span>
      </div>
      <ExecutionIndicator data={data} />
      <Handle className="process-handle process-handle--true" type="source" id="true" position={Position.Bottom} />
      <Handle className="process-handle process-handle--false" type="source" id="false" position={Position.Right} />
    </div>
  );
}

function LoopNodeComponent({ id, data, selected }: NodeProps<ProcessFlowNode>) {
  return (
    <div className={`process-node process-node--loop${selected ? " is-selected" : ""}${executionClass(data)}`} onClick={(event) => { event.stopPropagation(); data.onSelect(id); }}>
      <Handle type="target" position={Position.Left} />
      <Icon name="repeat" size={25} />
      <span>
        <strong>{data.name}</strong>
        <small>{data.subtitle}</small>
      </span>
      <ExecutionIndicator data={data} />
      <Handle className="process-handle process-handle--repeat" type="source" id="repeat" position={Position.Top} />
      <Handle className="process-handle process-handle--exit" type="source" id="exit" position={Position.Right} />
    </div>
  );
}

function ParallelGatewayNodeComponent({ id, data, selected }: NodeProps<ProcessFlowNode>) {
  const fork = data.kind === "parallel_fork";
  return (
    <div className={`process-node process-node--condition process-node--${data.kind}${selected ? " is-selected" : ""}${executionClass(data)}`} onClick={(event) => { event.stopPropagation(); data.onSelect(id); }}>
      <Handle type="target" position={Position.Left} />
      <div className="process-node__diamond">
        <span>
          <strong>{data.name}</strong>
          <small>{fork ? "параллельные ветки" : "дождаться всех"}</small>
        </span>
      </div>
      <ExecutionIndicator data={data} />
      <Handle className="process-handle" type="source" id="default" position={Position.Right} />
    </div>
  );
}

export const StartProcessNode = memo(StartNodeComponent);
export const EndProcessNode = memo(EndNodeComponent);
export const AgentProcessNode = memo(AgentNodeComponent);
export const ActivityProcessNode = memo(ActivityNodeComponent);
export const ConditionProcessNode = memo(ConditionNodeComponent);
export const LoopProcessNode = memo(LoopNodeComponent);
export const ParallelGatewayProcessNode = memo(ParallelGatewayNodeComponent);

export const processNodeTypes = {
  start: StartProcessNode,
  agent: AgentProcessNode,
  http: ActivityProcessNode,
  transform: ActivityProcessNode,
  wait: ActivityProcessNode,
  approval: ActivityProcessNode,
  artifact: ActivityProcessNode,
  condition: ConditionProcessNode,
  loop: LoopProcessNode,
  parallel_fork: ParallelGatewayProcessNode,
  parallel_join: ParallelGatewayProcessNode,
  signal: ActivityProcessNode,
  subprocess: ActivityProcessNode,
  end: EndProcessNode,
};
