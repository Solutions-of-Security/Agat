import { test, expect } from "@playwright/test";
import { decisionProfileJson } from "../fixtures/decision-shadow-profile.mjs";

test.use({ actionTimeout: 10_000 });

async function fixture(page, request, testInfo, suffix) {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (["error", "warning"].includes(message.type())) errors.push(message.text()); });
  const agentResponse = await request.post("/api/v1/agents", { data: {
    name: `Проверяющий ${suffix} ${testInfo.project.name}`, role: "Проверка данных",
    systemPrompt: "Сохрани исходные данные в результате.", model: "smoke-model",
  } });
  expect(agentResponse.ok(), await agentResponse.text()).toBe(true);
  const agent = await agentResponse.json();
  const node = (id, type, name, x, config = {}) => ({ id, type, name, position: { x, y: 160 }, config });
  const response = await request.post("/api/v1/processes", { data: {
    name: `Локальная проверка ${suffix} ${testInfo.project.name}`, graph: {
      nodes: [node("start", "start", "Старт", 0), node("agent", "agent", "Проверка данных", 260, {
        agentId: agent.id, approvalRequired: false,
      }), node("end", "end", "Готово", 520)],
      edges: [{ id: "s-a", source: "start", target: "agent", branch: "default" },
        { id: "a-e", source: "agent", target: "end", branch: "default" }],
    },
  } });
  expect(response.ok(), await response.text()).toBe(true);
  const process = await response.json();
  const read = async () => (await (await request.get(`/api/v1/processes/${process.id}`)).json());
  const readConfig = async () => (await read()).draftGraph.nodes.find(node => node.id === "agent").config;
  const inspector = page.locator(".process-inspector");
  const dialog = page.getByRole("dialog", { name: "Локальная проверка шага", exact: true });
  await page.goto(`/#processes?processId=${process.id}&nodeId=agent`);
  await expect(page).toHaveTitle(/АГАТ|Agat/i);
  await expect(page).toHaveURL(new RegExp(`processId=${process.id}`));
  await expect(inspector.getByRole("heading", { name: "Проверка данных", exact: true })).toBeVisible();
  return { agent, process, read, readConfig, inspector, dialog, errors };
}

async function fillBase(dialog) {
  await dialog.getByLabel("Вопрос проверки", { exact: true }).fill("Подтверждены ли выводы исходными данными?");
  await dialog.getByLabel("Профиль модели (profileJson)", { exact: false }).fill(decisionProfileJson);
}
async function screenshot(page, dialog, testInfo, name) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  const box = await dialog.boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(11);
  expect(box.y).toBeGreaterThanOrEqual(11);
  expect(box.y + box.height).toBeLessThanOrEqual(page.viewportSize().height - 11);
  expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize().width);
  expect(box.height).toBeLessThanOrEqual(page.viewportSize().height);
  await page.screenshot({ path: testInfo.outputPath(name), fullPage: false });
}
async function publish(page, f, version) {
  await f.inspector.getByRole("button", { name: "Закрыть настройки", exact: true }).click();
  await page.getByRole("button", { name: version === 1 ? "Опубликовать" : "Новая версия", exact: true }).click();
  await expect.poll(async () => (await f.read()).publishedVersion).toBe(version);
  await page.goto(`/#processes?processId=${f.process.id}&nodeId=agent`);
}

