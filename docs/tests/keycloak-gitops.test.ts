import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import Ajv from 'ajv';
import { AgatStore } from '../../apps/coordinator/src/database.ts';
import { normalizeProcessGraph } from '../../apps/coordinator/src/process-engine.ts';
import type { ProcessGraph } from '../../apps/coordinator/src/types.ts';
import { agents, inputPrompt, inputTemplate, integrationContract, processDefinition, requirementsSchema, REQUIRED_TOOLS, semanticErrors } from '../../scripts/keycloak-gitops-pack.mjs';

function validRequest() {
  const request = structuredClone(inputTemplate);
  Object.assign(request, { requestId: 'fixture-only-0001', owner: 'test-fixture' });
  Object.assign(request.target, { clusterRef: 'fixture-only', hostname: 'sso.example.invalid', ingressClass: 'fixture', tlsSecretRef: 'fixture/tls', clusterCredentialRef: 'fixture/cluster' });
  request.versions = { keycloak: '26.7.0', operator: '26.7.0', configCli: '6.4.0', argoCd: '3.2.0' }; // Synthetic syntax fixture; not a compatibility claim.
  Object.assign(request.database, { host: 'db.example.invalid', credentialsSecretRef: 'fixture/db', tlsCaSecretRef: 'fixture/ca', backupDestinationRef: 'fixture/backup', rpoMinutes: 15, rtoMinutes: 60 });
  Object.assign(request.git, { repoUrl: 'https://git.example.invalid/fixture/keycloak.git', credentialRef: 'fixture/git' });
  request.gitops.credentialRef = 'fixture/argocd';
  request.identity.realm = 'fixture';
  request.identity.clients = [{ clientId: 'fixture-web', type: 'public-browser', redirectUris: ['https://app.example.invalid/callback'], roles: ['reader'] }];
  Object.assign(request.acceptance, { testClientId: 'fixture-web', testCredentialRef: 'fixture/test-user', callbackUrl: 'https://app.example.invalid/callback', peakLoginsPerSecond: 1, p95LoginMilliseconds: 1000 });
  return request;
}

test('request validation rejects missing targets, plaintext secrets, latest, unsafe redirects and false HA', () => {
  const validate = new Ajv({ allErrors: true }).compile(requirementsSchema);
  const good = validRequest();
  assert.equal(validate(good), true, JSON.stringify(validate.errors));
  assert.deepEqual(semanticErrors(good), []);
  assert.equal(validate(inputTemplate), false, 'unselected infrastructure cannot pass');
  const secret = structuredClone(good);
  secret.database.password = 'synthetic-value-must-be-rejected';
  assert.equal(validate(secret), false, 'raw password is not an allowed property');
  const floating = structuredClone(good);
  floating.versions.keycloak = 'latest';
  assert.equal(validate(floating), false);
  const wildcard = structuredClone(good);
  wildcard.identity.clients[0].redirectUris[0] = 'https://app.example.invalid/*';
  assert.ok(semanticErrors(wildcard).length > 0);
  const ha = structuredClone(good);
  ha.environment = 'production-ha';
  assert.equal(semanticErrors(ha).length, 2, 'one pod and non-HA database cannot pass as HA');
  const existingAgat = structuredClone(good);
  existingAgat.identity.realm = 'agat';
  assert.ok(semanticErrors(existingAgat).length > 0);
  const limits = structuredClone(good);
  limits.resources.memoryLimitMiB = 1024;
  assert.ok(semanticErrors(limits).length > 0);
});

test('prefilled text lists every required parameter while unknown values remain invalid', () => {
  const block = inputPrompt.match(/```json\n([\s\S]*?)\n```/);
  assert.ok(block);
  const request = JSON.parse(block[1]);
  const checkKeys = (schema, value) => {
    if (schema.properties) {
      assert.deepEqual(Object.keys(value).sort(), Object.keys(schema.properties).sort());
      for (const [key, child] of Object.entries(schema.properties)) checkKeys(child, value[key]);
    } else if (schema.items) for (const item of value) checkKeys(schema.items, item);
  };
  checkKeys(requirementsSchema, request);
  assert.equal(request.environment, 'pilot');
  assert.equal(request.target.namespace, 'keycloak');
  assert.equal(request.gitops.controller, 'argocd');
  assert.equal(request.database.rpoMinutes, null);
  assert.match(request.target.clusterRef, /<ЗАПОЛНИТЬ:/);
  assert.equal(new Ajv().compile(requirementsSchema)(request), false);
});

