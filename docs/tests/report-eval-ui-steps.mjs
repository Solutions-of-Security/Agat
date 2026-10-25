import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import path from "node:path";

export async function exerciseReportEvalHandoff({ page, context, base, headers, store, artifact, projectId, screenshotDir, errors }) {
  const review = page.getByRole("region", { name: "Решение о результате", exact: true });
  const endpoint = `${base}api/v1/artifacts/${artifact.id}/eval-dataset`;
  const beforeRun = structuredClone(store.getRun(artifact.runId, projectId));
  const beforeRuns = store.getOverview(projectId).runs.length;
  const beforeDatasets = store.listEvalDatasets(projectId).length;
  const completions = () => store.listEvents(0, 1000, projectId).filter(event => event.type === "stage.completed").length;
  const beforeCompletions = completions();
  const shot = name => page.screenshot({ path: path.join(screenshotDir, `agat-eval-${projectId}-${name}.png`), fullPage: false });
  const consume = pattern => {
    const expected = errors.filter(error => error.url === endpoint && pattern.test(error.text));
    assert.ok(expected.length > 0, "the intentionally failing import request appears in console evidence");
    for (const item of expected) errors.splice(errors.indexOf(item), 1);
    assert.deepEqual(errors, []);
  };
  await review.getByRole("button", { name: "В Golden eval", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Пример для Golden eval", exact: true });
  const reference = dialog.getByLabel("Эталонный JSON", { exact: true });
  await reference.waitFor();
  const save = dialog.getByRole("button", { name: "Сохранить dataset v1", exact: true });
  assert.equal(await reference.inputValue(), ""); assert.equal(await save.isDisabled(), true);
  assert.equal(store.listEvalDatasets(projectId).length, beforeDatasets);
  // Summaries are part of the keyboard focus trap, including when collapsed.
  await dialog.getByRole("button", { name: "Закрыть", exact: true }).focus();
  let summaryReached = false;
  for (let index = 0; index < 8; index++) {
    await page.keyboard.press("Tab");
    if (await page.evaluate(() => document.activeElement?.tagName === "SUMMARY")) { summaryReached = true; break; }
  }
  assert.equal(summaryReached, true);
  await dialog.getByRole("button", { name: "Взять JSON как черновик", exact: true }).click();
  const validReference = await reference.inputValue();
  await dialog.getByLabel("Название набора", { exact: true }).fill(`Проверенный пример ${projectId}`);
  await dialog.getByLabel("Критерий 1", { exact: true }).fill("Подтверждённые числа и источники");
  await dialog.getByLabel("Как проверять критерий 1", { exact: true }).fill("Сверить 100, 120, 20% и фрагмент Counts.csv");
  const consent = dialog.getByRole("checkbox", { name: /^Я проверил/ });
  assert.equal(await consent.isChecked(), false); assert.equal(await save.isDisabled(), true);
  const invalid = JSON.parse(validReference); invalid.calculations[0].result = 999;
  await reference.fill(JSON.stringify(invalid)); await consent.check(); await save.click();
  await dialog.getByText("Проверьте расчёты в эталонном JSON", { exact: true }).waitFor();
  await page.waitForFunction(() => { const element = [...document.querySelectorAll('.report-eval-dialog [role="alert"]')].find(item => item.textContent === "Проверьте расчёты в эталонном JSON");
    const body = document.querySelector('.report-eval-dialog__body'); if (!element || !body) return false; const a = element.getBoundingClientRect(), b = body.getBoundingClientRect(); return a.top >= b.top && a.bottom <= b.bottom; });
  assert.equal(store.listEvalDatasets(projectId).length, beforeDatasets);
  await shot("invalid-reference"); consume(/status of 400/);
  await reference.fill(validReference); assert.equal(await consent.isChecked(), false);
  assert.equal(await dialog.getByText("Проверьте расчёты в эталонном JSON", { exact: true }).count(), 0);
  await consent.check();
  // Closing the modal retains a draft while its report stays mounted.
  await dialog.getByRole("button", { name: "Закрыть", exact: true }).click(); await dialog.waitFor({ state: "hidden" });
  await review.getByRole("button", { name: "В Golden eval", exact: true }).click(); await reference.waitFor();
  assert.equal(await reference.inputValue(), validReference);
  const history = store.getBusinessReportDecisions(artifact.id, projectId);
  const changed = await context.request.post(`${base}api/v1/artifacts/${artifact.id}/decisions`, { headers, data: {
    requestId: randomUUID(), expectedSha256: artifact.sha256, expectedRevision: history.revision, decision: "accepted", reason: "Конкурентный выбор перед Golden импортом" } });
  assert.equal(changed.status(), 201);
  await save.click();
  await dialog.getByText("Обновите контекст и повторно проверьте пример. Ваш эталон и критерии сохранены в форме.", { exact: true }).waitFor();
  assert.equal(await reference.inputValue(), validReference); assert.equal(await save.isDisabled(), true);
  assert.equal(store.listEvalDatasets(projectId).length, beforeDatasets); await shot("stale-context"); consume(/status of 409/);
  await dialog.getByRole("button", { name: "Обновить контекст", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.report-eval-consent input')?.matches(":disabled") === false);
  assert.equal(await consent.isChecked(), false); assert.equal(await reference.inputValue(), validReference);
  await consent.check(); await save.scrollIntoViewIfNeeded();
  const closeBounds = await dialog.getByRole("button", { name: "Закрыть", exact: true }).boundingBox();
  assert.ok(closeBounds.y >= 0 && closeBounds.y + closeBounds.height <= page.viewportSize().height, "the sticky close control stays visible at the end of a long form");
  await shot("reviewed-draft");
  const requests = [];
  const intercept = async route => {
    requests.push(route.request().postDataJSON());
    const response = await route.fetch(); const receipt = await response.json();
    assert.equal(response.status(), requests.length === 1 ? 201 : 200); assert.equal(receipt.created, requests.length === 1);
    if (requests.length === 1) await route.abort("failed"); else await route.fulfill({ response });
  };
  await page.route(endpoint, intercept);
  await save.click();
  const retry = dialog.getByRole("button", { name: "Повторить сохранение dataset", exact: true });
  await retry.waitFor();
  assert.equal(await reference.isDisabled(), true); assert.equal(await consent.isDisabled(), true);
  assert.equal(store.listEvalDatasets(projectId).length, beforeDatasets + 1);
  await dialog.getByRole("button", { name: "Закрыть", exact: true }).click(); await dialog.waitFor({ state: "hidden" });
  await review.getByRole("button", { name: "В Golden eval", exact: true }).click(); await retry.waitFor();
  assert.equal(await reference.isDisabled(), true); assert.equal(requests.length, 1, "reopening an uncertain import preserves its frozen intent without resending");
  await retry.scrollIntoViewIfNeeded(); await shot("unconfirmed"); consume(/net::ERR_FAILED/);
  await retry.click(); await dialog.getByText("Пример сохранён · dataset v1", { exact: true }).waitFor();
  await page.unroute(endpoint, intercept); assert.equal(requests.length, 2); assert.deepEqual(requests[1], requests[0]);
  assert.equal(requests[0].expectedRevision, history.revision + 1);
  const dataset = store.listEvalDatasets(projectId).find(item => item.name === `Проверенный пример ${projectId}`);
  assert.ok(dataset); assert.equal(dataset.currentVersion, 1); assert.equal(dataset.versions.length, 1);
  const version = dataset.versions[0], lineage = version.knowledgeSnapshot.reportImport;
  assert.equal(lineage.source.artifactId, artifact.id); assert.equal(lineage.source.sourceSha256, artifact.sha256);
  assert.equal(lineage.source.decisionRevision, history.revision + 1); assert.equal(lineage.holdoutEligible, false);
  assert.equal(version.examples[0].referenceOutput, validReference.trim());
  assert.equal(lineage.referenceSha256, createHash("sha256").update(validReference.trim()).digest("hex"));
  assert.equal(store.listEvents(0, 1000, projectId).filter(event => event.type === "business.report.eval.dataset.created").length, 1);
  await shot("saved");
  const revised = await context.request.post(`${base}api/v1/evals/datasets/${dataset.id}/versions`, { headers, data: {
    description: "Explicit synthetic v2 after the durable v1 receipt", rubric: version.rubric,
    examples: [{ name: version.examples[0].name, input: version.examples[0].input, referenceOutput: version.examples[0].referenceOutput,
      knowledgeCollectionIds: version.examples[0].knowledgeCollectionIds }] } });
  assert.equal(revised.status(), 201);
  assert.equal(store.listEvalDatasets(projectId).find(item => item.id === dataset.id).currentVersion, 2);
  await dialog.getByRole("link", { name: "Открыть dataset v1", exact: true }).click();
  await page.waitForURL(new RegExp(`#evals\\?datasetId=${dataset.id}&datasetVersion=1$`));
  const origin = page.getByRole("region", { name: "Происхождение примера", exact: true });
  await origin.getByText("Development · из отчёта", { exact: true }).waitFor();
  assert.equal(await page.locator(".registry-detail h2").innerText(), dataset.name);
  const target = page.locator('article[data-requested-version="true"]');
  assert.equal(await target.getAttribute("data-dataset-version"), "1");
  await page.waitForFunction(() => document.activeElement?.getAttribute("data-dataset-version") === "1");
  assert.equal(await origin.getByRole("link", { name: "Исходный отчёт", exact: true }).getAttribute("href"), `#runs/${artifact.runId}`);
  await origin.scrollIntoViewIfNeeded(); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  await shot("dataset-lineage");
  await page.reload(); await origin.getByText("Development · из отчёта", { exact: true }).waitFor();
  await origin.getByRole("link", { name: "Исходный отчёт", exact: true }).click(); await page.waitForURL(new RegExp(`#runs/${artifact.runId}$`));
  assert.equal(completions(), beforeCompletions); assert.equal(store.getOverview(projectId).runs.length, beforeRuns);
  assert.deepEqual(store.getRun(artifact.runId, projectId), beforeRun);
  assert.equal(store.db.prepare("SELECT COUNT(*) AS count FROM eval_experiments WHERE project_id = ?").get(projectId).count, 0);
  assert.deepEqual(errors, []);
}

export async function exerciseEvalChunkFailure({ browser, base, token, projectId, runId, screenshotDir }) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, serviceWorkers: "block" });
  await context.addInitScript(({ token, projectId }) => { localStorage.setItem("agat.admin-token.v1", token); localStorage.setItem("agat.project-id.v1", projectId); }, { token, projectId });
  try {
    const page = await context.newPage(); const errors = []; let aborted = 0;
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", message => { if (["error", "warning"].includes(message.type())) errors.push(`${message.text()} ${message.location().url}`); });
    await page.route("**/assets/ReportEvalHandoffDialog-*.js", route => { aborted++; return route.abort("failed"); });
    await page.goto(`${base}#runs/${runId}`);
    const review = page.getByRole("region", { name: "Решение о результате", exact: true });
    await review.getByRole("button", { name: "В Golden eval", exact: true }).click();
    await review.getByText("Не удалось загрузить форму Golden eval. История решений и экспорт доступны.", { exact: true }).waitFor();
    assert.ok(aborted > 0); assert.equal(await review.getByRole("button", { name: "Принять отчёт", exact: true }).isEnabled(), true);
    const result = page.locator("#run-result"); await result.getByRole("button", { name: "Форматированный", exact: true }).click();
    await result.locator(".report-markdown table").waitFor();
    const downloaded = page.waitForEvent("download"); await result.getByRole("button", { name: "Скачать PDF", exact: true }).click();
    assert.match((await downloaded).suggestedFilename(), /\.pdf$/); assert.equal(await page.locator("vite-error-overlay").count(), 0);
    await review.scrollIntoViewIfNeeded(); await page.screenshot({ path: path.join(screenshotDir, "agat-eval-chunk-failure.png"), fullPage: false });
    assert.deepEqual(errors.filter(message => !message.includes("ReportEvalHandoffDialog-") && message.trim() !== "Service Worker registration blocked by Playwright"), []);
  } finally { await context.close(); }
}
