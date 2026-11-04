import { createHash } from "node:crypto";

export const DECISION_SHADOW_PROFILE = "local_decision_shadow_v1";
export const DECISION_SHADOW_SCORE_PROFILE = "local_decision_shadow_v2";
export const SCORE_FINGERPRINT_VERSION = "binary64-v1";
export const DECISION_CALLER_TIMING_VERSION = "agat.decision.caller-timing.v1";
const SCHEMA = "agat.decision.v1";
type ObjectValue = Record<string, unknown>;

export interface ShadowOption {
  id: string;
  description: string;
  abstain: boolean;
  value?: boolean | number;
}

export interface DecisionShadowConfig {
  mode: "shadow";
  profileJson: string;
  timeoutMs: number;
  question: string;
  kind: "choice" | "boolean" | "score";
  options: ShadowOption[];
}

export interface DecisionShadowLease {
  profile: typeof DECISION_SHADOW_PROFILE | typeof DECISION_SHADOW_SCORE_PROFILE;
  profileSha256: string;
  timeoutMs: number;
  inputSha256: string;
  callerTimingVersion?: typeof DECISION_CALLER_TIMING_VERSION;
  callerAccountingVersion?: "agat.decision.caller-accounting.v1";
  assignmentId?: string;
  request: {
    schemaVersion: typeof SCHEMA;
    id: string;
    state: string;
    question: string;
    kind: DecisionShadowConfig["kind"];
    options: ShadowOption[];
    inputFingerprintVersion?: typeof SCORE_FINGERPRINT_VERSION;
  };
}

export interface DecisionShadowObservation {
  mode: "shadow";
  fallback: "primary";
  status: "ok" | "abstain" | "error" | "unavailable";
  reason: string;
  reusedFromStageId?: string;
  result?: ObjectValue;
  callerTiming?: DecisionCallerTiming;
}

export interface DecisionCallerTiming {
  schemaVersion: "agat.decision.caller-timing.v1";
  clock: "monotonic";
  boundary: "local_http_call";
  durationMs: number;
}

export function decisionCallerTiming(raw: unknown): DecisionCallerTiming {
  const timing = fields(raw, ["schemaVersion", "clock", "boundary", "durationMs"]);
  if (timing.schemaVersion !== DECISION_CALLER_TIMING_VERSION || timing.clock !== "monotonic" || timing.boundary !== "local_http_call") {
    throw new Error("Invalid decision caller timing identity");
  }
  return { schemaVersion: DECISION_CALLER_TIMING_VERSION, clock: "monotonic", boundary: "local_http_call",
    durationMs: number(timing.durationMs, 0, 86_400_000) };
}

function object(value: unknown): ObjectValue {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid decision object");
  return value as ObjectValue;
}

function fields(value: unknown, required: string[], optional: string[] = []): ObjectValue {
  const result = object(value);
  if (required.some((key) => !Object.hasOwn(result, key))
      || Object.keys(result).some((key) => ![...required, ...optional].includes(key))) {
    throw new Error("Missing or unknown decision fields");
  }
  return result;
}

function text(value: unknown, limit: number, identifier = false): string {
  if (typeof value !== "string" || !value.trim() || [...value].length > limit
      || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)
      || (identifier && !/^[A-Za-z0-9_.:-]+$/.test(value))) throw new Error("Invalid decision text");
  return value;
}

function number(value: unknown, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) {
    throw new Error("Invalid decision number");
  }
  return value;
}

export function decisionSha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

