import { createHmac } from "node:crypto";

export const PRODUCT_USAGE_SCHEMA = "agat.product-usage.v1";
export const PRODUCT_USAGE_LIMITS = { events: 10_000, bytes: 8 * 1024 * 1024 } as const;
export const PRODUCT_USAGE_BLOCKERS = ["graph", "runtime", "model", "team", "knowledge", "embeddings", "tools", "scopes", "version", "trigger", "capacity"] as const;

export interface ProductUsageDefinition {
  packId: "internal-report" | null;
  packVersion: number | null;
  processId: string | null;
  processVersion: number | null;
  graphSha256: string | null;
}
interface ProductUsageBase {
  schemaVersion: typeof PRODUCT_USAGE_SCHEMA;
  eventId: string;
  projectId: string;
  taskId: string;
  /** Project-scoped pseudonym of the task participant, never the reviewing operator. */
  participantKey: string | null;
  scenario: "internal_report";
  sample: "real" | "test" | "demo" | "unclassified";
  cohortId: string | null;
  occurredAt: string;
  /** First receipt time assigned by the coordinator; immutable after persistence. */
  recordedAt: string;
  definition: ProductUsageDefinition;
  execution: { kind: "local" | "cloud" | "mixed" | "unknown"; requestedModelSetSha256: string | null };
}
type ProductUsageDetails =
  | { name: "scenario_started"; source: "client"; data: { entryPoint: "pack" | "process" } }
  | { name: "template_installed"; source: "server"; data: { manifestSha256: string } }
  | { name: "readiness_blocked"; source: "server"; data: { categories: (typeof PRODUCT_USAGE_BLOCKERS)[number][] } }
  | { name: "run_submitted"; source: "server"; data: { runId: string; replayOfRunId: string | null } }
  | { name: "run_completed"; source: "server"; data: { runId: string; status: "completed" | "failed" | "cancelled" } }
  | { name: "result_opened"; source: "client"; data: { runId: string; artifactId: string } }
  | { name: "outcome_recorded"; source: "server"; data: { runId: string; artifactId: string; decisionId: string; revision: number; decision: "accepted" | "rework_requested" | "rejected" } };
export type ProductUsageEvent = ProductUsageBase & ProductUsageDetails;
export type ProductUsageContractCode = "invalid_event" | "limit_exceeded" | "event_id_conflict" | "fact_conflict"
  | "task_binding_conflict" | "run_binding_conflict" | "artifact_binding_conflict" | "decision_revision_conflict";
export class ProductUsageContractError extends Error {
  constructor(readonly code: ProductUsageContractCode) {
    // Never include rejected values: a malformed field can contain a prompt, URL or secret.
    super(`Product usage contract: ${code}`); this.name = "ProductUsageContractError";
  }
}
function check(value: unknown, code: ProductUsageContractCode = "invalid_event"): asserts value {
  if (!value) throw new ProductUsageContractError(code);
}
function record(value: unknown, fields: string[]): Record<string, unknown> {
  check(value && typeof value === "object" && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value)));
  const item = value as Record<string, unknown>;
  check(Object.keys(item).length === fields.length && fields.every(key => Object.hasOwn(item, key)));
  return item;
}
function id(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{8}-[a-f0-9]{4}-[48][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(value);
}
function hash(value: unknown): value is string { return typeof value === "string" && /^[a-f0-9]{64}$/.test(value); }
function positive(value: unknown): value is number { return typeof value === "number" && Number.isSafeInteger(value) && value > 0; }
export function productUsageTimestamp(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)) return false;
  return Number.isFinite(Date.parse(value)) && new Date(value).toISOString() === value;
}
export function productUsageProject(value: unknown): value is string {
  return typeof value === "string" && /^[a-zA-Z0-9_-]{1,100}$/.test(value);
}

