import { createHash } from "node:crypto";

export const sha256 = (value) => createHash("sha256").update(value).digest("hex");
export class QualificationError extends Error {
  constructor(code) { super(code); this.code = code; }
}
export function requireEvidence(condition, code) {
  if (!condition) throw new QualificationError(code);
}

// Only fixed codes leave the harness. Never serialize HTTP errors, model text,
// SDK exceptions, environment variables, credentials, or raw workflow payloads.
export function loopbackOrigin(value) {
  let url;
  try { url = new URL(value); } catch { throw new QualificationError("LOCAL_ENDPOINT_REQUIRED"); }
  requireEvidence(url.protocol === "http:" && ["127.0.0.1", "[::1]"].includes(url.hostname)
    && !url.username && !url.password && !url.search && !url.hash && url.pathname === "/", "LOCAL_ENDPOINT_REQUIRED");
  return url.origin;
}
export function temporalAddress(value) {
  requireEvidence(/^(127\.0\.0\.1|\[::1\]):\d{1,5}$/.test(value), "LOCAL_TEMPORAL_REQUIRED");
  const port = Number(value.slice(value.lastIndexOf(":") + 1));
  requireEvidence(port > 0 && port <= 65535, "LOCAL_TEMPORAL_REQUIRED");
  return value;
}
export function safeVersion(value) {
  requireEvidence(typeof value === "string" && /^[a-zA-Z0-9][a-zA-Z0-9._+() -]{0,100}$/.test(value), "VERSION_UNAVAILABLE");
  return value;
}
export function modelIdentity(tags, show, name, capability) {
  const canonical = name.includes(":") ? name : `${name}:latest`;
  const item = tags.models?.find((model) => model.name === canonical || model.name === name);
  requireEvidence(item && /^[a-f0-9]{64}$/.test(item.digest), "MODEL_NOT_INSTALLED");
  requireEvidence(show.capabilities?.includes(capability) && !show.remote_host && !show.remote_model, "LOCAL_MODEL_CAPABILITY_REQUIRED");
  if (capability === "completion") {
    requireEvidence(/^qwen3(?::|-)/.test(name) && ["qwen3", "qwen3moe"].includes(show.details?.family), "QWEN3_REQUIRED");
  }
  return { name, digest: item.digest, family: safeVersion(show.details.family),
    parameters: safeVersion(show.details.parameter_size), quantization: safeVersion(show.details.quantization_level) };
}
export function validateVector(vector) {
  requireEvidence(Array.isArray(vector) && vector.length > 16 && vector.every(Number.isFinite)
    && vector.some((value) => value !== 0), "EMBEDDING_VECTOR_INVALID");
  return { dimensions: vector.length, vectorSha256: sha256(JSON.stringify(vector)) };
}
export function validateIngestion(exported, installation, fixture, dimensions) {
  const collections = exported.collections.filter((item) => installation.knowledgeCollectionIds.includes(item.id));
  requireEvidence(collections.length === 1 && collections[0].embeddingModel === fixture.embeddingModel, "COLLECTION_MISMATCH");
  const documents = collections[0].documents;
  requireEvidence(documents.length === fixture.sources.length, "DOCUMENT_COUNT_MISMATCH");
  return fixture.sources.map((source) => {
    const doc = documents.find((item) => item.sourceUri === source.uri);
    // Public ingestion normalizes CRLF and trims outer whitespace.
    const normalized = source.content.replace(/\r\n?/g, "\n").trim();
    const expected = sha256(normalized);
    requireEvidence(doc?.status === "ready" && doc.content === normalized && doc.contentSha256 === expected, "INGESTION_CONTENT_MISMATCH");
    requireEvidence(doc.chunks.length > 0 && doc.chunks.every((chunk) => chunk.embeddingModel === fixture.embeddingModel
      && chunk.embeddingDimensions === dimensions && chunk.embeddedAt && chunk.contentSha256 === sha256(chunk.content)
      && doc.content.slice(chunk.charStart, chunk.charEnd) === chunk.content), "INGESTION_EMBEDDINGS_MISSING");
    return { sourceUri: source.uri, fixtureSha256: sha256(source.content), documentSha256: expected, chunks: doc.chunks.map((chunk) => ({
      id: chunk.id, sha256: chunk.contentSha256, dimensions: chunk.embeddingDimensions,
    })) };
  });
}
export function validateCitations(trace, installation, ingestion, model) {
  requireEvidence(trace.truncated === false, "TRACE_TRUNCATED");
  const roles = Object.entries(installation.agentIds);
  requireEvidence(roles.length === 3 && ["researcher", "analyst", "reviewer"].every((role) => Object.hasOwn(installation.agentIds, role)), "PACK_ROLES_MISMATCH");
  const events = trace.events.filter((event) => event.type === "knowledge.retrieved");
  const known = new Map();
  const retrievals = roles.map(([role, agentId]) => {
    const stages = trace.run.stages.filter((stage) => stage.agent?.id === agentId);
    requireEvidence(stages.length === 1 && stages[0].status === "completed", "AGENT_STAGE_INCOMPLETE");
    const stage = stages[0];
    const retrieval = events.filter((event) => event.stageId === stage.id);
    requireEvidence(retrieval.length === 1, "RETRIEVAL_COUNT_MISMATCH");
    const data = retrieval[0].data;
    requireEvidence(data.queries.length > 0 && data.queries.every((query) => !Object.hasOwn(query, "vector")
      && /^[a-f0-9]{64}$/.test(query.vectorSha256) && query.dimensions === ingestion[0].chunks[0].dimensions
      && query.embeddingModel === "embeddinggemma"
      && query.collectionIds.length === 1 && query.collectionIds[0] === installation.knowledgeCollectionIds[0]), "QUERY_PROVENANCE_INVALID");
    const hits = data.hits.map((hit) => {
      const source = ingestion.find((item) => item.sourceUri === hit.provenance.sourceUri);
      const chunk = source?.chunks.find((item) => item.id === hit.provenance.chunkId);
      requireEvidence(chunk && hit.provenance.collectionId === installation.knowledgeCollectionIds[0]
        && hit.provenance.documentSha256 === source.documentSha256 && hit.provenance.chunkSha256 === chunk.sha256
        && sha256(hit.content) === chunk.sha256 && /^K\d+$/.test(hit.marker) && Number.isFinite(hit.score), "RETRIEVAL_PROVENANCE_INVALID");
      requireEvidence(!known.has(hit.marker) || known.get(hit.marker) === source.sourceUri, "CITATION_COLLISION");
      known.set(hit.marker, source.sourceUri);
      return { marker: hit.marker, sourceUri: source.sourceUri, chunkSha256: chunk.sha256, score: hit.score };
    });
    requireEvidence(ingestion.every((source) => hits.some((hit) => hit.sourceUri === source.sourceUri)), "REQUIRED_SOURCE_NOT_RETRIEVED");
    const markers = [...new Set([...stage.output.matchAll(/\[(K\d+)\]/g)].map((match) => match[1]))];
    requireEvidence(markers.length > 0 && markers.every((marker) => known.has(marker)), "CITATION_UNKNOWN_OR_MISSING");
    requireEvidence(ingestion.every((source) => markers.some((marker) => known.get(marker) === source.sourceUri)), "REQUIRED_SOURCE_NOT_CITED");
    requireEvidence(stage.agent.model === model && stage.metrics?.model === model && stage.metrics.modelCalls > 0, "EXECUTED_MODEL_MISMATCH");
    return { role, stageId: stage.id, model, outputSha256: sha256(stage.output), markers, hits,
      modelCalls: stage.metrics.modelCalls,
      queries: data.queries.map((query) => ({ dimensions: query.dimensions, vectorSha256: query.vectorSha256 })) };
  });
  return retrievals;
}

