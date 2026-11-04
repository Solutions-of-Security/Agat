import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { describe, it } from "node:test";

import { dedupeProductUsageEvents, normalizeProductUsageEvent, PRODUCT_USAGE_LIMITS, PRODUCT_USAGE_SCHEMA,
  ProductUsageContractError, productUsageParticipantKey, type ProductUsageContractCode, type ProductUsageEvent } from "../src/product-usage.js";

const taskId = randomUUID(); const runId = randomUUID(); const artifactId = randomUUID();
const processId = randomUUID(); const participant = "a".repeat(64);
const at = "2026-10-25T10:00:00.000Z";
const later = "2026-10-25T10:01:00.000Z";
const definition = { packId: "internal-report", packVersion: 2, processId, processVersion: 3, graphSha256: "b".repeat(64) };
function event(name: ProductUsageEvent["name"], data?: unknown, overrides: Record<string, unknown> = {}): ProductUsageEvent {
  const payloads = {
    scenario_started: { entryPoint: "process" }, template_installed: { manifestSha256: "c".repeat(64) },
    readiness_blocked: { categories: ["model", "capacity"] }, run_submitted: { runId, replayOfRunId: null },
    run_completed: { runId, status: "completed" }, result_opened: { runId, artifactId },
    outcome_recorded: { runId, artifactId, decisionId: randomUUID(), revision: 1, decision: "accepted" },
  };
  return normalizeProductUsageEvent({ schemaVersion: PRODUCT_USAGE_SCHEMA, eventId: randomUUID(), projectId: "default", taskId,
    participantKey: participant, scenario: "internal_report", sample: "test", cohortId: null, occurredAt: at, recordedAt: at,
    definition, execution: { kind: "local", requestedModelSetSha256: "d".repeat(64) }, name,
    source: name === "scenario_started" || name === "result_opened" ? "client" : "server", data: data ?? payloads[name], ...overrides });
}
function fails(run: () => unknown, code: ProductUsageContractCode): void {
  assert.throws(run, error => error instanceof ProductUsageContractError && error.code === code);
}

