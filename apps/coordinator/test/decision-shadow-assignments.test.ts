import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import test from 'node:test';
import { appendShadowAssignment, assignmentHistory, assignmentHistoryDto, newAssignmentHistory, recordAssignmentObservation } from '../src/decision-shadow-assignments.ts';
const lease = { profile: 'local_decision_shadow_v1' as const, profileSha256: 'a'.repeat(64), inputSha256: 'b'.repeat(64), timeoutMs: 1000,
  request: { schemaVersion: 'agat.decision.v1' as const, id: 'stage', state: 'Synthetic fixture', question: 'Fixture?', kind: 'choice' as const, options: [] } };
test('history retains three assignments and separates missing, pending and recorded outcomes', () => {
  const first = appendShadowAssignment(newAssignmentHistory(), randomUUID(), 1, lease);
  const second = appendShadowAssignment(first, randomUUID(), 2, lease);
  const third = appendShadowAssignment(second, randomUUID(), 3, lease);
  assert.equal(first.assignments.length, 1); assert.equal(second.assignments.length, 2);
  assert.equal(assignmentHistoryDto(first, 1, 'waiting_external', true).assignments[0]!.outcome, 'pending');
  assert.equal(assignmentHistoryDto(first, 1, 'cancelled', true).assignments[0]!.outcome, 'ended_without_observation');
  assert.deepEqual(assignmentHistoryDto(third, 3, 'running', true).assignments.map(row => row.outcome), ['ended_without_observation','ended_without_observation','pending']);
  const recorded = recordAssignmentObservation(third, 3, { mode:'shadow',fallback:'primary',status:'unavailable',reason:'missing_result' })!;
  assert.deepEqual(assignmentHistoryDto(recorded, 3, 'completed', false).assignments.map(row => row.outcome), ['ended_without_observation','ended_without_observation','recorded']);
  assert.equal(third.assignments[2].observation, null);
  assert.throws(() => recordAssignmentObservation(recorded, 3, { mode:'shadow',fallback:'primary',status:'unavailable',reason:'missing_result' }));
});
test('duplicate identities, reversed attempts, replay assignments and numeric aliases are rejected', () => {
  const id = randomUUID(); const first = appendShadowAssignment(newAssignmentHistory(), id, 1, lease);
  assert.throws(() => appendShadowAssignment(first, id, 2, lease));
  assert.throws(() => appendShadowAssignment(first, randomUUID(), 1, lease));
  assert.throws(() => appendShadowAssignment(newAssignmentHistory('replay'), randomUUID(), 1, lease));
  assert.throws(() => assignmentHistory({ ...first,assignments:[{...first.assignments[0],stageAttempt:true}] }));
  assert.throws(() => assignmentHistory({ ...first,assignments:[{...first.assignments[0],callerTimeoutMs:1000.5}] }));
  assert.throws(() => assignmentHistory({ ...first,coverage:['complete'] }));
  assert.throws(() => assignmentHistory({ ...first,assignments:[{...first.assignments[0],observation:{mode:'shadow',fallback:'primary',status:['ok'],reason:'computed'}}] }));
  assert.equal(assignmentHistoryDto(undefined, 3, 'failed', false).coverage, 'legacy_gap');
  assert.deepEqual(assignmentHistoryDto(newAssignmentHistory('replay'), 1, 'completed', false).assignments, []);
});

test('an older writer cannot leave complete coverage after untracked dispatch or observation', () => {
  const first = appendShadowAssignment(newAssignmentHistory(), randomUUID(), 1, lease);
  const observation = { mode:'shadow' as const,fallback:'primary' as const,status:'unavailable' as const,reason:'busy' };
  assert.equal(assignmentHistoryDto(first, 1, 'completed', false, observation).coverage, 'legacy_gap');
  assert.equal(first.coverage, 'complete'); assert.equal(first.assignments[0]!.observation, null);
  assert.equal(assignmentHistoryDto(first, 2, 'running', true).coverage, 'legacy_gap');
  const resumed = appendShadowAssignment(first, randomUUID(), 3, lease);
  assert.equal(resumed.coverage, 'legacy_gap'); assert.equal(resumed.assignments.length, 2);
  const recorded = recordAssignmentObservation(first, 1, observation)!;
  assert.equal(assignmentHistoryDto(recorded, 2, 'completed', false, { reason:'busy',status:'unavailable',fallback:'primary',mode:'shadow' }).coverage, 'complete');
});
