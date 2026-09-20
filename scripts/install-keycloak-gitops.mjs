import assert from 'node:assert/strict';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { agents, exportPack, manifestHash, PACK_ID, PROCESS_NAME, processDefinition } from './keycloak-gitops-pack.mjs';

// Definitions only: this installer never publishes, starts, deploys, or changes policy.
const root = fileURLToPath(new URL('../', import.meta.url));
if (existsSync(`${root}.env`)) process.loadEnvFile(`${root}.env`);
const projectId = process.env.AGAT_PROJECT_ID || 'default';
const port = Number(process.env.AGAT_HTTP_PORT || 8788);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Некорректный AGAT_HTTP_PORT');
const baseUrl = `http://127.0.0.1:${port}`;
const token = process.env.AGAT_ADMIN_TOKEN;
if (!token) throw new Error('Нужен существующий AGAT_ADMIN_TOKEN в окружении или .env');
const headers = { 'X-Agat-Admin-Token': token, 'X-Agat-Project-Id': projectId, 'Content-Type': 'application/json' };
const api = async (path, body) => {
  const response = await fetch(`${baseUrl}/api/v1/${path}`, {
    method: body === undefined ? 'GET' : 'POST', headers,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: AbortSignal.timeout(15000),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}: ${JSON.stringify(value).replaceAll(token, '[REDACTED]').slice(0, 1000)}`);
  return value;
};
const writeJson = (name, value) => writeFileSync(`${root}docs/keycloak-gitops/${name}`, `${JSON.stringify(value, null, 2)}\n`);
// The API normalizes text fields with trim(), including artifact content.
const apiNodes = nodes => nodes.map(node => ({ ...node, config: Object.fromEntries(Object.entries(node.config).map(([key, value]) => [key, typeof value === 'string' ? value.trim() : value])) }));
const [health, overview, existingAgents, existingProcesses, mcp] = await Promise.all([
  api('health'), api('overview'), api('agents'), api('processes'), api('mcp/servers'),
]);
const model = process.env.AGAT_KEYCLOAK_MODEL || (overview.models.includes('qwen3.5:4b') ? 'qwen3.5:4b' : overview.models[0]);
if (!model) throw new Error('Нет доступной модели; укажите AGAT_KEYCLOAK_MODEL');
const snapshot = { baseUrl, projectId, model, nodes: overview.nodes.map(n => ({ name: n.name, status: n.status, models: n.models, agentRuntimes: n.agentRuntimes })), mcpServerCount: mcp.servers.length, runtime: health.processRuntime };
if (!process.argv.includes('--install')) {
  console.log(JSON.stringify(snapshot, null, 2));
  console.log('Для создания агентов и черновика используйте --install.');
  process.exit(0);
}
exportPack();
const ledgerPath = `${root}docs/keycloak-gitops/installation.json`;
const previous = existsSync(ledgerPath) ? JSON.parse(readFileSync(ledgerPath, 'utf8')) : null;
if (previous && (previous.baseUrl !== baseUrl || previous.projectId !== projectId || previous.manifestSha256 !== manifestHash(model))) {
  throw new Error('Параметры ранее сохранённой установки отличаются; автоматическая перезапись запрещена');
}
const ledger = previous || { packId: PACK_ID, manifestSha256: manifestHash(model), baseUrl, projectId, model, agentIds: {}, processId: null, createdAt: new Date().toISOString() };
let createdAgents = 0;
for (const { key, ...definition } of agents(model)) {
  let record = existingAgents.agents.find(a => a.name === definition.name);
  if (record) {
    for (const field of ['name', 'role', 'model', 'runtime', 'systemPrompt']) assert.equal(record[field], definition[field], `Существующий агент отличается: ${definition.name}/${field}`);
    assert.deepEqual(record.runtimeConfig, definition.runtimeConfig);
  } else {
    record = await api('agents', definition);
    createdAgents++;
  }
  assert.ok(record.id);
  ledger.agentIds[key] = record.id;
  writeJson('installation.json', ledger);
}
const desired = processDefinition(ledger.agentIds);
let saved = existingProcesses.processes.find(p => p.name === PROCESS_NAME);
let createdProcess = false;
if (saved) {
  saved = await api(`processes/${saved.id}`);
  assert.deepEqual(apiNodes(saved.draftGraph.nodes), apiNodes(desired.graph.nodes), 'Существующий процесс отличается; автоматическая перезапись запрещена');
  assert.deepEqual(saved.draftGraph.edges, desired.graph.edges, 'Существующие связи отличаются');
} else {
  saved = await api('processes', desired);
  createdProcess = true;
}
ledger.processId = saved.id;
assert.ok(ledger.processId);
const readBack = await api(`processes/${saved.id}`);
assert.deepEqual(apiNodes(readBack.draftGraph.nodes), apiNodes(desired.graph.nodes));
assert.deepEqual(readBack.draftGraph.edges, desired.graph.edges);
assert.equal(readBack.publishedVersion, 0, 'Установщик работает только с неопубликованным черновиком');
const requirementsSupported = Array.isArray(readBack.draftGraph.requiredTools)
  && JSON.stringify([...readBack.draftGraph.requiredTools].sort()) === JSON.stringify([...desired.graph.requiredTools].sort())
  && Array.isArray(readBack.draftGraph.mcpToolAllowlist)
  && JSON.stringify([...readBack.draftGraph.mcpToolAllowlist].sort()) === JSON.stringify([...desired.graph.mcpToolAllowlist].sort())
  && readBack.draftGraph.allowPartialStart === false;
Object.assign(ledger, {
  status: 'draft', publishedVersion: 0, runtime: health.processRuntime.mode,
  nativeExecutionGuardsSupported: requirementsSupported,
  url: `${baseUrl}/#processes`,
  blockers: [
    ...(!requirementsSupported ? ['Запущенный runtime не поддерживает requiredTools/mcpToolAllowlist/allowPartialStart. Перед публикацией нужна совместимая проверенная версия и повторное сохранение полного графа.'] : []),
    ...(mcp.servers.length === 0 ? ['MCP tools kcops не подключены; integration-contract.json описывает требуемый адаптер.'] : []),
    'Целевой кластер, PostgreSQL, domain/TLS, Git, Argo CD и secret refs ещё не заданы.',
    `Модель ${model} доступна, но качество этого сценария и сквозное развёртывание ещё не квалифицированы.`,
  ],
  verifiedAt: new Date().toISOString(),
});
writeJson('installation.json', ledger);
writeJson('process.installed.json', readBack);
console.log(JSON.stringify({ createdAgents, createdProcess, agentCount: Object.keys(ledger.agentIds).length, processId: ledger.processId, nodeCount: readBack.draftGraph.nodes.length, status: ledger.status, url: ledger.url, nativeExecutionGuardsSupported: requirementsSupported, blockers: ledger.blockers }, null, 2));
