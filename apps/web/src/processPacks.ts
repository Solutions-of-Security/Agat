import type { ScenarioPreflight } from "./scenarioPreflight";
import type { ProcessGraph } from "./types";

export interface ProcessPackInstallation {
  packId: string; version: number; manifestSha256: string; model: string; processId: string; processVersion: number;
  agentIds: Record<string, string>; knowledgeCollectionIds: string[]; installedAt: string;
}
export interface ProcessPackManifest {
  id: string; version: number; manifestSha256: string; name: string; description: string; execution: string;
  defaults: { model: string; embeddingModel: string };
  roles: Array<{ id: string; name: string; responsibility: string; systemPrompt: string }>;
  knowledge: { name: string; description: string; documents: Array<{ name: string; sourceUri: string; content: string }>; mode?: "project_collections" };
  tools: Array<{ id: string; name: string; requirement: string }>;
  policies: string[]; sample: { input: string; expected: string }; limitations: string;
  process: { graph: ProcessGraph };
  contractVersion?: "solution-pack.v1";
  schemas?: { input: Record<string, unknown>; output: Record<string, unknown> };
  profile?: { id: string; qualification: "unqualified"; runtime: "single"; runtimeProfile: "tool_loop_v1"; maxIterations: number };
  evaluation?: { gate: string; status: "required"; humanAcceptance: "required" };
}
export interface ProcessPackInput { version: number; manifestSha256: string; model: string; knowledgeCollectionIds?: string[] }
export interface ProcessPackActivation { version: number; manifestSha256: string; expectedActiveManifestSha256: string | null }
export interface ProcessPackChange { section: string; before: unknown; after: unknown }
export interface ProcessPackHistoryEntry { manifest: ProcessPackManifest; installation: ProcessPackInstallation }
export interface ProcessPackCatalog {
  packs: Array<ProcessPackManifest & { installation: ProcessPackInstallation | null }>;
  releases: Array<ProcessPackManifest & { installation: ProcessPackInstallation | null }>;
  activeInstallation: ProcessPackInstallation | null;
}
export interface ProcessPackPreview {
  manifest: ProcessPackManifest; installation: ProcessPackInstallation | null; preflight: ScenarioPreflight;
  activeInstallation: ProcessPackInstallation | null; changes: ProcessPackChange[];
}

export function reportPackRequest(question: string, period: string, requirements: string): { input: string; error: null } | { input: null; error: string } {
  if (!question.trim() || question.length > 8000) return { input: null, error: "Укажите вопрос для отчёта (до 8000 символов)." };
  if (!period.trim() || period.length > 160) return { input: null, error: "Укажите период документов (до 160 символов)." };
  const items = [...new Set(requirements.split(/\r?\n/).map(line => line.trim()).filter(Boolean))];
  if (items.length > 20 || items.some(item => item.length > 8000)) return { input: null, error: "Укажите до 20 требований, каждое — до 8000 символов, с новой строки." };
  const input = JSON.stringify({ schemaVersion: "report-request.v1", question: question.trim(), period: period.trim(), requirements: items });
  if (new TextEncoder().encode(input).byteLength > 100_000) return { input: null, error: "Вопрос и требования превышают 100 000 байт. Сократите текст перед запуском." };
  return { input, error: null };
}
