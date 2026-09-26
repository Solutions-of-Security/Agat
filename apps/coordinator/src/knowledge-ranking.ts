import { cosineSimilarity, KNOWLEDGE_MAX_SEARCH_CANDIDATES, normalizeEmbeddingVector } from "./knowledge.js";

export interface KnowledgeRankingOptions {
  vector: number[];
  topK: number;
}

export interface RankedKnowledgeCandidate {
  candidate: Record<string, unknown>;
  score: number;
}

/** Same float64 scorer for SQLite and the PostgreSQL I/O worker. */
export class KnowledgeCandidateRanker {
  readonly results: RankedKnowledgeCandidate[] = [];
  private readonly vector: number[];
  private readonly topK: number;
  private count = 0;

  constructor(options: KnowledgeRankingOptions) {
    this.vector = normalizeEmbeddingVector(options.vector);
    if (!Number.isInteger(options.topK) || options.topK < 1 || options.topK > 20) {
      throw new Error("Retrieval topK должен быть целым числом от 1 до 20");
    }
    this.topK = options.topK;
  }

  add(candidate: Record<string, unknown>): void {
    if (++this.count > KNOWLEDGE_MAX_SEARCH_CANDIDATES) {
      throw new Error(`Лимит локального retrieval — ${KNOWLEDGE_MAX_SEARCH_CANDIDATES} готовых фрагментов на query; сузьте набор коллекций или используйте индексируемый поиск`);
    }
    let storedVector: number[];
    try {
      storedVector = normalizeEmbeddingVector(typeof candidate.embedding_json === "string"
        ? JSON.parse(candidate.embedding_json) : null);
    } catch {
      throw new Error("Индекс embeddings содержит повреждённый вектор");
    }
    if (storedVector.length !== Number(candidate.embedding_dimensions)) {
      throw new Error("Индекс embeddings содержит неверную размерность вектора");
    }
    const score = cosineSimilarity(this.vector, storedVector);
    if (score === null || !Number.isFinite(score)) {
      throw new Error("Индекс embeddings не позволяет вычислить сходство запроса");
    }
    // Validate even candidates outside topK. Equal scores retain cursor order.
    if (this.results.length === this.topK && score <= this.results[this.results.length - 1]!.score) return;
    const { embedding_json: _embedding, ...metadata } = candidate;
    const before = this.results.findIndex(current => current.score < score);
    this.results.splice(before < 0 ? this.results.length : before, 0, { candidate: metadata, score });
    if (this.results.length > this.topK) this.results.pop();
  }
}

export function rankKnowledgeCandidates(
  candidates: Iterable<Record<string, unknown>>, options: KnowledgeRankingOptions,
): RankedKnowledgeCandidate[] {
  const ranker = new KnowledgeCandidateRanker(options);
  for (const candidate of candidates) ranker.add(candidate);
  return ranker.results;
}