// A deliberately bounded acceptance rubric for the two synthetic documents.
// It does not claim to prove arbitrary natural-language conclusions.
export function reportRubric(text) {
  const normalized = text.toLowerCase().replace(/[−–—]/g, "-")
    .replace(/\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}/g, "$1/$2")
    .replace(/\\(?:times|cdot)/g, "*").replace(/\\(?:text|mathrm)\{([^{}]+)\}/g, "$1")
    .replace(/[\\$`{}]/g, "").replace(/\*\*/g, "").replace(/\s+/g, " ");
  const criteria = {
    periods: /2026/.test(normalized) && /июл|2026-07/.test(normalized) && /август|2026-08/.test(normalized),
    requests: /\b100\b/.test(normalized) && /\b120\b/.test(normalized)
      && /(?:\+\s*20\s*%|(?:рост|увелич)[^.!?]{0,45}(?<![\d.,])20\s*%|(?<![\d.,])20\s*%[^.!?]{0,25}(?:рост|увелич))/.test(normalized),
    julyAverage: /(?<![\d.,])400\s*\/\s*100\s*=\s*4(?:[.,]0+)?(?![\d.,])/.test(normalized),
    augustAverage: /(?<![\d.,])360\s*\/\s*120\s*=\s*3(?:[.,]0+)?(?![\d.,])/.test(normalized),
    averageChange: /(?:-\s*25\s*%|(?:сни[жз]|уменьш|сокра)[^.!?]{0,65}(?<![\d.,])25\s*%)/.test(normalized),
    julyReopened: /(?<![\d.,])8\s*\/\s*100\s*(?:[×*]\s*100\s*%?\s*)?=\s*(?:8\s*%|0[.,]08(?![\d.,]))/.test(normalized),
    augustReopened: /(?<![\d.,])6\s*\/\s*120\s*(?:[×*]\s*100\s*%?\s*)?=\s*(?:5\s*%|0[.,]05(?![\d.,]))/.test(normalized),
    percentagePoints: /(?:-\s*3\s*(?:п\.?\s*п\.?|процентн)|(?:сни[жз]|уменьш|сокра)[^.!?]{0,45}(?<![\d.,])3\s*(?:п\.?\s*п\.?|процентн))/.test(normalized),
    synthetic: /синтетич|учебн/.test(normalized),
    noCausalClaim: /причин[^.!?]{0,100}(?:неизвест|нельзя|невозмож|не\s+(?:установ|определ|доказ|содерж|позвол))|(?:нельзя|невозмож|не\s+(?:позвол|доказ))[^.!?]{0,100}причин/.test(normalized),
    citations: /\[K\d+\]/.test(text),
  };
  const percentages = [...new Set([...normalized.matchAll(/[+-]?\s*\d{1,4}(?:[.,]\d{1,4})?\s*%/g)]
    .map((match) => match[0].replace(/\s/g, "")))].slice(0, 40);
  return { passed: Object.values(criteria).every(Boolean), criteria, numericObservations: { percentages } };
}

export function historyEvidence(history) {
  // No memo, arguments, results, failure messages, stack traces or host identity.
  return (history.events ?? []).map((event) => ({
    eventId: String(event.eventId), type: Number(event.eventType),
    ...(event.workflowTaskCompletedEventAttributes ? {
      qualificationWorker: /^qualification-worker-[12]$/.test(event.workflowTaskCompletedEventAttributes.identity)
        ? event.workflowTaskCompletedEventAttributes.identity : "redacted",
    } : {}),
  }));
}
