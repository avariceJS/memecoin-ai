import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.pw.ts",
  testIgnore: process.env.CC_MATRIX
    ? []
    : ["**/shell-matrix.pw.ts", "**/overview-matrix.pw.ts", "**/calendar-matrix.pw.ts", "**/research-matrix.pw.ts", "**/models-matrix.pw.ts", "**/sources-matrix.pw.ts"],
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  outputDir: "../.local/tasks/CC02.0/playwright",
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:5173",
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 1,
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    headless: true,
    screenshot: "off",
    trace: "off",
    video: "off",
    serviceWorkers: "block",
  },
});
