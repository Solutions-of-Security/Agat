import { createHash, randomUUID } from "node:crypto";
import { DECISION_ASSIGNMENT_INVENTORY, assignmentHistory, assignmentHistoryDto } from "./decision-shadow-assignments.js";
import { DECISION_CALLER_INVENTORY, callerAccounting, callerAccountingDto } from "./decision-caller-accounting.js";
import type { DecisionShadowConfig, DecisionShadowLease, DecisionShadowObservation } from "./local-decisions.js";

export const DECISION_COHORT_SCHEMA = "agat.decision.shadow-cohort.v1";
export const COHORT_LIMITS = { instances: 1000, stages: 10000, activityBytes: 16 * 1024 * 1024, exportBytes: 16 * 1024 * 1024 } as const;
export interface DecisionCohortScope { processVersion: number; startAt: string; endAt: string }
export interface DecisionStageSource {
  stage: Record<string, unknown>; activity: Record<string, unknown>; activeLease: boolean;
}

function requireValue(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
export function cohortScope(raw: unknown, observedAt: string): DecisionCohortScope {
  requireValue(raw && typeof raw === "object" && !Array.isArray(raw), "Invalid decision cohort scope");
  const scope = raw as Record<string, unknown>;
  requireValue(Object.keys(scope).length === 3 && ["processVersion", "startAt", "endAt"].every(k => Object.hasOwn(scope, k))
    && typeof scope.processVersion === "number" && Number.isSafeInteger(scope.processVersion) && scope.processVersion > 0,
    "Use a positive integer processVersion and an explicit cohort window");
  for (const value of [scope.startAt, scope.endAt]) {
    requireValue(typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)
      && Number.isFinite(Date.parse(value)) && new Date(value).toISOString() === value, "Use canonical UTC cohort dates");
  }
  const start = Date.parse(String(scope.startAt)); const end = Date.parse(String(scope.endAt));
  requireValue(start < end && end - start <= 7 * 86400_000 && end <= Date.parse(observedAt),
    "Use a completed positive cohort window of at most seven days");
  return scope as unknown as DecisionCohortScope;
}

// Keep the same ledger semantics as run trace; only cohort exports omit task context.
export function decisionTraceInventory(stages: DecisionStageSource[], includeContext = true) {
  const shadow = stages.filter(({ activity }) => activity.decisionShadowConfig || activity.decisionShadowLease || activity.decisionShadowObservation);
  return {
    decisionCallerAccounting: {
      schemaVersion: DECISION_CALLER_INVENTORY, scope: "caller_operation_intents",
      stages: shadow.map(({ stage, activity, activeLease }) => ({ stageId: String(stage.id),
        ...callerAccountingDto(activity.decisionShadowCallerAccounting, assignmentHistory(activity.decisionShadowAssignmentHistory),
          Number(stage.attempt), String(stage.status), activeLease) })),
    },
    decisionAssignmentHistory: {
      schemaVersion: DECISION_ASSIGNMENT_INVENTORY, scope: "coordinator_shadow_assignments",
      stages: shadow.map(({ stage, activity, activeLease }) => ({ stageId: String(stage.id),
        ...assignmentHistoryDto(activity.decisionShadowAssignmentHistory, Number(stage.attempt), String(stage.status), activeLease,
          activity.decisionShadowObservation as DecisionShadowObservation | undefined) })),
    },
    decisionStageInventory: {
      schemaVersion: "agat.decision.shadow-stage-inventory.v1", scope: "stored_shadow_stages",
      stages: shadow.map(({ stage, activity }) => {
        const lease = activity.decisionShadowLease as DecisionShadowLease | undefined;
        return { stageId: String(stage.id), stageStatus: String(stage.status), assigned: Boolean(lease),
          observationRecorded: Boolean(activity.decisionShadowObservation), profileSha256: lease?.profileSha256 ?? null,
          inputSha256: lease?.inputSha256 ?? null, callerTimeoutMs: lease?.timeoutMs ?? null };
      }),
    },
    decisionObservations: shadow.flatMap(({ stage, activity }) => {
      const config = activity.decisionShadowConfig as DecisionShadowConfig | undefined;
      const lease = activity.decisionShadowLease as DecisionShadowLease | undefined;
      return activity.decisionShadowObservation ? [{ stageId: String(stage.id), profileSha256: lease?.profileSha256 ?? null,
        inputSha256: lease?.inputSha256 ?? null, callerTimeoutMs: lease?.timeoutMs ?? null,
        ...(includeContext ? { context: config ? { kind: config.kind, question: config.question, options: config.options } : null } : {}),
        observation: activity.decisionShadowObservation }] : [];
    }),
  };
}

const observationFields = new Set(["mode", "fallback", "status", "reason", "reusedFromStageId", "result", "callerTiming"]);
const resultFields = new Set(["schemaVersion", "runtimeVersion", "id", "mode", "inputSha256", "model", "policy", "calibration",
  "status", "reason", "selectedOptionId", "value", "distribution", "durationMs", "selectedProbability", "margin", "inputTokens",
  "generatedTokens", "inputFingerprintVersions", "inputFingerprintVersion"]);
