import { test, expect } from "@playwright/test";

test("create an agent, launch a task and see the persisted result", async ({ page, request }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  await expect.poll(async () => {
    const response = await request.get("/api/v1/overview");
    if (!response.ok()) return false;
    const overview = await response.json();
    return overview.nodes.some((node) => node.status === "online" && node.models.includes("smoke-model"));
  }).toBe(true);

  const name = `Первый помощник ${testInfo.project.name}`;
  const runName = `Первый результат ${testInfo.project.name}`;
  await page.goto("/#agents");
  await expect(page).toHaveTitle(/АГАТ|Agat/i);
  await expect(page.getByRole("heading", { name: "Агенты", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Новый агент", exact: true }).click();
  const agent = page.getByRole("dialog", { name: "Новый агент" });
  await agent.getByLabel("Имя агента", { exact: true }).fill(name);
  await agent.getByLabel("Роль", { exact: true }).fill("Кратко пересказывает входной текст");
  await agent.getByLabel("Инструкция агента", { exact: true }).fill("Перескажи входной текст в трёх пунктах, сохраняя факты.");
  await agent.getByPlaceholder("Автовыбор по доступному узлу").fill("smoke-model");
  await agent.getByRole("button", { name: "Создать агента", exact: true }).click();
  await expect(agent).not.toBeVisible();
  await page.getByRole("button", { name: `Запустить агента ${name}`, exact: true }).click();
  const run = page.getByRole("dialog", { name: "Новый запуск" });
  await run.getByLabel("Название запуска", { exact: true }).fill(runName);
  await run.getByPlaceholder("Опишите ожидаемый результат, источники данных и ограничения").fill("Команда обработала 12 заявок. Три требуют согласования, остальные завершены.");
  await run.getByRole("button", { name: "Далее: цепочка" }).click();
  await expect(run.getByLabel("Выбранная цепочка агентов")).toContainText(name);
  await run.getByRole("button", { name: "Далее: проверка" }).click();
  const created = page.waitForResponse((response) => response.url().endsWith("/api/v1/runs") && response.request().method() === "POST");
  await run.getByRole("button", { name: "Запустить", exact: true }).click();
  const response = await created;
  expect(response.ok()).toBe(true);
  const { id } = await response.json();
  await expect(page.getByRole("button", { name: "Разрешить продолжение", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Разрешить продолжение", exact: true }).click();
  await expect.poll(async () => (await (await request.get(`/api/v1/runs/${id}`)).json()).status).toBe("completed");
  await expect(page.getByText(`Тестовый результат этапа «${name}» для запуска «${runName}».`, { exact: true }).first()).toBeVisible();
  await page.reload();
  await expect(page.getByText(`Тестовый результат этапа «${name}» для запуска «${runName}».`, { exact: true }).first()).toBeVisible();
  expect(errors).toEqual([]);
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("first-run.png"), fullPage: true });
});
