import { parentPort, workerData } from "node:worker_threads";
import { AgatStore } from "./database.js";
import { runWithPostgresSystemScope } from "./postgres-database.js";
import type { KnowledgeSearchRequest } from "./types.js";
import type { KnowledgeSearchStoreOptions } from "./knowledge-search-executor.js";

if (!parentPort) throw new Error("Retrieval worker requires a parent port");
const port = parentPort;
let store: AgatStore;
try {
  const { dbPath, options } = workerData as { dbPath: string; options: KnowledgeSearchStoreOptions };
  // Attach without seeding, removing demo data or registering a second replica.
  // Database/schema validation and every supplied trust policy still apply.
  store = runWithPostgresSystemScope(() => new AgatStore(dbPath, { ...options, schemaOnly: true }));
  port.postMessage({ type: "ready" });
} catch {
  port.postMessage({ type: "startup-error" });
  port.close();
}

port.on("message", (message: { type: string; id: number; deadline?: number; token: string; nodeId: string; leaseId: string; request: KnowledgeSearchRequest }) => {
  runWithPostgresSystemScope(() => {
    if (message.type === "close") {
      store.close(); port.close(); return;
    }
    if (message.type !== "search") throw new Error("Unknown retrieval worker operation");
    try {
      // Authentication happens after admission/queueing, with the same policy
      // options as the main store, not a cached node row from HTTP admission.
      const node = store.authenticateNode(message.token);
      if (!node || String(node.id) !== message.nodeId) {
        port.postMessage({ type: "result", id: message.id, ok: false, status: 401, error: "Токен узла недействителен" });
        return;
      }
      const value = store.searchKnowledge(message.nodeId, message.leaseId, message.request);
      port.postMessage({ type: "result", id: message.id, ok: true, value });
    } catch (error) {
      const code = error && typeof error === "object" && "code" in error ? error.code : undefined;
      const status = code === "AGAT_COMMIT_UNKNOWN" ? 503 : code === "AGAT_DEADLINE" || code === "57014" ? 504 : 400;
      port.postMessage({ type: "result", id: message.id, ok: false, status,
        error: error instanceof Error ? error.message : "Ошибка retrieval" });
    }
  }, message.deadline);
});
