import { defineConfig, devices } from "@playwright/test";

const backendUrl = process.env.DATABASE_URL;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3001",
    ...devices["Desktop Chrome"],
    ...(process.env.CI ? {} : { channel: "chrome" as const }),
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `${process.platform === "win32" ? ".venv\\Scripts\\python.exe" : "python"} -m uvicorn app.main:app --host 127.0.0.1 --port 8001`,
      cwd: "../backend",
      url: "http://127.0.0.1:8001/health",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        APP_ENV: "development",
        FRONTEND_ORIGIN: "http://127.0.0.1:3001",
        DATABASE_URL: process.env.CI && backendUrl ? backendUrl : "sqlite:///./dealguard-e2e.db",
      },
    },
    {
      command: "npm run dev -- --hostname 127.0.0.1 --port 3001",
      url: "http://127.0.0.1:3001",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { NEXT_PUBLIC_API_BASE_URL: "http://127.0.0.1:8001" },
    },
  ],
});
