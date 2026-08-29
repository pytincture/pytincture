import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";


const frontendDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryDirectory = path.resolve(frontendDirectory, "..", "..");
const e2ePython = process.env.PYTINCTURE_E2E_PYTHON
    || path.join(repositoryDirectory, ".venv", "bin", "python");

export default defineConfig({
    testDir: "./e2e-tests",
    testIgnore: ["standalone-wheel.spec.mjs", "saml-federated.spec.mjs"],
    timeout: 180_000,
    expect: { timeout: 120_000 },
    fullyParallel: false,
    workers: 1,
    retries: process.env.CI ? 1 : 0,
    reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
    use: {
        baseURL: "http://127.0.0.1:8079",
        screenshot: "only-on-failure",
        trace: "retain-on-failure",
        video: "retain-on-failure",
    },
    webServer: {
        command: `"${e2ePython}" ../../tests/e2e_server.py 2>&1 | tee ../../tests/e2e-server.log`,
        cwd: frontendDirectory,
        port: 8079,
        reuseExistingServer: !process.env.CI,
        timeout: 30_000,
    },
    projects: [
        { name: "chromium", use: { ...devices["Desktop Chrome"] } },
        { name: "firefox", use: { ...devices["Desktop Firefox"] } },
        { name: "webkit", use: { ...devices["Desktop Safari"] } },
    ],
});
