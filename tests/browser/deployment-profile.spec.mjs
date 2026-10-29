import { test, expect } from "@playwright/test";

test("deployment profile exposes the server's actual capabilities on desktop and mobile", async ({ page, request }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  const response = await request.get("/api/v1/deployment");
  expect(response.ok()).toBe(true);
  const deployment = await response.json();
  await page.goto("/#overview");
  await expect(page).toHaveTitle(/АГАТ|Agat/i);
  const panel = page.getByLabel("Профиль и возможности").first();
  await expect(panel).toBeVisible();
  await panel.locator("summary").click();
  await expect(panel).toContainText(deployment.authentication.label);
  for (const capability of deployment.capabilities) {
    await expect(panel.getByText(`${capability.label}: ${capability.available ? "доступно" : "недоступно"}`, { exact: true })).toBeVisible();
    await expect(panel).toContainText(capability.message);
  }
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
  const screenshot = testInfo.outputPath("deployment-profile.png");
  await page.screenshot({ path: screenshot, fullPage: true });
  await testInfo.attach(`deployment-${testInfo.project.name}`, { path: screenshot, contentType: "image/png" });
  await panel.locator("summary").click();
  await expect(panel.locator("ul")).not.toBeVisible();
});