function checkObservation(value: unknown): void {
  requireValue(value && typeof value === "object" && !Array.isArray(value), "Invalid stored decision observation");
  const observation = value as Record<string, unknown>;
  requireValue(Object.keys(observation).every(k => observationFields.has(k)) && observation.mode === "shadow" && observation.fallback === "primary"
    && ["ok", "abstain", "error", "unavailable"].includes(String(observation.status))
    && typeof observation.reason === "string" && /^[a-z0-9_]{1,80}$/.test(observation.reason), "Unsupported stored decision observation fields");
  if (observation.result !== undefined) {
    const result = observation.result;
    requireValue(result && typeof result === "object" && !Array.isArray(result)
      && Object.keys(result).every(k => resultFields.has(k)), "Unsupported stored decision result fields");
  }
}

export function cohortActivity(raw: unknown): Record<string, unknown> {
  if (raw === null || raw === undefined) return {};
  requireValue(typeof raw === "string", "Invalid stored stage activity");
  const activity: unknown = JSON.parse(raw);
  requireValue(activity && typeof activity === "object" && !Array.isArray(activity), "Invalid stored stage activity");
  const record = activity as Record<string, unknown>;
  if (record.decisionShadowObservation) checkObservation(record.decisionShadowObservation);
  for (const assignment of assignmentHistory(record.decisionShadowAssignmentHistory)?.assignments ?? []) {
    if (assignment.observation) checkObservation(assignment.observation);
  }
  for (const assignment of callerAccounting(record.decisionShadowCallerAccounting)?.assignments ?? []) {
    if (assignment.returned) requireValue(/^[a-z0-9_]{1,80}$/.test(assignment.returned.reason), "Unsupported stored caller reason");
  }
  return record;
}

export function cohortEnvelope(projectId: string, processId: string, scope: DecisionCohortScope, observedAt: string,
  dialect: string, instances: Record<string, unknown>[], stages: Record<string, unknown>[]) {
  requireValue(instances.length <= COHORT_LIMITS.instances && stages.length <= COHORT_LIMITS.stages, "Decision cohort row limit exceeded");
  const grouped = new Map<string, DecisionStageSource[]>(); let activityBytes = 0;
  const runs = new Set(instances.map(row => String(row.run_id)));
  requireValue(runs.size === instances.length, "Decision cohort has repeated run identities");
  for (const stage of stages) {
    requireValue(runs.has(String(stage.run_id)), "Cohort stage has no included run");
    activityBytes += typeof stage.activity_json === "string" ? Buffer.byteLength(stage.activity_json) : 0;
    requireValue(activityBytes <= COHORT_LIMITS.activityBytes, "Decision cohort activity byte limit exceeded");
    const activity = cohortActivity(stage.activity_json);
    const rows = grouped.get(String(stage.run_id)) ?? [];
    rows.push({ stage, activity, activeLease: Boolean(stage.lease_id) && Date.parse(String(stage.lease_expires_at ?? "")) > Date.parse(observedAt) });
    grouped.set(String(stage.run_id), rows);
  }
  const included = instances.map(row => ({ instanceId: String(row.id), runId: String(row.run_id),
    processVersion: Number(row.process_version), createdAt: String(row.created_at), status: String(row.status),
    replayOfInstanceId: row.replay_of_instance_id ?? null, replayMode: String(row.replay_mode) }));
  const traces = included.map(row => ({ run: { id: row.runId }, truncated: false,
    ...decisionTraceInventory(grouped.get(row.runId) ?? [], false) }));
  const result = { schemaVersion: DECISION_COHORT_SCHEMA, snapshotId: randomUUID(), observedAt,
    scope: { projectId, processId, ...scope, boundary: "process_instance_created_at_half_open" },
    snapshot: { dialect, consistency: "single_database_snapshot", storedCohortComplete: true, truncated: false },
    limits: COHORT_LIMITS, counts: { instances: included.length, runs: runs.size, storedStages: stages.length,
      storedShadowStages: traces.reduce((sum, t) => sum + t.decisionStageInventory.stages.length, 0) },
    runIdsSha256: createHash("sha256").update(JSON.stringify(included.map(i => i.runId))).digest("hex"), instances: included, traces,
    dataPolicy: { taskInputsIncluded: false, questionOptionsIncluded: false, primaryOutputsIncluded: false,
      eventsIncluded: false, artifactsIncluded: false, decisionProfileAndResultMetadataIncluded: true },
    populationCoverageVerified: false, eligibleWorkloadVerified: false, httpAttemptInventoryVerified: false,
    sloAccepted: false, routingEnabled: false, qualification: "not_assessed" };
  requireValue(Buffer.byteLength(JSON.stringify(result)) <= COHORT_LIMITS.exportBytes, "Decision cohort export byte limit exceeded");
  return result;
}
