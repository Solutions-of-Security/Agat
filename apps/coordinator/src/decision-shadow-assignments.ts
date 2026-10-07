import type { DecisionShadowLease, DecisionShadowObservation } from './local-decisions.js';

export const DECISION_ASSIGNMENT_HISTORY = 'agat.decision.shadow-assignment-history.v1';
export const DECISION_ASSIGNMENT_INVENTORY = 'agat.decision.shadow-assignment-inventory.v1';
export interface DecisionAssignment {
  assignmentId: string; stageAttempt: number; profileSha256: string; inputSha256: string;
  callerTimeoutMs: number; observation: DecisionShadowObservation | null;
}
export interface DecisionAssignmentHistory {
  schemaVersion: typeof DECISION_ASSIGNMENT_HISTORY;
  coverage: 'complete' | 'legacy_gap' | 'replay'; assignments: DecisionAssignment[];
}
const sha = /^[a-f0-9]{64}$/;
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
function requireValue(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
function record(value: unknown): value is Record<string, unknown> { return !!value && typeof value === 'object' && !Array.isArray(value); }
export function newAssignmentHistory(coverage: DecisionAssignmentHistory['coverage'] = 'complete'): DecisionAssignmentHistory {
  return { schemaVersion: DECISION_ASSIGNMENT_HISTORY, coverage, assignments: [] };
}
export function assignmentHistory(value: unknown): DecisionAssignmentHistory | undefined {
  if (value === undefined) return undefined;
  requireValue(record(value) && Object.keys(value).length === 3 && value.schemaVersion === DECISION_ASSIGNMENT_HISTORY
    && typeof value.coverage === 'string' && ['complete','legacy_gap','replay'].includes(value.coverage) && Array.isArray(value.assignments)
    && value.assignments.length <= 1000, 'Invalid shadow assignment history');
  const ids = new Set<string>(); let lastAttempt = 0;
  for (const row of value.assignments as unknown[]) {
    requireValue(record(row) && Object.keys(row).length === 6 && typeof row.assignmentId === 'string' && uuid.test(row.assignmentId)
      && !ids.has(row.assignmentId) && typeof row.stageAttempt === 'number' && Number.isSafeInteger(row.stageAttempt) && row.stageAttempt > lastAttempt
      && typeof row.profileSha256 === 'string' && sha.test(row.profileSha256) && typeof row.inputSha256 === 'string' && sha.test(row.inputSha256)
      && typeof row.callerTimeoutMs === 'number' && Number.isInteger(row.callerTimeoutMs) && row.callerTimeoutMs >= 100 && row.callerTimeoutMs <= 10_000
      && (row.observation === null || record(row.observation) && row.observation.mode === 'shadow' && row.observation.fallback === 'primary'
        && typeof row.observation.status === 'string' && ['ok','abstain','error','unavailable'].includes(row.observation.status)
        && typeof row.observation.reason === 'string'), 'Invalid shadow assignment row');
    ids.add(row.assignmentId); lastAttempt = row.stageAttempt;
  }
  requireValue(value.coverage !== 'replay' || value.assignments.length === 0, 'Replay cannot create shadow assignments');
  return value as unknown as DecisionAssignmentHistory;
}
export function appendShadowAssignment(history: DecisionAssignmentHistory, assignmentId: string, stageAttempt: number,
  lease: DecisionShadowLease): DecisionAssignmentHistory {
  requireValue(history.coverage !== 'replay', 'Replay cannot dispatch shadow');
  const previousAttempt = history.assignments.at(-1)?.stageAttempt ?? 0;
  const coverage = history.coverage === 'complete' && stageAttempt !== previousAttempt + 1 ? 'legacy_gap' as const : history.coverage;
  const updated = { ...history, coverage, assignments: [...history.assignments, { assignmentId, stageAttempt,
    profileSha256: lease.profileSha256, inputSha256: lease.inputSha256, callerTimeoutMs: lease.timeoutMs, observation: null }] };
  assignmentHistory(updated); return updated;
}
export function recordAssignmentObservation(history: DecisionAssignmentHistory | undefined, stageAttempt: number,
  observation: DecisionShadowObservation): DecisionAssignmentHistory | undefined {
  if (!history) return undefined;
  const index = history.assignments.findIndex(row => row.stageAttempt === stageAttempt);
  const assignment = history.assignments[index];
  requireValue(assignment && assignment.observation === null, 'Shadow assignment for observation not found');
  const assignments = history.assignments.map((row,i) => i === index ? { ...row, observation } : row);
  return { ...history, assignments };
}
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (record(value)) return Object.fromEntries(Object.keys(value).sort().map(key => [key,canonical(value[key])]));
  return value;
}
export function assignmentHistoryDto(value: unknown, currentAttempt: number, status: string, activeLease: boolean,
  observation?: DecisionShadowObservation) {
  const history = assignmentHistory(value);
  let coverage = history?.coverage ?? 'legacy_gap';
  const unassigned = observation?.status === 'unavailable' && ['disabled','unsupported_worker','invalid_input'].includes(observation.reason);
  if (coverage === 'complete' && history) {
    if (observation && !unassigned && !history.assignments.some(row => row.observation
      && JSON.stringify(canonical(row.observation)) === JSON.stringify(canonical(observation)))) coverage = 'legacy_gap';
    else if (!observation && currentAttempt > (history.assignments.at(-1)?.stageAttempt ?? 0)) coverage = 'legacy_gap';
  }
  return { coverage, assignments: (history?.assignments ?? []).map(row => {
    requireValue(row.stageAttempt <= currentAttempt, 'Shadow assignment exceeds stage attempt');
    return { ...row, outcome: row.observation ? 'recorded' as const
      : row.stageAttempt === currentAttempt && activeLease && !['completed','failed','cancelled'].includes(status)
        ? 'pending' as const : 'ended_without_observation' as const };
  }) };
}
