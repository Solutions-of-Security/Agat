export type DatabaseValue = string | number | bigint | null | Uint8Array;

export interface SyncStatement {
  all(...params: DatabaseValue[]): Record<string, unknown>[];
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
