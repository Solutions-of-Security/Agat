import { Worker } from "node:worker_threads";
import { BusinessReportOfficeError, normalizeOfficeFormat, type BusinessReportOfficeInput } from "./business-report-office-document.js";

let active = 0;
export async function renderBusinessReportOffice(input: BusinessReportOfficeInput): Promise<{ bytes: Buffer; documentSha256: string }> {
  normalizeOfficeFormat(input.format);
  if (active >= 2) throw new BusinessReportOfficeError("renderer_busy", "Экспорт документов занят. Повторите запрос позже", 503);
  active++;
  try {
    return await new Promise((resolve, reject) => {
      const moduleUrl = new URL(import.meta.url.endsWith(".ts") ? "./business-report-office-renderer.ts" : "./business-report-office-renderer.js", import.meta.url).href;
      const worker = new Worker(`
        const {parentPort, workerData} = require('node:worker_threads');
        if (workerData.moduleUrl.endsWith('.ts')) require('tsx/esm/api').register();
        import(workerData.moduleUrl).then(m => m.renderOfficeBytes(workerData.input)).then(result => {
          const bytes = new Uint8Array(result.bytes);
          parentPort.postMessage({result: {bytes, documentSha256: result.documentSha256}}, [bytes.buffer]);
        }, error => parentPort.postMessage({error: {code: error.code || 'renderer_unavailable', status: error.status || 503, message: error.message || 'Не удалось подготовить экспорт'}}));
      `, { eval: true, execArgv: [], workerData: { moduleUrl, input }, resourceLimits: { maxOldGenerationSizeMb: 256 } });
      let settled = false;
      const finish = (error?: Error, value?: { bytes: Uint8Array; documentSha256: string }) => {
        if (settled) return; settled = true; clearTimeout(timer); void worker.terminate();
        if (error) reject(error); else resolve({ bytes: Buffer.from(value!.bytes), documentSha256: value!.documentSha256 });
      };
      const timer = setTimeout(() => finish(new BusinessReportOfficeError("renderer_timeout", "Экспорт превысил 20 секунд. Исходный Markdown доступен для скачивания", 503)), 20_000);
      worker.once("message", message => finish(message.error ? new BusinessReportOfficeError(message.error.code, String(message.error.message).slice(0, 600), message.error.status) : undefined, message.result));
      worker.once("error", () => finish(new BusinessReportOfficeError("renderer_unavailable", "Не удалось подготовить экспорт: ошибка или превышение памяти renderer", 503)));
      worker.once("exit", code => { if (code !== 0) finish(new BusinessReportOfficeError("renderer_unavailable", "Выполнение экспорта прервано", 503)); });
    });
  } finally { active--; }
}
