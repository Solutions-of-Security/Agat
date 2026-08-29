import type {
  CreateKnowledgeCollectionInput,
  IngestKnowledgeDocumentInput,
  MemoryKind,
  SaveMemoryInput,
} from "./types.js";

export const KNOWLEDGE_MAX_DOCUMENT_CHARACTERS = 2_000_000;
export const KNOWLEDGE_MAX_VECTOR_DIMENSIONS = 4_096;
export const KNOWLEDGE_EMBEDDING_BATCH_SIZE = 32;
export const KNOWLEDGE_MAX_SEARCH_CANDIDATES = 5_000;

export interface NormalizedKnowledgeCollectionInput {
  name: string;
  description: string;
  embeddingModel: string;
  chunkSize: number;
  chunkOverlap: number;
  topK: number;
}

export interface NormalizedKnowledgeDocumentInput {
  name: string;
  sourceUri: string;
  mediaType: string;
  content: string;
}

export interface NormalizedMemoryInput {
  kind: MemoryKind;
  content: string;
  agentId: string | null;
  ttlSeconds: number | null;
}

export interface KnowledgeTextChunk {
  ordinal: number;
  content: string;
  charStart: number;
  charEnd: number;
}

function requiredText(value: unknown, label: string, maximum: number): string {
  if (typeof value !== "string") throw new Error(`${label} должно быть строкой`);
  const normalized = value.trim();
  if (!normalized) throw new Error(`${label} обязательно`);
  if (normalized.length > maximum) throw new Error(`${label}: максимум ${maximum} символов`);
  return normalized;
}

function optionalText(value: unknown, label: string, maximum: number): string {
  if (value === undefined || value === null || value === "") return "";
  return requiredText(value, label, maximum);
}

