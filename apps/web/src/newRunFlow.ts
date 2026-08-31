import type { Agent, CreateRunRequest, ResultDestination, SchedulerMode } from "./types";

export type RunPriorityPreset = "normal" | "high" | "urgent";

export const RUN_PRIORITY_VALUES: Record<RunPriorityPreset, number> = {
  normal: 50,
  high: 75,
  urgent: 100,
};

export interface RunDraft {
  name: string;
  input: string;
  executionMode: SchedulerMode;
  priority: number;
  approvalRequired: boolean;
  agentIds: string[];
  resultDestination: ResultDestination;
  artifactPath: string;
  knowledgeCollectionIds: string[];
}

export function initialRunAgentIds(
  agents: Array<Pick<Agent, "id" | "isBuiltIn">>,
  requestedIds?: string[] | null,
): string[] {
  const available = new Set(agents.map((agent) => agent.id));
  const requested = [...new Set(requestedIds ?? [])].filter((id) => available.has(id));
  if (requested.length > 0) return requested;

  const firstAgent = agents.find((agent) => agent.isBuiltIn) ?? agents[0];
  return firstAgent ? [firstAgent.id] : [];
}

export function moveRunAgent(agentIds: string[], agentId: string, direction: -1 | 1): string[] {
  const currentIndex = agentIds.indexOf(agentId);
  const targetIndex = currentIndex + direction;
  if (currentIndex < 0 || targetIndex < 0 || targetIndex >= agentIds.length) return agentIds;

  const reordered = [...agentIds];
  [reordered[currentIndex], reordered[targetIndex]] = [reordered[targetIndex], reordered[currentIndex]];
  return reordered;
}

export function priorityPresetForValue(priority: number): RunPriorityPreset {
  if (priority <= 60) return "normal";
  if (priority <= 85) return "high";
  return "urgent";
}

export function normalizeRunPriority(priority: number): number {
  if (!Number.isFinite(priority)) return RUN_PRIORITY_VALUES.normal;
  return Math.min(100, Math.max(0, Math.round(priority)));
}

export function buildCreateRunRequest(draft: RunDraft): CreateRunRequest {
  return {
    name: draft.name.trim(),
    input: draft.input.trim(),
    executionMode: draft.executionMode,
    priority: normalizeRunPriority(draft.priority),
    approvalRequired: draft.approvalRequired,
    agentIds: [...draft.agentIds],
    resultDestination: draft.resultDestination,
    artifactPath: draft.resultDestination === "artifacts" ? draft.artifactPath.trim() : "",
    knowledgeCollectionIds: [...draft.knowledgeCollectionIds],
  };
}
