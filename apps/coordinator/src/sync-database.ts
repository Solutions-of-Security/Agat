import type { KnowledgeRankingOptions, RankedKnowledgeCandidate } from "./knowledge-ranking.js";

export type DatabaseValue = string | number | bigint | null | Uint8Array;

export interface SyncStatement {
  all(...params: DatabaseValue[]): Record<string, unknown>[];
  // Consume inside an explicit transaction; early loop exit closes the cursor.
  iterate(...params: DatabaseValue[]): IterableIterator<Record<string, unknown>>;
  // Optional accelerator: identical exact scoring next to the DB cursor.
  rankKnowledgeCandidates?(options: KnowledgeRankingOptions, ...params: DatabaseValue[]): RankedKnowledgeCandidate[];
  get(...params: DatabaseValue[]): Record<string, unknown> | undefined;
  run(...params: DatabaseValue[]): {
    changes: number | bigint;
    lastInsertRowid: number | bigint;
  };
}

export interface SyncDatabase {
  readonly dialect: "sqlite" | "postgresql";
  close(): void;
  exec(sql: string): void;
  prepare(sql: string): SyncStatement;
}
