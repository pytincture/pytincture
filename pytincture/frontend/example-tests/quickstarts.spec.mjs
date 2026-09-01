import { expect, test } from "@playwright/test";


const SERVICE_URL = "http://127.0.0.1:8083";
const STANDALONE_URL = "http://127.0.0.1:8084";
const BUNDLED_RUNTIME_PATH = "/frontend/dist/pytincture.min.js";

function collectDiagnostics(page) {
    const consoleErrors = [];
    const pageErrors = [];
    const failedRequests = [];
    const httpErrors = [];
    const requests = [];
    page.on("console", message => {
        if (message.type() === "error") {
            consoleErrors.push(message.text());
        }
    });
    page.on("pageerror", error => pageErrors.push(error.message));
    page.on("request", request => requests.push(request.url()));
    page.on("requestfailed", request => {
        failedRequests.push({ failure: request.failure(), url: request.url() });
    });
    page.on("response", response => {
        if (response.status() >= 400) {
            httpErrors.push({ status: response.status(), url: response.url() });
        }
    });
    return { consoleErrors, failedRequests, httpErrors, pageErrors, requests };
}

async function installLifecycleCollector(page) {
    await page.addInitScript(() => {
        window.__quickstartLifecycle = [];
        window.addEventListener("pytincture:lifecycle", event => {
            window.__quickstartLifecycle.push(event.detail);
        });
    });
}

async function expectCleanRun(page, diagnostics) {
    const lifecycle = await page.evaluate(() => window.__quickstartLifecycle);
    expect(lifecycle.some(event => event.type === "error")).toBe(false);
    expect(lifecycle.at(-1)?.type).toBe("ready");
    expect(diagnostics.consoleErrors).toEqual([]);
    expect(diagnostics.pageErrors).toEqual([]);
    expect(diagnostics.failedRequests).toEqual([]);
    expect(diagnostics.httpErrors).toEqual([]);
    return lifecycle;
}

test("service quickstart runs the packaged example", async ({ page, request }) => {
    const diagnostics = collectDiagnostics(page);
    await installLifecycleCollector(page);

    const response = await page.goto(`${SERVICE_URL}/`);
    expect(response?.ok()).toBe(true);
    await expect(page).toHaveURL(`${SERVICE_URL}/hello`);
    await expect(page.getByRole("heading", { name: "Hello from Pytincture" })).toBeVisible();
    await expect(page.getByText("Python is running in your browser.")).toBeVisible();
    expect(new URL(page.url()).search).toBe("");

    const health = await (await request.get(`${SERVICE_URL}/healthz`)).json();
    expect(health.status).toBe("ok");
    const lifecycle = await expectCleanRun(page, diagnostics);
    const compatibility = lifecycle.find(event => event.type === "compatibility")?.compatibility;
    expect(compatibility.runtimeVersion).toBe(health.version);
    expect(compatibility.widgetPackage).toBe("dhxpyt");
    expect(compatibility.widgetVersion).toBe("0.9.18");
    expect(diagnostics.requests.some(url => new URL(url).pathname === "/hello/appcode/appcode.pyt")).toBe(true);
    expect(diagnostics.requests.some(url => (
        new URL(url).pathname === "/hello/frontend/vendor/materialdesignicons/materialdesignicons.css"
    ))).toBe(true);
    expect(diagnostics.requests.some(url => /(?:cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com)/.test(url))).toBe(false);
});

test("standalone quickstart runs with its bundled runtime", async ({ page }) => {
    const diagnostics = collectDiagnostics(page);
    await installLifecycleCollector(page);

    const response = await page.goto(`${STANDALONE_URL}/`);
    expect(response?.ok()).toBe(true);
    await expect(page.getByRole("heading", { name: "Hello from standalone Pytincture" })).toBeVisible();
    expect(page.url()).toBe(`${STANDALONE_URL}/`);

    const lifecycle = await expectCleanRun(page, diagnostics);
    const compatibility = lifecycle.find(event => event.type === "compatibility")?.compatibility;
    expect(compatibility.widgetPackage).toBe("dhxpyt");
    expect(compatibility.widgetVersion).toBe("0.9.18");
    const runtimeRequest = diagnostics.requests.find(url => (
        new URL(url).pathname === BUNDLED_RUNTIME_PATH
    ));
    expect(runtimeRequest).toBeTruthy();
    expect(new URL(runtimeRequest).origin).toBe(STANDALONE_URL);
    const selfHostedAssets = diagnostics.requests.filter(url => (
        new URL(url).pathname.startsWith("/frontend/pyodide/")
        || new URL(url).pathname.startsWith("/frontend/vendor/materialdesignicons/")
        || new URL(url).pathname.startsWith("/frontend/dist/")
    ));
    expect(selfHostedAssets.length).toBeGreaterThan(5);
    expect(selfHostedAssets.every(url => new URL(url).origin === STANDALONE_URL)).toBe(true);
    expect(diagnostics.requests.some(url => /(?:cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com)/.test(url))).toBe(false);
});
