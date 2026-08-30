import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";


const frontendDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryDirectory = path.resolve(frontendDirectory, "..", "..");
const examplePython = process.env.PYTINCTURE_EXAMPLE_PYTHON
    || path.join(repositoryDirectory, ".venv", "bin", "python");

export default defineConfig({
    testDir: "./example-tests",
    outputDir: "test-results-examples",
    timeout: 180_000,
    expect: { timeout: 120_000 },
    fullyParallel: false,
    workers: 1,
    retries: process.env.CI ? 1 : 0,
    reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
    use: {
        screenshot: "only-on-failure",
        trace: "retain-on-failure",
        video: "retain-on-failure",
    },
    webServer: [
        {
            command: `"${examplePython}" -m uvicorn service:app --app-dir ../../examples/quickstart/service --host 127.0.0.1 --port 8083 2>&1 | tee ../../tests/quickstart-service.log`,
            cwd: frontendDirectory,
            port: 8083,
            reuseExistingServer: !process.env.CI,
            timeout: 30_000,
        },
        {
            command: `"${examplePython}" -m http.server 8084 --bind 127.0.0.1 --directory ../../examples/quickstart/standalone 2>&1 | tee ../../tests/quickstart-standalone.log`,
            cwd: frontendDirectory,
            port: 8084,
            reuseExistingServer: !process.env.CI,
            timeout: 30_000,
        },
    ],
    projects: [
        { name: "chromium", use: { ...devices["Desktop Chrome"] } },
        { name: "firefox", use: { ...devices["Desktop Firefox"] } },
        { name: "webkit", use: { ...devices["Desktop Safari"] } },
    ],
});
