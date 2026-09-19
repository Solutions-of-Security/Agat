import type { KnowledgeSource, UploadKnowledgeDocumentRequest } from "./types";

export const MAX_KNOWLEDGE_FILE_BYTES = 5 * 1024 * 1024;
const DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export async function readKnowledgeFile(file: File): Promise<UploadKnowledgeDocumentRequest | { name: string; mediaType: string; content: string }> {
  const extension = file.name.split(".").at(-1)?.toLowerCase();
  const binaryType = extension === "pdf" ? "application/pdf" : extension === "docx" ? DOCX_TYPE
    : ["application/pdf", DOCX_TYPE].includes(file.type) ? file.type : null;
  if (file.size > (binaryType ? MAX_KNOWLEDGE_FILE_BYTES : 2_000_000)) {
    throw new Error(binaryType ? "Файл больше 5 МиБ" : "Текстовый файл больше 2 МБ");
  }
  if (!file.size) throw new Error("Выбранный файл пуст");
  if (binaryType) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    const parts: string[] = [];
    for (let index = 0; index < bytes.length; index += 32_768) parts.push(String.fromCharCode(...bytes.subarray(index, index + 32_768)));
    return { name: file.name, mediaType: binaryType, contentBase64: btoa(parts.join("")) };
  }
  if (!file.type.startsWith("text/") && !["md", "txt", "csv", "json"].includes(extension ?? "")) {
    throw new Error("Выберите PDF, DOCX или текстовый файл");
  }
  return { name: file.name, mediaType: file.type.startsWith("text/") ? file.type : "text/plain", content: await file.text() };
}

export function knowledgeDocumentHref(projectId: string, documentId: string): string {
  return `#knowledge?${new URLSearchParams({ projectId, documentId })}`;
}

export function knowledgeSourceHref(source: KnowledgeSource): string {
  const p = source.provenance;
  const query = new URLSearchParams({ projectId: p.projectId, documentId: p.documentId, chunkId: p.chunkId, sha256: p.documentSha256 });
  if (p.pageNumber) query.set("page", String(p.pageNumber));
  return `#knowledge?${query}`;
}

export function knowledgeSourceLocation(source: KnowledgeSource): string {
  const p = source.provenance;
  return `${p.pageNumber ? `стр. ${p.pageNumber} · ` : ""}фрагмент ${p.chunkOrdinal + 1}`;
}

export function knowledgeReport(output: string, sources: KnowledgeSource[], baseUrl: string): string {
  if (!sources.length) return output;
  const escape = (value: string) => value.replace(/[\\`*_{}\[\]()<>#!|]/g, "\\$&").replace(/[\r\n]+/g, " ");
  return `${output}\n\n## Источники Knowledge\n\nФрагменты, переданные модели; их связь с выводами требует проверки.\n\n${sources.map((source) => {
    const p = source.provenance;
    return `- [${escape(source.marker)} · ${escape(p.documentName)} · ${knowledgeSourceLocation(source)}](${new URL(knowledgeSourceHref(source), baseUrl).href})\n`
      + `  Этап: ${escape(source.stageId)}; retrieval: ${escape(source.retrievalId)}; символы ${p.charStart}–${p.charEnd}.\n`
      + `  SHA-256 текста: ${p.documentSha256}; фрагмента: ${p.chunkSha256}${p.originalSha256 ? `; файла: ${p.originalSha256}` : ""}.\n\n`
      + (source.content ?? source.excerpt).split("\n").map((line) => `  > ${escape(line)}`).join("\n");
  }).join("\n\n")}\n`;
}
