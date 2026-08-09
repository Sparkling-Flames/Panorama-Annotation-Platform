import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  retries: 0,
  // The local harness uses one shared SQLite database, which cannot safely serve concurrent writers.
  workers: 1,
  use: {
    baseURL: process.env.PANORAMA_E2E_FRONTEND_URL ?? "http://127.0.0.1:4173",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
