export type ScenarioTrigger = { kind: "manual" } | { kind: "schedule" } | { kind: "webhook"; webhookId: string };

export interface ScenarioPreflightInput {
  version?: number;
  knowledgeCollectionIds?: string[];
  trigger?: ScenarioTrigger;
}

export interface ScenarioPreflightContext {
  runtime: { mode: "database" | "temporal"; connected: boolean };
  mcpEnabled: boolean;
  sandbox?: { available: boolean; networkPolicyEnforced: boolean } | null;
  schedule?: { paused: boolean; nextActionTimes: string[]; knowledgeCollectionIds?: string[] } | null;
  scheduleError?: boolean;
  /** Set only by authenticated trigger handlers, never by the public start payload. */
  executionTrigger?: ScenarioTrigger;
}

export interface ScenarioBlocker {
  code: string;
  category: "graph" | "runtime" | "model" | "team" | "knowledge" | "embeddings" | "tools" | "scopes" | "version" | "trigger" | "capacity";
  /** A queue blocker also prevents immediate execution. */
  blocks: "queue" | "run";
  message: string;
  recovery: { label: string; href: string };
}

export interface ScenarioPreflight {
  checkedAt: string;
  processId: string | null;
  version: number | null;
  saved: boolean;
  queueable: boolean;
  runnableNow: boolean;
  scenarioVerified: boolean;
  verification: { instanceId: string; runId: string; completedAt: string; href: string } | null;
  fingerprint: string;
  blockers: ScenarioBlocker[];
  notices: string[];
}

export function recoveryLink(view: string, target: Record<string, string> = {}): string {
  const query = new URLSearchParams(target).toString();
  return `#${view}${query ? `?${query}` : ""}`;
}

export function scenarioTrigger(value: unknown): ScenarioTrigger {
  if (value === undefined) return { kind: "manual" };
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Trigger должен быть объектом");
  const trigger = value as Record<string, unknown>;
  if (trigger.kind === "manual" || trigger.kind === "schedule") return { kind: trigger.kind };
  if (trigger.kind === "webhook" && typeof trigger.webhookId === "string" && trigger.webhookId.length > 0 && trigger.webhookId.length <= 100) {
    return { kind: "webhook", webhookId: trigger.webhookId };
  }
  throw new Error("Выберите manual, schedule или webhook с webhookId");
}

export class ScenarioPreflightError extends Error {
  constructor(readonly preflight: ScenarioPreflight) {
    super(preflight.blockers.map((blocker) => blocker.message).join(" "));
  }
}
