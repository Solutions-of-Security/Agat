import { createHash } from "node:crypto";

import type {
  McpPolicyDecision,
  McpPolicyDocument,
  McpPolicyEffect,
  McpPolicyRule,
  McpRiskDecision,
  McpRiskTier,
  McpToolPolicy,
  McpToolRisk,
} from "./types.js";

const RISKS: McpToolRisk[] = ["read", "write", "destructive", "unknown"];
const TIERS = new Set<McpRiskTier>(["low", "elevated", "high", "critical"]);
const EFFECTS = new Set<McpPolicyEffect>(["allow", "approval", "deny"]);
const RULE_ID = /^[a-z][a-z0-9_-]{0,63}$/;
const NAMESPACE_PATTERN = /^(?:\*|[a-z][a-z0-9_]{0,23})$/;
const TOOL_PATTERN = /^[A-Za-z0-9_.*?-]{1,256}$/;

export const DEFAULT_MCP_POLICY: McpPolicyDocument = {
  schemaVersion: 1,
  name: "baseline-enterprise",
  defaults: {
    read: { tier: "low", effect: "allow", approvals: 0 },
    write: { tier: "elevated", effect: "approval", approvals: 1 },
    destructive: { tier: "critical", effect: "approval", approvals: 2 },
    unknown: { tier: "high", effect: "approval", approvals: 1 },
  },
  rules: [],
};

function requiredText(value: unknown, field: string, maxLength: number): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${field} обязателен`);
  const result = value.trim();
  if (result.length > maxLength) throw new Error(`${field}: максимум ${maxLength} символов`);
  return result;
}

function stringList(
  value: unknown,
  field: string,
  validator: (item: string) => boolean,
  fallback: string[],
): string[] {
  const source = value === undefined ? fallback : value;
  if (!Array.isArray(source) || source.length === 0 || source.length > 100) {
    throw new Error(`${field} должен быть непустым массивом (не более 100 значений)`);
  }
  const normalized = [...new Set(source.map((item) => {
    if (typeof item !== "string") throw new Error(`${field} содержит нестроковое значение`);
    return item.trim();
  }))].sort();
  if (normalized.some((item) => !validator(item))) throw new Error(`${field} содержит недопустимый шаблон`);
  return normalized;
}

function normalizeDecision(value: unknown, field: string, risk?: McpToolRisk): McpRiskDecision {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${field} должен быть объектом`);
  const raw = value as Record<string, unknown>;
  if (!TIERS.has(raw.tier as McpRiskTier)) throw new Error(`${field}.tier недопустим`);
  if (!EFFECTS.has(raw.effect as McpPolicyEffect)) throw new Error(`${field}.effect недопустим`);
  if (![0, 1, 2].includes(Number(raw.approvals)) || !Number.isInteger(Number(raw.approvals))) {
    throw new Error(`${field}.approvals должен быть 0, 1 или 2`);
  }
  const decision: McpRiskDecision = {
    tier: raw.tier as McpRiskTier,
    effect: raw.effect as McpPolicyEffect,
    approvals: Number(raw.approvals) as 0 | 1 | 2,
  };
  if (decision.effect === "allow" && decision.approvals !== 0) {
    throw new Error(`${field}: allow требует approvals=0`);
  }
  if (decision.effect === "approval" && decision.approvals === 0) {
    throw new Error(`${field}: approval требует approvals=1 или 2`);
  }
  if (decision.effect === "deny" && decision.approvals !== 0) {
    throw new Error(`${field}: deny требует approvals=0`);
  }
  if ((risk === "destructive" || decision.tier === "critical")
    && decision.effect !== "deny"
    && (decision.effect !== "approval" || decision.approvals !== 2)) {
    throw new Error(`${field}: critical/destructive side effect требует deny или two-person approval`);
  }
  return decision;
}

function normalizeRule(value: unknown, position: number): McpPolicyRule {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`rules[${position}] должен быть объектом`);
  }
  const raw = value as Record<string, unknown>;
  const id = requiredText(raw.id, `rules[${position}].id`, 64);
  if (!RULE_ID.test(id)) throw new Error(`rules[${position}].id имеет недопустимый формат`);
  const match = raw.match && typeof raw.match === "object" && !Array.isArray(raw.match)
    ? raw.match as Record<string, unknown>
    : {};
  const risks = stringList(
    match.risks,
    `rules[${position}].match.risks`,
    (item) => RISKS.includes(item as McpToolRisk),
    RISKS,
  ) as McpToolRisk[];
  const decision = normalizeDecision(raw.decision, `rules[${position}].decision`);
  if (risks.includes("destructive") && decision.effect !== "deny"
    && (decision.effect !== "approval" || decision.approvals !== 2)) {
    throw new Error(`rules[${position}]: правило для destructive требует deny или two-person approval`);
  }
  return {
    id,
    description: typeof raw.description === "string" ? raw.description.trim().slice(0, 300) : "",
    match: {
      serverNamespaces: stringList(
        match.serverNamespaces,
        `rules[${position}].match.serverNamespaces`,
        (item) => NAMESPACE_PATTERN.test(item),
        ["*"],
      ),
      toolPatterns: stringList(
        match.toolPatterns,
        `rules[${position}].match.toolPatterns`,
        (item) => TOOL_PATTERN.test(item),
        ["*"],
      ),
      risks,
    },
    decision,
  };
}

