import assert from "node:assert/strict";
import test from "node:test";
import { KnowledgeCandidateRanker, rankKnowledgeCandidates } from "../src/knowledge-ranking.js";

const row = (id: number, vector: number[]) => ({ id, embedding_json: JSON.stringify(vector),
  embedding_dimensions: vector.length, content: `Source ${id}`, content_sha256: `fixture-${id}` });

test("shared ranker keeps stable unrounded topK and sends only selected metadata", () => {
  const candidates = [row(1, [1, 1.000000001]), row(2, [1, 1]), row(3, [2, 2]), row(4, [0, 1])];
  const original = structuredClone(candidates);
  const result = rankKnowledgeCandidates(candidates, { vector: [1, 0], topK: 3 });
  assert.deepEqual(result.map(hit => hit.candidate.id), [2, 3, 1]);
  assert.equal(result[0]!.score, result[1]!.score);
  assert.ok(result[1]!.score > result[2]!.score);
  assert.ok(result.every(hit => !("embedding_json" in hit.candidate)));
  assert.equal(result[0]!.candidate.content_sha256, "fixture-2");
  assert.deepEqual(candidates, original);
});

test("shared ranker validates late candidates outside topK and closes the input iterator on failure", () => {
  for (const invalid of [
    { ...row(3, [0, 1]), embedding_json: "broken" },
    { ...row(3, [0, 1]), embedding_json: "[0,0]" },
    { ...row(3, [0, 1]), embedding_dimensions: 3 },
    row(3, [1, 0, 0]),
  ]) {
    let closed = false;
    function* candidates() {
      try { yield row(1, [1, 0]); yield row(2, [0, 1]); yield invalid; }
      finally { closed = true; }
    }
    assert.throws(() => rankKnowledgeCandidates(candidates(), { vector: [1, 0], topK: 1 }), /Индекс embeddings/);
    assert.equal(closed, true);
  }
});

test("invalid ranker options fail before opening a cursor", () => {
  let consumed = false;
  function* candidates() { consumed = true; yield row(1, [1, 0]); }
  for (const topK of [0, 21, 1.5, Number.NaN]) {
    assert.throws(() => rankKnowledgeCandidates(candidates(), { vector: [1, 0], topK }), /topK/);
  }
  assert.throws(() => new KnowledgeCandidateRanker({ vector: [0, 0], topK: 1 }), /нулевым/);
  assert.equal(consumed, false);
});
