import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const directory = path.dirname(fileURLToPath(import.meta.url));
const python = process.env.PYTINCTURE_E2E_PYTHON || path.resolve(directory, '../../.venv/bin/python');
export default defineConfig({
    testDir: './e2e-tests', testMatch: 'live-microapps.spec.mjs', timeout: 120000,
    expect: {timeout: 30000}, workers: 1, retries: 0, reporter: 'line',
    use: {baseURL: 'http://127.0.0.1:41839', trace: 'retain-on-failure'},
    webServer: {command: `"${python}" ../../tests/live_microapp_server.py`, cwd: directory,
        port: 41839, reuseExistingServer: false, timeout: 30000},
    projects: [{name: 'chromium', use: {...devices['Desktop Chrome']}}],
});
