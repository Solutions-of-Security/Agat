import { AsyncResource } from "node:async_hooks";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Worker } from "node:worker_threads";
import type { StoreOptions } from "./database.js";
import type { KnowledgeSearchHit, KnowledgeSearchRequest } from "./types.js";

export type KnowledgeSearchResult = { hits: KnowledgeSearchHit[] };
export interface KnowledgeSearchService {
  search(token: string, nodeId: string, leaseId: string, request: KnowledgeSearchRequest): Promise<KnowledgeSearchResult>;
  snapshot(): { active: number; queued: number; maxPending: number; accepting: boolean };
}
export class KnowledgeSearchExecutorError extends Error {
  constructor(readonly status: 400 | 401 | 429 | 503 | 504, message: string) { super(message); }
}

// Objects with methods must be initialized in their owning thread. Reject them
// explicitly instead of silently dropping a caller's dependencies or policy.
export type KnowledgeSearchStoreOptions = Omit<StoreOptions, "telemetry" | "artifactObjectStore">;
interface Pending {
  id: number;
  token: string;
  nodeId: string;
  leaseId: string;
  request: KnowledgeSearchRequest;
  deadline: number;
  timer: NodeJS.Timeout;
  resource: AsyncResource;
  resolve: (value: KnowledgeSearchResult) => void;
  reject: (error: Error) => void;
}

/** One persistent owner of the entire synchronous retrieval transaction.
 * The coordinator enables this only for an explicit PostgreSQL opt-in.
 * SQLite remains available to qualification tests of writer-lock contention.
 */
export class KnowledgeSearchExecutor implements KnowledgeSearchService {
  private readonly worker: Worker;
  private readonly queue: Pending[] = [];
  private active?: Pending;
  private sequence = 0;
  private accepting = true;
  private closing = false;
  private stopped = false;
  private closeSent = false;
  private closeTimer?: NodeJS.Timeout;
  private readonly exited: Promise<void>;
  private resolveExit!: () => void;

  private constructor(worker: Worker, private readonly maxPending: number, private readonly timeoutMs: number) {
    this.worker = worker;
    this.exited = new Promise(resolve => { this.resolveExit = resolve; });
    worker.on("error", () => this.fail(new KnowledgeSearchExecutorError(503, "Исполнитель retrieval недоступен; результат активного запроса неизвестен")));
    worker.on("exit", () => {
      clearTimeout(this.closeTimer);
      this.stopped = true;
      if (this.active || this.queue.length || !this.closing) {
        this.fail(new KnowledgeSearchExecutorError(503, "Исполнитель retrieval остановлен; результат активного запроса неизвестен"));
      }
      this.accepting = false;
      this.resolveExit();
    });
    worker.on("message", message => {
      if (message?.type !== "result") return;
      const task = this.active;
      if (!task || message.id !== task.id) {
        this.fail(new KnowledgeSearchExecutorError(503, "Нарушен протокол исполнителя retrieval"));
        return;
      }
      if (performance.now() >= task.deadline) {
        this.fail(new KnowledgeSearchExecutorError(504, "Ответ retrieval получен после deadline; результат может быть сохранён"));
        return;
      }
      if (!message.ok && (message.status === 503 || message.status === 504)) {
        this.fail(new KnowledgeSearchExecutorError(message.status, "PostgreSQL retrieval прерван; результат активного запроса неизвестен"));
        return;
      }
      this.active = undefined;
      if (message.ok) this.finish(task, undefined, message.value);
      else this.finish(task, new KnowledgeSearchExecutorError(message.status === 401 ? 401 : 400, String(message.error)));
      this.dispatch();
    });
  }

