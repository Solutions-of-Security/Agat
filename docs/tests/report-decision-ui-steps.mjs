import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import path from "node:path";

// A real server commits the first POST; only its response is lost in transit.
export async function exerciseReportDecision({ page, context, base, headers, store, artifact, projectId, screenshotDir, errors }) {
  const endpoint = `${base}api/v1/artifacts/${artifact.id}/decisions`;
  const review = page.getByRole("region", { name: "Решение о результате", exact: true });
  const savedHistory = () => store.getBusinessReportDecisions(artifact.id, projectId);
  const completions = () => store.listEvents(0, 1000, projectId).filter(event => event.type === "stage.completed").length;
  const beforeCompletions = completions();
  const beforeRuns = store.getOverview(projectId).runs.length;
  const originalRun = structuredClone(store.getRun(artifact.runId, projectId));
  const shot = name => page.screenshot({ path: path.join(screenshotDir, `agat-review-${projectId}-${name}.png`), fullPage: false });
  const consumeFailure = pattern => {
    const expected = errors.filter(error => error.url === endpoint && pattern.test(error.text));
    assert.ok(expected.length > 0, "the injected HTTP failure was observed in the browser console");
    for (const item of expected) errors.splice(errors.indexOf(item), 1);
    assert.deepEqual(errors, [], "only the injected request failure is expected");
  };
  await review.getByText("Решение ещё не сохранено", { exact: true }).waitFor();
  assert.equal(await review.locator("form").count(), 0, "no decision is selected in advance");
  const unavailable = route => route.fulfill({ status: 503, json: { error: "History temporarily unavailable in QA" } });
  await page.route(endpoint, unavailable);
  await review.getByRole("button", { name: "Обновить историю", exact: true }).click();
  await review.getByRole("button", { name: "Повторить загрузку решений", exact: true }).waitFor();
  assert.equal(await review.getByRole("button", { name: "Принять отчёт", exact: true }).isDisabled(), true);
  consumeFailure(/status of 503/);
  await page.unroute(endpoint, unavailable);
  await review.getByRole("button", { name: "Повторить загрузку решений", exact: true }).click();
  await page.waitForFunction(() => ![...document.querySelectorAll(".report-review button")].find(button => button.textContent === "Нужны исправления")?.disabled);
  await review.getByRole("button", { name: "Нужны исправления", exact: true }).click();
  const form = review.getByRole("form", { name: "Запрос исправлений" });
  assert.equal(await form.getByRole("button", { name: "Сохранить решение", exact: true }).isDisabled(), true);
  const reason = "Уточните период.\n<script>window.__reviewInjected=true</script>";
  await form.getByLabel("Комментарий (обязательно)", { exact: true }).fill(reason);
  await form.scrollIntoViewIfNeeded(); await shot("choice");
  assert.deepEqual(errors, [], "normal review renders without console warnings or errors");
  const requests = [];
  const intercept = async route => {
    if (route.request().method() !== "POST") return route.continue();
    requests.push(route.request().postDataJSON());
    const response = await route.fetch();
    const receipt = await response.json();
    assert.equal(response.status(), requests.length === 1 ? 201 : 200);
    assert.equal(receipt.created, requests.length === 1);
    if (requests.length === 1) await route.abort("failed");
    else await route.fulfill({ response });
  };
  await page.route(endpoint, intercept);
  await form.getByRole("button", { name: "Сохранить решение", exact: true }).click();
  await form.getByRole("button", { name: "Повторить сохранение", exact: true }).waitFor();
  assert.equal(savedHistory().revision, 1, "server committed before the response was lost");
  assert.equal(await form.getByRole("textbox").isDisabled(), true, "uncertain intent remains frozen for retry");
  await form.scrollIntoViewIfNeeded(); await shot("unconfirmed");
  consumeFailure(/net::ERR_FAILED/);
  await form.getByRole("button", { name: "Повторить сохранение", exact: true }).click();
  await review.getByText("Решение записано в историю.", { exact: true }).waitFor();
  await page.unroute(endpoint, intercept);
  assert.equal(requests.length, 2); assert.deepEqual(requests[1], requests[0]);
  assert.equal(savedHistory().revision, 1);
  assert.equal(savedHistory().decisions.length, 1);
  const rework = savedHistory().decisions[0];
  assert.equal(rework.reason, reason); assert.ok(rework.reworkTaskId);
  assert.equal(store.listEvents(0, 1000, projectId).filter(event => event.type === "business.report.decision.recorded").length, 1);
  assert.equal(await page.evaluate(() => window.__reviewInjected), undefined);
  await review.getByRole("button", { name: "Открыть задачу", exact: true }).click();
  const task = page.getByRole("dialog", { name: "Задача исправления", exact: true });
  await task.getByText("Требуется ручное исправление", { exact: true }).waitFor();
  assert.equal(await task.locator(".report-task__reason").innerText(), reason);
  const taskBounds = await task.boundingBox();
  assert.ok(taskBounds.x >= 0 && taskBounds.x + taskBounds.width <= page.viewportSize().width);
  assert.equal(await task.evaluate(element => element.scrollWidth <= element.clientWidth + 1), true);
  await task.getByRole("link", { name: "Исходный запуск", exact: true }).focus();
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement?.tagName === "SUMMARY" && document.activeElement.textContent === "Текст задания для передачи"), true, "the final collapsed summary remains reachable inside the focus trap");
  // Force the supported clipboard-denied path, rather than relying on host permissions.
  await page.evaluate(() => { Object.defineProperty(navigator.clipboard, "writeText", { configurable: true, value: () => Promise.reject(new Error("clipboard denied in QA")) }); });
  await task.getByRole("button", { name: "Скопировать задание", exact: true }).click();
  const brief = task.getByRole("textbox", { name: "Текст задания", exact: true });
  await brief.waitFor({ state: "visible" });
  await page.waitForFunction(() => { const element = document.querySelector('textarea[aria-label="Текст задания"]'); return element && document.activeElement === element && element.selectionEnd === element.value.length; });
  for (const part of [artifact.sha256, reason, `/#runs/${artifact.runId}`, "ещё не выполнено"]) assert.ok((await brief.inputValue()).includes(part));
  assert.equal(await brief.evaluate(element => document.activeElement === element && element.selectionEnd === element.value.length), true);
  await shot("task");
  const downloadEvent = page.waitForEvent("download");
  await task.getByRole("button", { name: "Скачать исходный отчёт", exact: true }).click();
  const downloaded = await downloadEvent;
  assert.equal(downloaded.suggestedFilename(), artifact.name);
  assert.equal(createHash("sha256").update(readFileSync(await downloaded.path())).digest("hex"), artifact.sha256);
  await page.keyboard.press("Escape"); await task.waitFor({ state: "hidden" });
  await page.waitForFunction(() => document.activeElement?.textContent === "Открыть задачу");
  assert.equal(await review.getByRole("button", { name: "Открыть задачу", exact: true }).evaluate(element => element === document.activeElement), true);

  // A second client on the same verified local account changes the revision.
  await review.getByRole("button", { name: "Принять отчёт", exact: true }).click();
  const acceptForm = review.getByRole("form", { name: "Принять отчёт", exact: true });
  await acceptForm.getByLabel("Комментарий (необязательно)", { exact: true }).fill("Мой выбор до обновления истории");
  const concurrent = await context.request.post(endpoint, { headers, data: { requestId: randomUUID(), expectedSha256: artifact.sha256,
    expectedRevision: 1, decision: "accepted", reason: "Проверено во втором клиенте" } });
  assert.equal(concurrent.status(), 201); assert.equal((await concurrent.json()).reworkTask, null);
  await acceptForm.getByRole("button", { name: "Сохранить решение", exact: true }).click();
  await review.getByText("История или версия отчёта изменилась. Прочитайте новое решение и выберите действие заново.", { exact: true }).waitFor();
  await review.getByText("Отчёт принят", { exact: true }).waitFor();
  assert.equal(await review.locator("form").count(), 0);
  assert.equal(savedHistory().revision, 2, "stale UI cannot overwrite a later decision");
  assert.equal(savedHistory().decisions.filter(item => item.reworkTaskId).length, 1);
  await review.scrollIntoViewIfNeeded(); await shot("conflict"); consumeFailure(/status of 409/);
  await page.reload();
  await review.getByText("Отчёт принят", { exact: true }).waitFor();
  await review.getByText("История решений · 2", { exact: true }).click();
  assert.equal(await review.getByRole("list", { name: "История решений" }).locator("li").count(), 2);
  await review.getByRole("button", { name: "Задача исправления №1", exact: true }).click();
  await task.getByText("Запрос заменён более новым решением", { exact: true }).waitFor();
  assert.equal(await task.getByRole("button", { name: "Скопировать задание", exact: true }).isDisabled(), true);
  await shot("superseded"); await task.getByRole("button", { name: "Закрыть", exact: true }).click();
  // Exercise the actual 50-row cursor boundary, rather than a mocked long history.
  for (let revision = 2; revision < 52; revision++) {
    const response = await context.request.post(endpoint, { headers, data: { requestId: randomUUID(), expectedSha256: artifact.sha256,
      expectedRevision: revision, decision: "accepted", reason: `Synthetic pagination check ${revision + 1}` } });
    assert.equal(response.status(), 201);
  }
  await page.reload();
  await review.getByText("История решений · 52", { exact: true }).click();
  const rows = review.getByRole("list", { name: "История решений" }).locator("li");
  assert.equal(await rows.count(), 50);
  await review.getByRole("button", { name: "Показать предыдущие решения", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll('.report-review ol[aria-label="История решений"] > li').length === 52);
  assert.equal(await rows.count(), 52);
  assert.equal(await review.getByRole("button", { name: "Показать предыдущие решения", exact: true }).count(), 0);
  assert.equal(completions(), beforeCompletions);
  assert.equal(store.getOverview(projectId).runs.length, beforeRuns);
  assert.deepEqual(store.getRun(artifact.runId, projectId), originalRun, "decisions leave completed run, stages and approvals intact");
  assert.deepEqual(errors, []);
}

