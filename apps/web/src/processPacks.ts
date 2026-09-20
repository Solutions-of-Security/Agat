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
  knowledge: { name: string; description: string; documents: Array<{ name: string; sourceUri: string; content: string }> };
  tools: Array<{ id: string; name: string; requirement: string }>;
  policies: string[]; sample: { input: string; expected: string }; limitations: string;
  process: { graph: ProcessGraph };
}
export interface ProcessPackInput { version: number; manifestSha256: string; model: string }
export interface ProcessPackPreview { manifest: ProcessPackManifest; installation: ProcessPackInstallation | null; preflight: ScenarioPreflight }
