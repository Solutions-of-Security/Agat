import {
  ActivityCancellationType,
  CancellationScope,
  condition,
  continueAsNew,
  defineQuery,
  defineSignal,
  defineUpdate,
  executeChild,
  isCancellation,
  log,
  makeContinueAsNewFunc,
  ParentClosePolicy,
  patched,
  proxyActivities,
  setHandler,
  sleep,
  startChild,
  workflowInfo,
} from "@temporalio/workflow";

import type { ActivityOptions } from "@temporalio/common";
import { SCHEDULED_START_ACTIVITY_ID } from "./contracts.js";
import type * as activities from "./activities.js";
import type {
  DurableProcessState,
  ProcessWakeAck,
  ProcessWorkflowInput,
  ScheduledProcessWorkflowInput,
} from "./contracts.js";

const processActivityOptions = {
  startToCloseTimeout: "15 seconds",
  scheduleToCloseTimeout: "2 minutes",
  retry: {
    initialInterval: "1 second",
    backoffCoefficient: 2,
    maximumInterval: "15 seconds",
    maximumAttempts: 8,
    nonRetryableErrorTypes: ["ConfigurationError", "CoordinatorRequestError"],
  },
} satisfies ActivityOptions;
const { tickProcess, startScheduledProcess } = proxyActivities<typeof activities>(processActivityOptions);
const { startScheduledProcess: startCancellableScheduledProcess } = proxyActivities<typeof activities>({
  ...processActivityOptions,
  activityId: SCHEDULED_START_ACTIVITY_ID,
  cancellationType: ActivityCancellationType.TRY_CANCEL,
});
// A missing create acknowledgement is not evidence that creation failed. Keep
// the same idempotent Activity alive until it is acknowledged or cancelled;
// individual attempts stay bounded and permanent request errors still fail.
const { startScheduledProcess: startDurableScheduledProcess } = proxyActivities<typeof activities>({
  startToCloseTimeout: "15 seconds",
  activityId: SCHEDULED_START_ACTIVITY_ID,
  cancellationType: ActivityCancellationType.TRY_CANCEL,
  retry: {
    initialInterval: "1 second",
    backoffCoefficient: 2,
    maximumInterval: "15 seconds",
    nonRetryableErrorTypes: ["ConfigurationError", "CoordinatorRequestError"],
  },
});

// Cancellation remains pending during a coordinator outage. The application
// cancellation endpoint is idempotent, including its compensation transition.
const { cancelProcess, cancelScheduledProcess, tickProcess: tickCancelledProcess } = proxyActivities<typeof activities>({
  startToCloseTimeout: "15 seconds",
  retry: {
    initialInterval: "1 second",
    backoffCoefficient: 2,
    maximumInterval: "30 seconds",
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

  try {
    return await driveProcess();
  } catch (error) {
    if (isCancellation(error) && patched("agat-process-cancellation-cleanup-v1")) {
      await CancellationScope.nonCancellable(async () => {
        state = await cancelProcess(input);
        // Cancellation can arm compensations. Keep the workflow alive until
        // the existing application state machine reaches a terminal state.
        while (!terminalStatuses.has(state.status)) {
          const observedRevision = revision;
          await condition(() => revision !== observedRevision, nextCheckDelay(state));
          state = await tickCancelledProcess(input);
        }
      });
    }
    throw error;
  }

  async function driveProcess(): Promise<DurableProcessState> {
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
}

export async function agatScheduledProcessWorkflow(
  input: ScheduledProcessWorkflowInput,
): Promise<DurableProcessState> {
  const cancellationIntent = patched("agat-scheduled-cancellation-intent-v1");
  const durableCreation = cancellationIntent && patched("agat-scheduled-start-durable-retry-v1");
  let childStarted = false;
  try {
    const start = durableCreation ? startDurableScheduledProcess
      : cancellationIntent ? startCancellableScheduledProcess : startScheduledProcess;
    const childInput = await start(input);
    log.info("Scheduled process instance created", {
      instanceId: childInput.instanceId,
      processId: childInput.processId,
      projectId: childInput.projectId,
    });
    const options = {
      args: [childInput] as [ProcessWorkflowInput],
      workflowId: `agat-process-${childInput.instanceId}`,
      parentClosePolicy: ParentClosePolicy.REQUEST_CANCEL,
    };
    if (!cancellationIntent) return await executeChild(agatProcessWorkflow, options);
    const child = await startChild(agatProcessWorkflow, options);
    childStarted = true;
    return await child.result();
  } catch (error) {
    if (cancellationIntent && !childStarted && isCancellation(error)) {
      await CancellationScope.nonCancellable(async () => {
        // The creation Activity can still reach HTTP/COMMIT after it is cancelled.
        // Use its explicit identity, not this cleanup Activity's identity, to
        // durably fence that request even when we never received an instance ID.
        let state = await cancelScheduledProcess(input);
        while (state && !terminalStatuses.has(state.status)) {
          await sleep(nextCheckDelay(state));
          state = await tickCancelledProcess({ instanceId: state.instanceId, processId: input.processId, projectId: input.projectId });
        }
      });
    }
    throw error;
  }
}
