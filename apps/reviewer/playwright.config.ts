import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173",
    headless: true,
    // Reuse a preinstalled Chromium when present (CI / hosted runners); otherwise Playwright's own download.
    launchOptions: process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {},
  },
  webServer: process.env.E2E_NO_SERVER ? undefined : { command: "pnpm dev", url: "http://127.0.0.1:5173", reuseExistingServer: true, timeout: 60_000 },
});
