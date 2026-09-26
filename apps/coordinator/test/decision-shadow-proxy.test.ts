import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { DecisionProxyCancelled, forwardDecisionRequest } from "../../../scripts/lib/decision-shadow-proxy.js";

async function listen(server: http.Server) {
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();assert.ok(address && typeof address === "object");
  return `http://127.0.0.1:${address.port}`;
}
async function close(server: http.Server) {
  server.closeAllConnections();if (server.listening) await new Promise<void>(resolve => server.close(() => resolve()));
}
async function until(check: () => boolean) {
  const deadline = Date.now() + 1500;
  while (!check()) { assert.ok(Date.now() < deadline, "Expected transport event did not arrive");await delay(5); }
}
function call(url: string, optIn = true) {
  let error: Error | undefined;
  const request = http.request(url + "/v1/decisions", { method: "POST", headers: {
    "Content-Type": "application/json", "X-Agat-Decision-Profile": "a".repeat(64),
    ...(optIn ? { "X-Agat-Decision-Cancel-On-Disconnect": "1" } : {}) } });
  const finished = new Promise<{ status: number; body: string } | null>(resolve => {
    request.on("error", value => { error = value;resolve(null); });
    request.on("response", response => {
      let body = "";response.on("data", chunk => { body += chunk; });
      response.on("end", () => resolve({ status: response.statusCode!, body }));
    });
  });
  request.end('{"state":"Запрос для проверки транспорта"}');
  return { request, finished, error: () => error };
}
async function fixture(handler: http.RequestListener) {
  const upstream = http.createServer(handler), target = await listen(upstream);
  const owner = new AbortController(), errors: unknown[] = [];let completed = 0;
  const proxy = http.createServer(async (req, res) => {
    try {
      const chunks = [];for await (const chunk of req) chunks.push(chunk);
      const response = await forwardDecisionRequest(req, res, target,
        req.method === "POST" ? Buffer.concat(chunks).toString("utf8") : undefined, owner.signal);
      if (!res.destroyed) res.writeHead(response.httpStatus, { "content-type": "application/json" }).end(response.text);
    } catch (error) { errors.push(error);if (!res.destroyed) res.writeHead(502).end(); }
    finally { completed++; }
  });
  const url = await listen(proxy);
  return { url, owner, errors, completed: () => completed,
    cleanup: async () => { owner.abort();await close(proxy);await close(upstream); } };
}

test("observer forwards exact body/profile/opt-in and does not cancel after a completed body or response", { timeout: 5000 }, async () => {
  const seen: Array<{ body: string; profile: unknown; cancel: unknown; premature: boolean }> = [];
  const state = await fixture((req, res) => {
    const row = { body: "", profile: req.headers["x-agat-decision-profile"],
      cancel: req.headers["x-agat-decision-cancel-on-disconnect"], premature: false };seen.push(row);
    req.on("data", chunk => { row.body += chunk; });
    req.on("end", () => setTimeout(() => res.end('{"status":"ok"}'), 30));
    res.on("close", () => { row.premature = !res.writableFinished; });
  });
  try {
    for (let i = 0; i < 2; i++) assert.deepEqual(await call(state.url).finished, { status: 200, body: '{"status":"ok"}' });
    await until(() => state.completed() === 2);
    assert.deepEqual(state.errors, []);assert.equal(seen.length, 2);
    for (const row of seen) {
      assert.equal(row.cancel, "1");assert.equal(row.profile, "a".repeat(64));assert.equal(row.premature, false);
      assert.equal(row.body, '{"state":"Запрос для проверки транспорта"}');
    }
  } finally { await state.cleanup(); }
});

test("an opted-in caller disconnect closes the pending upstream and is recorded as client cancellation", { timeout: 5000 }, async () => {
  let entered = false, premature = false;
  const state = await fixture((req, res) => {
    req.resume();req.on("end", () => { entered = true; });res.on("close", () => { premature = !res.writableFinished; });
  });
  try {
    const client = call(state.url);await until(() => entered);client.request.destroy();await client.finished;
    await until(() => premature && state.completed() === 1);
    assert.equal(state.errors.length, 1);assert.ok(state.errors[0] instanceof DecisionProxyCancelled);
  } finally { await state.cleanup(); }
});

test("legacy callers without opt-in may disconnect while the upstream computation completes", { timeout: 5000 }, async () => {
  let response: http.ServerResponse | undefined, premature = false, header: unknown;
  const state = await fixture((req, res) => {
    req.resume();req.on("end", () => { response = res; });header = req.headers["x-agat-decision-cancel-on-disconnect"];
    res.on("close", () => { premature = !res.writableFinished; });
  });
  try {
    const client = call(state.url, false);await until(() => !!response);client.request.destroy();await client.finished;
    await delay(40);assert.equal(premature, false);assert.equal(state.completed(), 0);assert.equal(header, undefined);
    response!.end('{"status":"ok"}');await until(() => state.completed() === 1);
    assert.deepEqual(state.errors, []);assert.equal(premature, false);
  } finally { await state.cleanup(); }
});

test("owner cancellation closes upstream independently of peer opt-in", { timeout: 5000 }, async () => {
  let entered = false, premature = false;
  const state = await fixture((req, res) => {
    req.resume();req.on("end", () => { entered = true; });res.on("close", () => { premature = !res.writableFinished; });
  });
  try {
    const client = call(state.url, false);await until(() => entered);state.owner.abort();
    assert.equal((await client.finished)?.status, 502);await until(() => premature && state.completed() === 1);
    assert.equal(state.errors.length, 1);assert.ok(!(state.errors[0] instanceof DecisionProxyCancelled));
  } finally { await state.cleanup(); }
});

test("busy status is preserved and the response byte bound is enforced while reading", { timeout: 5000 }, async () => {
  let oversized = false;
  const state = await fixture((req, res) => {
    req.resume();req.on("end", () => oversized ? res.end("x".repeat(131073)) : res.writeHead(503).end('{"reason":"busy"}'));
  });
  try {
    assert.deepEqual(await call(state.url).finished, { status: 503, body: '{"reason":"busy"}' });
    oversized = true;assert.equal((await call(state.url).finished)?.status, 502);
    assert.equal(state.errors.length, 1);assert.match(String(state.errors[0]), /too large/);
  } finally { await state.cleanup(); }
});
