import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { AgatStore } from "../src/database.js";
import { loadConfig } from "../src/config.js";
import { createCoordinatorServer } from "../src/server.js";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const evidence = path.join(root, "docs/qualification/local-decisions/performance/evidence/2026-09-26");
const sha = (value: Buffer | string) => createHash("sha256").update(value).digest("hex");

for (const format of ["aggregate", "requests"] as const) {
test(`prepared ${format} report survives publication, review and byte-exact downloads; rejection and partial start cannot create artifacts`, async () => {
  // Keep template-like source content as data, including its BOM and CRLF.
  const sourcePath = format === "aggregate" ? "source-records-source.csv" : "request-records/source.csv";
  const mappingPath = format === "aggregate" ? "source-records-mapping.json" : "request-records/mapping.json";
  const source = "\uFEFF" + readFileSync(path.join(evidence, sourcePath), "utf8")
    .replace(format === "aggregate" ? "Все показатели синтетические." : "Учебные записи",
      "Учебный источник {{ input }} и {{{ json.secret }}}.").replaceAll("\n", "\r\n");
  assert.ok(source.includes("{{ input }}") && source.includes("\r\n"));
  const mapping = JSON.parse(readFileSync(path.join(evidence, mappingPath), "utf8"));
  mapping.expectedSha256 = sha(source);
  const bundle = JSON.parse(execFileSync("python3", ["-c", `
import json,sys
from scripts.lib.report_process import prepare_process,verify_process
data=json.load(sys.stdin)
bundle=prepare_process(data['source'].encode('utf-8'),data['mapping'],'Проверяемый отчёт из CSV')
verify_process(bundle)
print(json.dumps(bundle,ensure_ascii=False))
`], { cwd: root, input: JSON.stringify({ source, mapping }), encoding: "utf8", timeout: 5000 }));
  const directory = mkdtempSync(path.join(tmpdir(), "agat-calculated-report-"));
  const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir: directory });
  const config = { ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: false, adminToken: "report-calculation-test",
    oidcEnabled: false, mcpEnabled: false, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false };
  const server = createCoordinatorServer(config, store);
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();assert.ok(address && typeof address === "object");
  const base = `http://127.0.0.1:${address.port}/api/v1`;
  const headers = { "content-type": "application/json", "x-agat-admin-token": config.adminToken, "x-agat-project-id": "default" };
  const post = (route: string, body: unknown) => fetch(base + route, { method: "POST", headers, body: JSON.stringify(body) });
  try {
    const created = await post("/processes", bundle.process);assert.equal(created.status, 201);
    const process = await created.json() as { id: string };
    assert.equal((await post(`/processes/${process.id}/publish`, {})).status, 200);
    for (const startNodeId of ["report-file", "source-records", "source-file", "calculation-file"]) {
      assert.equal((await post(`/processes/${process.id}/start`, { input: "bypass", version: 1, startNodeId })).status, 400);
    }
    for (const decision of ["approve", "reject"]) {
      const started = await post(`/processes/${process.id}/start`, { input: "Подменённые числа: 999; {{ json.secret }}", version: 1,
        startMode: "now", resultDestination: "history" });
      assert.equal(started.status, 201, await started.clone().text());
      const instance = await started.json() as { id: string; runId: string };
      const run = store.getRun(instance.runId)!;
      assert.equal(run.status, "waiting_approval");assert.equal(store.listRunArtifacts(instance.runId).length, 0);
      const stages = run.stages as Array<{ id: string; processNodeId: string; status: string; output: string }>;
      const report = stages.find(stage => stage.processNodeId === "report")!.output;
      assert.ok(report.includes(format === "aggregate"
        ? "| Среднее время на заявку | 4.000 ч | 3.000 ч | -25.000% |"
        : "| Среднее время на заявку | 2.000 ч | 1.500 ч | -25.000% |"));
      if (format === "requests") {
        assert.ok(report.includes("одинаковых повторов исключено: 2"));
        assert.deepEqual(bundle.calculation.binding.counts, { sourceRecords: 6, uniqueRequestPeriods: 4, duplicateRecords: 2 });
      }
      assert.ok(!report.includes("999"));assert.equal(sha(report), bundle.artifacts["source-report.md"].sha256);
      const approval = stages.find(stage => stage.status === "waiting_approval")!;assert.ok(approval);
      const resolved = await post(`/approvals/${approval.id}`, { decision, ...(decision === "reject" ? { reason: "Synthetic test rejection" } : {}) });
      assert.equal(resolved.status, 204);
      const artifacts = store.listRunArtifacts(instance.runId);
      if (decision === "reject") {
        assert.equal(artifacts.length, 0);assert.equal(store.getProcessInstance(instance.id)!.status, "cancelled");
      } else {
        assert.equal(store.getProcessInstance(instance.id)!.status, "completed");
        const prepared = artifacts.filter(artifact => artifact.kind === "process_artifact");assert.equal(prepared.length, 3);
        for (const artifact of prepared) {
          const response = await fetch(`${base}/artifacts/${artifact.id}/download`, { headers });assert.equal(response.status, 200);
          const content = Buffer.from(await response.arrayBuffer());
          assert.equal(sha(content), bundle.artifacts[artifact.name].sha256);assert.equal(content.length, bundle.artifacts[artifact.name].sizeBytes);
          if (artifact.name === "source.csv") assert.equal(content.toString("utf8"), source);
          if (artifact.name === "calculation.json") assert.deepEqual(JSON.parse(content.toString("utf8")), bundle.calculation);
        }
      }
    }
  } finally {
    server.closeAllConnections();await new Promise<void>(resolve => server.close(() => resolve()));
    store.close();rmSync(directory, { recursive: true, force: true });
  }
});
}