test('canonical exports are reproducible and all six agents and eight tools are bound', () => {
  const definitions = agents();
  const ids = Object.fromEntries(definitions.map(a => [a.key, `role-${a.key}`]));
  const definition = processDefinition(ids);
  const normalized = normalizeProcessGraph(definition.graph as ProcessGraph, new Set(Object.values(ids)));
  assert.equal(normalized.nodes.filter(n => n.type === 'agent').length, 6);
  assert.equal(normalized.nodes.filter(n => n.type === 'approval').length, 3);
  assert.equal(normalized.nodes.filter(n => n.type === 'signal').length, 4);
  assert.equal(normalized.allowPartialStart, false);
  assert.equal(normalized.nodes.find(n => n.type === 'start')!.config.inputTemplate, inputPrompt);
  assert.equal(readFileSync(new URL('../keycloak-gitops/input.prompt.md', import.meta.url), 'utf8').trim(), inputPrompt);
  assert.deepEqual([...normalized.requiredTools!].sort(), [...REQUIRED_TOOLS].sort());
  assert.deepEqual([...normalized.mcpToolAllowlist!].sort(), [...REQUIRED_TOOLS].sort());
  for (const [file, expected] of Object.entries({ 'agents.json': definitions, 'process.template.json': definition, 'requirements.schema.json': requirementsSchema, 'input.template.json': inputTemplate, 'integration-contract.json': integrationContract })) {
    const exported = JSON.parse(readFileSync(new URL(`../keycloak-gitops/${file}`, import.meta.url), 'utf8'));
    assert.deepEqual(exported, expected, `${file} matches its source`);
  }
});

function fixture(guarded = false) {
  const dir = mkdtempSync(path.join(tmpdir(), 'agat-keycloak-pack-test-'));
  const store = new AgatStore(':memory:', { seedDemo: false, artifactsDir: dir });
  store.updateModelRouterPolicy({ enabled: false });
  const ids = {} as Record<string, string>;
  for (const { key, ...definition } of agents('fixture-model')) ids[key] = String(store.createAgent(definition).id);
  const definition = processDefinition(ids, { guarded });
  // Lifecycle tests isolate native sequencing from the not-yet-implemented adapter.
  if (!guarded) Object.assign(definition.graph, { allowPartialStart: false, mcpToolAllowlist: [] });
  const process = store.createProcess(definition);
  const id = String(process.id);
  store.publishProcess(id);
  const worker = store.registerNode({ name: 'fixture-only', platform: 'test', enrollmentToken: 'unused', models: ['fixture-model'], agentRuntimes: ['single', 'langgraph'], agentRuntimeProfiles: ['tool_loop_v1'], maxConcurrency: 1 }).id;
  return { store, id, ids, worker, close: () => { store.close(); rmSync(dir, { recursive: true, force: true }); } };
}

test('missing real MCP tools block the guarded process even with an online worker', () => {
  const f = fixture(true);
  try {
    const p = f.store.preflightProcess(f.id, { version: 1 })!;
    assert.equal(p.runnableNow, false);
    assert.equal(p.blockers.filter(b => b.code.startsWith('tool_missing:')).length, REQUIRED_TOOLS.length);
    assert.throws(() => f.store.startProcess(f.id, { input: JSON.stringify(validRequest()), version: 1, startMode: 'now' }));
  } finally { f.close(); }
});

