import assert from "node:assert/strict";
import test from "node:test";
import { Worker } from "node:worker_threads";
import { PostgresResponseBuffer, PostgresResponseTimeout, POSTGRES_RESPONSE_HEADER_BYTES } from "../src/postgres-response-buffer.js";

const workerCode = `
  const { parentPort } = require('node:worker_threads');
  parentPort.on('message', ({ shared, value, notifyFirst }) => {
    const header = new Int32Array(shared, 0, 4);
    const publish = () => {
      const bytes = Buffer.from(JSON.stringify(value));
      new Uint8Array(shared, ${POSTGRES_RESPONSE_HEADER_BYTES}).set(bytes);
      Atomics.store(header, 1, bytes.length);
      Atomics.store(header, 0, 1);
      Atomics.notify(header, 0, 1);
    };
    if (notifyFirst) {
      Atomics.notify(header, 0, 1);
      setTimeout(publish, 25);
    } else publish();
  });
`;

test("one shared allocation serves repeated large/small responses without corrupting previous results", async () => {
  const worker = new Worker(workerCode, { eval: true });
  const channel = new PostgresResponseBuffer(1024 * 1024);
  const allocations = new Set<SharedArrayBuffer>();
  const answers: string[] = [];
  try {
    for (let sequence = 0; sequence < 100; sequence++) {
      const value = { sequence, content: sequence % 3 === 0 ? "Данные 🐈".repeat(20_000) : "ok" };
      const answer = channel.exchange(shared => {
        allocations.add(shared);worker.postMessage({ shared, value });
      }, 5000);
      assert.deepEqual(JSON.parse(answer), value);
      answers.push(answer);
    }
    assert.equal(allocations.size, 1, "response storage must not grow with SQL call count");
    answers.forEach((answer, sequence) => assert.equal(JSON.parse(answer).sequence, sequence));
  } finally { await worker.terminate(); }
});

test("notification without a ready flag does not expose the previous response", async () => {
  const worker = new Worker(workerCode, { eval: true });
  const channel = new PostgresResponseBuffer(1024);
  try {
    assert.equal(channel.exchange(shared => worker.postMessage({ shared, value: "old" }), 5000), '"old"');
    assert.equal(channel.exchange(shared => worker.postMessage({ shared, value: "new", notifyFirst: true }), 5000), '"new"');
  } finally { await worker.terminate(); }
});

test("already-published responses, timeout and corrupted length remain explicit", () => {
  const channel = new PostgresResponseBuffer(100);
  const publish = (shared: SharedArrayBuffer, length: number) => {
    const header = new Int32Array(shared, 0, 4);
    new Uint8Array(shared, POSTGRES_RESPONSE_HEADER_BYTES).set(Buffer.from('"ready"'));
    Atomics.store(header, 1, length);Atomics.store(header, 0, 1);
  };
  assert.equal(channel.exchange(shared => publish(shared, 7), 100), '"ready"');
  assert.throws(() => channel.exchange(shared => publish(shared, 101), 100), /повреждённый ответ/);
  assert.throws(() => channel.exchange(() => {}, 5), PostgresResponseTimeout);
});
