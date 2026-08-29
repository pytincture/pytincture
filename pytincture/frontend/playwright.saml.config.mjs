import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";


const frontendDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryDirectory = path.resolve(frontendDirectory, "..", "..");
const samlPython = process.env.PYTINCTURE_SAML_PYTHON;

if (!samlPython) {
    throw new Error("PYTINCTURE_SAML_PYTHON must identify the wheel-only Python environment");
}

export default defineConfig({
    testDir: "./e2e-tests",
    testMatch: "saml-federated.spec.mjs",
    outputDir: "test-results-saml",
    timeout: 240_000,
    expect: { timeout: 120_000 },
    fullyParallel: false,
    workers: 1,
    retries: process.env.CI ? 1 : 0,
    reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
    use: {
        baseURL: "http://127.0.0.1:8084",
        screenshot: "only-on-failure",
        trace: "retain-on-failure",
        video: "retain-on-failure",
    },
    webServer: {
        command: `"${samlPython}" -I ../../tests/federated_saml_server.py 2>&1 | tee ../../tests/federated-saml-server.log`,
        cwd: frontendDirectory,
        port: 8084,
        reuseExistingServer: !process.env.CI,
        timeout: 90_000,
    },
    projects: [
        { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    ],
});
