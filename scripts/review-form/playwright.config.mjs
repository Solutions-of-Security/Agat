import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "../../tests/browser", testMatch: "review-form.spec.mjs", timeout: 30000, workers: 1, retries: 0,
  reporter: "list", use: { screenshot: "only-on-failure", trace: "retain-on-failure" },
  projects: [
    { name: "form-desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1100 } } },
    { name: "form-mobile", use: { ...devices["Pixel 7"] } },
  ],
});
