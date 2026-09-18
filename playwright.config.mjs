import { defineConfig, devices } from "@playwright/test";

const port = process.env.AGAT_SMOKE_PORT || "8796";
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./tests/browser",
  timeout: 45_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL, trace: "retain-on-failure", screenshot: "only-on-failure", video: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: "node scripts/smoke-server.mjs",
    url: `${baseURL}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 40_000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
  },
});
