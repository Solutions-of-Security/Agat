import assert from "node:assert/strict";
import type http from "node:http";

const cancelHeader = "x-agat-decision-cancel-on-disconnect";
export class DecisionProxyCancelled extends Error {
  constructor() { super("decision_client_disconnected"); }
}

/** Preserve the runtime's explicit disconnect contract through an observation proxy. */
export async function forwardDecisionRequest(req: http.IncomingMessage, res: http.ServerResponse,
  target: string, body: string | undefined, cancelled: AbortSignal) {
  const origin = new URL(target);
  assert.ok(origin.protocol === "http:" && origin.hostname === "127.0.0.1" && origin.port
    && !origin.username && !origin.password && !origin.search && !origin.hash && origin.pathname === "/");
  assert.ok((req.method === "GET" && req.url === "/health") || (req.method === "POST" && req.url === "/v1/decisions"));
  const peer = new AbortController();
  const optIn = req.method === "POST" && req.headers[cancelHeader] === "1";
  const onClose = () => { if (optIn && !res.writableFinished) peer.abort(); };
  // IncomingMessage.close also fires after a complete request body, while a
  // healthy caller is still waiting. Only a premature response close cancels.
  if (optIn) {
    res.once("close", onClose);
    if (res.destroyed) onClose();
  }
  try {
    const headers: Record<string, string> = { "content-type": "application/json" };
    for (const name of ["x-agat-decision-profile", cancelHeader]) {
      if (req.headers[name] !== undefined) headers[name] = String(req.headers[name]);
    }
    const response = await fetch(`${origin.origin}${req.url}`, { method: req.method, body, headers, redirect: "error",
      signal: AbortSignal.any([cancelled, peer.signal, AbortSignal.timeout(12_000)]) });
    assert.ok(response.body, "Missing decision response body");
    const reader = response.body.getReader();let size = 0;const chunks: Buffer[] = [];
    try {
      while (true) {
        const { value, done } = await reader.read();if (done) break;
        size += value.byteLength;
        if (size > 131072) { await reader.cancel();throw new Error("Decision response too large"); }
        chunks.push(Buffer.from(value));
      }
    } finally { reader.releaseLock(); }
    return { httpStatus: response.status, text: Buffer.concat(chunks).toString("utf8") };
  } catch (error) {
    if (peer.signal.aborted) throw new DecisionProxyCancelled();
    throw error;
  } finally {
    res.removeListener("close", onClose);
  }
}
