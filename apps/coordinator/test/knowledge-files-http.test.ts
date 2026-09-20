import assert from "node:assert/strict";
import test from "node:test";
import { AgatStore } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import { KNOWLEDGE_DOCX_MEDIA_TYPE } from "../src/knowledge-files.js";
import { docxFile } from "./knowledge-file-fixtures.js";

test("knowledge HTTP uploads and previews DOCX, persists parse errors, authorizes reindex and isolates files/sources", async () => {
  const store = new AgatStore(":memory:", { seedDemo: false });
  const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
    adminToken: "knowledge-http-test", oidcEnabled: false, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false }, store);
  try {
    store.createProject({ id: "isolated", name: "Isolated" });
    const collection = store.createKnowledgeCollection({ name: "HTTP", embeddingModel: "embed" }, "isolated");
    await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
    const address = server.address(); assert.ok(address && typeof address === "object");
    const base = `http://127.0.0.1:${address.port}/api/v1`;
    const headers = { "content-type": "application/json", "x-agat-admin-token": "knowledge-http-test", "x-agat-project-id": "isolated" };
    const request = (url: string, method = "GET", body?: unknown, overrides = {}) => fetch(base + url, { method, headers: { ...headers, ...overrides }, ...(body ? { body: JSON.stringify(body) } : {}) });
    const uploadUrl = `/knowledge/collections/${collection.id}/documents/upload`;
    const originalFile = docxFile();
    const input = { name: "Policy.docx", mediaType: KNOWLEDGE_DOCX_MEDIA_TYPE, contentBase64: originalFile.toString("base64") };
    assert.equal((await request(uploadUrl, "POST", input, { "x-agat-admin-token": "wrong" })).status, 401);
    const response = await request(uploadUrl, "POST", input);
    assert.equal(response.status, 201);
    const document = await response.json() as { id: string; status: string };
    assert.equal(document.status, "pending");
    const documentUrl = `/knowledge/documents/${document.id}`;
    const preview = await (await request(documentUrl)).json() as { content: string; chunks: unknown[]; originalBase64?: string };
    assert.match(preview.content, /Проверенный факт/);
    assert.equal(preview.chunks.length, 1);
    assert.equal(preview.originalBase64, undefined);
    const file = await request(`${documentUrl}/file`);
    assert.equal(file.headers.get("content-type"), KNOWLEDGE_DOCX_MEDIA_TYPE);
    assert.match(file.headers.get("content-disposition")!, /attachment/);
    assert.deepEqual(Buffer.from(await file.arrayBuffer()), originalFile);
    assert.equal((await request(`${documentUrl}/reindex`, "POST", undefined, { "x-agat-admin-token": "wrong" })).status, 401);
    const reindex = await request(`${documentUrl}/reindex`, "POST");
    assert.equal(reindex.status, 200);
    assert.equal((await reindex.json() as { id: string }).id, document.id);
    for (const suffix of ["", "/file", "/reindex"]) {
      assert.equal((await request(documentUrl + suffix, suffix === "/reindex" ? "POST" : "GET", undefined, { "x-agat-project-id": "default" })).status, 404);
    }
    const corrupt = await request(uploadUrl, "POST", { ...input, name: "Corrupt.docx", contentBase64: "YnJva2Vu" });
    assert.equal(corrupt.status, 201);
    const failed = await corrupt.json() as { status: string; parseError: string };
    assert.equal(failed.status, "failed");
    assert.match(failed.parseError, /Ошибка разбора/);
    assert.equal((await request(uploadUrl, "POST", { ...input, contentBase64: "invalid" })).status, 400);
    assert.equal((await request("/runs/nonexistent/knowledge")).status, 404);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    store.close();
  }
});
