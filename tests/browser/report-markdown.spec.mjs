import { test, expect } from "@playwright/test";
import { readFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

// Browser plugin not available. Flow: result -> formatted table -> exact source -> unchanged source/download.
test("formatted report keeps source links exact, active content inert and source/download unchanged", async ({ page, request }, testInfo) => {
  const errors = []; const external = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (["error", "warning"].includes(message.type())) errors.push(message.text()); });
  page.on("request", request => { if (new URL(request.url()).hostname === "invalid.example") external.push(request.url()); });
  const collectionResponse = await request.post("/api/v1/knowledge/collections", { data: {
    name: `Markdown sources ${testInfo.project.name}`, embeddingModel: "fixture-embedding", chunkSize: 4000, chunkOverlap: 0 } });
  expect(collectionResponse.ok()).toBe(true); const collection = await collectionResponse.json();
  const documentResponse = await request.post(`/api/v1/knowledge/collections/${collection.id}/documents/upload`, { data: {
    name: "Counts.csv", mediaType: "text/csv", contentBase64: Buffer.from("period,requests\n2026-07,100\n2026-08,120").toString("base64") } });
  expect(documentResponse.ok()).toBe(true); const uploaded = await documentResponse.json();
  const document = await (await request.get(`/api/v1/knowledge/documents/${uploaded.id}`)).json();
  const snippet = document.chunks[0];
  const created = await request.post("/api/v1/runs", { data: { name: `Markdown QA ${testInfo.project.name}`,
    input: "UI fixture only", agentIds: ["collector"], approvalRequired: false } });
  expect(created.ok()).toBe(true); const { id } = await created.json();
  await expect.poll(async () => (await (await request.get(`/api/v1/runs/${id}`)).json()).status).toBe("completed");
  const run = await (await request.get(`/api/v1/runs/${id}`)).json();
  const original = run.stages.at(-1).output;
  const text = '# Отчёт по заявкам\n\nИюль и август: **проверяемые данные** [K1].\n\n| Метрика | Июль | Август | Источник |\n| --- | --- | --- | --- |\n| Заявки | 100 | 120 | [K1] |\n\nНеизвестный [K9]. [Remote](https://invalid.example/)\n\n![Tracking](https://invalid.example/image)\n\n<script>window.__reportInjected=true</script>\n\n> Причины изменений неизвестны.';
  const presented = structuredClone(run); presented.stages.at(-1).output = text;
  const sources = [{ marker: "K1", stageId: run.stages.at(-1).id, retrievalId: "ui-fixture", createdAt: document.createdAt,
    excerpt: snippet.content, content: snippet.content, provenance: {
      projectId: "default", collectionId: collection.id, collectionName: collection.name, documentId: document.id,
      documentName: document.name, documentSha256: document.contentSha256, chunkId: document.chunks[0].id,
      chunkOrdinal: 0, charStart: snippet.charStart, charEnd: snippet.charEnd, chunkSha256: snippet.contentSha256,
      originalSha256: document.originalSha256, pageNumber: null, sourceUri: null } }];
  await page.route(`**/api/v1/runs/${id}`, route => route.fulfill({ json: presented }));
  await page.route("**/api/v1/overview", async route => {
    const upstream = await route.fetch(); const overview = await upstream.json();
    await route.fulfill({ response: upstream, json: { ...overview, runs: overview.runs.map(run => run.id === id ? presented : run) } });
  });
  await page.route(`**/api/v1/runs/${id}/knowledge`, route => route.fulfill({ json: { sources } }));
  await page.goto(`/#runs/${id}`);
  await expect(page).toHaveURL(new RegExp(`#runs/${id}$`)); await expect(page).toHaveTitle(/АГАТ|Agat/i);
  const result = page.locator("#run-result");
  await expect(result.locator(":scope > pre")).toHaveText(text);
  await result.getByRole("button", { name: "Форматированный", exact: true }).click();
  await expect(result.getByRole("heading", { name: "Отчёт по заявкам" })).toBeVisible();
  await expect(result.locator("table")).toContainText("120");
  const table = result.getByRole("region", { name: "Таблица отчёта" });
  if (await table.evaluate(element => element.scrollWidth > element.clientWidth)) {
    await table.focus(); await page.keyboard.press("ArrowRight");
    await expect.poll(() => table.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
    await table.evaluate(element => { element.scrollLeft = 0; });
  }
  await expect(result.locator(".report-markdown script, .report-markdown img, .report-markdown iframe")).toHaveCount(0);
  await expect(result.getByRole("link", { name: "Remote", exact: true })).toHaveCount(0);
  await expect(result.getByRole("link", { name: "[K9]", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => window.__reportInjected)).toBeUndefined();
  expect(external).toEqual([]);
  await result.locator(".report-markdown").scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  const screenshots = path.join(tmpdir(), "agat-markdown-qa"); await mkdir(screenshots, { recursive: true });
  await page.screenshot({ path: path.join(screenshots, `${testInfo.project.name}-formatted.png`), fullPage: false });
  const link = result.locator(".report-markdown").getByRole("link", { name: "[K1]", exact: true }).first();
  await link.focus(); await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`documentId=${document.id}`));
  const preview = page.getByRole("region", { name: "Предпросмотр документа" });
  await expect(preview.locator("mark")).toHaveText(snippet.content);
  await page.goto(`/#runs/${id}`);
  await expect(result.locator(":scope > pre")).toHaveText(text);
  const downloadEvent = page.waitForEvent("download");
  await result.getByRole("button", { name: "Скачать", exact: true }).click();
  const download = await downloadEvent;
  const downloaded = await readFile(await download.path(), "utf8");
  expect(downloaded.startsWith(text)).toBe(true); expect(downloaded).toContain(document.chunks[0].contentSha256);
  expect((await (await request.get(`/api/v1/runs/${id}`)).json()).stages.at(-1).output).toBe(original);
  await expect(page.locator("vite-error-overlay")).toHaveCount(0); expect(errors).toEqual([]);
});

test.describe("uncached renderer failure", () => {
  test.use({ serviceWorkers: "block" });
  test("a failed deferred formatter keeps the full source result available", async ({ page, request }, testInfo) => {
    const created = await request.post("/api/v1/runs", { data: { name: `Formatter offline ${testInfo.project.name}`,
      input: "Original result must stay available", agentIds: ["collector"], approvalRequired: false } });
    expect(created.ok()).toBe(true); const { id } = await created.json();
    await expect.poll(async () => (await (await request.get(`/api/v1/runs/${id}`)).json()).status).toBe("completed");
    const run = await (await request.get(`/api/v1/runs/${id}`)).json();
    const original = run.stages.at(-1).output;
    await page.route("**/assets/ReportMarkdown-*.js", route => route.abort("failed"));
    await page.goto(`/#runs/${id}`);
    const result = page.locator("#run-result");
    await result.getByRole("button", { name: "Форматированный", exact: true }).click();
    await expect(result.getByRole("alert")).toContainText("Исходный текст и скачивание сохранены");
    await expect(result.locator(":scope > pre")).toHaveText(original);
    await result.getByRole("button", { name: "Исходный текст", exact: true }).click();
    await expect(result.getByRole("button", { name: "Исходный текст", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(result.locator(":scope > pre")).toHaveText(original);
  });
});
