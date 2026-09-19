import { createHash } from "node:crypto";
import { Worker } from "node:worker_threads";
import type { ExtractedKnowledgeFile } from "./knowledge-file-parser.js";
import { normalizeKnowledgeDocumentInput } from "./knowledge.js";
import type { UploadKnowledgeDocumentInput } from "./types.js";

export const KNOWLEDGE_MAX_FILE_BYTES = 5 * 1024 * 1024;
export const KNOWLEDGE_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export function normalizeKnowledgeUpload(input: UploadKnowledgeDocumentInput) {
  if (!input || !["application/pdf", KNOWLEDGE_DOCX_MEDIA_TYPE].includes(input.mediaType)) {
    throw new Error("Поддерживаются PDF и DOCX");
  }
  const metadata = normalizeKnowledgeDocumentInput({ name: input.name, sourceUri: input.sourceUri, content: "file" });
  const encoded = input.contentBase64;
  if (typeof encoded !== "string" || !encoded || encoded.length > Math.ceil(KNOWLEDGE_MAX_FILE_BYTES / 3) * 4
    || encoded.length % 4 !== 0 || /[^A-Za-z0-9+/=]/.test(encoded)) {
    throw new Error("Нужен файл в base64 размером до 5 МиБ");
  }
  const bytes = Buffer.from(encoded, "base64");
  if (bytes.length > KNOWLEDGE_MAX_FILE_BYTES || bytes.toString("base64") !== encoded) throw new Error("Некорректный base64 или файл больше 5 МиБ");
  return { ...metadata, mediaType: input.mediaType, bytes, originalBase64: encoded,
    originalSha256: createHash("sha256").update(bytes).digest("hex") };
}

let activeParsers = 0;

export async function parseKnowledgeFile(bytes: Uint8Array, mediaType: string): Promise<ExtractedKnowledgeFile> {
  if (activeParsers >= 2) throw new Error("Разбор документов занят. Повторите индексацию позже");
  activeParsers += 1;
  try {
    return await new Promise<ExtractedKnowledgeFile>((resolve, reject) => {
      const moduleUrl = new URL(import.meta.url.endsWith(".ts") ? "./knowledge-file-parser.ts" : "./knowledge-file-parser.js", import.meta.url).href;
      const worker = new Worker(`
        const { parentPort, workerData } = require('node:worker_threads');
        if (workerData.moduleUrl.endsWith('.ts')) require('tsx/esm/api').register();
        const loaded = import(workerData.moduleUrl);
        loaded.then(m => m.extractKnowledgeFile(workerData.bytes, workerData.mediaType))
          .then(result => parentPort.postMessage({ result }), error => parentPort.postMessage({ error: error.message }));
      `, { eval: true, execArgv: [], workerData: { moduleUrl, bytes: new Uint8Array(bytes), mediaType },
        resourceLimits: { maxOldGenerationSizeMb: 256 } });
      let settled = false;
      const finish = (error?: Error, result?: ExtractedKnowledgeFile) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        void worker.terminate();
        if (error) reject(error); else resolve(result!);
      };
      const timer = setTimeout(() => finish(new Error("Разбор документа превысил 20 секунд")), 20_000);
      worker.once("message", (message: { result?: ExtractedKnowledgeFile; error?: string }) => {
        finish(message.error ? new Error(message.error.slice(0, 600)) : undefined, message.result);
      });
      worker.once("error", () => finish(new Error("Не удалось разобрать документ: ошибка или превышение памяти парсера")));
      worker.once("exit", (code) => { if (code !== 0) finish(new Error("Разбор документа прерван")); });
    });
  } finally {
    activeParsers -= 1;
  }
}
