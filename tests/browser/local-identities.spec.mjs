import { test, expect } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { AgatStore } from "../../apps/coordinator/dist/database.js";
import { loadConfig } from "../../apps/coordinator/dist/config.js";
import { createCoordinatorServer } from "../../apps/coordinator/dist/server.js";
import { LocalIdentityProvider } from "../../apps/coordinator/dist/local-identities.js";
test.use({ actionTimeout: 15000 });

test("local identity login, access management, restart and logout on desktop/mobile", async ({ page, context }, testInfo) => {
  test.setTimeout(90000);
  const directory = await mkdtemp(path.join(os.tmpdir(), "agat-local-browser-"));
  const port = Number(process.env.AGAT_LOCAL_SMOKE_PORT || "8797");
  const origin = `http://127.0.0.1:${port}`;
  const dbPath = path.join(directory, "authority.db");
  let store; let server; let owner; let shutdown;
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.type() === "error" && !/server responded with a status of (401|403)/.test(message.text())) errors.push(message.text()); });
  async function start() {
    store = new AgatStore(dbPath, { seedDemo: false, credentialsKey: "synthetic-browser-fixture-only", artifactsDir: path.join(directory, "artifacts") });
    if (!owner) {
      store.createProject({ id: "alpha", name: "Alpha" }); store.createProject({ id: "beta", name: "Beta" });
      owner = new LocalIdentityProvider(store.db, origin).bootstrap("local-owner");
    }
    shutdown = new AbortController();
    server = createCoordinatorServer({ ...loadConfig(), host: "127.0.0.1", port, authMode: "local", deploymentProfile: "lite", localAuthOrigin: origin,
      serveWeb: true, webDistPath: fileURLToPath(new URL("../../apps/web/dist", import.meta.url)), adminToken: "", oidcEnabled: false, temporalEnabled: false,
      mcpEnabled: false, a2aEnabled: false, sandboxEnabled: false, localWorkerLauncherEnabled: false, allowedOrigins: [origin] }, store, undefined, undefined, undefined, undefined, undefined, shutdown.signal);
    await new Promise((resolve, reject) => { server.once("error", reject); server.listen(port, "127.0.0.1", resolve); });
  }
  async function stop() {
    shutdown?.abort();
    if (server?.listening) { await new Promise(resolve => server.close(resolve)); }
    store?.close(); store = null;
  }
  async function login(token) {
    await page.getByLabel("Персональный ключ", { exact: true }).fill(token);
    await page.getByRole("button", { name: "Войти", exact: true }).click();
    await expect(page.getByLabel("Активный проект")).toBeVisible();
  }
  try {
    await start(); await page.goto(origin);
    await expect(page.getByRole("heading", { name: "Вход в Агат" })).toBeVisible();
    await page.getByLabel("Персональный ключ", { exact: true }).fill("invalid-key"); await page.getByRole("button", { name: "Войти", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("недействительны"); await expect(page.getByLabel("Персональный ключ", { exact: true })).toHaveValue("");
    await login(owner.token);
    await page.reload(); await expect(page.getByLabel("Активный проект")).toBeVisible();
    const cookies = await context.cookies(origin); const cookie = cookies.find(item => item.name === "agat-local-session");
    expect(cookie?.httpOnly).toBe(true); expect(cookie?.sameSite).toBe("Strict");
    await test.step("restart the authority with an active authenticated SSE client", async () => { await stop(); await start(); });
    await page.reload(); await expect(page.getByLabel("Активный проект")).toBeVisible();
    await page.getByLabel("Открыть меню профиля").click(); await page.getByRole("button", { name: "Пользователи и доступ", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Пользователи и доступ" }); await expect(dialog).toBeVisible();
    const bootstrapToken = owner.token;
    await dialog.getByRole("button", { name: "Заменить ключ", exact: true }).click();
    const replacement = dialog.getByLabel("Новый индивидуальный ключ"); await expect(replacement).toBeVisible(); owner.token = await replacement.inputValue();
    await dialog.getByRole("button", { name: "Скрыть ключ", exact: true }).click();
    await page.reload(); await expect(page.getByLabel("Активный проект")).toBeVisible();
    await page.getByLabel("Открыть меню профиля").click(); await page.getByRole("button", { name: "Пользователи и доступ", exact: true }).click(); await expect(dialog).toBeVisible();
    await dialog.getByLabel("Имя субъекта", { exact: true }).fill("local-viewer");
    await dialog.getByRole("listbox", { name: "Проекты субъекта", exact: true }).selectOption(["alpha"]);
    await dialog.getByRole("button", { name: "Создать субъекта", exact: true }).click();
    await expect(dialog.getByLabel("Выбранный субъект")).toContainText("local-viewer");
    await dialog.getByLabel("Название ключа", { exact: true }).fill("Viewer login");
    await dialog.getByRole("button", { name: "Выдать ключ", exact: true }).click();
    const tokenField = dialog.getByLabel("Новый индивидуальный ключ"); await expect(tokenField).toBeVisible(); const viewerToken = await tokenField.inputValue();
    expect(viewerToken.startsWith("agat_personal_")).toBe(true);
    await dialog.getByRole("button", { name: "Скрыть ключ", exact: true }).click(); await expect(tokenField).toHaveCount(0);
    await dialog.evaluate(element => element.scrollTo({ top: 0 }));
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("local-access.png") });
    await testInfo.attach("local-access", { path: testInfo.outputPath("local-access.png"), contentType: "image/png" });
    await dialog.getByLabel("Закрыть управление доступом").click();
    if (!await page.getByRole("button", { name: "Выйти", exact: true }).isVisible()) await page.getByLabel("Открыть меню профиля").click();
    await page.getByRole("button", { name: "Выйти", exact: true }).click(); await expect(page.getByRole("heading", { name: "Вход в Агат" })).toBeVisible();
    await page.reload(); await expect(page.getByRole("heading", { name: "Вход в Агат" })).toBeVisible(); await login(viewerToken);
    await expect(page.getByLabel("Активный проект").locator("option")).toHaveText(["Alpha"]);
    await page.getByLabel("Открыть меню профиля").click(); await expect(page.getByRole("button", { name: "Пользователи и доступ", exact: true })).toHaveCount(0); await expect(page.getByText("local-viewer", { exact: true })).toBeVisible();
    const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
    expect(storage.includes(owner.token)).toBe(false); expect(storage.includes(bootstrapToken)).toBe(false); expect(storage.includes(viewerToken)).toBe(false);
    expect(await page.evaluate(() => document.cookie.includes("agat-local-session"))).toBe(false);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(errors).toEqual([]);
  } finally { await stop(); await rm(directory, { recursive: true, force: true }); }
});
