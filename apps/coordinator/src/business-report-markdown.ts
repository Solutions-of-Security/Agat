import { createHash } from "node:crypto";
import { validateBusinessReportOutput, type BusinessReport, type BusinessValidationContext } from "./business-contracts.js";
import { appendKnowledgeSources } from "./knowledge-citations.js";
import type { KnowledgeSource } from "./types.js";

export const BUSINESS_REPORT_MARKDOWN_FORMAT = "business-report-markdown.v1" as const;
export type BusinessReportFormat = typeof BUSINESS_REPORT_MARKDOWN_FORMAT;

export class BusinessReportRenderingError extends Error {
  constructor(readonly code: "validation_failed" | "display_sources_unavailable" | "report_too_large", readonly reasons: string[] = []) {
    const message = code === "validation_failed" ? `Отчёт не прошёл проверку: ${reasons.join(", ")}`
      : code === "display_sources_unavailable" ? "Источники отчёта недоступны для отображения"
        : "Отчёт превышает лимит 800000 байт; сокращение без проверки запрещено";
    super(message); this.name = "BusinessReportRenderingError";
  }
}

export function normalizeBusinessReportFormat(raw: unknown): BusinessReportFormat {
  if (raw !== BUSINESS_REPORT_MARKDOWN_FORMAT) throw new Error("Неизвестный формат бизнес-отчёта");
  return raw;
}

function escape(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/[\\`*_{}\[\]()<>#!|.+-]/g, "\\$&")
    .replace(/\r\n?/g, "\n");
}
const references = (refs: string[]) => refs.map((ref) => `[${ref}]`).join(" ");
function formula(operation: BusinessReport["calculations"][number]["operation"], a: number, b: number): string {
  switch (operation) {
    case "sum": return `${a} + ${b}`;
    case "difference": return `${a} − ${b}`;
    case "ratio": return `${a} / ${b}`;
    case "percent_change": return `(${b} − ${a}) / abs(${a}) × 100, %`;
    case "percentage_points": return `(${b} − ${a}) × 100, п.п.`;
  }
}

/** Deterministic presentation of one canonical validated JSON result; no model calls. */
export function renderValidatedBusinessReport(output: string, sources: KnowledgeSource[], context: BusinessValidationContext) {
  const validation = validateBusinessReportOutput(output, context);
  if (validation.status !== "validated") throw new BusinessReportRenderingError("validation_failed", validation.codes);
  const report = JSON.parse(output) as BusinessReport;
  const lines = ["# Внутренний отчёт", "", "## Краткий результат", "", escape(report.summary), "", "## Выводы", ""];
  for (const claim of report.claims) lines.push(`- **${escape(claim.id)}**: ${escape(claim.text).replace(/\n/g, "  \n  ")} ${references(claim.sourceRefs)}`);
  if (report.calculations.length) {
    lines.push("", "## Расчёты", "", "| Показатель | Формула | Результат | Источники |", "| --- | --- | --- | --- |");
    for (const calculation of report.calculations) lines.push(`| ${escape(calculation.id)} | ${formula(calculation.operation, ...calculation.operands)} | ${calculation.result} | ${references(calculation.sourceRefs)} |`);
  }
  lines.push("", "## Ограничения", "");
  for (const limitation of report.limitations) lines.push(`- ${escape(limitation).replace(/\n/g, "  \n  ")}`);
  if (!report.limitations.length) lines.push("Ограничения не указаны автором результата; полнота требует проверки.");
  lines.push("", "## Границы проверки", "", "Код проверил структуру, доступность ссылок и формализованные расчёты на указанных операндах. Соответствие утверждений источникам и выбор операндов требуют проверки человеком.",
    "", `Контракт: ${validation.contract}. SHA-256 схемы: ${validation.schemaSha256}.`,
    `SHA-256 исходного JSON: ${validation.outputSha256}.`, "");
  const markers = new Set([...report.claims, ...report.calculations].flatMap((item) => item.sourceRefs));
  const selectedSources = sources.filter((source) => markers.has(source.marker));
  if (markers.size !== new Set(selectedSources.map((source) => source.marker)).size) throw new BusinessReportRenderingError("display_sources_unavailable");
  const content = appendKnowledgeSources(lines.join("\n"), selectedSources);
  if (Buffer.byteLength(content, "utf8") > 800_000) throw new BusinessReportRenderingError("report_too_large");
  return { content, receipt: { format: BUSINESS_REPORT_MARKDOWN_FORMAT, contract: validation.contract,
    schemaSha256: validation.schemaSha256, inputSha256: validation.outputSha256,
    contentSha256: createHash("sha256").update(content).digest("hex"), sizeBytes: Buffer.byteLength(content, "utf8"),
    factualClaims: validation.factualClaims } };
}
