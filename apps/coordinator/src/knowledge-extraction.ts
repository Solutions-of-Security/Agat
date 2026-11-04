import { createHash } from "node:crypto";

/** Coverage is a description of the parser's scope, never a proof of visual completeness. */
export interface KnowledgeExtractionReport {
  schemaVersion: "document-extraction.v1";
  profile: "provided-text.v1" | "pdf-text-layer.v1" | "docx-main-document.v1" | "csv-values.v1" | "xlsx-values.v1";
  textCharacters: number;
  totalPages: number | null;
  emptyPages: number[];
  omittedParts: string[];
  features: string[];
  reviewRequired: boolean;
  fidelity: "not_assessed";
}
export interface KnowledgeExtractionReviewInput {
  originalSha256: string; contentSha256: string; reportSha256: string;
  extractionRevision: string;
  decision: "use_extracted_text_only"; reason: string;
}
export interface KnowledgeExtractionReview extends KnowledgeExtractionReviewInput {
  schemaVersion: "document-extraction-review.v1"; documentId: string; actor: string; reviewedAt: string;
}
export interface KnowledgeExtractionProvenance {
  profile: KnowledgeExtractionReport["profile"]; reportSha256: string; extractionRevision: string;
  reviewSha256: string | null; decision: "use_extracted_text_only" | null;
  emptyPageCount: number | null; omittedPartsCount: number | null; features: string[]; fidelity: "not_assessed";
}
export function extractionReport(profile: KnowledgeExtractionReport["profile"], text: string,
  details: Partial<Pick<KnowledgeExtractionReport, "totalPages" | "emptyPages" | "omittedParts" | "features">> = {}): KnowledgeExtractionReport {
  return { schemaVersion: "document-extraction.v1", profile, textCharacters: text.length,
    totalPages: details.totalPages ?? null, emptyPages: details.emptyPages ?? [], omittedParts: details.omittedParts ?? [], features: details.features ?? [],
    reviewRequired: profile === "pdf-text-layer.v1" || profile === "docx-main-document.v1", fidelity: "not_assessed" };
}
export function extractionReportSha256(report: KnowledgeExtractionReport): string { return createHash("sha256").update(JSON.stringify(report)).digest("hex"); }

export function validateExtractionReview(input: KnowledgeExtractionReviewInput): KnowledgeExtractionReviewInput {
  if (!input || typeof input !== "object" || Array.isArray(input)
    || Object.keys(input).some(key => !["originalSha256", "contentSha256", "reportSha256", "extractionRevision", "decision", "reason"].includes(key))
    || typeof input.extractionRevision !== "string" || !/^[a-f0-9-]{36}$/.test(input.extractionRevision)
    || [input.originalSha256, input.contentSha256, input.reportSha256].some(hash => typeof hash !== "string" || !/^[a-f0-9]{64}$/.test(hash))
    || input.decision !== "use_extracted_text_only" || typeof input.reason !== "string" || !input.reason.trim() || input.reason.length > 1000) {
    throw new Error("Подтвердите использование только извлечённого текста, укажите причину до 1000 символов и текущие SHA-256 файла, текста и разбора");
  }
  return { ...input, reason: input.reason.trim() };
}
