import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { AgatStore } from '/Users/mdavliatshin/Documents/Workspace/Agat/apps/coordinator/src/database.ts';
import { migratePostgresSchemaAndAdmit } from '/Users/mdavliatshin/Documents/Workspace/Agat/apps/coordinator/src/postgres-schema-migrator.ts';

const chunks = 500;
const dimensions = 4096;
const vector = Array(dimensions).fill(1 / 3);
const sourcePath = '/Users/mdavliatshin/Documents/Workspace/Agat/apps/coordinator/src/database.ts';
const plan = { classification: 'synthetic-vector-transport-diagnostic', chunks, dimensions,
  embeddingJsonBytesPerChunk: Buffer.byteLength(JSON.stringify(vector)),
  databaseSourceSha256: createHash('sha256').update(fs.readFileSync(sourcePath)).digest('hex') };
fs.writeFileSync('/tmp/agat-rag-bridge-plan.json', JSON.stringify(plan, null, 2) + '\n', { flag: 'wx' });
await migratePostgresSchemaAndAdmit();
const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), 'agat-rag-bridge-'));
const store = new AgatStore(':postgresql:', { stateStoreDriver: 'postgresql', seedDemo: false,
  postgresSchemaMode: 'runtime', artifactsDir: artifacts,
  coordinatorInstanceId: 'rag-bridge-probe', region: 'eu-test-1', residencyDomain: 'eu-test',
  requireSignedWorkerReleases: false,
  postgres: { systemUrl: process.env.AGAT_POSTGRES_URL!, tenantUrl: process.env.AGAT_POSTGRES_TENANT_URL!,
    roleMode: 'runtime', applicationName: 'rag-bridge-probe', poolMax: 1, connectTimeoutMs: 5000,
    idleTimeoutMs: 30000, statementTimeoutMs: 30000, sslMode: 'disable' } });
try {
  const projectId = 'rag-bridge-fixture';
  store.createProject({ id: projectId, name: projectId, homeRegion: 'eu-test-1', allowedRegions: ['eu-test-1'], residencyDomain: 'eu-test' });
  const agent = store.createAgent({ name: 'Bridge fixture', role: 'Test', systemPrompt: 'Fixture', model: 'bridge-fixture' }, projectId);
  const node = store.registerNode({ enrollmentToken: 'unused', name: 'Bridge fixture', platform: 'test',
    models: ['bridge-fixture'], embeddingModels: ['bridge-fixture'], maxConcurrency: 1,
    region: 'eu-test-1', residencyDomain: 'eu-test' }).id;
  const collection = String(store.createKnowledgeCollection({ name: 'Bridge fixture', embeddingModel: 'bridge-fixture',
    chunkSize: 400, chunkOverlap: 0, topK: 1 }, projectId).id);
  const document = store.ingestKnowledgeDocument(collection, { name: 'Synthetic repeated chunks', content: 'A'.repeat(chunks * 400) }, projectId);
  assert.equal(document.chunkCount, chunks);
  let batches = 0;
  for (let lease; (lease = store.leaseKnowledgeEmbedding(node));) {
    store.completeKnowledgeEmbedding(node, lease.leaseId, lease.chunks.map(chunk => ({ chunkId: chunk.id, embedding: vector })));
    batches++;
  }
  const count = Number(store.db.prepare('SELECT COUNT(*) AS count FROM knowledge_chunks WHERE embedding_json IS NOT NULL').get()!.count);
  assert.equal(count, chunks);
  const run = store.createRun({ name: 'Bridge capacity', input: 'Synthetic query', agentIds: [String(agent.id)],
    approvalRequired: false, knowledgeCollectionIds: [collection] }, projectId);
  const lease = store.leaseNext(node)!; assert.equal(lease.run.id, run.id);
  let searchError: string | null = null;
  let hits = 0;
  try { hits = store.searchKnowledge(node, lease.leaseId, { queries: [{ collectionIds: [collection], embeddingModel: 'bridge-fixture', topK: 1, vector }] }).hits.length; }
  catch (error) { searchError = String(error); }
  const events = (store.getRunTrace(run.id, projectId)!.events as Array<{ type: string }>).filter(e => e.type === 'knowledge.retrieved');
  const result = { ...plan, indexedChunks: count, batches, hits, searchError, retrievalEvents: events.length };
  fs.writeFileSync('/tmp/agat-rag-bridge-before.json', JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
  console.log(JSON.stringify(result));
  assert.match(searchError ?? '', /exceeds bridge limit/);
  assert.equal(events.length, 0);
} finally { store.close(); fs.rmSync(artifacts, { recursive: true, force: true }); }
