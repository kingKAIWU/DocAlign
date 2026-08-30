import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://127.0.0.1:18080", trace: "on-first-retry" },
  webServer: {
    command: "uv run python -m scripts.run_e2e_server --port 18080",
    cwd: "../..",
    url: "http://127.0.0.1:18080/api/v1/health",
    reuseExistingServer: false,
  },
});
