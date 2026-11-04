import { defineConfig, devices } from "@playwright/test";

const backendUrl = process.env.DATABASE_URL;
const frontendPort = process.env.PLAYWRIGHT_FRONTEND_PORT ?? "3001";
const backendPort = process.env.PLAYWRIGHT_BACKEND_PORT ?? "8001";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  reporter: "list",
  expect: { timeout: 15_000 },
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    ...devices["Desktop Chrome"],
    ...(process.env.CI && process.env.PLAYWRIGHT_CHANNEL !== "chrome" ? {} : { channel: "chrome" as const }),
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `${process.platform === "win32" ? ".venv\\Scripts\\python.exe" : "python"} -m uvicorn app.main:app --host 127.0.0.1 --port ${backendPort}`,
      cwd: "../backend",
      url: `http://127.0.0.1:${backendPort}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        APP_ENV: "development",
        FRONTEND_ORIGIN: `http://127.0.0.1:${frontendPort}`,
        AUTH_REGISTER_MAX_ATTEMPTS: "100",
        LLM_API_KEY: "",
        LLM_MODEL: "",
        LOCAL_EMBEDDING_MODEL_PATH: "",
        DATABASE_URL: process.env.CI && backendUrl ? backendUrl : "sqlite:///./dealguard-e2e.db",
      },
    },
    {
      command: `npm run dev -- --hostname 127.0.0.1 --port ${frontendPort}`,
      url: `http://127.0.0.1:${frontendPort}`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        NEXT_PUBLIC_API_BASE_URL: `http://127.0.0.1:${backendPort}`,
        DEALGUARD_NEXT_DIST_DIR: `.next-e2e-${frontendPort}`,
      },
    },
  ],
});
