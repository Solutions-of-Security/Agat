// Real UI and coordinator; only the model/embeddings worker is deterministic.
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import path from "node:path";
import { build } from "vite";
import { AgatStore } from "../../apps/coordinator/src/database.ts";
import { loadConfig } from "../../apps/coordinator/src/config.ts";
import { createCoordinatorServer } from "../../apps/coordinator/src/server.ts";
import { getInternalReportPack } from "../../apps/coordinator/src/process-packs.ts";
import { exerciseReportDecision, exerciseDecisionChunkFailure } from "./report-decision-ui-steps.mjs";
import { exerciseReportEvalHandoff, exerciseEvalChunkFailure } from "./report-eval-ui-steps.mjs";

const { chromium } = await import(process.env.AGAT_PLAYWRIGHT_MODULE || "playwright");
const artifactsDir = mkdtempSync(path.join(tmpdir(), "agat-pack-ui-"));
const screenshotDir = path.resolve(process.env.AGAT_PACK_UI_SCREENSHOTS || tmpdir());
mkdirSync(screenshotDir, { recursive: true });
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
    page.on("pageerror", (error) => errors.push({ text: error.message, url: null }));
    page.on("console", (message) => { if (["error", "warning"].includes(message.type())) errors.push({ text: message.text(), url: message.location().url }); });
    await page.goto(`${base}#processes`);
    assert.match(await page.title(), /Агат|Agat/i);
    await page.getByRole("button", { name: "Новый процесс", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Новый процесс" });
    await dialog.getByRole("button", { name: "Установить пакет", exact: true }).waitFor();
    assert.equal(await dialog.getByLabel("Версия пакета", { exact: true }).inputValue(), "2", "a cold project offers the user-document release");
    assert.equal(await dialog.getByRole("button", { name: "Установить пакет", exact: true }).isDisabled(), true, "empty source selection cannot install");
    await dialog.getByLabel("Версия пакета", { exact: true }).selectOption("1");
    await page.waitForFunction(() => !document.querySelector("#install-report-pack")?.disabled);
    await dialog.evaluate((element) => { element.scrollTop = 0; });
    await page.screenshot({ path: path.join(screenshotDir, `agat-pack-${projectId}-overview.png`), fullPage: false });
    assert.match(await dialog.innerText(), /Пакет ещё не установлен в текущем проекте/);
    if (projectId === "desktop") assert.match(await dialog.innerText(), /нет свободного online worker/);
    await dialog.getByRole("button", { name: "Установить пакет", exact: true }).click();
    await dialog.getByText("Пакет установлен в текущем проекте.", { exact: false }).waitFor();
    assert.equal(await dialog.getByRole("button", { name: "Запустить пример", exact: true }).isDisabled(), true);
    const installation = store.getProcessPackInstallation(pack.id, projectId);
    assert.ok(installation);
    const recovery = dialog.getByRole("link", { name: "Завершить индексацию коллекции" });
    assert.equal(await recovery.getAttribute("href"), `#knowledge?collectionId=${installation.knowledgeCollectionIds[0]}`);
    await page.screenshot({ path: path.join(screenshotDir, `agat-pack-${projectId}-blocked.png`), fullPage: false });
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
    await page.screenshot({ path: path.join(screenshotDir, `agat-pack-${projectId}-ready.png`), fullPage: false });
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

    // Same real project, now a binary CSV through the common ingest API, then v1 -> v2 -> v1 -> v2.
    const headers = { "x-agat-admin-token": token, "x-agat-project-id": projectId };
    const createdCollection = await context.request.post(`${apiBase}/api/v1/knowledge/collections`, { headers,
      data: { name: `My report sources ${projectId}`, embeddingModel: pack.defaults.embeddingModel, chunkSize: 4000, chunkOverlap: 0 } });
    assert.equal(createdCollection.status(), 201); const ownCollection = await createdCollection.json();
    const uploaded = await context.request.post(`${apiBase}/api/v1/knowledge/collections/${ownCollection.id}/documents/upload`, { headers,
      data: { name: "Counts.csv", mediaType: "text/csv", contentBase64: Buffer.from("period,requests\n2026-07,100\n2026-08,120\n").toString("base64") } });
    assert.equal(uploaded.status(), 201); const ownDocument = await uploaded.json();
    await page.goto(`${base}#processes?packId=internal-report`);
    await dialog.getByLabel("Версия пакета", { exact: true }).selectOption("2");
    assert.equal(await dialog.getByRole("button", { name: "Установить пакет", exact: true }).isDisabled(), true);
    await dialog.getByRole("checkbox", { name: new RegExp(`My report sources ${projectId}`) }).check();
    await dialog.getByRole("region", { name: "Различия версий", exact: true }).waitFor();
    await dialog.getByRole("button", { name: "Установить пакет", exact: true }).click();
    await dialog.getByRole("button", { name: "Переключить на v2", exact: true }).waitFor();
    assert.equal(store.getProcessPackInstallation(pack.id, projectId).version, 1, "installation preserves the active release");
    const ownInstallation = store.getProcessPackInstallation(pack.id, projectId, 2);
    assert.deepEqual(ownInstallation.knowledgeCollectionIds, [ownCollection.id]);
    assert.equal(store.listProcesses(projectId).length, 2);
    assert.equal(await dialog.getByRole("checkbox", { name: new RegExp(`My report sources ${projectId}`) }).isDisabled(), true);
    await dialog.getByRole("region", { name: "Различия версий", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(screenshotDir, `agat-pack-v2-${projectId}-diff.png`), fullPage: false });
    for (let job = store.leaseKnowledgeEmbedding(node); job; job = store.leaseKnowledgeEmbedding(node)) {
      store.completeKnowledgeEmbedding(node, job.leaseId, job.chunks.map(chunk => ({ chunkId: chunk.id, embedding: [1, 0, 0] })));
    }
    await dialog.getByRole("button", { name: "Проверить снова" }).click();
    await dialog.getByRole("button", { name: "Переключить на v2", exact: true }).click();
    await dialog.getByText("Активная версия пакета: v2.", { exact: false }).waitFor();
    await dialog.getByLabel("Версия пакета", { exact: true }).selectOption("1");
    await dialog.getByRole("button", { name: "Вернуться к v1", exact: true }).click();
    await dialog.getByText("Активная версия пакета: v1.", { exact: false }).waitFor();
    assert.equal(store.getProcessPackInstallation(pack.id, projectId).version, 1);
    await dialog.getByLabel("Версия пакета", { exact: true }).selectOption("2");
    await dialog.getByRole("button", { name: "Переключить на v2", exact: true }).click();
    await dialog.getByText("Активная версия пакета: v2.", { exact: false }).waitFor();
    assert.equal(store.listProcesses(projectId).length, 2, "activation and rollback do not duplicate resources");
    await dialog.getByLabel("Вопрос для отчёта", { exact: true }).fill("Сравни число заявок и покажи изменение со ссылкой на CSV.");
    assert.equal(await dialog.getByRole("button", { name: "Запустить отчёт", exact: true }).isDisabled(), true, "period is mandatory");
    await dialog.getByLabel("Период документов", { exact: true }).fill("Июль и август 2026");
    await dialog.getByLabel("Требования к отчёту", { exact: true }).fill("Покажи формулу\nОтдели причины от фактов");
    await dialog.locator(".process-pack__history > summary").click();
    assert.equal(await dialog.locator(".process-pack__history > ul > li").count(), 2);
    await dialog.getByRole("region", { name: "Задание для отчёта", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(screenshotDir, `agat-pack-v2-${projectId}-request.png`), fullPage: false });
    assert.ok((await dialog.boundingBox()).width <= viewport.width);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await dialog.getByRole("button", { name: "Запустить отчёт", exact: true }).click();
    await page.waitForURL(/#runs\//);
    const ownRunId = new URL(page.url()).hash.split("/")[1];
    assert.equal(JSON.parse(store.getRun(ownRunId, projectId).input).period, "Июль и август 2026");
    let sourceMarker;
    for (const role of getInternalReportPack(2).roles) {
      const lease = store.leaseNext(node); assert.ok(lease); assert.equal(lease.agent.id, ownInstallation.agentIds[role.id]);
      const retrieved = store.searchKnowledge(node, lease.leaseId, { queries: [{ embeddingModel: pack.defaults.embeddingModel, collectionIds: [ownCollection.id], vector: [1, 0, 0] }] });
      sourceMarker = retrieved.hits[0].marker;
      store.completeLease(node, lease.leaseId, role.id === "reviewer" ? JSON.stringify({ schemaVersion: "business-report.v1", status: "ready", summary: "Объём заявок вырос",
        claims: [{ id: "counts", text: "100 → 120 заявок", sourceRefs: [sourceMarker] }],
        calculations: [{ id: "growth", operation: "percent_change", operands: [100, 120], result: 20, sourceRefs: [sourceMarker] }], artifactRefs: [], limitations: ["Причины изменений неизвестны"] }) : `100 → 120 [${sourceMarker}].`);
    }
    assert.equal(store.getRun(ownRunId, projectId).status, "waiting_approval");
    assert.equal(store.listRunArtifacts(ownRunId).length, 0);
    const ownApproval = store.getRun(ownRunId, projectId).stages.find(stage => stage.status === "waiting_approval");
    assert.equal((await context.request.post(`${apiBase}/api/v1/approvals/${ownApproval.id}`, { headers, data: { decision: "approve" } })).status(), 204);
    const ownArtifact = store.listRunArtifacts(ownRunId).find(item => item.name === "internal-report.md"); assert.ok(ownArtifact);
    const ownDownload = await context.request.get(`${apiBase}/api/v1/artifacts/${ownArtifact.id}/download`, { headers });
    assert.equal(ownDownload.status(), 200); assert.ok((await ownDownload.text()).includes(`documentId=${ownDocument.id}`));
    await page.reload();
    const result = page.locator("#run-result");
    await result.getByRole("button", { name: "Форматированный", exact: true }).click();
    await result.locator(".report-markdown table").waitFor();
    const office = result.getByRole("group", { name: "Экспорт сохранённого отчёта" });
    await office.getByRole("button", { name: "Скачать DOCX", exact: true }).waitFor();
    const modelCompletions = store.listEvents(0, 1000, projectId).filter(event => event.type === "stage.completed").length;
    for (const format of ["docx", "pdf", "pdf"]) {
      const downloaded = page.waitForEvent("download");
      await office.getByRole("button", { name: `Скачать ${format.toUpperCase()}`, exact: true }).click();
      const file = await downloaded; assert.equal(file.suggestedFilename(), `internal-report.${format}`);
      const bytes = readFileSync(await file.path());
      const saved = store.listRunArtifacts(ownRunId).filter(item => item.kind === "business_report_export" && item.name.endsWith(`.${format}`));
      assert.equal(saved.length, 1, "repeated export reuses the stored file");
      assert.equal(createHash("sha256").update(bytes).digest("hex"), saved[0].sha256);
      assert.equal(saved[0].sourceArtifactId, ownArtifact.id);
      if (format === "pdf") assert.equal(bytes.subarray(0, 5).toString(), "%PDF-");
      await office.getByRole("button", { name: "Скачать DOCX", exact: true }).waitFor({ state: "visible" });
      await page.waitForFunction(() => !document.querySelector('.report-export button')?.disabled);
    }
    assert.equal(store.listEvents(0, 1000, projectId).filter(event => event.type === "stage.completed").length, modelCompletions, "export must not invoke a model");
    await office.scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.screenshot({ path: path.join(screenshotDir, `agat-pack-v2-${projectId}-office.png`), fullPage: false });
    await result.locator(".report-markdown").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(screenshotDir, `agat-pack-v2-${projectId}-report.png`), fullPage: false });
    await exerciseReportDecision({ page, context, base, headers, store, artifact: ownArtifact, projectId, screenshotDir, errors });
    await exerciseReportEvalHandoff({ page, context, base, headers, store, artifact: ownArtifact, projectId, screenshotDir, errors });
    if (projectId === "desktop") await exerciseDecisionChunkFailure({ browser, base, token, projectId, runId: ownRunId, screenshotDir });
    if (projectId === "desktop") await exerciseEvalChunkFailure({ browser, base, token, projectId, runId: ownRunId, screenshotDir });
    await result.getByRole("button", { name: "Форматированный", exact: true }).click();
    await result.locator(".report-markdown").getByRole("link", { name: `[${sourceMarker}]`, exact: true }).first().focus();
    await page.keyboard.press("Enter");
    await page.waitForURL(new RegExp(`documentId=${ownDocument.id}`));
    const highlight = page.getByRole("region", { name: "Предпросмотр документа" }).locator("mark");
    await highlight.waitFor(); assert.match(await highlight.innerText(), /100/); assert.match(await highlight.innerText(), /120/);
    await page.goto(`${base}#processes?packId=internal-report`); await page.reload();
    assert.equal(await dialog.getByLabel("Версия пакета", { exact: true }).inputValue(), "2");
    assert.equal(await dialog.getByRole("checkbox", { name: new RegExp(`My report sources ${projectId}`) }).isChecked(), true);
    assert.equal(store.listProcesses(projectId).length, 2);
    assert.equal(await page.locator("vite-error-overlay").count(), 0);
    assert.deepEqual(errors, []);
    console.log(`PASS ${projectId}: ${base} · v1/v2 install/update/rollback -> CSV -> validated report -> exact DOCX/PDF -> decision/manual task -> reviewed Golden import/stale-context refresh/lost-response retry -> dataset v1 lineage -> exact source; no unexpected console errors`);
    await context.close();
  }
} catch (error) {
  if (lastPage && !lastPage.isClosed()) {
    await lastPage.screenshot({ path: path.join(screenshotDir, "agat-pack-ui-failure.png") });
    console.error(`UI failure URL: ${lastPage.url()}`);
    console.error((await lastPage.locator("body").innerText()).slice(-6000));
  }
  throw error;
} finally {
  await browser?.close(); server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve)); store.close(); rmSync(artifactsDir, { recursive: true, force: true });
}
