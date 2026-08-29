import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";


const frontendDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryDirectory = path.resolve(frontendDirectory, "..", "..");
const standalonePython = process.env.PYTINCTURE_STANDALONE_PYTHON;

if (!standalonePython) {
    throw new Error("PYTINCTURE_STANDALONE_PYTHON must identify the wheel-only Python environment");
}

export default defineConfig({
    testDir: "./e2e-tests",
    testMatch: "standalone-wheel.spec.mjs",
    outputDir: "test-results-standalone",
    timeout: 180_000,
    expect: { timeout: 120_000 },
    fullyParallel: false,
    workers: 1,
    retries: process.env.CI ? 1 : 0,
    reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
    use: {
        baseURL: "http://127.0.0.1:8082",
        screenshot: "only-on-failure",
        trace: "retain-on-failure",
        video: "retain-on-failure",
    },
    webServer: {
        command: `"${standalonePython}" -I ../../tests/standalone_server.py 2>&1 | tee ../../tests/standalone-server.log`,
        cwd: frontendDirectory,
        port: 8082,
        reuseExistingServer: !process.env.CI,
        timeout: 30_000,
    },
    projects: [
        { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    ],
});