  static async create(dbPath: string, options: KnowledgeSearchStoreOptions, limits: { maxPending?: number; timeoutMs?: number } = {}): Promise<KnowledgeSearchExecutor> {
    const maxPending = limits.maxPending ?? 4, timeoutMs = limits.timeoutMs ?? 30_000;
    if (!Number.isInteger(maxPending) || maxPending < 1 || maxPending > 64
      || !Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 60_000) {
      throw new Error("Некорректные ограничения исполнителя retrieval");
    }
    if ("telemetry" in options || "artifactObjectStore" in options) throw new Error("Retrieval worker требует сериализуемые StoreOptions");
    if ((options.stateStoreDriver ?? "sqlite") === "sqlite"
      && (!path.isAbsolute(dbPath) || !fs.existsSync(dbPath))) throw new Error("Retrieval worker требует существующий SQLite-файл");
    const copied = structuredClone(options);
    const compiled = new URL("./knowledge-search-worker.js", import.meta.url);
    const url = fs.existsSync(fileURLToPath(compiled)) ? compiled : new URL("./knowledge-search-worker.ts", import.meta.url);
    const worker = new Worker(url, {
      execArgv: url.pathname.endsWith(".ts") ? ["--import", "tsx"] : [],
      workerData: { dbPath, options: copied },
    });
    const executor = new KnowledgeSearchExecutor(worker, maxPending, timeoutMs);
    try {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => done(new Error("Retrieval worker startup timeout")), 60_000);
        const message = (value: { type?: string }) => {
          if (value?.type === "ready") done();
          else if (value?.type === "startup-error") done(new Error("Retrieval worker initialization failed"));
        };
        const failure = () => done(new Error("Retrieval worker initialization failed"));
        const done = (error?: Error) => {
          clearTimeout(timer); worker.off("message", message); worker.off("error", failure); worker.off("exit", failure);
          if (error) reject(error); else resolve();
        };
        worker.on("message", message); worker.once("error", failure); worker.once("exit", failure);
      });
      if (!executor.accepting) throw new Error("Retrieval worker stopped during initialization");
      return executor;
    } catch (error) {
      executor.fail(new KnowledgeSearchExecutorError(503, "Исполнитель retrieval не запущен"));
      await executor.exited;
      throw error;
    }
  }

  async search(token: string, nodeId: string, leaseId: string, request: KnowledgeSearchRequest): Promise<KnowledgeSearchResult> {
    if (!this.accepting) throw new KnowledgeSearchExecutorError(503, "Исполнитель retrieval закрыт");
    if (this.queue.length + Number(Boolean(this.active)) >= this.maxPending) {
      throw new KnowledgeSearchExecutorError(429, "Очередь retrieval заполнена");
    }
    if (Buffer.byteLength(JSON.stringify(request)) > 1_048_576) throw new KnowledgeSearchExecutorError(400, "Retrieval запрос слишком большой");
    // Freeze the accepted input before the caller can mutate a queued request.
    const copied = structuredClone(request);
    return new Promise((resolve, reject) => {
      const task: Pending = { id: ++this.sequence, token, nodeId, leaseId, request: copied,
        deadline: performance.now() + this.timeoutMs, resource: new AsyncResource("KnowledgeSearch"),
        resolve, reject, timer: setTimeout(() => this.expire(task), this.timeoutMs) };
      this.queue.push(task);
      this.dispatch();
    });
  }

  snapshot(): { active: number; queued: number; maxPending: number; accepting: boolean } {
    return { active: Number(Boolean(this.active)), queued: this.queue.length, maxPending: this.maxPending, accepting: this.accepting };
  }

  async close(): Promise<void> {
    this.accepting = false; this.closing = true;
    for (const task of this.queue.splice(0)) this.finish(task, new KnowledgeSearchExecutorError(503, "Исполнитель retrieval закрывается"));
    this.dispatch();
    await this.exited;
  }

  private dispatch(): void {
    if (this.stopped || this.active) return;
    while (this.queue.length) {
      const task = this.queue.shift()!;
      if (performance.now() >= task.deadline) {
        this.finish(task, new KnowledgeSearchExecutorError(504, "Истёк срок ожидания retrieval в очереди"));
        continue;
      }
      this.active = task;
      try { this.worker.postMessage({ type: "search", id: task.id, deadline: task.deadline, token: task.token, nodeId: task.nodeId, leaseId: task.leaseId, request: task.request }); }
      catch { this.fail(new KnowledgeSearchExecutorError(503, "Не удалось передать retrieval исполнителю")); }
      return;
    }
    if (this.closing && !this.closeSent) {
      this.closeSent = true;
      this.closeTimer = setTimeout(() => this.fail(new KnowledgeSearchExecutorError(503, "Истёк срок остановки retrieval worker")), 5_000);
      this.worker.postMessage({ type: "close" });
    }
  }

  private expire(task: Pending): void {
    if (this.active === task) {
      this.fail(new KnowledgeSearchExecutorError(504, "Retrieval превысил deadline; результат активного запроса неизвестен"));
    } else {
      const index = this.queue.indexOf(task);
      if (index >= 0) { this.queue.splice(index, 1); this.finish(task, new KnowledgeSearchExecutorError(504, "Истёк срок ожидания retrieval в очереди")); }
    }
  }

  private finish(task: Pending, error?: Error, value?: KnowledgeSearchResult): void {
    clearTimeout(task.timer);
    task.resource.runInAsyncScope(() => { if (error) task.reject(error); else task.resolve(value!); });
    task.resource.emitDestroy();
    task.token = "";
  }

  private fail(error: Error): void {
    this.accepting = false; this.closing = true;
    if (this.active) { this.finish(this.active, error); this.active = undefined; }
    for (const task of this.queue.splice(0)) this.finish(task, error);
    if (!this.stopped) void this.worker.terminate().catch(() => {});
  }
}