test("configure and publish Boolean shadow, cancel safely and retain the primary path when disabled", async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  const f = await fixture(page, request, testInfo, "Boolean");
  const setup = f.inspector.getByRole("button", { name: "Настроить проверку", exact: true });
  await setup.click();
  await expect(f.dialog.getByLabel("Тип проверки", { exact: false })).toBeFocused();
  await f.dialog.getByRole("button", { name: "Применить к шагу", exact: true }).click();
  await expect(f.dialog.getByRole("alert")).toContainText("Введите вопрос");
  expect((await f.readConfig()).decisionShadow).toBeUndefined();
  await fillBase(f.dialog);
  await page.keyboard.press("Escape");
  await expect(f.dialog).not.toBeVisible();
  await expect(setup).toBeFocused();
  expect((await f.readConfig()).decisionShadow).toBeUndefined();
  await setup.click();
  await expect(f.dialog.getByLabel("Вопрос проверки", { exact: true })).toHaveValue("");
  await fillBase(f.dialog);
  await f.dialog.getByLabel("Время ожидания, мс", { exact: true }).fill("3500");
  await f.dialog.getByLabel("Тип проверки", { exact: false }).focus();
  await f.dialog.evaluate(element => { element.scrollTop = 0; element.querySelector(".decision-shadow-profile").scrollTop = 0; });
  await screenshot(page, f.dialog, testInfo, "boolean-editor.png");
  const apply = f.dialog.getByRole("button", { name: "Применить к шагу", exact: true });
  await apply.focus(); await page.keyboard.press("Tab");
  await expect(f.dialog.getByRole("button", { name: "Закрыть проверку", exact: true })).toBeFocused();
  await apply.click();
  await expect(f.dialog).not.toBeVisible();
  await expect(f.inspector.getByRole("button", { name: "Изменить проверку", exact: true })).toBeFocused();
  await expect.poll(async () => (await f.readConfig()).decisionShadow?.profileJson).toBe(decisionProfileJson);
  const config = await f.readConfig();
  expect(config.decisionShadow.options.map(option => option.value)).toEqual([false, true]);
  expect(config.decisionShadow.timeoutMs).toBe(3500);
  expect(config.agentId).toBe(f.agent.id);
  expect(config.approvalRequired).toBe(false);
  await publish(page, f, 1);
  await page.reload();
  await f.inspector.getByRole("button", { name: "Изменить проверку", exact: true }).click();
  await expect(f.dialog.getByLabel("Профиль модели (profileJson)", { exact: false })).toHaveValue(decisionProfileJson);
  await expect(f.dialog.getByLabel("Значение варианта 1", { exact: true })).toHaveValue("false");
  await f.dialog.getByLabel("Вопрос проверки", { exact: true }).fill("Этот черновик отменён");
  await f.dialog.getByRole("button", { name: "Отмена", exact: true }).click();
  expect((await f.readConfig()).decisionShadow).toEqual(config.decisionShadow);
  const started = await request.post(`/api/v1/processes/${f.process.id}/start`, { data: {
    input: "Исходные данные для основного агента", version: 1, startMode: "now",
  } });
  expect(started.ok(), await started.text()).toBe(true);
  const instance = await started.json();
  await expect.poll(async () => (await (await request.get(`/api/v1/runs/${instance.runId}`)).json()).status).toBe("completed");
  const run = await (await request.get(`/api/v1/runs/${instance.runId}`)).json();
  expect(run.stages).toHaveLength(1);
  expect(run.stages[0].output).toContain("Тестовый результат этапа");
  const trace = await (await request.get(`/api/v1/runs/${instance.runId}/trace`)).json();
  expect(trace.decisionObservations).toHaveLength(1);
  expect(trace.decisionObservations[0].observation).toMatchObject({ status: "unavailable", reason: "disabled", fallback: "primary" });
  await f.inspector.getByRole("button", { name: "Убрать проверку", exact: true }).click();
  await expect.poll(async () => Object.hasOwn(await f.readConfig(), "decisionShadow")).toBe(false);
  expect((await f.readConfig()).agentId).toBe(f.agent.id);
  expect((await f.readConfig()).approvalRequired).toBe(false);
  await page.reload();
  await expect(setup).toBeVisible();
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  expect(f.errors).toEqual([]);
});

