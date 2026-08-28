import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";


const frontendDirectory = path.dirname(fileURLToPath(import.meta.url));


export default defineConfig({
    testDir: "./browser-tests",
    timeout: 30_000,
    retries: process.env.CI ? 2 : 0,
    reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
    use: {
        baseURL: "http://127.0.0.1:41737",
        screenshot: "only-on-failure",
        trace: "retain-on-failure",
        video: "retain-on-failure",
    },
    webServer: {
        command: "python3 -m http.server 41737 --bind 127.0.0.1",
        cwd: frontendDirectory,
        port: 41737,
        reuseExistingServer: !process.env.CI,
    },
    projects: [
        { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    ],
});