export async function exerciseDecisionChunkFailure({ browser, base, token, projectId, runId, screenshotDir }) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, serviceWorkers: "block" });
  await context.addInitScript(({ token, projectId }) => {
    localStorage.setItem("agat.admin-token.v1", token); localStorage.setItem("agat.project-id.v1", projectId);
  }, { token, projectId });
  try {
    const page = await context.newPage(); let aborted = 0;
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", message => { if (["error", "warning"].includes(message.type())) errors.push(`${message.text()} ${message.location().url}`); });
    await page.route("**/assets/ReportDecisionPanel-*.js", route => { aborted++; return route.abort("failed"); });
    await page.goto(`${base}#runs/${runId}`);
    assert.match(await page.title(), /Агат|Agat/i);
    const result = page.locator("#run-result");
    await result.getByText("Не удалось загрузить действия с отчётом. Чтение и скачивание доступны.", { exact: true }).waitFor();
    assert.ok(aborted > 0, "a cold context really requested the missing chunk");
    await result.getByRole("button", { name: "Форматированный", exact: true }).click();
    await result.locator(".report-markdown table").waitFor();
    const download = page.waitForEvent("download");
    await result.getByRole("button", { name: "Скачать PDF", exact: true }).click();
    assert.match((await download).suggestedFilename(), /\.pdf$/);
    assert.equal(await page.locator("vite-error-overlay").count(), 0);
    await result.locator(".report-review").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(screenshotDir, "agat-review-chunk-failure.png"), fullPage: false });
    assert.ok(errors.length > 0, "missing chunk failure is visible in isolated console evidence");
    assert.deepEqual(errors.filter(message => !message.includes("ReportDecisionPanel-") && message.trim() !== "Service Worker registration blocked by Playwright"), [], "only the deliberately missing chunk and Playwright service-worker warning may fail");
  } finally { await context.close(); }
}