export function normalizeMcpPolicyDocument(value: unknown): McpPolicyDocument {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("MCP policy document должен быть JSON-объектом");
  }
  const raw = value as Record<string, unknown>;
  if (raw.schemaVersion !== 1) throw new Error("Поддерживается только MCP policy schemaVersion=1");
  if (!raw.defaults || typeof raw.defaults !== "object" || Array.isArray(raw.defaults)) {
    throw new Error("MCP policy defaults обязателен");
  }
  const defaultsValue = raw.defaults as Record<string, unknown>;
  const defaults = Object.fromEntries(RISKS.map((risk) => [
    risk,
    normalizeDecision(defaultsValue[risk], `defaults.${risk}`, risk),
  ])) as unknown as Record<McpToolRisk, McpRiskDecision>;
  if (!Array.isArray(raw.rules) || raw.rules.length > 200) {
    throw new Error("MCP policy rules должен быть массивом не более чем из 200 правил");
  }
  const rules = raw.rules.map(normalizeRule);
  if (new Set(rules.map((rule) => rule.id)).size !== rules.length) {
    throw new Error("MCP policy rule id должны быть уникальны");
  }
  return {
    schemaVersion: 1,
    name: requiredText(raw.name, "MCP policy name", 100),
    defaults,
    rules,
  };
}

export function serializeMcpPolicy(document: McpPolicyDocument): string {
  return JSON.stringify(normalizeMcpPolicyDocument(document));
}

export function mcpPolicySha256(document: McpPolicyDocument): string {
  return createHash("sha256").update(serializeMcpPolicy(document)).digest("hex");
}

function globMatches(pattern: string, value: string): boolean {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replaceAll("*", ".*").replaceAll("?", ".");
  return new RegExp(`^${escaped}$`).test(value);
}

function matchingRule(
  document: McpPolicyDocument,
  namespace: string,
  toolName: string,
  risk: McpToolRisk,
): McpPolicyRule | null {
  return document.rules.find((rule) =>
    rule.match.risks.includes(risk)
    && rule.match.serverNamespaces.some((pattern) => globMatches(pattern, namespace))
    && rule.match.toolPatterns.some((pattern) => globMatches(pattern, toolName))) ?? null;
}

export function evaluateMcpPolicy(input: {
  document: McpPolicyDocument;
  version: number;
  sha256: string;
  namespace: string;
  toolName: string;
  risk: McpToolRisk;
  legacyPolicy: McpToolPolicy;
  emergencyDeny?: boolean;
}): McpPolicyDecision {
  const document = normalizeMcpPolicyDocument(input.document);
  const rule = matchingRule(document, input.namespace, input.toolName, input.risk);
  const configured = rule?.decision ?? document.defaults[input.risk];
  let effect = configured.effect;
  let approvals = configured.approvals;
  const reasons = [rule ? `rule:${rule.id}` : `default:${input.risk}`];

  if (input.legacyPolicy === "deny") {
    effect = "deny";
    approvals = 0;
    reasons.push("legacy:deny");
  } else if (input.legacyPolicy === "approval" && effect !== "deny" && approvals < 1) {
    effect = "approval";
    approvals = 1;
    reasons.push("legacy:approval-floor");
  }
  if ((input.risk === "destructive" || configured.tier === "critical") && effect !== "deny") {
    effect = "approval";
    approvals = 2;
    reasons.push("four-eyes:mandatory");
  }
  if (input.emergencyDeny) {
    effect = "deny";
    approvals = 0;
    reasons.push("emergency-deny");
  }

  return {
    tier: configured.tier,
    effect,
    approvals,
    legacyPolicy: input.legacyPolicy,
    ruleId: rule?.id ?? null,
    reason: reasons.join(" · "),
    policyVersion: input.version,
    policySha256: input.sha256,
  };
}

function redactedValue(value: unknown, sensitive: boolean, depth = 0): unknown {
  if (sensitive) return "[redacted]";
  if (depth >= 4) return "[truncated]";
  if (typeof value === "string") return value.length > 160 ? `${value.slice(0, 157)}…` : value;
  if (typeof value === "number" || typeof value === "boolean" || value === null) return value;
  if (Array.isArray(value)) return value.slice(0, 20).map((item) => redactedValue(item, false, depth + 1));
  if (!value || typeof value !== "object") return String(value);
  return Object.fromEntries(Object.entries(value as Record<string, unknown>).slice(0, 40).map(([key, child]) => [
    key,
    redactedValue(child, /authorization|credential|password|secret|token|api[_-]?key|private[_-]?key/i.test(key), depth + 1),
  ]));
}

function objectDiff(before: Record<string, unknown>, after: Record<string, unknown>): Array<Record<string, unknown>> {
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].sort().slice(0, 100);
  const changes: Array<Record<string, unknown>> = [];
  for (const key of keys) {
    const left = before[key];
    const right = after[key];
    if (JSON.stringify(left) === JSON.stringify(right)) continue;
    const sensitive = /authorization|credential|password|secret|token|api[_-]?key|private[_-]?key/i.test(key);
    if (!(key in before)) {
      changes.push({ op: "add", path: `/${key}`, after: redactedValue(right, sensitive) });
      continue;
    }
    if (!(key in after)) {
      changes.push({ op: "remove", path: `/${key}`, before: redactedValue(left, sensitive) });
      continue;
    }
    changes.push({
      op: "replace",
      path: `/${key}`,
      before: redactedValue(left, sensitive),
      after: redactedValue(right, sensitive),
    });
  }
  return changes;
}

export function mcpArgumentsPreviewDiff(argumentsValue: Record<string, unknown>): Array<Record<string, unknown>> {
  const before = argumentsValue.before ?? argumentsValue.current;
  const after = argumentsValue.after ?? argumentsValue.desired;
  if (before && typeof before === "object" && !Array.isArray(before)
    && after && typeof after === "object" && !Array.isArray(after)) {
    return objectDiff(before as Record<string, unknown>, after as Record<string, unknown>);
  }
  return [{ op: "invoke", path: "/", after: redactedValue(argumentsValue, false) }];
}
