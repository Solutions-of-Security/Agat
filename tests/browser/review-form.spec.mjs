import { test, expect } from "@playwright/test";
import { spawn, execFileSync } from "node:child_process";
import { readFile, mkdir } from "node:fs/promises";
import path from "node:path";

let server, url;
const bundle = JSON.parse(execFileSync("python3", ["-c", "import json; from scripts.test.test_decision_review_form import bundle_fixture; print(json.dumps(bundle_fixture(), ensure_ascii=False))"], { encoding: "utf8" }));
async function startFixture(cases = 3) {
  const child = spawn("python3", ["-u", "scripts/review-form/fixture-server.py", "--cases", String(cases)], { stdio: ["ignore", "pipe", "pipe"] });
  const fixtureUrl = await new Promise((resolve, reject) => {
    let output = ""; const timer = setTimeout(() => reject(new Error("Synthetic form server did not start")), 10000);
    child.on("error", error => { clearTimeout(timer); reject(error); }); child.on("exit", code => { clearTimeout(timer); reject(new Error(`Synthetic server exited ${code}`)); });
    child.stdout.on("data", chunk => { output += chunk; if (output.includes("\n")) { clearTimeout(timer); resolve(JSON.parse(output.split("\n")[0]).url); } });
  });
  return { child, url: fixtureUrl };
}
async function stopFixture(child) {
  if (child && child.exitCode === null) { const exited = new Promise(resolve => child.once("exit", resolve)); child.kill("SIGTERM"); await exited; }
}
test.beforeAll(async () => {
  const fixture = await startFixture(); server = fixture.child; url = fixture.url;
});
test.afterAll(async () => { await stopFixture(server); });

async function answer(page, index, rationale = "Синтетическое обоснование для проверки интерфейса.") {
  const navigation = page.getByRole("navigation", { name: "Выберите вопрос" });
  if (!(await navigation.isVisible())) await page.getByText("Номера вопросов", { exact: true }).click();
  await page.getByRole("button", { name: new RegExp(`^Вопрос ${index}:`) }).click();
  await page.getByRole("radio", { name: /^Прочая помощь/ }).check();
  await page.getByLabel("Почему вы выбрали этот вариант?").fill(rationale);
}

