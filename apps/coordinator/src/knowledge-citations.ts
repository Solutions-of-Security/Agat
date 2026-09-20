import type { KnowledgeSource } from "./types.js";

export function knowledgeSourceHref(source: KnowledgeSource): string {
  const p = source.provenance;
  const query = new URLSearchParams({ projectId: p.projectId, documentId: p.documentId,
    chunkId: p.chunkId, sha256: p.documentSha256 });
  if (p.pageNumber) query.set("page", String(p.pageNumber));
  return `/#knowledge?${query}`;
}

export function appendKnowledgeSources(output: string, sources: KnowledgeSource[]): string {
  if (!sources.length) return output;
  const escape = (text: string) => text.replace(/[\\`*_{}\[\]()<>#!|]/g, "\\$&").replace(/[\r\n]+/g, " ");
  return `${output}\n\n## Источники Knowledge\n\nФрагменты, переданные модели; их связь с выводами требует проверки.\n\n${sources.map((source) => {
    const p = source.provenance;
    const location = `${p.pageNumber ? `стр. ${p.pageNumber}, ` : ""}фрагмент ${p.chunkOrdinal + 1}`;
    return `- [${escape(source.marker)} · ${escape(p.documentName)} · ${location}](${knowledgeSourceHref(source)})\n`
      + `  Этап: ${escape(source.stageId)}; retrieval: ${escape(source.retrievalId)}; символы ${p.charStart}–${p.charEnd}.\n`
      + `  SHA-256 текста: ${p.documentSha256}; фрагмента: ${p.chunkSha256}${p.originalSha256 ? `; файла: ${p.originalSha256}` : ""}.\n\n`
      + (source.content ?? source.excerpt).split("\n").map((line) => `  > ${escape(line)}`).join("\n");
  }).join("\n\n")}\n`;
}
