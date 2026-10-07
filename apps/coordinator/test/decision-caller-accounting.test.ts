import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import test from 'node:test';
import { appendCallerAssignment, beginCallerIntent, callerAccounting, callerAccountingDto, newCallerAccounting, recordCallerReturn } from '../src/decision-caller-accounting.ts';
import { appendShadowAssignment, newAssignmentHistory, recordAssignmentObservation } from '../src/decision-shadow-assignments.ts';
const lease = { profile: 'local_decision_shadow_v1' as const, profileSha256: 'a'.repeat(64), inputSha256: 'b'.repeat(64), timeoutMs: 1000,
  request: { schemaVersion: 'agat.decision.v1' as const, id: 'stage', state: 'Synthetic fixture', question: 'Fixture?', kind: 'choice' as const, options: [] } };
const observation = { mode:'shadow' as const,fallback:'primary' as const,status:'unavailable' as const,reason:'timeout',
  callerTiming: { schemaVersion:'agat.decision.caller-timing.v1' as const,clock:'monotonic' as const,boundary:'local_http_call' as const,durationMs:1000 } };

test('return receipts require negotiated intents and immutable observation bindings', () => {
  const id = randomUUID(); const assigned = appendShadowAssignment(newAssignmentHistory(), id, 1, lease);
  const empty = appendCallerAssignment(newCallerAccounting(), id, 1, true);
  assert.deepEqual(recordCallerReturn(empty, 1, observation), empty);
  const begun = beginCallerIntent(empty, 1, { schemaVersion: empty.schemaVersion, assignmentId: id });
  assert.equal(begun.receipt.mayInvoke, true); assert.equal(empty.assignments[0]!.intent, false);
  assert.equal(beginCallerIntent(begun.history, 1, { schemaVersion: empty.schemaVersion, assignmentId: id }).receipt.mayInvoke, false);
  const returned = recordCallerReturn(begun.history, 1, observation)!;
  const recorded = recordAssignmentObservation(assigned, 1, observation)!;
  assert.equal(callerAccountingDto(returned, recorded, 1, 'completed', false).assignments[0]!.outcome, 'returned');
  assert.deepEqual(recordCallerReturn(returned, 1, { ...observation, reason:'busy' }), returned);
  const oldWriter = callerAccountingDto(begun.history, recorded, 1, 'completed', false);
  assert.equal(oldWriter.coverage, 'legacy_gap'); assert.equal(oldWriter.assignments[0]!.outcome, 'data_gap');
  assert.equal(callerAccountingDto(begun.history, assigned, 1, 'running', true).assignments[0]!.outcome, 'intent_pending');
  assert.equal(callerAccountingDto(begun.history, assigned, 1, 'cancelled', false).assignments[0]!.outcome, 'return_missing');
});

test('unknown schemas, aliases, duplicate identities and replay intents fail closed', () => {
  const id = randomUUID(); const empty = appendCallerAssignment(newCallerAccounting(), id, 1, true);
  for (const patch of [{ intent:1 }, { negotiated:1 }, { stageAttempt:true }, { returned:{} }]) {
    assert.throws(() => callerAccounting({ ...empty, assignments:[{ ...empty.assignments[0],...patch }] }));
  }
  assert.throws(() => callerAccounting({ ...empty,coverage:['complete'] }));
  assert.throws(() => appendCallerAssignment(empty, id, 2, true));
  assert.throws(() => appendCallerAssignment(newCallerAccounting('replay'), id, 1, true));
  assert.throws(() => beginCallerIntent(appendCallerAssignment(newCallerAccounting(), id, 1, false), 1,
    { schemaVersion:empty.schemaVersion,assignmentId:id }));
  assert.throws(() => beginCallerIntent(empty, 1, { schemaVersion:'v2',assignmentId:id }));
  assert.equal(callerAccountingDto(undefined, undefined, 1, 'completed', false).coverage, 'legacy_gap');
  assert.equal(callerAccountingDto(empty, appendShadowAssignment(newAssignmentHistory(), randomUUID(), 1, lease), 1, 'running', true).coverage, 'legacy_gap');
  assert.equal(appendCallerAssignment(empty, randomUUID(), 3, true).coverage, 'legacy_gap');
});
