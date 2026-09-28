import { createHash } from "node:crypto";
import { activityInfo, ApplicationFailure, log } from "@temporalio/activity";

import type {
  DurableProcessState,
  ProcessWorkflowInput,
  ScheduledProcessWorkflowInput,
} from "./contracts.js";

const coordinatorUrl = (process.env.AGAT_COORDINATOR_INTERNAL_URL ?? "http://agat-coordinator:8787").replace(/\/+$/, "");
const internalToken = process.env.AGAT_TEMPORAL_INTERNAL_TOKEN ?? "";

function assertConfiguration(): void {
  if (!internalToken) throw ApplicationFailure.nonRetryable("AGAT_TEMPORAL_INTERNAL_TOKEN не задан", "ConfigurationError");
}

export async function tickProcess(input: ProcessWorkflowInput): Promise<DurableProcessState> {
  assertConfiguration();
  let response: Response;
  try {
    response = await fetch(
      `${coordinatorUrl}/api/v1/internal/processes/${encodeURIComponent(input.instanceId)}/tick`,
      {
        method: "POST",
        headers: {
          accept: "application/json",
          "content-type": "application/json",
          "x-agat-temporal-token": internalToken,
        },
        body: JSON.stringify({ projectId: input.projectId }),
        signal: AbortSignal.timeout(10_000),
      },
    );
  } catch (error) {
    log.warn("Coordinator временно недоступен", { instanceId: input.instanceId });
    throw error;
  }

  const body = await response.json().catch(() => ({})) as DurableProcessState & { error?: string };
  if (response.ok) return body;
  const message = body.error || `Coordinator вернул HTTP ${response.status}`;
  if (response.status === 400 || response.status === 401 || response.status === 403 || response.status === 404) {
    throw ApplicationFailure.nonRetryable(message, "CoordinatorRequestError");
  }
  throw new Error(message);
}

export async function startScheduledProcess(input: ScheduledProcessWorkflowInput): Promise<ProcessWorkflowInput> {
  assertConfiguration();
  const info = activityInfo();
  if (!info.workflowExecution) {
    throw ApplicationFailure.nonRetryable("scheduled-start требует Workflow Activity", "ConfigurationError");
  }
  // Attempt numbers and task tokens change on retry; the Workflow Run + Activity
  // identity is stable. A different schedule occurrence receives its own key.
  const key = "agat-scheduled-v1:" + createHash("sha256").update(JSON.stringify([
    info.namespace, info.workflowExecution.runId, info.activityId,
  ])).digest("hex");
  let response: Response;
  try {
    response = await fetch(
      `${coordinatorUrl}/api/v1/internal/processes/${encodeURIComponent(input.processId)}/scheduled-start`,
      {
        method: "POST",
        headers: {
          accept: "application/json",
          "content-type": "application/json",
          "x-agat-temporal-token": internalToken,
          "idempotency-key": key,
        },
        body: JSON.stringify({
          projectId: input.projectId,
          input: input.input,
          priority: input.priority,
          knowledgeCollectionIds: input.knowledgeCollectionIds,
        }),
        signal: AbortSignal.timeout(10_000),
      },
    );
  } catch (error) {
    log.warn("Coordinator не создал scheduled process", { processId: input.processId });
    throw error;
  }

  const body = await response.json().catch(() => ({})) as Partial<ProcessWorkflowInput> & { error?: string; code?: string };
  if (
    response.ok
    && typeof body.instanceId === "string"
    && typeof body.processId === "string"
    && typeof body.projectId === "string"
  ) {
    return {
      instanceId: body.instanceId,
      processId: body.processId,
      projectId: body.projectId,
    };
  }
  const message = body.error || `Coordinator вернул HTTP ${response.status}`;
  if ([400, 401, 403, 404].includes(response.status)
    || (response.status === 409 && body.code === "SCHEDULED_START_IDEMPOTENCY_CONFLICT")) {
    throw ApplicationFailure.nonRetryable(message, "CoordinatorRequestError");
  }
  throw new Error(message);
}
