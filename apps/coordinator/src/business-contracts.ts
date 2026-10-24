import { createHash } from "node:crypto";
import { Ajv } from "ajv";

export const BUSINESS_REPORT_CONTRACT = "business-report.v1" as const;
export type BusinessOutputContract = typeof BUSINESS_REPORT_CONTRACT;

const text = { type: "string", minLength: 1, maxLength: 8_000, pattern: "\\S" };
const identifier = { type: "string", minLength: 1, maxLength: 100, pattern: "^[A-Za-z0-9_-]+$" };
const sourceRefs = { type: "array", minItems: 1, maxItems: 20, uniqueItems: true,
  items: { type: "string", pattern: "^K[1-9][0-9]{0,7}$" } };

/** Bundled schemas are code: callers can select a version, never supply executable schemas. */
export const businessReportSchema = {
  $schema: "http://json-schema.org/draft-07/schema#",
  type: "object", additionalProperties: false,
  required: ["schemaVersion", "status", "summary", "claims", "calculations", "artifactRefs", "limitations"],
  properties: {
    schemaVersion: { const: BUSINESS_REPORT_CONTRACT },
    status: { enum: ["ready", "insufficient"] }, summary: text,
    claims: { type: "array", maxItems: 100, items: {
      type: "object", additionalProperties: false, required: ["id", "text", "sourceRefs"],
      properties: { id: identifier, text, sourceRefs },
    } },
    calculations: { type: "array", maxItems: 100, items: {
      type: "object", additionalProperties: false,
      required: ["id", "operation", "operands", "result", "sourceRefs"],
      properties: {
        id: identifier, operation: { enum: ["sum", "difference", "ratio", "percent_change", "percentage_points"] },
        operands: { type: "array", minItems: 2, maxItems: 2,
          items: { type: "number", minimum: -1e12, maximum: 1e12 } },
        result: { type: "number", minimum: -1e15, maximum: 1e15 }, sourceRefs,
      },
    } },
    artifactRefs: { type: "array", maxItems: 8, items: {
      type: "object", additionalProperties: false, required: ["id", "sha256"],
      properties: { id: identifier, sha256: { type: "string", pattern: "^[a-f0-9]{64}$" } },
    } },
    limitations: { type: "array", maxItems: 30, uniqueItems: true, items: text },
  },
} as const;

export interface BusinessReport {
  schemaVersion: BusinessOutputContract;
  status: "ready" | "insufficient";
  summary: string;
  claims: Array<{ id: string; text: string; sourceRefs: string[] }>;
  calculations: Array<{ id: string; operation: "sum" | "difference" | "ratio" | "percent_change" | "percentage_points";
    operands: [number, number]; result: number; sourceRefs: string[] }>;
  artifactRefs: Array<{ id: string; sha256: string }>;
  limitations: string[];
}

export interface BusinessValidationContext {
  sourceMarkers: ReadonlySet<string>;
  /** Resolves only existing, readable artifacts in the authoritative run/project scope. */
  artifactSha256: (id: string) => string | null;
}
export interface BusinessValidationReceipt {
  contract: BusinessOutputContract;
  schemaSha256: string;
  outputSha256: string;
  status: "validated" | "invalid" | "review_required";
  codes: string[];
  factualClaims: "human_review_required";
}

const schemaSha256 = createHash("sha256").update(JSON.stringify(businessReportSchema)).digest("hex");
const ajv = new Ajv({ strict: true, allErrors: false, coerceTypes: false, useDefaults: false, removeAdditional: false });
const validateSchema = ajv.compile<BusinessReport>(businessReportSchema);

export function normalizeBusinessOutputContract(raw: unknown): BusinessOutputContract {
  if (raw !== BUSINESS_REPORT_CONTRACT) throw new Error("Неизвестный контракт бизнес-результата");
  return raw;
}

/** No Markdown unwrapping, coercion, inferred defaults, network resolution or automatic repair. */
export function validateBusinessReportOutput(output: string, context: BusinessValidationContext): BusinessValidationReceipt {
  const receipt: BusinessValidationReceipt = { contract: BUSINESS_REPORT_CONTRACT, schemaSha256,
    outputSha256: createHash("sha256").update(output).digest("hex"), status: "invalid", codes: [],
    factualClaims: "human_review_required" };
  const fail = (code: string) => ({ ...receipt, codes: [code] });
  if (Buffer.byteLength(output, "utf8") > 200_000) return fail("output_too_large");
  let value: unknown;
  try { value = JSON.parse(output); } catch { return fail("invalid_json"); }
  if (!validateSchema(value)) return fail("schema_invalid");
  const ids = new Set<string>();
  for (const item of [...value.claims, ...value.calculations]) {
    if (ids.has(item.id)) return fail("duplicate_item_id");
    ids.add(item.id);
    if (item.sourceRefs.some((ref) => !context.sourceMarkers.has(ref))) return fail("source_out_of_scope");
  }
  for (const calculation of value.calculations) {
    const [a, b] = calculation.operands;
    if ((calculation.operation === "ratio" && b === 0)
      || (calculation.operation === "percent_change" && a === 0)) return fail("undefined_calculation");
    const expected = calculation.operation === "sum" ? a + b
      : calculation.operation === "difference" ? a - b
        : calculation.operation === "ratio" ? a / b
          : calculation.operation === "percent_change" ? (b - a) / Math.abs(a) * 100 : (b - a) * 100;
    const tolerance = Math.max(1e-9, Number.EPSILON * 8 * Math.max(Math.abs(expected), Math.abs(calculation.result)));
    if (!Number.isFinite(expected) || Math.abs(expected - calculation.result) > tolerance) return fail("calculation_mismatch");
  }
  const artifactIds = new Set<string>();
  for (const artifact of value.artifactRefs) {
    if (artifactIds.has(artifact.id)) return fail("duplicate_artifact_id");
    artifactIds.add(artifact.id);
    try {
      if (context.artifactSha256(artifact.id) !== artifact.sha256) return fail("artifact_unavailable_or_mismatched");
    } catch { return fail("artifact_unavailable_or_mismatched"); }
  }
  if (value.status === "insufficient") {
    if (!value.limitations.length) return fail("missing_limitations");
    return { ...receipt, status: "review_required", codes: ["insufficient_evidence"] };
  }
  if (!value.claims.length) return fail("missing_claims");
  return { ...receipt, status: "validated" };
}
