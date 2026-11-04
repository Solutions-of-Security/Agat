import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { AgatStore } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";
import { BusinessReportOfficeError } from "../src/business-report-office-document.js";
import { completedOfficeReport } from "./business-report-office-fixtures.js";

test("exports persist exact source/renderer identity, reuse retries and survive changed/deleted current knowledge", async () => {
  const root = mkdtempSync(path.join(os.tmpdir(), "agat-office-store-"));
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: root });
  try {
    const { canonical, documentId, runId } = completedOfficeReport(store);
    const before = readFileSync(store.getArtifactDownload(String(canonical.id))!.filePath);
    assert.equal((canonical.rendering as { contentSha256: string }).contentSha256, canonical.sha256);
    assert.ok(store.deleteKnowledgeDocument(documentId));
    const input = { format: "docx", expectedSha256: canonical.sha256, origin: "https://agat.example" };
    const first = (await store.exportBusinessReportArtifact(String(canonical.id), input))!;
    assert.equal(first.created, true);
    const repeated = (await store.exportBusinessReportArtifact(String(canonical.id), input))!;
    assert.equal(repeated.created, false); assert.deepEqual(repeated.artifact, first.artifact);
    const pdf = (await store.exportBusinessReportArtifact(String(canonical.id), { ...input, format: "pdf" }))!;
    assert.equal(pdf.created, true);
    const meta = first.artifact.rendering as Record<string, unknown>, pdfMeta = pdf.artifact.rendering as Record<string, unknown>;
    assert.equal(meta.sourceSha256, canonical.sha256); assert.equal(meta.documentSha256, pdfMeta.documentSha256);
    for (const artifact of [first.artifact, pdf.artifact]) {
      const download = store.getArtifactDownload(String(artifact.id))!;
      assert.equal(createHash("sha256").update(readFileSync(download.filePath)).digest("hex"), artifact.sha256);
      assert.equal(artifact.sourceArtifactId, canonical.id);
    }
    assert.deepEqual(readFileSync(store.getArtifactDownload(String(canonical.id))!.filePath), before);
    assert.equal(store.listRunArtifacts(runId).filter(artifact => artifact.kind === "business_report_export").length, 2);
    assert.equal(store.listEvents(0, 1000).filter(event => event.type === "business.report.exported").length, 2);
    store.createProject({ id: "foreign", name: "Foreign" });
    assert.equal(await store.exportBusinessReportArtifact(String(canonical.id), input, "foreign"), null);
    assert.equal(store.getRunArtifacts(runId, "foreign"), null);
    await assert.rejects(store.exportBusinessReportArtifact(String(canonical.id), { ...input, expectedSha256: "0".repeat(64) }),
      (error: unknown) => error instanceof BusinessReportOfficeError && error.code === "source_changed");
  } finally { store.close(); rmSync(root, { recursive: true, force: true }); }
});

test("legacy report receipts are recovered only from the exact generated stage; stored metadata survives event retention", async () => {
  const root = mkdtempSync(path.join(os.tmpdir(), "agat-office-legacy-"));
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: root });
  try {
    const { canonical, runId } = completedOfficeReport(store);
    store.db.prepare("UPDATE artifacts SET rendering_json = NULL WHERE id = ?").run(String(canonical.id));
    assert.equal((store.listRunArtifacts(runId).find(row => row.id === canonical.id)!.rendering as { contentSha256: string }).contentSha256, canonical.sha256);
    const input = { format: "pdf", expectedSha256: canonical.sha256, origin: "https://agat.example" };
    assert.equal((await store.exportBusinessReportArtifact(String(canonical.id), input))!.created, true);
    store.db.prepare("DELETE FROM events WHERE run_id = ? AND type = 'business.report.rendered'").run(runId);
    assert.equal((await store.exportBusinessReportArtifact(String(canonical.id), input))!.created, false);
    store.db.prepare("UPDATE artifacts SET rendering_json = NULL WHERE id = ?").run(String(canonical.id));
    await assert.rejects(store.exportBusinessReportArtifact(String(canonical.id), { ...input, format: "docx" }), /квитанции проверки/);
  } finally { store.close(); rmSync(root, { recursive: true, force: true }); }
});

test("source removal during rendering aborts persistence instead of publishing a stale derivative", async () => {
  const root = mkdtempSync(path.join(os.tmpdir(), "agat-office-race-"));
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: root });
  try {
    const { canonical, runId } = completedOfficeReport(store);
    const pending = store.exportBusinessReportArtifact(String(canonical.id), { format: "pdf", expectedSha256: canonical.sha256, origin: "https://agat.example" });
    store.db.prepare("UPDATE artifacts SET storage_state = 'deleted', deleted_at = ? WHERE id = ?").run(new Date().toISOString(), String(canonical.id));
    await assert.rejects(pending, (error: unknown) => error instanceof BusinessReportOfficeError && error.code === "report_not_ready");
    assert.equal(store.listRunArtifacts(runId).filter(artifact => artifact.kind === "business_report_export").length, 0);
    assert.equal(store.listEvents(0, 1000).filter(event => event.type === "business.report.exported").length, 0);
  } finally { store.close(); rmSync(root, { recursive: true, force: true }); }
});

test("HTTP export pins expected bytes and this installation's origin; list and downloads remain project scoped", async () => {
  const root = mkdtempSync(path.join(os.tmpdir(), "agat-office-http-"));
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: root });
  const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false,
    adminToken: "office-http-fixture", oidcEnabled: false, a2aEnabled: false, sandboxEnabled: false, localWorkerLauncherEnabled: false }, store);
  try {
    const { canonical, runId } = completedOfficeReport(store);
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
    const address = server.address(); assert.ok(address && typeof address === "object");
    const origin = `http://127.0.0.1:${address.port}`;
    const headers = { "x-agat-admin-token": "office-http-fixture", "content-type": "application/json" };
    const body = { format: "pdf", expectedSha256: canonical.sha256, origin };
    const post = (data = body, extra = {}) => fetch(`${origin}/api/v1/artifacts/${canonical.id}/export`, { method: "POST", headers: { ...headers, ...extra }, body: JSON.stringify(data) });
    assert.equal((await post(body, { "x-agat-admin-token": "wrong" })).status, 401);
    assert.equal((await post({ ...body, origin: "https://foreign.example" })).status, 400);
    assert.equal((await post({ ...body, expectedSha256: "0".repeat(64) })).status, 409);
    const first = await post(); assert.equal(first.status, 201, await first.clone().text());
    const result = await first.json() as { artifact: Record<string, unknown> };
    const file = await fetch(`${origin}/api/v1/artifacts/${result.artifact.id}/download`, { headers });
    assert.equal(file.status, 200); assert.match(file.headers.get("content-disposition")!, /report.pdf/);
    const bytes = Buffer.from(await file.arrayBuffer()); assert.equal(bytes.subarray(0, 5).toString(), "%PDF-");
    assert.equal(createHash("sha256").update(bytes).digest("hex"), result.artifact.sha256);
    assert.equal((await post()).status, 200);
    const list = await fetch(`${origin}/api/v1/runs/${runId}/artifacts`, { headers });
    assert.equal(list.status, 200); assert.ok(!JSON.stringify(await list.json()).includes("content_blob"));
    store.createProject({ id: "foreign", name: "Foreign" });
    assert.equal((await post(body, { "x-agat-project-id": "foreign" })).status, 404);
    assert.equal((await fetch(`${origin}/api/v1/runs/${runId}/artifacts`, { headers: { ...headers, "x-agat-project-id": "foreign" } })).status, 404);
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); store.close(); rmSync(root, { recursive: true, force: true }); }
});
