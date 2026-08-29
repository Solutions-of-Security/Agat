import {
  condition,
  continueAsNew,
  defineQuery,
  defineSignal,
  defineUpdate,
  executeChild,
  log,
  makeContinueAsNewFunc,
  ParentClosePolicy,
  proxyActivities,
  setHandler,
  workflowInfo,
} from "@temporalio/workflow";

import type * as activities from "./activities.js";
import type {
  DurableProcessState,
  ProcessWakeAck,
  ProcessWorkflowInput,
  ScheduledProcessWorkflowInput,
} from "./contracts.js";

const { tickProcess, startScheduledProcess } = proxyActivities<typeof activities>({
  startToCloseTimeout: "15 seconds",
  scheduleToCloseTimeout: "2 minutes",
  retry: {
    initialInterval: "1 second",
    backoffCoefficient: 2,
    maximumInterval: "15 seconds",
    maximumAttempts: 8,
    nonRetryableErrorTypes: ["ConfigurationError", "CoordinatorRequestError"],
  },
});

export const processChangedSignal = defineSignal<[string]>("processChanged");
export const processChangedUpdate = defineUpdate<ProcessWakeAck, [string]>("processChangedV1");
export const processStateQuery = defineQuery<DurableProcessState | null>("processState");

const terminalStatuses = new Set(["completed", "failed", "cancelled"]);

function nextCheckDelay(state: DurableProcessState): number {
  if (state.waitUntil) {
    const waitUntil = new Date(state.waitUntil).getTime();
    if (Number.isFinite(waitUntil)) return Math.max(1_000, waitUntil - Date.now());
  }
  if (state.status === "waiting_approval" || state.status === "waiting_external") return 60 * 60 * 1_000;
  if (state.status === "compensating") return 60 * 1_000;
  return 5 * 60 * 1_000;
}

export async function agatProcessWorkflow(input: ProcessWorkflowInput): Promise<DurableProcessState> {
  let revision = 0;
  let state: DurableProcessState | null = null;

  setHandler(processChangedSignal, () => {
    revision += 1;
  });
  setHandler(
    processChangedUpdate,
    (reason) => {
      revision += 1;
      return {
        acceptedRevision: revision,
        reason,
        state,
      };
    },
    {
      validator: (reason) => {
        if (typeof reason !== "string" || reason.length < 1 || reason.length > 120) {
          throw new Error("Причина processChangedV1 должна содержать от 1 до 120 символов");
        }
      },
    },
  );
  setHandler(processStateQuery, () => state);

  log.info("Durable process workflow started", {
    instanceId: input.instanceId,
    processId: input.processId,
    projectId: input.projectId,
  });

  while (true) {
    const observedRevision = revision;
    state = await tickProcess(input);
    if (terminalStatuses.has(state.status)) {
      log.info("Durable process workflow completed", {
        instanceId: input.instanceId,
        status: state.status,
        transitionCount: state.transitionCount,
      });
      return state;
    }

    const info = workflowInfo();
    if (info.continueAsNewSuggested || info.targetWorkerDeploymentVersionChanged) {
      if (info.targetWorkerDeploymentVersionChanged) {
        await makeContinueAsNewFunc<typeof agatProcessWorkflow>({
          initialVersioningBehavior: "AUTO_UPGRADE",
        })(input);
      }
      await continueAsNew<typeof agatProcessWorkflow>(input);
    }

    await condition(() => revision !== observedRevision, nextCheckDelay(state));
  }
}

export async function agatScheduledProcessWorkflow(
  input: ScheduledProcessWorkflowInput,
): Promise<DurableProcessState> {
  const childInput = await startScheduledProcess(input);
  log.info("Scheduled process instance created", {
    instanceId: childInput.instanceId,
    processId: childInput.processId,
    projectId: childInput.projectId,
  });
  return await executeChild(agatProcessWorkflow, {
    args: [childInput],
    workflowId: `agat-process-${childInput.instanceId}`,
    parentClosePolicy: ParentClosePolicy.REQUEST_CANCEL,
  });
}
