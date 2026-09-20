// Real UI and coordinator; only the model/embeddings worker is deterministic.
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { build } from "vite";
import { AgatStore } from "../../apps/coordinator/src/database.ts";
import { loadConfig } from "../../apps/coordinator/src/config.ts";
import { createCoordinatorServer } from "../../apps/coordinator/src/server.ts";
import { getInternalReportPack } from "../../apps/coordinator/src/process-packs.ts";

const { chromium } = await import(process.env.AGAT_PLAYWRIGHT_MODULE || "playwright");
const artifactsDir = mkdtempSync(path.join(tmpdir(), "agat-pack-ui-"));
const store = new AgatStore(":memory:", { seedDemo: false, artifactsDir });
store.updateModelRouterPolicy({ enabled: false });
const token = "process-pack-ui-test-only";
const webDistPath = path.join(artifactsDir, "web");
const server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port: 0, serveWeb: true, webDistPath,
  adminToken: token, oidcEnabled: false, mcpEnabled: true, sandboxEnabled: false, a2aEnabled: false, localWorkerLauncherEnabled: false }, store);
let browser, lastPage;
try {
  await build({ root: path.resolve("apps/web"), build: { outDir: webDistPath, emptyOutDir: true } });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const apiBase = `http://127.0.0.1:${server.address().port}`;
  console.log("UI test: coordinator ready");
  const base = `${apiBase}/`;
  browser = await chromium.launch({ headless: true, ...(process.env.AGAT_BROWSER_PATH ? { executablePath: process.env.AGAT_BROWSER_PATH } : {}) });
  console.log("UI test: browser ready");
  const pack = getInternalReportPack();
  for (const [projectId, viewport] of [["desktop", { width: 1440, height: 1100 }], ["mobile", { width: 390, height: 844 }]]) {
    store.createProject({ id: projectId, name: projectId });
    const context = await browser.newContext({ viewport });
    await context.addInitScript(({ token, projectId }) => {
      localStorage.setItem("agat.admin-token.v1", token);
      localStorage.setItem("agat.project-id.v1", projectId);
    }, { token, projectId });
    const page = await context.newPage();
    lastPage = page;
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    await page.goto(`${base}#processes`);
    assert.match(await page.title(), /Агат|Agat/i);
    await page.getByRole("button", { name: "Новый процесс", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Новый процесс" });
    await dialog.getByRole("button", { name: "Установить пакет", exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector("#install-report-pack")?.disabled);
    await dialog.evaluate((element) => { element.scrollTop = 0; });
    await page.screenshot({ path: path.join(tmpdir(), `agat-pack-${projectId}-overview.png`), fullPage: false });
    assert.match(await dialog.innerText(), /Пакет ещё не установлен в текущем проекте/);
    if (projectId === "desktop") assert.match(await dialog.innerText(), /нет свободного online worker/);
    await dialog.getByRole("button", { name: "Установить пакет", exact: true }).click();
    await dialog.getByText("Пакет установлен в текущем проекте.", { exact: false }).waitFor();
    assert.equal(await dialog.getByRole("button", { name: "Запустить пример", exact: true }).isDisabled(), true);
    const installation = store.getProcessPackInstallation(pack.id, projectId);
    assert.ok(installation);
    const recovery = dialog.getByRole("link", { name: "Завершить индексацию коллекции" });
    assert.equal(await recovery.getAttribute("href"), `#knowledge?collectionId=${installation.knowledgeCollectionIds[0]}`);
    await page.screenshot({ path: path.join(tmpdir(), `agat-pack-${projectId}-blocked.png`), fullPage: false });
    await recovery.click();
    await page.waitForURL(/#knowledge\?collectionId=/);
    assert.equal(await dialog.isVisible(), false);
    await page.goto(`${base}#processes?packId=internal-report`);
    await dialog.getByRole("button", { name: "Запустить пример", exact: true }).waitFor();
    assert.equal(await dialog.getByRole("button", { name: "Запустить пример", exact: true }).isDisabled(), true);
    assert.equal(store.listProcesses(projectId).length, 1, "returning to installer must not duplicate the process");
    const node = store.registerNode({ enrollmentToken: "test", name: `worker-${projectId}`, platform: "test", models: [pack.defaults.model], embeddingModels: [pack.defaults.embeddingModel] }).id;
    for (let job = store.leaseKnowledgeEmbedding(node); job; job = store.leaseKnowledgeEmbedding(node)) {
      store.completeKnowledgeEmbedding(node, job.leaseId, job.chunks.map((chunk) => ({ chunkId: chunk.id, embedding: [1, 0, 0] })));
    }
    await dialog.getByRole("button", { name: "Проверить снова" }).click();
    await page.waitForFunction(() => [...document.querySelectorAll("button")].some((button) => button.textContent === "Запустить пример" && !button.disabled));
    assert.equal(await dialog.locator("li[data-ready='true']").filter({ hasText: "Можно выполнить сейчас" }).count(), 1);
    assert.equal(await dialog.locator("li[data-ready='false']").filter({ hasText: "Проверено на сценарии" }).count(), 1);
    await dialog.getByRole("button", { name: "Запустить пример", exact: true }).scrollIntoViewIfNeeded();
    assert.equal(await page.locator("vite-error-overlay").count(), 0);
    const bounds = await dialog.boundingBox();
    assert.ok(bounds.width <= viewport.width && bounds.x >= 0);
    await page.screenshot({ path: path.join(tmpdir(), `agat-pack-${projectId}-ready.png`), fullPage: false });
    await dialog.getByRole("button", { name: "Запустить пример", exact: true }).click();
    await page.waitForURL(/#runs\//);
    const runId = new URL(page.url()).hash.split("/")[1];
    for (const role of pack.roles) {
      const lease = store.leaseNext(node); assert.ok(lease);
      assert.equal(lease.agent.id, installation.agentIds[role.id]);
      store.completeLease(node, lease.leaseId, `${pack.sample.expected}\nИсточники: SUPPORT-DEMO v1; REPORT-DEMO v1.`);
    }
    assert.equal(store.getRun(runId, projectId).status, "waiting_approval");
    const stage = store.getRun(runId, projectId).stages.find((stage) => stage.status === "waiting_approval");
    const approved = await context.request.post(`${apiBase}/api/v1/approvals/${stage.id}`, { headers: { "x-agat-admin-token": token, "x-agat-project-id": projectId }, data: { decision: "approve" } });
    assert.equal(approved.status(), 204);
    const artifact = store.listRunArtifacts(runId).find((item) => item.name === "internal-report.md"); assert.ok(artifact);
    const download = await context.request.get(`${apiBase}/api/v1/artifacts/${artifact.id}/download`, { headers: { "x-agat-admin-token": token, "x-agat-project-id": projectId } });
    assert.match(await download.text(), /SUPPORT-DEMO/);
    assert.deepEqual(errors, []);
    console.log(`PASS ${projectId}: ${base} · install, recovery target, disabled/ready start, run, approval, artifact; no console errors`);
    await context.close();
  }
} catch (error) {
  if (lastPage && !lastPage.isClosed()) {
    await lastPage.screenshot({ path: path.join(tmpdir(), "agat-pack-ui-failure.png") });
    console.error((await lastPage.locator("body").innerText()).slice(-6000));
  }
  throw error;
} finally {
  await browser?.close(); server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve)); store.close(); rmSync(artifactsDir, { recursive: true, force: true });
}
