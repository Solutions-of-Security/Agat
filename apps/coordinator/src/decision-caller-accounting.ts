import { decisionCallerTiming, type DecisionCallerTiming, type DecisionShadowObservation } from './local-decisions.js';
import type { DecisionAssignmentHistory } from './decision-shadow-assignments.js';

export const DECISION_CALLER_ACCOUNTING = 'agat.decision.caller-accounting.v1';
export const DECISION_CALLER_INVENTORY = 'agat.decision.caller-inventory.v1';
interface CallerAssignment {
  assignmentId: string; stageAttempt: number; negotiated: boolean; intent: boolean;
  returned: { callerTiming: DecisionCallerTiming; status: DecisionShadowObservation['status']; reason: string } | null;
}
interface CallerAccounting {
  schemaVersion: typeof DECISION_CALLER_ACCOUNTING;
  coverage: 'complete' | 'legacy_gap' | 'replay'; assignments: CallerAssignment[];
}
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
function requireValue(value: unknown, message: string): asserts value { if (!value) throw new Error(message); }
function record(value: unknown): value is Record<string, unknown> { return !!value && typeof value === 'object' && !Array.isArray(value); }
export function newCallerAccounting(coverage: CallerAccounting['coverage'] = 'complete'): CallerAccounting {
  return { schemaVersion: DECISION_CALLER_ACCOUNTING, coverage, assignments: [] };
}
export function callerAccounting(value: unknown): CallerAccounting | undefined {
  if (value === undefined) return undefined;
  requireValue(record(value) && Object.keys(value).length === 3 && value.schemaVersion === DECISION_CALLER_ACCOUNTING
    && typeof value.coverage === 'string' && ['complete','legacy_gap','replay'].includes(value.coverage)
    && Array.isArray(value.assignments) && value.assignments.length <= 1000, 'Invalid caller accounting');
  const ids = new Set<string>(); let lastAttempt = 0;
  for (const row of value.assignments as unknown[]) {
    requireValue(record(row) && Object.keys(row).length === 5 && typeof row.assignmentId === 'string' && uuid.test(row.assignmentId)
      && !ids.has(row.assignmentId) && typeof row.stageAttempt === 'number' && Number.isSafeInteger(row.stageAttempt) && row.stageAttempt > lastAttempt
      && typeof row.negotiated === 'boolean' && typeof row.intent === 'boolean' && (!row.intent || row.negotiated)
      && (row.returned === null || record(row.returned) && Object.keys(row.returned).length === 3 && row.intent
        && typeof row.returned.status === 'string' && ['ok','abstain','error','unavailable'].includes(row.returned.status)
        && typeof row.returned.reason === 'string' && row.returned.reason.length <= 200), 'Invalid caller assignment');
    if (row.returned !== null) decisionCallerTiming((row.returned as Record<string,unknown>).callerTiming);
    ids.add(row.assignmentId); lastAttempt = row.stageAttempt;
  }
  requireValue(value.coverage !== 'replay' || value.assignments.length === 0, 'Replay cannot create caller intents');
  return value as unknown as CallerAccounting;
}
export function appendCallerAssignment(history: CallerAccounting, assignmentId: string, stageAttempt: number, negotiated: boolean): CallerAccounting {
  requireValue(history.coverage !== 'replay', 'Replay cannot dispatch caller');
  const previousAttempt = history.assignments.at(-1)?.stageAttempt ?? 0;
  const updated = { ...history, coverage: history.coverage === 'complete' && stageAttempt !== previousAttempt + 1 ? 'legacy_gap' as const : history.coverage,
    assignments: [...history.assignments, { assignmentId, stageAttempt, negotiated, intent: false, returned: null }] };
  callerAccounting(updated); return updated;
}
export function beginCallerIntent(history: CallerAccounting | undefined, stageAttempt: number, raw: unknown) {
  requireValue(record(raw) && Object.keys(raw).length === 2 && raw.schemaVersion === DECISION_CALLER_ACCOUNTING
    && typeof raw.assignmentId === 'string' && uuid.test(raw.assignmentId), 'Invalid caller intent identity');
  const row = history?.assignments.find(item => item.stageAttempt === stageAttempt && item.assignmentId === raw.assignmentId);
  requireValue(history && row?.negotiated, 'Caller accounting was not negotiated for this assignment');
  const mayInvoke = !row.intent;
  return { history: { ...history, assignments: history.assignments.map(item => item === row ? { ...item, intent: true } : item) },
    receipt: { schemaVersion: DECISION_CALLER_ACCOUNTING, assignmentId: row.assignmentId, mayInvoke } };
}
export function recordCallerReturn(history: CallerAccounting | undefined, stageAttempt: number, observation: DecisionShadowObservation): CallerAccounting | undefined {
  const row = history?.assignments.find(item => item.stageAttempt === stageAttempt);
  if (!history || !row?.intent || row.returned || !observation.callerTiming) return history;
  return { ...history, assignments: history.assignments.map(item => item === row ? { ...item, returned: {
    callerTiming: observation.callerTiming!, status: observation.status, reason: observation.reason } } : item) };
}
export function callerAccountingDto(value: unknown, assignmentHistory: DecisionAssignmentHistory | undefined,
  currentAttempt: number, status: string, activeLease: boolean) {
  const history = callerAccounting(value);
  let coverage = history?.coverage ?? 'legacy_gap';
  if (coverage === 'complete' && (!assignmentHistory || assignmentHistory.coverage !== 'complete'
    || assignmentHistory.assignments.length !== history!.assignments.length)) coverage = 'legacy_gap';
  const assignments = (history?.assignments ?? []).map(row => {
    const assigned = assignmentHistory?.assignments.find(item => item.assignmentId === row.assignmentId && item.stageAttempt === row.stageAttempt);
    const observation = assigned?.observation;
    const synthetic = observation?.status === 'unavailable' && ['disabled','dry_run','missing_result'].includes(observation.reason);
    const gap = !assigned || row.returned !== null && (!observation?.callerTiming || observation.status !== row.returned.status
      || observation.reason !== row.returned.reason || observation.callerTiming.durationMs !== row.returned.callerTiming.durationMs)
      || row.negotiated && !!observation && !synthetic && !row.returned;
    if (gap) coverage = 'legacy_gap';
    return { ...row, outcome: gap ? 'data_gap' as const : row.returned ? 'returned' as const
      : !row.negotiated ? 'unnegotiated' as const : !row.intent ? 'no_intent_recorded' as const
        : row.stageAttempt === currentAttempt && activeLease && !['completed','failed','cancelled'].includes(status)
          ? 'intent_pending' as const : 'return_missing' as const };
  });
  return { coverage, assignments };
}
