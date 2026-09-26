import assert from "node:assert/strict";
import { createHash } from "node:crypto";

const digest = (value: string) => createHash("sha256").update(value).digest("hex");
export type RagFixture = { embeddingModel: string;
  sources: Array<{ id: string; name: string; sourceUri: string; content: string }> };
type JsonTransport = (url: string, body?: unknown, timeoutMs?: number, cancelled?: AbortSignal) => Promise<any>;
export const embeddingSettings = { truncate: false, keep_alive: "5m", options: { num_ctx: 2048 } };

function localOrigin(value: string) {
  const url = new URL(value);
  assert.ok(url.protocol === "http:" && url.hostname === "127.0.0.1" && url.port && !url.username && !url.password
    && !url.search && !url.hash && url.pathname === "/", "Explicit loopback embedding endpoint required");
  return url.origin;
}

export function validateRag(value: RagFixture) {
  assert.ok(typeof value?.embeddingModel === "string" && /^[a-z0-9_.:-]{1,100}$/.test(value.embeddingModel));
  assert.ok(Array.isArray(value.sources) && value.sources.length === 2, "Exactly two diagnostic sources required");
  assert.equal(new Set(value.sources.map(source => source.id)).size, 2);
  assert.equal(new Set(value.sources.map(source => source.sourceUri)).size, 2);
  for (const source of value.sources) {
    assert.match(source.id, /^[a-z][a-z0-9_-]{0,40}$/);
    assert.ok(typeof source.name === "string" && source.name.length > 0 && source.name.length <= 100);
    assert.match(source.sourceUri, /^agat:\/\/decision-rag\/[a-z0-9-]{1,80}$/);
    assert.ok(typeof source.content === "string" && source.content.length > 0 && source.content.length <= 4000
      && source.content === source.content.trim() && !source.content.includes("\r")
      && Buffer.byteLength(source.content) <= 8192, "One bounded, normalized chunk per source required");
  }
}

export async function embeddingIdentity(url: string, name: string, expectedDigest: string, transport: JsonTransport) {
  url = localOrigin(url);
  assert.match(expectedDigest, /^[a-f0-9]{64}$/);
  const [tags, show, version] = await Promise.all([transport(`${url}/api/tags`),
    transport(`${url}/api/show`, { model: name }), transport(`${url}/api/version`)]);
  const found = tags.models?.find((entry: Record<string, unknown>) => entry.name === name);
  assert.equal(found?.digest, expectedDigest, "Embedding weights changed");
  assert.ok(!found.remote_host && !found.remote_model && !show.remote_host && !show.remote_model
    && show.capabilities?.includes("embedding"), "Installed local embedding model required");
  return { name, digest: expectedDigest, version: version.version, details: show.details,
    descriptionSha256: digest(JSON.stringify(show)), generation: embeddingSettings };
}

/** Convert the actual worker's OpenAI embedding request to native Ollama without truncation. */
export async function forwardEmbedding(input: any, model: string, target: string,
  transport: JsonTransport, cancelled: AbortSignal) {
  target = localOrigin(target);
  assert.equal(input.model, model);
  assert.deepEqual(Object.keys(input).sort(), ["input", "model"]);
  assert.ok(Array.isArray(input.input) && input.input.length >= 1 && input.input.length <= 2
    && input.input.every((text: unknown) => typeof text === "string" && text.length > 0 && Buffer.byteLength(text) <= 8192));
  const result = await transport(`${target}/api/embed`, { model, input: input.input, ...embeddingSettings }, 30_000, cancelled);
  assert.equal(result.model, model);
  assert.ok(Array.isArray(result.embeddings) && result.embeddings.length === input.input.length);
  const dimensions = result.embeddings[0]?.length;
  assert.ok(Number.isInteger(dimensions) && dimensions >= 1 && dimensions <= 4096);
  assert.ok(result.embeddings.every((vector: unknown) => Array.isArray(vector) && vector.length === dimensions
    && vector.every(Number.isFinite) && vector.some(value => value !== 0)), "Invalid or mixed embedding vectors");
  assert.ok(Number.isInteger(result.prompt_eval_count) && result.prompt_eval_count > 0);
  assert.ok(Number.isSafeInteger(result.total_duration) && result.total_duration >= 0
    && Number.isSafeInteger(result.load_duration) && result.load_duration >= 0 && result.load_duration <= result.total_duration);
  return { body: { model, data: result.embeddings.map((vector: number[], index: number) => ({ index, embedding: vector })),
      usage: { prompt_tokens: result.prompt_eval_count, total_tokens: result.prompt_eval_count } },
    evidence: { model, items: input.input.length, dimensions, inputSha256: input.input.map(digest),
      vectorSha256: result.embeddings.map((vector: number[]) => digest(JSON.stringify(vector))),
      inputTokens: result.prompt_eval_count, nativeTotalMs: result.total_duration / 1e6, nativeLoadMs: result.load_duration / 1e6 } };
}