test("Russian rubric, no default answers, safe source markup and responsive layout", async ({ page }, info) => {
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  await page.goto(url);
  await expect(page.locator("html")).toHaveAttribute("lang", "ru");
  await expect(page.getByRole("heading", { name: "Экспертная оценка обращений" })).toBeVisible();
  await expect(page.locator("#count")).toHaveText("Отвечено 0 из 3");
  await expect(page.getByRole("radio")).toHaveCount(5);
  await expect(page.locator('input[type="radio"]:checked')).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Скачать итоговые ответы" })).toBeDisabled();
  await expect(page.locator("#options")).not.toContainText(/Incident|Enhancement|Access|Other|Insufficient/);
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "К текущему вопросу" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#question-title")).toBeFocused();
  await page.getByText("Команды и вывод программы — исходный текст", { exact: true }).click();
  await expect(page.locator("#source-body pre")).toContainText("<script>not executed</script>");
  await expect(page.locator("#source-body script")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
  if (process.env.AGAT_REVIEW_FORM_SCREENSHOTS) {
    await mkdir(process.env.AGAT_REVIEW_FORM_SCREENSHOTS, { recursive: true });
    await page.screenshot({ path: path.join(process.env.AGAT_REVIEW_FORM_SCREENSHOTS, `${info.project.name}.png`), fullPage: true });
  }
});

test("partially typed draft downloads and imports, including selection without rationale", async ({ page }) => {
  await page.goto(url);
  await page.getByRole("radio", { name: /^Инцидент/ }).check();
  const pending = page.waitForEvent("download"); await page.getByRole("button", { name: "Скачать черновик", exact: true }).click();
  const download = await pending; const file = JSON.parse(await readFile(await download.path(), "utf8"));
  expect(file.status).toBe("draft"); expect(file.submissionConfirmed).toBe(false);
  expect(file.answers[0].optionLabelRu).toBe("Инцидент"); expect(file.answers[0].rationale).toBe("");
  await page.reload(); await expect(page.getByRole("radio", { name: /^Инцидент/ })).toBeChecked();
  await page.locator("#file").setInputFiles({ name: "synthetic-draft.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(file)) });
  await expect(page.getByRole("dialog")).toBeVisible(); await page.getByRole("button", { name: "Загрузить", exact: true }).click();
  await expect(page.getByRole("radio", { name: /^Инцидент/ })).toBeChecked();
  await expect(page.locator("#count")).toHaveText("Отвечено 0 из 3");
});

test("completed export requires confirmation, remains editable, and round-trips through Python", async ({ page }) => {
  await page.goto(url); await page.getByLabel("Ваше имя или псевдоним").fill("Синтетический участник");
  for (let i = 1; i <= 3; i++) await answer(page, i);
  await expect(page.locator("#count")).toHaveText("Отвечено 3 из 3");
  await page.getByRole("button", { name: "Скачать итоговые ответы" }).click();
  await page.getByRole("button", { name: "Вернуться", exact: true }).click();
  await expect(page.getByLabel("Почему вы выбрали этот вариант?")).toBeEnabled();
  await page.getByRole("button", { name: "Скачать итоговые ответы" }).click();
  const pending = page.waitForEvent("download"); await page.getByRole("button", { name: "Подтвердить и скачать" }).click();
  const download = await pending; const raw = await readFile(await download.path(), "utf8"); const file = JSON.parse(raw);
  expect(file.status).toBe("submitted"); expect(file.submissionConfirmed).toBe(true);
  expect(file.answers).toHaveLength(3); expect(file.answers.every(a => /[А-Яа-яЁё]/.test(a.rationale))).toBe(true);
  const converted = JSON.parse(execFileSync("python3", ["-c", "import json,sys; from scripts.test.test_decision_review_form import bundle_fixture; from scripts.lib.decision_review_form import export_review; b=bundle_fixture(); print(json.dumps(export_review(json.load(sys.stdin),b['manifest'],b['blank'],b['manifest']['initialReviewFileSha256']),ensure_ascii=False))"], { input: raw, encoding: "utf8" }));
  expect(converted.poolSha256).toBe(bundle.blank.poolSha256); expect(converted.pool).toEqual(bundle.blank.pool);
  expect(converted.labels.every(l => l.expectedOptionId === "other")).toBe(true);
  await expect(page.getByLabel("Почему вы выбрали этот вариант?")).toBeDisabled();
  await page.getByRole("button", { name: "Вернуться к правке" }).click(); await page.getByRole("button", { name: "Продолжить правку" }).click();
  await expect(page.getByLabel("Почему вы выбрали этот вариант?")).toBeEnabled();
});

test("legacy draft, invalid import and independent participant storage", async ({ page }) => {
  await page.goto(url);
  const legacy = structuredClone(bundle.blank); legacy.reviewerId = "user-development-review";
  Object.assign(legacy.labels[0], { expectedOptionId: "access", rationale: "Синтетическое обоснование изменения прав." });
  await page.locator("#file").setInputFiles({ name: "review.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(legacy)) });
  await expect(page.locator("#position")).toHaveText("Вопрос 2 из 3"); await expect(page.locator("#count")).toHaveText("Отвечено 1 из 3");
  const bad = structuredClone(legacy); bad.pool.cases[0].request.state += " changed";
  await page.locator("#file").setInputFiles({ name: "bad.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(bad)) });
  await expect(page.locator("#notice")).toContainText("Файл не загружен"); await expect(page.locator("#count")).toHaveText("Отвечено 1 из 3");
  await page.getByLabel("Участник", { exact: true }).selectOption("expert"); await page.getByRole("button", { name: "Сменить участника", exact: true }).click();
  await expect(page.locator("#count")).toHaveText("Отвечено 0 из 3");
  await page.getByLabel("Участник", { exact: true }).selectOption("first"); await page.getByRole("button", { name: "Сменить участника", exact: true }).click();
  await expect(page.locator("#count")).toHaveText("Отвечено 1 из 3");
});

test("storage failure retains usable download, and clear requires confirmation", async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(globalThis, "localStorage", { get() { throw new DOMException("Unavailable", "SecurityError"); } }));
  await page.goto(url); await answer(page, 1);
  await expect(page.locator("#storage")).toContainText("Хранилище браузера недоступно");
  await page.getByRole("button", { name: "Очистить ответ", exact: true }).click(); await page.getByRole("button", { name: "Вернуться", exact: true }).click();
  await expect(page.locator("#count")).toHaveText("Отвечено 1 из 3");
  const pending = page.waitForEvent("download"); await page.getByRole("button", { name: "Скачать черновик", exact: true }).click();
  const downloaded = await pending; expect(JSON.parse(await readFile(await downloaded.path(), "utf8")).answers[0].rationale).toContain("Синтетическое");
  await page.getByRole("button", { name: "Очистить ответ", exact: true }).click(); await page.getByRole("button", { name: "Очистить", exact: true }).click();
  await expect(page.locator("#count")).toHaveText("Отвечено 0 из 3"); await expect(page.getByLabel("Почему вы выбрали этот вариант?")).toHaveValue("");
});

test("49-question navigation and long-source expansion preserve a blank survey", async ({ page }, info) => {
  const fixture = await startFixture(49);
  try {
    await page.goto(fixture.url);
    await expect(page.locator("#progress")).toHaveAttribute("max", "49");
    const expand = page.getByRole("button", { name: "Развернуть весь текст", exact: true });
    await expect(expand).toBeVisible(); await expand.click();
    await expect(page.locator("#expand-source")).toHaveAttribute("aria-expanded", "true");
    expect(await page.locator("#source-body").evaluate(el => el.clientHeight === el.scrollHeight)).toBe(true);
    await page.getByRole("button", { name: "Свернуть текст", exact: true }).click();
    await expect(page.locator("#expand-source")).toHaveAttribute("aria-expanded", "false");
    const navigation = page.getByRole("navigation", { name: "Выберите вопрос" });
    if (!(await navigation.isVisible())) await page.getByText("Номера вопросов", { exact: true }).click();
    await expect(navigation.getByRole("button")).toHaveCount(49);
    await page.getByRole("button", { name: "Вопрос 49: без ответа", exact: true }).click();
    await expect(page.locator("#position")).toHaveText("Вопрос 49 из 49");
    await expect(page.locator('input[type="radio"]:checked')).toHaveCount(0);
    await expect(page.locator("#count")).toHaveText("Отвечено 0 из 49");
    await page.getByRole("button", { name: "Вопрос 2: без ответа", exact: true }).click();
    if (info.project.name.includes("mobile")) await page.getByText("Номера вопросов", { exact: true }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (process.env.AGAT_REVIEW_FORM_SCREENSHOTS) {
      await mkdir(process.env.AGAT_REVIEW_FORM_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: path.join(process.env.AGAT_REVIEW_FORM_SCREENSHOTS, `${info.project.name}-49.png`), fullPage: true });
    }
  } finally { await stopFixture(fixture.child); }
});
