import { memo } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type Edge,
  type EdgeProps,
  type XYPosition,
} from "@xyflow/react";

import type { ProcessBranch } from "../types";
import { Icon } from "./Icon";

export interface ProcessEdgeData extends Record<string, unknown> {
  branch: ProcessBranch;
  onInsert: (edgeId: string, flowPosition: XYPosition, screenPosition: XYPosition) => void;
  executed?: boolean;
  active?: boolean;
}

export type ProcessFlowEdge = Edge<ProcessEdgeData, "process">;

const branchLabels: Record<ProcessBranch, string> = {
  default: "",
  true: "да",
  false: "нет",
  repeat: "повтор",
  exit: "выход",
};

function ProcessEdgeComponent({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  selected,
  data,
}: EdgeProps<ProcessFlowEdge>) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 14,
    offset: 22,
  });
  const branch = data?.branch ?? "default";

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        className={[
          selected ? "is-selected" : "",
          data?.executed ? "is-executed" : "",
          data?.active ? "is-active" : "",
        ].filter(Boolean).join(" ") || undefined}
      />
      <EdgeLabelRenderer>
        <div
          className="process-edge-controls nodrag nopan"
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
        >
          {branchLabels[branch] ? <span>{branchLabels[branch]}</span> : null}
          <button
            type="button"
            title="Вставить шаг"
            aria-label="Вставить шаг в соединение"
            onClick={(event) => {
              event.stopPropagation();
              data?.onInsert(
                id,
                { x: labelX, y: labelY },
                { x: event.clientX + 10, y: event.clientY + 10 },
              );
            }}
          >
            <Icon name="plus" size={12} />
          </button>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

export const ProcessEdge = memo(ProcessEdgeComponent);
export const processEdgeTypes = { process: ProcessEdge };
