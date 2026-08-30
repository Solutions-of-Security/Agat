import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  canonicalMigrationRow,
  migrationTableOrder,
} from "../src/sqlite-postgres-migrator.js";

function table(name: string, dependencies: string[] = []) {
  return {
    name,
    columns: [{ name: "id", type: "TEXT", primaryKeyPosition: 1 }],
    primaryKey: ["id"],
    dependencies,
  };
}

describe("SQLite to PostgreSQL migration contract", () => {
  it("canonicalizes integer and binary representations across drivers", () => {
    const columns = [
      { name: "id", type: "BIGINT", primaryKeyPosition: 1 },
      { name: "content", type: "BLOB", primaryKeyPosition: 0 },
      { name: "label", type: "TEXT", primaryKeyPosition: 0 },
    ];
    const sqlite = canonicalMigrationRow(columns, {
      id: 42n,
      content: new Uint8Array([0, 1, 2, 255]),
      label: "stable",
    });
    const postgres = canonicalMigrationRow(columns, {
      id: "42",
      content: Buffer.from([0, 1, 2, 255]),
      label: "stable",
    });
    assert.equal(sqlite, postgres);
  });

  it("orders parent tables before dependent tables deterministically", () => {
    assert.deepEqual(migrationTableOrder([
      table("artifacts", ["runs"]),
      table("stages", ["runs", "agents"]),
      table("agents", ["projects"]),
      table("runs", ["projects"]),
      table("projects"),
    ]), ["projects", "agents", "runs", "artifacts", "stages"]);
  });

  it("fails closed on cross-table dependency cycles", () => {
    assert.throws(() => migrationTableOrder([
      table("left", ["right"]),
      table("right", ["left"]),
    ]), /Циклические межтабличные foreign keys/);
  });
});