export function verifyIngestion(exported: any, collectionId: string, rag: RagFixture) {
  const collection = exported.collections.find((entry: any) => entry.id === collectionId);
  assert.ok(collection && collection.embeddingModel === rag.embeddingModel && collection.documents.length === 2);
  const sources = rag.sources.map(source => {
    const document = collection.documents.find((entry: any) => entry.sourceUri === source.sourceUri);
    assert.ok(document && document.status === "ready" && document.content === source.content
      && document.contentSha256 === digest(source.content) && document.chunks.length === 1, "Ingestion source changed");
    const chunk = document.chunks[0];
    assert.ok(chunk.content === source.content && chunk.contentSha256 === digest(source.content)
      && chunk.charStart === 0 && chunk.charEnd === source.content.length && chunk.embeddedAt
      && chunk.embeddingModel === rag.embeddingModel && Number.isInteger(chunk.embeddingDimensions) && chunk.embeddingDimensions > 0,
      "Embedding/provenance not persisted");
    return { id: source.id, sourceUri: source.sourceUri, documentSha256: document.contentSha256,
      chunkId: chunk.id, chunkSha256: chunk.contentSha256, dimensions: chunk.embeddingDimensions };
  });
  assert.equal(new Set(sources.map(source => source.dimensions)).size, 1, "Embedding dimensions changed during ingestion");
  return { collectionId, embeddingModel: rag.embeddingModel, dimensions: sources[0]!.dimensions, sources };
}

export function verifyRetrieval(trace: any, stage: any, ingestion: ReturnType<typeof verifyIngestion>) {
  const events = trace.events.filter((event: any) => event.type === "knowledge.retrieved" && event.stageId === stage.id);
  assert.equal(events.length, 1, "Exactly one retrieval event per stage required");
  const data = events[0].data;
  assert.ok(Array.isArray(data.queries) && data.queries.length === 1);
  const query = data.queries[0];
  assert.ok(!Object.hasOwn(query, "vector") && /^[a-f0-9]{64}$/.test(query.vectorSha256)
    && query.dimensions === ingestion.dimensions && query.embeddingModel === ingestion.embeddingModel);
  assert.deepEqual(query.collectionIds, [ingestion.collectionId]);
  assert.ok(Array.isArray(data.hits) && data.hits.length === 2, "Both source chunks must be retrieved");
  const verifyHit = (hit: any) => {
    const source = ingestion.sources.find(item => item.chunkId === hit.provenance.chunkId);
    assert.ok(source && hit.provenance.collectionId === ingestion.collectionId && source.sourceUri === hit.provenance.sourceUri
      && source.documentSha256 === hit.provenance.documentSha256 && source.chunkSha256 === hit.provenance.chunkSha256
      && digest(hit.content) === source.chunkSha256 && /^K[0-9]+$/.test(hit.marker) && Number.isFinite(hit.score), "Retrieval provenance mismatch");
    return { sourceId: source.id, marker: hit.marker, chunkSha256: source.chunkSha256, score: hit.score };
  };
  const hits = data.hits.map(verifyHit);
  assert.equal(new Set(hits.map((hit: any) => hit.sourceId)).size, 2);
  assert.equal(new Set(hits.map((hit: any) => hit.marker)).size, 2);
  // A later stage can legitimately retain a citation from an earlier stage.
  // Coordinator allocates fresh K markers per retrieval, including the same
  // source chunk. Only earlier/current stages of this run authorize aliases.
  assert.ok(Number.isInteger(stage.position));
  const visibleStages = new Set(trace.run.stages.filter((item: any) => Number.isInteger(item.position)
    && item.position <= stage.position).map((item: any) => item.id));
  assert.ok(visibleStages.has(stage.id));
  const known = new Map<string, ReturnType<typeof verifyHit>>();
  for (const event of trace.events.filter((item: any) => item.type === "knowledge.retrieved" && visibleStages.has(item.stageId))) {
    for (const hit of event.data.hits) {
      const verified = verifyHit(hit), previous = known.get(verified.marker);
      assert.ok(!previous || (previous.sourceId === verified.sourceId && previous.chunkSha256 === verified.chunkSha256), "Citation alias changed source");
      known.set(verified.marker, verified);
    }
  }
  const markers: string[] = [...new Set<string>([...String(stage.output).matchAll(/\[(K[0-9]+)\]/g)].map(match => match[1]!))];
  const citedSourceIds = [...new Set(markers.flatMap(marker => known.has(marker) ? [known.get(marker)!.sourceId] : []))];
  return { queries: [{ vectorSha256: query.vectorSha256, dimensions: query.dimensions }], hits,
    knownCitations: [...known.values()], outputMarkers: markers, unknownMarkers: markers.filter(marker => !known.has(marker)),
    citedSourceIds,
    // Citation presence is measured separately from transport completion; it is
    // not a proof that the model interpreted a retrieved source correctly.
    bothSourcesCited: ingestion.sources.every(source => citedSourceIds.includes(source.id)) };
}