test("edit Score and Choice candidates, preserve order and prevent invalid local drafts", async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  const f = await fixture(page, request, testInfo, "Score-Choice");
  await f.inspector.getByRole("button", { name: "Настроить проверку", exact: true }).click();
  await f.dialog.getByLabel("Тип проверки", { exact: false }).selectOption("score");
  await fillBase(f.dialog);
  await f.dialog.getByLabel("Описание варианта 1", { exact: true }).fill("Недостаточно данных");
  await f.dialog.getByLabel("Описание варианта 2", { exact: true }).fill("Полные данные");
  await f.dialog.getByLabel("Значение варианта 1", { exact: true }).fill("1,5");
  await f.dialog.getByRole("button", { name: "Применить к шагу", exact: true }).click();
  await expect(f.dialog.getByRole("alert")).toContainText("нужно конечное число");
  expect((await f.readConfig()).decisionShadow).toBeUndefined();
  await f.dialog.getByLabel("Значение варианта 1", { exact: true }).fill("-0");
  await f.dialog.getByLabel("Значение варианта 2", { exact: true }).fill("1.5");
  await f.dialog.getByRole("button", { name: "Переместить вариант 2 вверх", exact: true }).click();
  await expect(f.dialog.getByLabel("Описание варианта 1", { exact: true })).toHaveValue("Полные данные");
  await expect(f.dialog.getByRole("alert")).toHaveCount(0);
  await f.dialog.getByLabel("Описание варианта 1", { exact: true }).scrollIntoViewIfNeeded();
  await screenshot(page, f.dialog, testInfo, "score-options.png");
  await f.dialog.getByRole("button", { name: "Применить к шагу", exact: true }).click();
  await expect.poll(async () => (await f.readConfig()).decisionShadow?.options.map(option => option.value)).toEqual([1.5, 0]);
  await publish(page, f, 1);
  await page.reload();
  await f.inspector.getByRole("button", { name: "Изменить проверку", exact: true }).click();
  await expect(f.dialog.getByLabel("Значение варианта 1", { exact: true })).toHaveValue("1.5");
  await f.dialog.getByLabel("Тип проверки", { exact: false }).selectOption("choice");
  await f.dialog.getByLabel("Описание варианта 1", { exact: true }).fill("Данных недостаточно");
  await f.dialog.getByLabel("Описание варианта 2", { exact: true }).fill("Достаточно для вывода");
  await f.dialog.getByLabel("Вариант 1 означает отказ от оценки", { exact: true }).check();
  await f.dialog.getByRole("button", { name: "Добавить вариант", exact: true }).click();
  await f.dialog.getByLabel("Код варианта 3", { exact: true }).fill("extra");
  await f.dialog.getByLabel("Описание варианта 3", { exact: true }).fill("Временный вариант");
  await f.dialog.getByRole("button", { name: "Удалить вариант 3", exact: true }).click();
  await expect(f.dialog.getByRole("group", { name: "Вариант 3", exact: true })).toHaveCount(0);
  await f.dialog.getByLabel("Код варианта 2", { exact: true }).fill("option_1");
  await f.dialog.getByRole("button", { name: "Применить к шагу", exact: true }).click();
  await expect(f.dialog.getByRole("alert")).toContainText("Коды вариантов должны различаться");
  expect((await f.readConfig()).decisionShadow.kind).toBe("score");
  await f.dialog.getByLabel("Код варианта 2", { exact: true }).fill("accepted");
  await f.dialog.getByRole("button", { name: "Переместить вариант 1 вниз", exact: true }).click();
  await expect(f.dialog.getByRole("alert")).toHaveCount(0);
  await f.dialog.getByLabel("Вариант 2 означает отказ от оценки", { exact: true }).scrollIntoViewIfNeeded();
  await screenshot(page, f.dialog, testInfo, "choice-options.png");
  await f.dialog.getByRole("button", { name: "Применить к шагу", exact: true }).click();
  await expect.poll(async () => (await f.readConfig()).decisionShadow?.kind).toBe("choice");
  await publish(page, f, 2);
  await page.reload();
  const result = (await f.readConfig()).decisionShadow;
  expect(result.profileJson).toBe(decisionProfileJson);
  expect(result.mode).toBe("shadow");
  expect(result.options.map(option => option.id)).toEqual(["accepted", "option_1"]);
  expect(result.options.map(option => option.abstain)).toEqual([false, true]);
  expect(result.options.every(option => !Object.hasOwn(option, "value"))).toBe(true);
  await f.inspector.getByRole("button", { name: "Изменить проверку", exact: true }).click();
  await expect(f.dialog.getByLabel("Вариант 2 означает отказ от оценки", { exact: true })).toBeChecked();
  await f.dialog.getByRole("button", { name: "Отмена", exact: true }).click();
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  expect(f.errors).toEqual([]);
});