/** Format validation only. The caller must authenticate and resolve all server-owned fields. */
export function normalizeProductUsageEvent(raw: unknown): ProductUsageEvent {
  const item = record(raw, ["schemaVersion", "eventId", "projectId", "taskId", "participantKey", "scenario", "sample", "cohortId",
    "occurredAt", "recordedAt", "definition", "execution", "name", "source", "data"]);
  check(item.schemaVersion === PRODUCT_USAGE_SCHEMA && id(item.eventId) && productUsageProject(item.projectId) && id(item.taskId)
    && (item.participantKey === null || hash(item.participantKey)) && item.scenario === "internal_report"
    && typeof item.sample === "string" && ["real", "test", "demo", "unclassified"].includes(item.sample)
    && (item.cohortId === null || (item.sample === "real" && id(item.cohortId)))
    && productUsageTimestamp(item.occurredAt) && productUsageTimestamp(item.recordedAt));
  // Coordinator facts cannot be received before they occurred. Client clocks are untrusted.
  check(item.source !== "server" || item.occurredAt <= item.recordedAt);
  const definition = record(item.definition, ["packId", "packVersion", "processId", "processVersion", "graphSha256"]);
  check((definition.packId === null && definition.packVersion === null)
    || (definition.packId === "internal-report" && positive(definition.packVersion)));
  const pinned = id(definition.processId) && positive(definition.processVersion) && hash(definition.graphSha256);
  check(pinned || (definition.processId === null && definition.processVersion === null && definition.graphSha256 === null));
  const execution = record(item.execution, ["kind", "requestedModelSetSha256"]);
  check(typeof execution.kind === "string" && ["local", "cloud", "mixed", "unknown"].includes(execution.kind)
    && (execution.requestedModelSetSha256 === null || hash(execution.requestedModelSetSha256)));
  let data: ProductUsageEvent["data"];
  switch (item.name) {
    case "scenario_started": {
      const value = record(item.data, ["entryPoint"]);
      check(item.source === "client" && (value.entryPoint === "pack" || value.entryPoint === "process"));
      data = { entryPoint: value.entryPoint }; break;
    }
    case "template_installed": {
      const value = record(item.data, ["manifestSha256"]);
      check(item.source === "server" && hash(value.manifestSha256) && definition.packId !== null && pinned);
      data = { manifestSha256: value.manifestSha256 }; break;
    }
    case "readiness_blocked": {
      const value = record(item.data, ["categories"]);
      check(item.source === "server" && Array.isArray(value.categories) && value.categories.length > 0
        && value.categories.length <= PRODUCT_USAGE_BLOCKERS.length && new Set(value.categories).size === value.categories.length
        && value.categories.every(category => PRODUCT_USAGE_BLOCKERS.includes(category)));
      data = { categories: [...value.categories].sort() }; break;
    }
    case "run_submitted": {
      const value = record(item.data, ["runId", "replayOfRunId"]);
      check(item.source === "server" && pinned && id(value.runId) && (value.replayOfRunId === null || id(value.replayOfRunId))
        && value.runId !== value.replayOfRunId);
      data = { runId: value.runId, replayOfRunId: value.replayOfRunId }; break;
    }
    case "run_completed": {
      const value = record(item.data, ["runId", "status"]);
      check(item.source === "server" && pinned && id(value.runId) && typeof value.status === "string" && ["completed", "failed", "cancelled"].includes(value.status));
      data = { runId: value.runId, status: value.status as "completed" | "failed" | "cancelled" }; break;
    }
    case "result_opened": {
      const value = record(item.data, ["runId", "artifactId"]);
      check(item.source === "client" && pinned && id(value.runId) && id(value.artifactId));
      data = { runId: value.runId, artifactId: value.artifactId }; break;
    }
    case "outcome_recorded": {
      const value = record(item.data, ["runId", "artifactId", "decisionId", "revision", "decision"]);
      check(item.source === "server" && pinned && id(value.runId) && id(value.artifactId) && id(value.decisionId)
        && positive(value.revision) && value.revision <= 1000 && typeof value.decision === "string" && ["accepted", "rework_requested", "rejected"].includes(value.decision));
      data = { runId: value.runId, artifactId: value.artifactId, decisionId: value.decisionId, revision: value.revision,
        decision: value.decision as "accepted" | "rework_requested" | "rejected" }; break;
    }
    default: throw new ProductUsageContractError("invalid_event");
  }
  // Rebuild a canonical object instead of retaining arbitrary objects supplied by the caller.
  return { schemaVersion: PRODUCT_USAGE_SCHEMA, eventId: item.eventId, projectId: item.projectId, taskId: item.taskId,
    participantKey: item.participantKey, scenario: item.scenario, sample: item.sample, cohortId: item.cohortId,
    occurredAt: item.occurredAt, recordedAt: item.recordedAt,
    definition: { packId: definition.packId, packVersion: definition.packVersion, processId: definition.processId,
      processVersion: definition.processVersion, graphSha256: definition.graphSha256 },
    execution: { kind: execution.kind, requestedModelSetSha256: execution.requestedModelSetSha256 },
    name: item.name, source: item.source, data } as ProductUsageEvent;
}