function boundedInteger(value: unknown, minimum: number, maximum: number, fallback: number, label: string): number {
  if (value === undefined || value === null) return fallback;
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${label}: целое число от ${minimum} до ${maximum}`);
  }
  return value;
}

export function normalizeKnowledgeCollectionInput(
  input: CreateKnowledgeCollectionInput,
): NormalizedKnowledgeCollectionInput {
  const chunkSize = boundedInteger(input.chunkSize, 400, 4_000, 1_200, "Размер фрагмента");
  const chunkOverlap = boundedInteger(input.chunkOverlap, 0, 1_000, 160, "Перекрытие фрагментов");
  if (chunkOverlap >= chunkSize) throw new Error("Перекрытие должно быть меньше размера фрагмента");
  return {
    name: requiredText(input.name, "Название коллекции", 120),
    description: optionalText(input.description, "Описание коллекции", 1_000),
    embeddingModel: requiredText(input.embeddingModel, "Embedding-модель", 200),
    chunkSize,
    chunkOverlap,
    topK: boundedInteger(input.topK, 1, 20, 6, "Количество фрагментов"),
  };
}

export function normalizeKnowledgeDocumentInput(
  input: IngestKnowledgeDocumentInput,
): NormalizedKnowledgeDocumentInput {
  const content = requiredText(
    typeof input.content === "string" ? input.content.replace(/\r\n?/g, "\n") : input.content,
    "Содержимое документа",
    KNOWLEDGE_MAX_DOCUMENT_CHARACTERS,
  );
  const sourceUri = optionalText(input.sourceUri, "Источник документа", 2_048);
  if (/[\u0000-\u001f\u007f]/u.test(sourceUri)) {
    throw new Error("Источник документа содержит управляющие символы");
  }
  const mediaType = optionalText(input.mediaType, "Media type", 120) || "text/plain";
  if (!/^text\/[a-z0-9.+-]+(?:;\s*charset=[a-z0-9._-]+)?$/i.test(mediaType)) {
    throw new Error("В первом релизе Local RAG поддерживаются только text/* документы");
  }
  return {
    name: requiredText(input.name, "Название документа", 180),
    sourceUri,
    mediaType,
    content,
  };
}

export function normalizeMemoryInput(input: SaveMemoryInput): NormalizedMemoryInput {
  if (input.kind !== "working" && input.kind !== "episodic") {
    throw new Error("Память должна иметь тип working или episodic");
  }
  const agentId = input.agentId === undefined || input.agentId === null || input.agentId === ""
    ? null
    : requiredText(input.agentId, "Идентификатор агента", 100);
  const ttlSeconds = input.kind === "working"
    ? boundedInteger(input.ttlSeconds, 60, 2_592_000, 86_400, "TTL working memory")
    : null;
  return {
    kind: input.kind,
    content: requiredText(input.content, "Содержимое памяти", 20_000),
    agentId,
    ttlSeconds,
  };
}

export function normalizeKnowledgeCollectionIds(value: unknown): string[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new Error("knowledgeCollectionIds должен быть массивом");
  const ids = [...new Set(value.map((item) => {
    if (typeof item !== "string" || !/^[a-zA-Z0-9_-]{1,100}$/.test(item)) {
      throw new Error("Некорректный идентификатор knowledge collection");
    }
    return item;
  }))];
  if (ids.length > 32) throw new Error("К запуску можно подключить не больше 32 коллекций");
  return ids;
}

export function chunkKnowledgeText(content: string, chunkSize: number, overlap: number): KnowledgeTextChunk[] {
  const chunks: KnowledgeTextChunk[] = [];
  let start = 0;
  while (start < content.length) {
    let end = Math.min(content.length, start + chunkSize);
    if (end < content.length) {
      const window = content.slice(start, end);
      const minimumBreak = Math.floor(window.length * 0.6);
      const candidates = [window.lastIndexOf("\n\n"), window.lastIndexOf("\n"), window.lastIndexOf(". "), window.lastIndexOf(" ")]
        .filter((index) => index >= minimumBreak);
      if (candidates.length > 0) end = start + Math.max(...candidates) + 1;
    }

    const raw = content.slice(start, end);
    const leading = raw.length - raw.trimStart().length;
    const trailing = raw.length - raw.trimEnd().length;
    const chunkStart = start + leading;
    const chunkEnd = Math.max(chunkStart, end - trailing);
    const text = content.slice(chunkStart, chunkEnd);
    if (text) {
      chunks.push({ ordinal: chunks.length, content: text, charStart: chunkStart, charEnd: chunkEnd });
    }
    if (end >= content.length) break;
    const next = Math.max(start + 1, end - overlap);
    start = next;
    while (start < content.length && /\s/u.test(content[start] ?? "")) start += 1;
  }
  return chunks;
}

export function normalizeEmbeddingVector(value: unknown): number[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > KNOWLEDGE_MAX_VECTOR_DIMENSIONS) {
    throw new Error(`Embedding должен содержать от 1 до ${KNOWLEDGE_MAX_VECTOR_DIMENSIONS} чисел`);
  }
  let normSquared = 0;
  const vector = value.map((item) => {
    if (typeof item !== "number" || !Number.isFinite(item) || Math.abs(item) > 1_000_000) {
      throw new Error("Embedding содержит некорректное число");
    }
    normSquared += item * item;
    return item;
  });
  if (normSquared === 0) throw new Error("Embedding не может быть нулевым вектором");
  return vector;
}

export function cosineSimilarity(left: number[], right: number[]): number | null {
  if (left.length !== right.length || left.length === 0) return null;
  let dot = 0;
  let leftNorm = 0;
  let rightNorm = 0;
  for (let index = 0; index < left.length; index += 1) {
    const leftValue = left[index]!;
    const rightValue = right[index]!;
    dot += leftValue * rightValue;
    leftNorm += leftValue * leftValue;
    rightNorm += rightValue * rightValue;
  }
  if (leftNorm === 0 || rightNorm === 0) return null;
  return Math.max(-1, Math.min(1, dot / Math.sqrt(leftNorm * rightNorm)));
}
