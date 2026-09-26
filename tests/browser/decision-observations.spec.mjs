import { test, expect } from "@playwright/test";

test("inspect shadow observations without changing the primary result", async ({ page, request }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (["error", "warning"].includes(message.type())) errors.push(message.text()); });
  const name = `Shadow UI ${testInfo.project.name}`;
  const response = await request.post("/api/v1/runs", { data: { name, input: "UI fixture input",
    agentIds: ["collector", "analyst", "editor"], approvalRequired: false } });
  expect(response.ok(), await response.text()).toBe(true);
  const { id } = await response.json();
  await expect.poll(async () => (await (await request.get(`/api/v1/runs/${id}`)).json()).status).toBe("completed");
  const run = await (await request.get(`/api/v1/runs/${id}`)).json();
  const originalOutput = run.stages.at(-1).output;
  await page.goto(`/#runs/${id}`);
  await expect(page).toHaveTitle(/АГАТ|Agat/i);
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("run-initial-viewport.png"), fullPage: false });
  await page.getByText("Технические детали", { exact: true }).click();
  await expect(page.getByRole("region", { name: "Локальные решения (shadow)" })).toHaveCount(0);

  // Rendering fixtures only; actual worker/coordinator/MLX persistence is tested separately.
  const observations = run.stages.map((stage, index) => ({ stageId: stage.id, profileSha256: "a".repeat(64),
    context: { kind: "boolean", question: "Подтверждён ли запуск исходными данными?", options: [
      { id: "no", description: "Запуск не подтверждён", value: false, abstain: false },
      { id: "yes", description: "Запуск подтверждён", value: true, abstain: false }] },
    observation: index === 2 ? { mode: "shadow", fallback: "primary", status: "unavailable", reason: "timeout" } : {
      mode: "shadow", fallback: "primary", status: index === 0 ? "ok" : "abstain",
      reason: index === 0 ? "accepted" : "below_threshold",
      ...(index === 1 ? { reusedFromStageId: "previous-run-stage" } : {}),
      result: { value: index === 0 ? false : null, selectedOptionId: "no", selectedProbability: index === 0 ? 0.95 : 0.55,
        margin: index === 0 ? 0.9 : 0.1, policy: { minProbability: 0.8, minMargin: 0.1 },
        durationMs: 382.5, runtimeVersion: "fixture", inputSha256: "b".repeat(64),
        model: { repository: "test-only/decision-fixture", revision: "fixture" },
        calibration: { status: "uncalibrated", temperature: 1 },
        distribution: [{ id: "no", probability: index === 0 ? 0.95 : 0.55, logit: Math.log(index === 0 ? 0.95 : 0.55) },
          { id: "yes", probability: index === 0 ? 0.05 : 0.45, logit: Math.log(index === 0 ? 0.05 : 0.45) }] },
    } }));
  await page.route(`**/api/v1/runs/${id}/trace`, async (route) => {
    const original = await route.fetch();
    const trace = await original.json();
    await route.fulfill({ response: original, json: { ...trace, decisionObservations: observations } });
  });
  await page.reload();
  await page.getByText("Технические детали", { exact: true }).click();
  const panel = page.getByRole("region", { name: "Локальные решения (shadow)" });
  await expect(panel).toBeVisible();
  await expect(panel.getByText("Нет (false)", { exact: true })).toBeVisible();
  await expect(panel.getByText("Подтверждён ли запуск исходными данными?", { exact: false }).first()).toBeVisible();
  await expect(panel.getByText("Решение не принято", { exact: true })).toBeVisible();
  await expect(panel.getByText("Истёк срок ожидания локальной модели", { exact: true })).toBeVisible();
  await expect(panel.getByText("Сохранённое наблюдение из этапа", { exact: false })).toBeVisible();
  await panel.getByText("Распределение и профиль", { exact: true }).first().focus();
  await page.keyboard.press("Enter");
  await expect(panel.getByRole("table").first()).toBeVisible();
  await expect(panel.getByText("Запуск не подтверждён", { exact: true }).first()).toBeVisible();
  await expect(panel.getByText("a".repeat(64), { exact: true }).first()).toBeVisible();
  await expect(page.locator("#run-result")).toContainText(originalOutput);
  await panel.evaluate((element) => {
    element.scrollIntoView({ block: "start", behavior: "instant" });
    // Keep the section heading clear of the shell's sticky navigation in evidence.
    window.scrollBy({ top: -48, behavior: "instant" });
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("decision-shadow-viewport.png"), fullPage: false });
  await page.screenshot({ path: testInfo.outputPath("decision-shadow-page.png"), fullPage: true });
  await panel.getByText("Распределение и профиль", { exact: true }).first().click();
  await expect(panel.getByRole("table").first()).not.toBeVisible();

  observations[0].context = { kind: "score", question: "Оцените полноту по числовой шкале", options: [
    { id: "no", description: "Неполные данные", value: 0, abstain: false },
    { id: "yes", description: "Полные данные", value: 1.5, abstain: false }] };
  observations[0].observation.result.value = 0.075;
  await page.reload();
  await page.getByText("Технические детали", { exact: true }).click();
  await expect(panel.getByText("Среднее по шкале", { exact: true })).toBeVisible();
  await expect(panel.getByText("0.075", { exact: true })).toBeVisible();
  await panel.getByText("Распределение и профиль", { exact: true }).first().click();
  await expect(panel.getByText("Уровень: 1.5", { exact: true })).toBeVisible();
  await expect(page.locator("#run-result")).toContainText(originalOutput);
  await panel.evaluate((element) => {
    element.scrollIntoView({ block: "start", behavior: "instant" });
    window.scrollBy({ top: -48, behavior: "instant" });
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("decision-score-viewport.png"), fullPage: false });
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  expect(errors).toEqual([]);
});