for (const accept of [true, false]) {
  test(`native lifecycle requires all four external proofs and ${accept ? 'saves handover on final acceptance' : 'stops without handover on final rejection'}`, () => {
    const f = fixture();
    try {
      const instance = f.store.startProcess(f.id, { input: JSON.stringify(validRequest()), version: 1, startMode: 'now', resultDestination: 'artifacts' })!;
      const instanceId = String(instance.id), runId = String(instance.runId);
      const status = () => f.store.getProcessInstance(instanceId)!.status;
      const complete = (key: string) => {
        const lease = f.store.leaseNext(f.worker)!;
        assert.ok(lease);
        assert.equal(lease.agent.id, f.ids[key]);
        assert.deepEqual(lease.mcpTools, []);
        // A model's claimed success must never bypass the next native gate.
        f.store.completeLease(f.worker, lease.leaseId, JSON.stringify({ status: 'READY', summary: 'Simulated model claims all checks passed', requestId: 'fixture-only-0001' }));
      };
      const approve = (decision: boolean) => {
        assert.equal(status(), 'waiting_approval');
        const stage = (f.store.getRun(runId)!.stages as Array<{ id: string; status: string }>).find(s => s.status === 'waiting_approval')!;
        f.store.decideApproval(stage.id, decision);
      };
      const prove = (phase: string) => {
        assert.equal(status(), 'waiting_external');
        assert.equal(f.store.leaseNext(f.worker), null, 'agents cannot advance without the external signal');
        assert.equal(f.store.deliverProcessSignal(f.id, `kc.${phase}.verified`, { instanceId: 'unrelated-instance', payload: {} })!.delivered, false);
        const payload = { schemaVersion: 1, phase, requestId: 'fixture-only-0001', instanceId, target: 'fixture-only', operationId: `fixture-${phase}`, desiredStateSha256: 'a'.repeat(64), commitSha: 'b'.repeat(40), verifiedAt: '2026-09-19T00:00:00Z', checks: [{ id: 'synthetic-control-flow-only', status: 'PASS' }], evidenceUri: `urn:fixture:${phase}` };
        assert.equal(f.store.deliverProcessSignal(f.id, `kc.${phase}.verified`, { instanceId, payload })!.delivered, true);
        assert.equal(f.store.deliverProcessSignal(f.id, `kc.${phase}.verified`, { instanceId, payload })!.delivered, false, 'duplicate proof is not delivered twice');
      };
      complete('architect'); approve(true);
      complete('infrastructure');
      assert.equal(f.store.deliverProcessSignal(f.id, 'kc.acceptance.verified', { instanceId, payload: {} })!.delivered, false, 'future signal cannot skip earlier phases');
      prove('environment');
      complete('keycloak'); complete('gitops'); prove('change'); approve(true);
      complete('release'); prove('release');
      complete('acceptance'); prove('acceptance');
      assert.equal(f.store.listRunArtifacts(runId).some(a => a.name === 'keycloak-handover.md'), false);
      approve(accept);
      assert.equal(status(), accept ? 'completed' : 'cancelled');
      const artifacts = f.store.listRunArtifacts(runId);
      assert.equal(artifacts.some(a => a.name === 'keycloak-handover.md'), accept);
      assert.equal(artifacts.filter(a => /keycloak-(environment|change|release|acceptance)-evidence\.json/.test(a.name)).length, 4);
    } finally { f.close(); }
  });
}

test('partial start and late proof after cancellation cannot bypass gates', () => {
  const f = fixture();
  try {
    assert.throws(() => f.store.startProcess(f.id, { input: '{}', version: 1, startNodeId: 'handover-artifact' }));
    const instance = f.store.startProcess(f.id, { input: JSON.stringify(validRequest()), version: 1, startMode: 'now' })!;
    f.store.cancelProcessInstance(String(instance.id));
    assert.equal(f.store.deliverProcessSignal(f.id, 'kc.acceptance.verified', { instanceId: String(instance.id), payload: {} })!.delivered, false);
    assert.equal(f.store.getProcessInstance(String(instance.id))!.status, 'cancelled');
  } finally { f.close(); }
});

test('missing external evidence times out as failed, without a handover artifact', () => {
  const f = fixture();
  try {
    const instance = f.store.startProcess(f.id, { input: JSON.stringify(validRequest()), version: 1, startMode: 'now' })!;
    const instanceId = String(instance.id), runId = String(instance.runId);
    let lease = f.store.leaseNext(f.worker)!;
    f.store.completeLease(f.worker, lease.leaseId, '{}');
    const review = (f.store.getRun(runId)!.stages as Array<{ id: string; status: string }>).find(s => s.status === 'waiting_approval')!;
    f.store.decideApproval(review.id, true);
    lease = f.store.leaseNext(f.worker)!;
    f.store.completeLease(f.worker, lease.leaseId, '{}');
    assert.equal(f.store.getProcessInstance(instanceId)!.status, 'waiting_external');
    f.store.db.prepare("UPDATE process_signal_waits SET expires_at = '2000-01-01T00:00:00.000Z' WHERE instance_id = ?").run(instanceId);
    f.store.leaseNext(f.worker); // Normal scheduler maintenance expires timed-out signals.
    assert.equal(f.store.getProcessInstance(instanceId)!.status, 'failed');
    assert.equal(f.store.listRunArtifacts(runId).some(a => a.name === 'keycloak-handover.md'), false);
    assert.equal(f.store.deliverProcessSignal(f.id, 'kc.environment.verified', { instanceId, payload: {} })!.delivered, false);
  } finally { f.close(); }
});