/** This is pseudonymization, not anonymization. A shared API token is not an individual. */
export function productUsageParticipantKey(projectId: string, verifiedSubject: string, secret: Uint8Array): string {
  check(productUsageProject(projectId) && typeof verifiedSubject === "string" && verifiedSubject.trim().length > 0
    && Buffer.byteLength(verifiedSubject) <= 4096 && secret instanceof Uint8Array && secret.byteLength >= 32);
  return createHmac("sha256", secret).update(JSON.stringify([PRODUCT_USAGE_SCHEMA, projectId, verifiedSubject])).digest("hex");
}

export function productUsageFactKey(event: ProductUsageEvent): string {
  const prefix = [event.projectId, event.name];
  switch (event.name) {
    case "scenario_started": return JSON.stringify([...prefix, event.taskId]);
    case "template_installed": return JSON.stringify([...prefix, event.taskId, event.definition.packVersion, event.data.manifestSha256]);
    case "readiness_blocked": return JSON.stringify([...prefix, event.eventId]);
    case "run_submitted": case "run_completed": return JSON.stringify([...prefix, event.data.runId]);
    case "result_opened": return JSON.stringify([...prefix, event.taskId, event.data.runId, event.data.artifactId]);
    case "outcome_recorded": return JSON.stringify([...prefix, event.data.decisionId]);
  }
}
function eventIntent(event: ProductUsageEvent, fact = false): string {
  const { recordedAt: _recorded, eventId, occurredAt, ...rest } = event;
  return JSON.stringify({ ...rest, ...(!fact ? { eventId } : {}),
    ...(!fact || event.source === "server" ? { occurredAt } : {}) });
}

/** No I/O, persistence, collector export or inference; safe for trusted bounded snapshots. */
export function dedupeProductUsageEvents(raw: unknown): { events: ProductUsageEvent[]; duplicateCount: number } {
  check(Array.isArray(raw) && raw.length <= PRODUCT_USAGE_LIMITS.events, "limit_exceeded");
  const identities = new Map<string, string>(); const facts = new Map<string, ProductUsageEvent>();
  const tasks = new Map<string, string>(); const runs = new Map<string, string>();
  const artifacts = new Map<string, string>(); const revisions = new Map<string, string>();
  const replayLinks: { projectId: string; taskId: string; parentRunId: string }[] = [];
  let bytes = 0; let duplicateCount = 0;
  const bind = (map: Map<string, string>, key: string, value: string, code: ProductUsageContractCode) => {
    check(!map.has(key) || map.get(key) === value, code); map.set(key, value);
  };
  for (const value of raw) {
    const event = normalizeProductUsageEvent(value);
    bytes += Buffer.byteLength(JSON.stringify(event)); check(bytes <= PRODUCT_USAGE_LIMITS.bytes, "limit_exceeded");
    bind(identities, JSON.stringify([event.projectId, event.eventId]), eventIntent(event), "event_id_conflict");
    bind(tasks, JSON.stringify([event.projectId, event.taskId]), JSON.stringify([event.participantKey, event.scenario, event.sample, event.cohortId]), "task_binding_conflict");
    if ("runId" in event.data) {
      bind(runs, JSON.stringify([event.projectId, event.data.runId]), JSON.stringify([event.taskId, event.definition]), "run_binding_conflict");
    }
    if ("artifactId" in event.data) bind(artifacts, JSON.stringify([event.projectId, event.data.artifactId]), event.data.runId, "artifact_binding_conflict");
    if (event.name === "run_submitted" && event.data.replayOfRunId !== null) replayLinks.push({ projectId: event.projectId, taskId: event.taskId, parentRunId: event.data.replayOfRunId });
    if (event.name === "outcome_recorded") bind(revisions, JSON.stringify([event.projectId, event.data.artifactId, event.data.revision]), event.data.decisionId, "decision_revision_conflict");
    const key = productUsageFactKey(event); const prior = facts.get(key);
    if (prior) {
      check(eventIntent(prior, true) === eventIntent(event, true), "fact_conflict"); duplicateCount++;
      // Repeated client observations keep the earliest coordinator receipt, never a client clock.
      if (event.recordedAt < prior.recordedAt || (event.recordedAt === prior.recordedAt && event.eventId < prior.eventId)) facts.set(key, event);
    } else facts.set(key, event);
  }
  for (const link of replayLinks) {
    const parent = runs.get(JSON.stringify([link.projectId, link.parentRunId]));
    check(!parent || JSON.parse(parent)[0] === link.taskId, "run_binding_conflict");
  }
  return { events: [...facts.values()].sort((a, b) => a.recordedAt.localeCompare(b.recordedAt) || a.projectId.localeCompare(b.projectId) || a.eventId.localeCompare(b.eventId)), duplicateCount };
}
