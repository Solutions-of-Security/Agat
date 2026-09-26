/** Owned, bounded proxy process used only by the Python cancellation diagnostic. */
import assert from "node:assert/strict";
import http from "node:http";
import { parseArgs } from "node:util";
import { forwardDecisionRequest } from "./lib/decision-shadow-proxy.js";

const { values } = parseArgs({ options: { target: { type: "string" } } });
const target = new URL(values.target!);
assert.ok(target.protocol === "http:" && target.hostname === "127.0.0.1" && target.port
  && !target.username && !target.password && !target.search && !target.hash && target.pathname === "/");
const owner = new AbortController();
const server = http.createServer(async (req, res) => {
  try {
    const chunks: Buffer[] = [];let size = 0;
    for await (const chunk of req) { size += chunk.length;assert.ok(size <= 131072);chunks.push(chunk); }
    const result = await forwardDecisionRequest(req, res, target.origin,
      req.method === "POST" ? Buffer.concat(chunks).toString("utf8") : undefined, owner.signal);
    if (!res.destroyed) res.writeHead(result.httpStatus, { "content-type": "application/json" }).end(result.text);
  } catch { if (!res.destroyed) res.writeHead(502).end(); }
});
let stopping = false;
function stop() {
  if (stopping) return;stopping = true;owner.abort();clearTimeout(lifetime);
  server.closeAllConnections();server.close(() => process.exit(0));
}
const lifetime = setTimeout(stop, 30_000);
process.stdin.resume();process.stdin.on("end", stop);
process.once("SIGTERM", stop);process.once("SIGINT", stop);
server.listen(0, "127.0.0.1", () => {
  const address = server.address();assert.ok(address && typeof address === "object");
  process.stdout.write(JSON.stringify({ port: address.port, pid: process.pid }) + "\n");
});
