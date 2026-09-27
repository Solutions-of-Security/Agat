import assert from "node:assert/strict";
import test from "node:test";
import { knowledgeSearchPoolAllocation, loadConfig } from "../src/config.js";
import { evaluatePostgresConnectionBudget } from "../src/postgres-admission.js";

function config(overrides: Record<string, string | undefined> = {}) {
  const values = { AGAT_STATE_STORE_DRIVER: "sqlite", AGAT_ARTIFACT_STORE_DRIVER: undefined,
    AGAT_POSTGRES_POOL_MAX: "4", AGAT_KNOWLEDGE_SEARCH_EXECUTION: undefined,
    AGAT_KNOWLEDGE_SEARCH_MAX_PENDING: undefined, AGAT_KNOWLEDGE_SEARCH_TIMEOUT_MS: undefined, ...overrides };
  const previous = Object.fromEntries(Object.keys(values).map(key => [key, process.env[key]]));
  try {
    for (const [key, value] of Object.entries(values)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; }
    return loadConfig();
  } finally {
    for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; }
  }
}

test("isolated retrieval is an explicit PostgreSQL opt-in with a reserved pool budget", () => {
  const defaults = config();
  assert.equal(defaults.knowledgeSearchExecution, "sync");
  assert.equal(defaults.knowledgeSearchMaxPending, 4); assert.equal(defaults.knowledgeSearchTimeoutMs, 30_000);
  assert.throws(() => config({ AGAT_KNOWLEDGE_SEARCH_EXECUTION: "isolated" }), /PostgreSQL/);
  for (const mode of ["", "async", "unknown"]) assert.throws(() => config({ AGAT_KNOWLEDGE_SEARCH_EXECUTION: mode }), /AGAT_KNOWLEDGE_SEARCH_EXECUTION/);
  const isolated = { AGAT_STATE_STORE_DRIVER: "postgresql", AGAT_KNOWLEDGE_SEARCH_EXECUTION: "isolated" };
  assert.throws(() => config({ ...isolated, AGAT_POSTGRES_POOL_MAX: "1" }), /AGAT_POSTGRES_POOL_MAX/);
  assert.equal(config({ ...isolated, AGAT_POSTGRES_POOL_MAX: "2" }).knowledgeSearchExecution, "isolated");
  assert.deepEqual(knowledgeSearchPoolAllocation("sync", 1), { coordinator: 1, retrieval: 0 });
});

test("retrieval queue and deadline settings reject malformed values and enforce their hard bounds", () => {
  for (const value of ["", "0", "65", "1.5", "1e1", "0x10", "4garbage"]) {
    assert.throws(() => config({ AGAT_KNOWLEDGE_SEARCH_MAX_PENDING: value }), /AGAT_KNOWLEDGE_SEARCH_MAX_PENDING/);
  }
  for (const value of ["", "99", "60001", "1e4", "100.5", "100ms"]) {
    assert.throws(() => config({ AGAT_KNOWLEDGE_SEARCH_TIMEOUT_MS: value }), /AGAT_KNOWLEDGE_SEARCH_TIMEOUT_MS/);
  }
  for (const value of [1, 64]) assert.equal(config({ AGAT_KNOWLEDGE_SEARCH_MAX_PENDING: String(value) }).knowledgeSearchMaxPending, value);
  for (const value of [100, 60_000]) assert.equal(config({ AGAT_KNOWLEDGE_SEARCH_TIMEOUT_MS: String(value) }).knowledgeSearchTimeoutMs, value);
});

test("coordinator and retrieval pools together fit the already admitted per-role capacity", () => {
  for (let poolMax = 2; poolMax <= 32; poolMax++) {
    const allocated = knowledgeSearchPoolAllocation("isolated", poolMax);
    assert.equal(allocated.coordinator + allocated.retrieval, poolMax);
    assert.equal(allocated.retrieval, 1); assert.ok(allocated.coordinator >= 1);
  }
  const allocated = knowledgeSearchPoolAllocation("isolated", 5);
  const result = evaluatePostgresConnectionBudget({ maxConnections: 100, superuserReservedConnections: 3,
    reservedConnections: 0, currentConnections: 4, expectedReplicas: 2,
    poolMax: allocated.coordinator + allocated.retrieval, budgetPercent: 80, externalReserve: 10,
    runtimeRoleConnectionLimit: 10, tenantRoleConnectionLimit: 10 });
  assert.equal(result.plannedRuntimeConnections, 10); assert.equal(result.plannedTenantConnections, 10);
  assert.equal(result.plannedTotalConnections, 20); assert.equal(result.pass, true);
});
