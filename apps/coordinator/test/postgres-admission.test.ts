import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { evaluatePostgresConnectionBudget } from "../src/postgres-admission.js";

describe("PostgreSQL connection admission", () => {
  it("admits both system and tenant pools with explicit operational headroom", () => {
    const result = evaluatePostgresConnectionBudget({
      maxConnections: 100,
      superuserReservedConnections: 3,
      reservedConnections: 0,
      currentConnections: 4,
      expectedReplicas: 3,
      poolMax: 5,
      budgetPercent: 80,
      externalReserve: 10,
      runtimeRoleConnectionLimit: 20,
      tenantRoleConnectionLimit: 20,
    });
    assert.equal(result.plannedTotalConnections, 30);
    assert.equal(result.availableForNewAgatConnections, 63);
    assert.equal(result.pass, true);
  });

  it("rejects aggregate oversubscription even when each individual role limit fits", () => {
    const result = evaluatePostgresConnectionBudget({
      maxConnections: 30,
      superuserReservedConnections: 3,
      reservedConnections: 0,
      currentConnections: 3,
      expectedReplicas: 4,
      poolMax: 4,
      budgetPercent: 80,
      externalReserve: 4,
      runtimeRoleConnectionLimit: 20,
      tenantRoleConnectionLimit: 20,
    });
    assert.equal(result.pass, false);
    assert.match(result.reasons.join(" "), /connection budget/);
  });

  it("rejects a role CONNECTION LIMIT below replica pool demand", () => {
    const result = evaluatePostgresConnectionBudget({
      maxConnections: 200,
      superuserReservedConnections: 3,
      reservedConnections: 0,
      currentConnections: 2,
      expectedReplicas: 3,
      poolMax: 4,
      budgetPercent: 80,
      externalReserve: 5,
      runtimeRoleConnectionLimit: 8,
      tenantRoleConnectionLimit: 12,
    });
    assert.equal(result.pass, false);
    assert.match(result.reasons.join(" "), /runtime role CONNECTION LIMIT/);
  });
});
