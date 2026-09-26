import { test, expect } from "@playwright/test";

test("grouped citations open each exact source fragment without changing the report", async ({ page, request }, testInfo) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (["error", "warning"].includes(message.type())) errors.push(message.text()); });
  const collectionResponse = await request.post("/api/v1/knowledge/collections", { data: {
    name: `Citation UI ${testInfo.project.name}`, embeddingModel: "fixture-embedding", chunkSize: 4000, chunkOverlap: 0, topK: 2 } });
  expect(collectionResponse.ok(), await collectionResponse.text()).toBe(true);
  const collection = await collectionResponse.json();const documents = [];
  for (const [name, content] of [["Июль.md", "Учебный источник июля: 100 заявок."], ["Август.md", "Учебный источник августа: 120 заявок."]]) {
    const response = await request.post(`/api/v1/knowledge/collections/${collection.id}/documents`, {
      data: { name, mediaType: "text/markdown", content } });
    expect(response.ok(), await response.text()).toBe(true);
    const { id } = await response.json();
    documents.push(await (await request.get(`/api/v1/knowledge/documents/${id}`)).json());
  }
  const name = `Проверка ссылок ${testInfo.project.name}`;
  const response = await request.post("/api/v1/runs", { data: { name, input: "UI citation fixture",
    agentIds: ["collector"], approvalRequired: false } });
  expect(response.ok(), await response.text()).toBe(true);
  const { id } = await response.json();
  await expect.poll(async () => (await (await request.get(`/api/v1/runs/${id}`)).json()).status).toBe("completed");
  const run = await (await request.get(`/api/v1/runs/${id}`)).json();
  const originalOutput = run.stages.at(-1).output;
  const text = "Источники: [K1, K2]. Повторная ссылка [K1/K2]. Неизвестный [K9]. Диапазон [K1-K2].";
  // The stored run is untouched. These are rendering fixtures using real
  // ingested documents/previews; model/retrieval execution has separate tests.
  const presented = structuredClone(run);presented.stages.at(-1).output = text;
  const sources = documents.map((document, index) => ({ marker: `K${index + 1}`, stageId: run.stages.at(-1).id,
    retrievalId: `fixture-${index}`, createdAt: document.createdAt, excerpt: document.content, content: document.content,
    provenance: { projectId: "default", collectionId: collection.id, collectionName: collection.name, documentId: document.id,
      documentName: document.name, documentSha256: document.contentSha256, chunkId: document.chunks[0].id,
      chunkOrdinal: 0, charStart: 0, charEnd: document.content.length, chunkSha256: document.chunks[0].contentSha256,
      originalSha256: null, pageNumber: null, sourceUri: null } }));
  await page.route(`**/api/v1/runs/${id}`, route => route.fulfill({ json: presented }));
  await page.route("**/api/v1/overview", async route => {
    const upstream = await route.fetch();const overview = await upstream.json();
    await route.fulfill({ response: upstream, json: { ...overview,
      runs: overview.runs.map(item => item.id === id ? presented : item) } });
  });
  await page.route(`**/api/v1/runs/${id}/trace`, async route => {
    const upstream = await route.fetch();const trace = await upstream.json();
    await route.fulfill({ response: upstream, json: { ...trace, run: presented } });
  });
  await page.route(`**/api/v1/runs/${id}/knowledge`, route => route.fulfill({ json: { sources } }));
  await page.goto(`/#runs/${id}`);
  await expect(page).toHaveURL(new RegExp(`#runs/${id}$`));await expect(page).toHaveTitle(/АГАТ|Agat/i);
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("citation-initial-viewport.png"), fullPage: false });
  const report = page.locator("#run-result > pre");
  await expect(report).toHaveText(text);
  await expect(report.getByRole("link")).toHaveCount(4);
  await expect(report.getByRole("link", { name: "K9", exact: true })).toHaveCount(0);
  await report.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("citation-report.png"), fullPage: false });
  for (const [index, document] of documents.entries()) {
    const link = report.getByRole("link", { name: `K${index + 1}`, exact: true }).first();
    const href = await link.getAttribute("href");
    expect(href).toContain(`documentId=${document.id}`);expect(href).toContain(`chunkId=${document.chunks[0].id}`);
    await link.focus();await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`documentId=${document.id}`));
    const preview = page.getByRole("region", { name: "Предпросмотр документа" });
    await expect(preview.getByRole("heading", { name: document.name })).toBeVisible();
    await expect(preview.locator("mark")).toHaveText(document.content);
    await preview.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`citation-source-${index + 1}.png`), fullPage: false });
    await page.goto(`/#runs/${id}`);
    await expect(report).toHaveText(text);
  }
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);expect(errors).toEqual([]);
  expect((await (await request.get(`/api/v1/runs/${id}`)).json()).stages.at(-1).output).toBe(originalOutput);
});