// This serializer only handles the contract's fixed ASCII keys. Score numbers
// are encoded as binary64 before hashing, preserving legacy Choice/Boolean IDs.
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical((value as ObjectValue)[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function readProfile(profileJson: string): ObjectValue {
  const profile = fields(JSON.parse(profileJson), ["schemaVersion", "runtimeVersion", "model", "policy", "calibration"], ["inputFingerprintVersions"]);
  if (profile.schemaVersion !== SCHEMA) throw new Error("Unsupported decision schema");
  text(profile.runtimeVersion, 80);
  if (Object.hasOwn(profile, "inputFingerprintVersions") && (!Array.isArray(profile.inputFingerprintVersions)
      || profile.inputFingerprintVersions.length === 0
      || new Set(profile.inputFingerprintVersions).size !== profile.inputFingerprintVersions.length
      || profile.inputFingerprintVersions.some((version) => !["python-json-v1", SCORE_FINGERPRINT_VERSION].includes(version)))) {
    throw new Error("Unsupported input fingerprint versions");
  }
  const model = object(profile.model);
  for (const key of ["artifactSha256", "tokenizerSha256", "implementationSha256"]) {
    if (typeof model[key] !== "string" || !/^[a-f0-9]{64}$/.test(model[key])) throw new Error("Unpinned decision model");
  }
  for (const key of ["repository", "revision", "promptVersion", "backend", "quantization"]) text(model[key], 200);
  const policy = fields(profile.policy, ["id", "minProbability", "minMargin", "sha256"]);
  text(policy.id, 100, true);
  number(policy.minProbability, 0, 1);
  number(policy.minMargin, 0, 1);
  if (typeof policy.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(policy.sha256)) throw new Error("Unpinned decision policy");
  const calibration = object(profile.calibration);
  number(calibration.temperature, 0.01, 100);
  if (calibration.semantics !== "softmax_over_allowed_options"
      || (calibration.status === "uncalibrated" && calibration.temperature !== 1)) throw new Error("Invalid calibration semantics");
  if (!["fitted", "uncalibrated"].includes(String(calibration.status))) throw new Error("Unknown calibration status");
  if (calibration.status === "fitted" && (typeof calibration.artifactSha256 !== "string" || !/^[a-f0-9]{64}$/.test(calibration.artifactSha256))) {
    throw new Error("Unpinned decision calibration");
  }
  return profile;
}

export function normalizeDecisionShadowConfig(raw: unknown): DecisionShadowConfig {
  const config = fields(raw, ["mode", "profileJson", "timeoutMs", "question", "kind", "options"]);
  if (config.mode !== "shadow" || !["choice", "boolean", "score"].includes(String(config.kind))) {
    throw new Error("Decision supports Choice/Boolean/Score in shadow mode only");
  }
  const profileJson = text(config.profileJson, 16_000);
  const profile = readProfile(profileJson);
  if (config.kind === "score" && (!Array.isArray(profile.inputFingerprintVersions)
      || !profile.inputFingerprintVersions.includes(SCORE_FINGERPRINT_VERSION))) {
    throw new Error("Score requires a runtime with the binary64-v1 fingerprint contract");
  }
  const timeoutMs = number(config.timeoutMs, 100, 10_000);
  if (!Number.isInteger(timeoutMs)) throw new Error("Decision timeout must be an integer");
  if (!Array.isArray(config.options) || config.options.length < 2 || config.options.length > 10) {
    throw new Error("Decision requires 2 to 10 options");
  }
  const options = config.options.map((rawOption): ShadowOption => {
    const option = fields(rawOption, ["id", "description"], ["abstain", "value"]);
    const abstain = option.abstain === undefined ? false : option.abstain;
    if (typeof abstain !== "boolean") throw new Error("Invalid decision abstain flag");
    if ((config.kind === "boolean" && (typeof option.value !== "boolean" || abstain))
        || (config.kind === "choice" && Object.hasOwn(option, "value")) || (config.kind === "score" && abstain)) {
      throw new Error("Invalid decision option value");
    }
    const value = config.kind === "score" ? number(option.value, -1_000_000, 1_000_000) : option.value;
    return { id: text(option.id, 80, true), description: text(option.description, 1000), abstain,
      ...(config.kind !== "choice" ? { value: (value === 0 ? 0 : value) as boolean | number } : {}) };
  });
  if (new Set(options.map((option) => option.id)).size !== options.length
      || new Set(options.map((option) => option.description)).size !== options.length
      || (config.kind === "boolean" && (options.length !== 2 || new Set(options.map((o) => o.value)).size !== 2))
      || (config.kind === "score" && new Set(options.map((o) => o.value)).size !== options.length)) {
    throw new Error("Duplicate or invalid decision options");
  }
  return { mode: "shadow", profileJson, timeoutMs, question: text(config.question, 2000),
    kind: config.kind as DecisionShadowConfig["kind"], options };
}

export function supportsDecisionShadow(capability: unknown, config: DecisionShadowConfig): boolean {
  return capability === DECISION_SHADOW_SCORE_PROFILE
    || (capability === DECISION_SHADOW_PROFILE && config.kind !== "score");
}

export function createDecisionShadowLease(config: DecisionShadowConfig, stageId: string, state: string): DecisionShadowLease {
  const request: DecisionShadowLease["request"] = { schemaVersion: SCHEMA, id: text(stageId, 100, true), state: text(state, 24_000),
    question: config.question, kind: config.kind, options: config.options,
    ...(config.kind === "score" ? { inputFingerprintVersion: SCORE_FINGERPRINT_VERSION } : {}) };
  const { id: _id, ...input } = request;
  const hashInput = config.kind !== "score" ? input : { ...input, options: input.options.map((option) => {
    const bytes = Buffer.alloc(8);
    bytes.writeDoubleBE(option.value === 0 ? 0 : option.value as number);
    return { ...option, value: { float64be: bytes.toString("hex") } };
  }) };
  if (Buffer.byteLength(JSON.stringify(request)) > 128 * 1024) throw new Error("Decision input too large");
  return { profile: config.kind === "score" ? DECISION_SHADOW_SCORE_PROFILE : DECISION_SHADOW_PROFILE,
    profileSha256: decisionSha256(config.profileJson),
    callerTimingVersion: DECISION_CALLER_TIMING_VERSION,
    timeoutMs: config.timeoutMs, inputSha256: decisionSha256(canonical(hashInput)), request };
}

export function unavailableDecision(reason: string): DecisionShadowObservation {
  return { mode: "shadow", fallback: "primary", status: "unavailable", reason };
}

export function validateDecisionShadowResult(
  raw: unknown, lease: DecisionShadowLease, profileJson: string,
): DecisionShadowObservation {
  let callerTiming: DecisionCallerTiming | undefined;
  try {
    const envelope = object(raw);
    if (Object.hasOwn(envelope, "callerTiming")) {
      if (lease.callerTimingVersion !== DECISION_CALLER_TIMING_VERSION) throw new Error("Decision caller timing was not negotiated");
      callerTiming = decisionCallerTiming(envelope.callerTiming);
    }
    if (envelope.status === "unavailable") {
      fields(envelope, ["status", "reason"], ["callerTiming"]);
      if (!["timeout", "busy", "unreachable", "profile_mismatch", "invalid_response", "disabled", "dry_run", "cancelled"].includes(String(envelope.reason))) {
        throw new Error("Invalid availability reason");
      }
      return { ...unavailableDecision(String(envelope.reason)), ...(callerTiming ? { callerTiming } : {}) };
    }
    fields(envelope, ["result"], ["callerTiming"]);
    const result = fields(envelope.result,
      ["schemaVersion", "runtimeVersion", "id", "mode", "inputSha256", "model", "policy", "calibration", "status", "reason", "selectedOptionId", "value", "distribution", "durationMs"],
      ["selectedProbability", "margin", "inputTokens", "generatedTokens", "inputFingerprintVersions", "inputFingerprintVersion"]);
    const profile = readProfile(profileJson);
    if (result.mode !== "shadow" || result.id !== lease.request.id || result.inputSha256 !== lease.inputSha256) {
      throw new Error("Decision input mismatch");
    }
    if (Object.hasOwn(result, "inputFingerprintVersions") !== Object.hasOwn(profile, "inputFingerprintVersions")
        || Object.hasOwn(result, "inputFingerprintVersion") !== Object.hasOwn(lease.request, "inputFingerprintVersion")
        || result.inputFingerprintVersion !== lease.request.inputFingerprintVersion) {
      throw new Error("Decision fingerprint version mismatch");
    }
    for (const key of Object.keys(profile)) {
      if (canonical(profile[key]) !== canonical(result[key])) throw new Error("Decision profile mismatch");
    }
    number(result.durationMs, 0, 86_400_000);
    if (!Array.isArray(result.distribution)) throw new Error("Invalid distribution");
    if (result.status === "error") {
      if (!["invalid_request", "context_too_long", "calibration_out_of_scope", "invalid_scores", "backend_error", "inference_timeout", "inference_cancelled", "backend_unavailable"].includes(String(result.reason))
          || result.distribution.length || result.selectedOptionId !== null || result.value !== null
          || ["selectedProbability", "margin", "inputTokens", "generatedTokens"].some((k) => Object.hasOwn(result, k))) {
        throw new Error("Invalid decision error");
      }
    } else {
      const options = lease.request.options;
      if (result.distribution.length !== options.length) throw new Error("Incomplete distribution");
      const distribution = result.distribution.map((entry, index) => {
        const item = fields(entry, ["id", "probability", "logit"]);
        if (item.id !== options[index]!.id) throw new Error("Decision candidate mismatch");
        return { id: options[index]!.id, probability: number(item.probability, 0, 1),
          logit: number(item.logit, -Number.MAX_VALUE, Number.MAX_VALUE) };
      });
      const temperature = Number(object(profile.calibration).temperature);
      const top = Math.max(...distribution.map((p) => p.logit));
      const weights = distribution.map((p) => Math.exp((p.logit - top) / temperature));
      const total = weights.reduce((a, b) => a + b, 0);
      distribution.forEach((p, i) => {
        if (Math.abs(p.probability - weights[i]! / total) > 1e-10) throw new Error("Decision probability mismatch");
      });
      const ranked = distribution.map((_, i) => i).sort((a, b) => weights[b]! - weights[a]!);
      const selected = options[ranked[0]!]!;
      const probability = weights[ranked[0]!]! / total;
      const margin = probability - weights[ranked[1]!]! / total;
      const policy = object(profile.policy);
      const reason = selected.abstain ? "abstain_option"
        : probability < Number(policy.minProbability) || margin < Number(policy.minMargin) ? "below_threshold" : "accepted";
      const status = reason === "accepted" ? "ok" : "abstain";
      const value = status !== "ok" ? null : lease.request.kind === "choice" ? selected.id : selected.value;
      let valueMatches = result.value === value;
      if (status === "ok" && lease.request.kind === "score") {
        const products = options.map((option, i) => weights[i]! / total * (option.value as number));
        const expected = products.reduce((a, b) => a + b, 0);
        // Python/JS exp and summation can differ by a few ulps. Scale the bound
        // by sum(abs(terms)) to handle cancellation without masking wrong units.
        const tolerance = Math.max(Number.MIN_VALUE * options.length * 8,
          Number.EPSILON * options.length * 8 * products.reduce((a, b) => a + Math.abs(b), 0));
        valueMatches = Math.abs(number(result.value, -1_000_000, 1_000_000) - expected) <= tolerance;
      }
      if (result.status !== status || result.reason !== reason || result.selectedOptionId !== selected.id || !valueMatches
          || Math.abs(number(result.selectedProbability, 0, 1) - probability) > 1e-10
          || Math.abs(number(result.margin, 0, 1) - margin) > 1e-10
          || !Number.isInteger(number(result.inputTokens, 1, 4096)) || result.generatedTokens !== 0) {
        throw new Error("Decision semantics mismatch");
      }
    }
    return { mode: "shadow", fallback: "primary", status: result.status as "ok" | "abstain" | "error",
      reason: String(result.reason), result, ...(callerTiming ? { callerTiming } : {}) };
  } catch {
    // Untrusted responses must neither fail the primary stage nor enter logs.
    return { ...unavailableDecision("invalid_response"), ...(callerTiming ? { callerTiming } : {}) };
  }
}
