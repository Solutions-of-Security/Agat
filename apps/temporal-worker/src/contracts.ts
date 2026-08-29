export type DurableProcessStatus =
  | "queued"
  | "running"
  | "waiting_approval"
  | "waiting_external"
  | "compensating"
  | "completed"
  | "failed"
  | "cancelled";

export interface ProcessWorkflowInput {
  instanceId: string;
  processId: string;
  projectId: string;
}

export interface ScheduledProcessWorkflowInput {
  processId: string;
  projectId: string;
  input: string;
  priority: number;
  knowledgeCollectionIds: string[];
}

export interface DurableProcessState {
  instanceId: string;
  status: DurableProcessStatus;
  currentNodeId: string | null;
  currentNodeType: string | null;
  waitUntil: string | null;
  transitionCount: number;
  activeNodeIds?: string[];
  pendingSignalNames?: string[];
  updatedAt: string;
}

export interface ProcessWakeAck {
  acceptedRevision: number;
  reason: string;
  state: DurableProcessState | null;
}
