import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  loadConfig,
  validateTemporalCoordinatorConfig,
} from "../src/config.js";
import {
  createDatabaseProcessRuntime,
  normalizeProcessScheduleInput,
  processScheduleId,
  temporalConnectionOptions,
} from "../src/process-runtime.js";

describe("Temporal production hardening", () => {
  it("rejects malformed Temporal environment values before startup", () => {
    const previousTls = process.env.AGAT_TEMPORAL_TLS;
    const previousApiKey = process.env.AGAT_TEMPORAL_API_KEY;
    try {
      process.env.AGAT_TEMPORAL_TLS = "sometimes";
      assert.throws(() => loadConfig(), /Некорректное boolean-значение/);
      process.env.AGAT_TEMPORAL_TLS = "true";
      process.env.AGAT_TEMPORAL_API_KEY = "secret\nheader";
      assert.throws(() => loadConfig(), /AGAT_TEMPORAL_API_KEY.*управляющие символы/);
    } finally {
      if (previousTls === undefined) delete process.env.AGAT_TEMPORAL_TLS;
      else process.env.AGAT_TEMPORAL_TLS = previousTls;
      if (previousApiKey === undefined) delete process.env.AGAT_TEMPORAL_API_KEY;
      else process.env.AGAT_TEMPORAL_API_KEY = previousApiKey;
    }
  });

  it("fails closed for incomplete production transport and unsafe PostgreSQL roles", () => {
    const base = loadConfig();
    assert.throws(
      () => validateTemporalCoordinatorConfig({
        ...base,
        temporalEnabled: true,
        temporalInternalToken: "internal",
        temporalTarget: "cloud",
        temporalTls: true,
        temporalApiKey: "",
      }),
      /AGAT_TEMPORAL_API_KEY/,
    );
    assert.throws(
      () => validateTemporalCoordinatorConfig({ ...base, stateStoreDriver: "postgresql" }),
      /AGAT_POSTGRES_URL.*AGAT_POSTGRES_TENANT_URL/,
    );
    assert.throws(
      () => validateTemporalCoordinatorConfig({
        ...base,
        stateStoreDriver: "postgresql",
        postgresUrl: "postgresql://agat:system@127.0.0.1:5432/agat",
        postgresTenantUrl: "postgresql://agat:tenant@127.0.0.1:5432/agat",
        requireSignedWorkerReleases: false,
      }),
      /разные least-privilege roles/,
    );
    assert.doesNotThrow(
      () => validateTemporalCoordinatorConfig({
        ...base,
        stateStoreDriver: "postgresql",
        postgresUrl: "postgresql://agat_system:system@127.0.0.1:5432/agat",
        postgresTenantUrl: "postgresql://agat_tenant:tenant@127.0.0.1:5432/agat",
        requireSignedWorkerReleases: false,
      }),
    );
  });

  it("builds a secret-bearing connection descriptor without leaking it into the runtime snapshot", () => {
    const base = loadConfig();
    const config = {
      ...base,
      temporalEnabled: true,
      temporalInternalToken: "internal",
      temporalTarget: "cloud" as const,
      temporalAddress: "namespace.account.tmprl.cloud:7233",
      temporalTls: true,
      temporalApiKey: "cloud-secret",
    };
    validateTemporalCoordinatorConfig(config);
    const options = temporalConnectionOptions(config);
    assert.equal(options.address, "namespace.account.tmprl.cloud:7233");
    assert.equal(options.tls, true);
    assert.equal(options.apiKey, "cloud-secret");

    const snapshot = createDatabaseProcessRuntime().snapshot();
    assert.deepEqual(snapshot, {
      mode: "database",
      connected: true,
      namespace: null,
      taskQueue: null,
      target: null,
      tls: false,
    });
    assert.equal(JSON.stringify(snapshot).includes("cloud-secret"), false);
  });

  it("normalizes bounded project-scoped schedules and stable Temporal IDs", () => {
    assert.equal(processScheduleId("process_42"), "agat-process-schedule-process_42");
    const schedule = normalizeProcessScheduleInput({
      input: "  Собери ежедневный отчёт  ",
      everySeconds: 3_600,
      priority: 80,
      paused: true,
      knowledgeCollectionIds: ["handbook", "handbook", "incidents"],
    }, "process_42", "project-a");
    assert.deepEqual(schedule, {
      processId: "process_42",
      projectId: "project-a",
      input: "Собери ежедневный отчёт",
      kind: "interval",
      everySeconds: 3_600,
      cronExpression: "",
      calendar: null,
      timezone: "UTC",
      priority: 80,
      paused: true,
      knowledgeCollectionIds: ["handbook", "incidents"],
    });
    assert.throws(
      () => normalizeProcessScheduleInput({ input: "x", everySeconds: 59 }, "process_42", "project-a"),
      /everySeconds/,
    );
    assert.deepEqual(normalizeProcessScheduleInput({
      input: "cron",
      kind: "cron",
      cronExpression: "0 9 * * MON-FRI",
      timezone: "Europe/Moscow",
      priority: 50,
      knowledgeCollectionIds: [],
    }, "process_42", "project-a"), {
      processId: "process_42",
      projectId: "project-a",
      input: "cron",
      kind: "cron",
      everySeconds: 0,
      cronExpression: "0 9 * * MON-FRI",
      calendar: null,
      timezone: "Europe/Moscow",
      priority: 50,
      paused: false,
      knowledgeCollectionIds: [],
    });
    assert.throws(() => processScheduleId("../escape"), /Некорректный ID/);
  });
});