describe("UX-306 metadata-only event contract", () => {
  it("keeps seven checkpoint kinds distinct from traces and reconstructs canonical data", () => {
    const kinds: ProductUsageEvent["name"][] = ["scenario_started", "template_installed", "readiness_blocked", "run_submitted", "run_completed", "result_opened", "outcome_recorded"];
    const original = kinds.map(name => event(name));
    const result = dedupeProductUsageEvents(original);
    assert.equal(result.events.length, 7); assert.equal(result.duplicateCount, 0);
    assert.deepEqual(result.events.find(item => item.name === "readiness_blocked")?.data, { categories: ["capacity", "model"] });
    const raw = structuredClone(original[0]!);
    const clean = normalizeProductUsageEvent(raw);
    raw.definition.processVersion = 99;
    assert.equal(clean.definition.processVersion, 3);
  });

  it("rejects private/free-text extensions at every boundary without echoing their contents", () => {
    const base = event("outcome_recorded"); const secret = "private prompt https://secret.example/source invoice.docx";
    const packets: unknown[] = [
      { ...base, prompt: secret }, { ...base, output: secret }, { ...base, error: secret },
      { ...base, data: { ...base.data, comment: secret } }, { ...base, definition: { ...definition, name: secret } },
      { ...base, execution: { ...base.execution, model: secret } }, { ...base, participantKey: secret },
      { ...base, projectId: secret }, { ...base, taskId: secret },
      { ...event("readiness_blocked"), data: { categories: [secret] } },
    ];
    for (const packet of packets) {
      assert.throws(() => normalizeProductUsageEvent(packet), error => error instanceof ProductUsageContractError
        && error.code === "invalid_event" && !error.message.includes(secret));
    }
    fails(() => normalizeProductUsageEvent(Object.assign(Object.create({ output: secret }), base)), "invalid_event");
    fails(() => normalizeProductUsageEvent({ ...base, data: { ...base.data, url: secret } }), "invalid_event");
    fails(() => normalizeProductUsageEvent({ ...base, sample: { toString: () => "test" } }), "invalid_event");
  });

  it("requires server provenance labels for durable facts, without treating labels as authentication", () => {
    for (const name of ["template_installed", "readiness_blocked", "run_submitted", "run_completed", "outcome_recorded"] as const) {
      fails(() => normalizeProductUsageEvent({ ...event(name), source: "client" }), "invalid_event");
    }
    fails(() => normalizeProductUsageEvent({ ...event("result_opened"), source: "server" }), "invalid_event");
    fails(() => normalizeProductUsageEvent({ ...event("run_completed"), occurredAt: later }), "invalid_event");
    assert.equal(event("scenario_started", undefined, { occurredAt: later }).occurredAt, later); // Client clock is not a duration source.
  });

  it("represents missing identities explicitly and keeps demo/test out of real cohorts", () => {
    const unknown = event("scenario_started", undefined, { participantKey: null, sample: "unclassified", definition: {
      packId: null, packVersion: null, processId: null, processVersion: null, graphSha256: null,
    }, execution: { kind: "unknown", requestedModelSetSha256: null } });
    assert.equal(unknown.participantKey, null); assert.equal(unknown.definition.processVersion, null);
    assert.equal(event("scenario_started", undefined, { sample: "real", cohortId: randomUUID() }).sample, "real");
    for (const sample of ["test", "demo", "unclassified"]) fails(() => event("scenario_started", undefined, { sample, cohortId: randomUUID() }), "invalid_event");
    fails(() => event("run_submitted", undefined, { definition: { ...definition, graphSha256: null } }), "invalid_event");
    fails(() => event("scenario_started", undefined, { occurredAt: "2026-02-30T00:00:00.000Z" }), "invalid_event");
    fails(() => event("scenario_started", undefined, { recordedAt: "2026-10-25T13:00:00+03:00" }), "invalid_event");
    fails(() => event("scenario_started", undefined, { schemaVersion: "agat.product-usage.v2" }), "invalid_event");
  });

  it("deduplicates response-loss retries and semantic facts across IDs in any input order", () => {
    const start = event("scenario_started"); const completion = event("run_completed");
    const opened = event("result_opened");
    const retry = { ...completion, recordedAt: later };
    const secondServerId = { ...completion, eventId: randomUUID(), recordedAt: later };
    const secondOpening = { ...opened, eventId: randomUUID(), occurredAt: later, recordedAt: later };
    const input = [retry, secondOpening, start, secondServerId, completion, opened];
    const forward = dedupeProductUsageEvents(input); const reverse = dedupeProductUsageEvents([...input].reverse());
    assert.deepEqual(forward, reverse);
    assert.equal(forward.duplicateCount, 3); assert.equal(forward.events.length, 3);
    assert.equal(forward.events.find(item => item.name === "run_completed")?.recordedAt, at);
    assert.equal(forward.events.find(item => item.name === "result_opened")?.eventId, opened.eventId);
  });

  it("rejects changed intent, receipt identity collisions and conflicting terminal facts", () => {
    const start = event("scenario_started");
    fails(() => dedupeProductUsageEvents([start, { ...start, data: { entryPoint: "pack" } }]), "event_id_conflict");
    const completed = event("run_completed");
    fails(() => dedupeProductUsageEvents([completed, { ...completed, eventId: randomUUID(), data: { runId, status: "failed" } }]), "fact_conflict");
    fails(() => dedupeProductUsageEvents([completed, { ...completed, eventId: randomUUID(), occurredAt: later, recordedAt: later }]), "fact_conflict");
    const decision = event("outcome_recorded");
    fails(() => dedupeProductUsageEvents([decision, event("outcome_recorded")]), "decision_revision_conflict");
  });

  it("binds runs, artifacts and the task participant across reviews and replay", () => {
    const start = event("scenario_started"); const completion = event("run_completed");
    fails(() => dedupeProductUsageEvents([start, event("result_opened", undefined, { participantKey: "e".repeat(64) })]), "task_binding_conflict");
    fails(() => dedupeProductUsageEvents([start, event("run_submitted", undefined, { sample: "demo" })]), "task_binding_conflict");
    fails(() => dedupeProductUsageEvents([completion, event("result_opened", undefined, { taskId: randomUUID() })]), "run_binding_conflict");
    fails(() => dedupeProductUsageEvents([completion, event("result_opened", undefined, { definition: { ...definition, processVersion: 4 } })]), "run_binding_conflict");
    fails(() => dedupeProductUsageEvents([event("result_opened"), event("result_opened", { runId: randomUUID(), artifactId })]), "artifact_binding_conflict");
    const replay = event("run_submitted", { runId: randomUUID(), replayOfRunId: runId }, { definition: { ...definition, processVersion: 4 } });
    assert.equal(dedupeProductUsageEvents([completion, replay]).events.length, 2); // A reviewed correction can change the frozen version.
    fails(() => dedupeProductUsageEvents([{ ...replay, taskId: randomUUID() }, completion]), "run_binding_conflict");
    fails(() => event("run_submitted", { runId, replayOfRunId: runId }), "invalid_event");
  });

  it("retains append-only rework and acceptance revisions and namespaces project IDs", () => {
    const first = event("outcome_recorded", { runId, artifactId, decisionId: randomUUID(), revision: 1, decision: "rework_requested" });
    const next = event("outcome_recorded", { runId, artifactId, decisionId: randomUUID(), revision: 2, decision: "accepted" }, { occurredAt: later, recordedAt: later });
    const result = dedupeProductUsageEvents([first, next, { ...first, projectId: "another-project" }]);
    assert.equal(result.events.length, 3); assert.equal(result.duplicateCount, 0);
  });

  it("uses keyed project-scoped pseudonyms without accepting short secrets or leaking subjects", () => {
    const secret = Buffer.alloc(32, 7); const subject = "verified-person@example.test";
    const key = productUsageParticipantKey("default", subject, secret);
    assert.match(key, /^[a-f0-9]{64}$/);
    assert.equal(key, productUsageParticipantKey("default", subject, secret));
    assert.notEqual(key, productUsageParticipantKey("other-project", subject, secret));
    assert.notEqual(key, productUsageParticipantKey("default", subject, Buffer.alloc(32, 8)));
    assert.equal(JSON.stringify(event("scenario_started", undefined, { participantKey: key })).includes(subject), false);
    fails(() => productUsageParticipantKey("default", subject, Buffer.alloc(31)), "invalid_event");
  });

  it("bounds untrusted streams before aggregation and rejects non-JSON structures", () => {
    fails(() => dedupeProductUsageEvents({}), "limit_exceeded");
    fails(() => dedupeProductUsageEvents(Array(PRODUCT_USAGE_LIMITS.events + 1).fill(event("scenario_started"))), "limit_exceeded");
    const packet = event("scenario_started", undefined, { projectId: "p".repeat(100) });
    assert.ok(Buffer.byteLength(JSON.stringify(packet)) * PRODUCT_USAGE_LIMITS.events > PRODUCT_USAGE_LIMITS.bytes);
    fails(() => dedupeProductUsageEvents(Array(PRODUCT_USAGE_LIMITS.events).fill(packet)), "limit_exceeded");
    fails(() => normalizeProductUsageEvent(null), "invalid_event");
    fails(() => normalizeProductUsageEvent([]), "invalid_event");
    assert.deepEqual(dedupeProductUsageEvents([]), { events: [], duplicateCount: 0 });
  });
});
