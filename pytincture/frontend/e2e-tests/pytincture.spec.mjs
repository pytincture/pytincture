import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";


const WIDGET_WHEEL = "dhxpyt-0.9.17+backend-py3-none-any.whl";
const PERFORMANCE_BUDGETS = JSON.parse(readFileSync(
    new URL("../../../contracts/performance-budgets-v1.json", import.meta.url),
));

function percentile(values, percent) {
    const ordered = [...values].sort((left, right) => left - right);
    return ordered[Math.max(0, Math.ceil(percent * ordered.length) - 1)];
}

function collectDiagnostics(page) {
    const consoleEntries = [];
    const requests = [];
    page.on("console", message => {
        consoleEntries.push({ type: message.type(), text: message.text() });
    });
    page.on("request", request => {
        requests.push({ method: request.method(), resourceType: request.resourceType(), url: request.url() });
    });
    page.on("requestfailed", request => {
        requests.push({ method: request.method(), failure: request.failure(), url: request.url() });
    });
    return { consoleEntries, requests };
}

async function attachFailureDiagnostics(testInfo, diagnostics) {
    await testInfo.attach("console.json", {
        body: Buffer.from(JSON.stringify(diagnostics.consoleEntries, null, 2)),
        contentType: "application/json",
    });
    await testInfo.attach("network.json", {
        body: Buffer.from(JSON.stringify(diagnostics.requests, null, 2)),
        contentType: "application/json",
    });
}

async function blockExternalWidgetIndex(page) {
    await page.route("**/*", async route => {
        const url = new URL(route.request().url());
        if (url.hostname !== "127.0.0.1" && url.href.toLowerCase().includes("dhxpyt")) {
            const isJsonIndex = url.pathname.endsWith("/json");
            await route.fulfill({
                status: 200,
                headers: {
                    "Access-Control-Allow-Origin": "http://127.0.0.1:8079",
                    "Content-Type": isJsonIndex ? "application/json" : "text/html",
                },
                body: isJsonIndex
                    ? JSON.stringify({ info: { version: "0.0.0" }, releases: {} })
                    : "<!doctype html><html><body></body></html>",
            });
            return;
        }
        await route.continue();
    });
}

async function loginAndStartPackagedApp(page) {
    await page.addInitScript(() => {
        window.__pytinctureTestBffErrors = true;
        window.__pytinctureLifecycle = [];
        window.addEventListener("pytincture:lifecycle", event => {
            window.__pytinctureLifecycle.push(event.detail);
        });
    });
    await blockExternalWidgetIndex(page);
    await page.goto("/e2e_app");
    await expect(page).toHaveURL(/\/e2e_app\/login$/);
    await expect(page.getByText("E2E credentials: e2e@example.com / demo-password")).toBeVisible();
    await page.getByPlaceholder("Email").fill("e2e@example.com");
    await page.getByPlaceholder("Password").fill("demo-password");
    await Promise.all([
        page.waitForURL(/\/e2e_app$/),
        page.getByRole("button", { name: "Login with Email" }).click(),
    ]);
    await expect(page.locator("#e2e-ready")).toBeVisible();
}

async function callAuthenticatedBff(page) {
    return page.evaluate(async () => {
        const csrfToken = document.cookie
            .split(";")
            .map(value => value.trim().split("="))
            .find(([name]) => name === "pytincture_csrf")
            ?.slice(1).join("=") || "";
        const invoke = async (method, kwargs) => {
            const response = await fetch(`/e2e_app/classcall/e2e_data.py/E2EData/${method}`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrfToken,
                },
                body: JSON.stringify({ args: [], kwargs }),
            });
            return { status: response.status, body: await response.text() };
        };
        return {
            sync: await invoke("sync_call", { value: 11 }),
            asyncResult: await invoke("async_call", { value: 22 }),
            stream: await invoke("stream_call", { count: 3 }),
        };
    });
}

async function measureAuthenticatedBff(page, sampleCount) {
    return page.evaluate(async count => {
        const csrfToken = document.cookie
            .split(";")
            .map(value => value.trim().split("="))
            .find(([name]) => name === "pytincture_csrf")
            ?.slice(1).join("=") || "";
        const measurements = [];
        for (let index = 0; index < count; index += 1) {
            const startedAt = performance.now();
            const response = await fetch("/e2e_app/classcall/e2e_data.py/E2EData/sync_call", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrfToken,
                },
                body: JSON.stringify({ args: [], kwargs: { value: index } }),
            });
            if (!response.ok) {
                throw new Error(`BFF performance sample failed with HTTP ${response.status}`);
            }
            await response.text();
            measurements.push(performance.now() - startedAt);
        }
        return measurements;
    }, sampleCount);
}

test("BFF documentation uses packaged hash-locked assets", async ({ page }) => {
    const diagnostics = collectDiagnostics(page);
    const response = await page.goto("/bff-docs");
    expect(response.ok()).toBe(true);
    await expect(page).toHaveURL("/bff-docs");
    await expect(page.locator(".swagger-ui .info .title")).toContainText("pyTincture API");

    const documentationRequests = diagnostics.requests.filter(entry => {
        const url = new URL(entry.url);
        return url.pathname.includes("swagger-ui")
            || url.pathname.endsWith("/bff-docs.js")
            || url.pathname.endsWith("/bff-docs/openapi.json");
    });
    expect(documentationRequests.length).toBe(4);
    for (const entry of documentationRequests) {
        const url = new URL(entry.url);
        expect(url.origin).toBe("http://127.0.0.1:8079");
        expect(url.searchParams.get("uuid")).toMatch(/^[a-f0-9]{32}$/);
    }
    expect(diagnostics.requests.some(entry => entry.url.includes("cdn.jsdelivr.net"))).toBe(false);
    expect(diagnostics.consoleEntries.filter(entry => entry.type === "error")).toEqual([]);
});

test("authenticated packaged and inline apps run through real Pyodide", async ({ page, request }, testInfo) => {
    const diagnostics = collectDiagnostics(page);
    const performanceEvidence = { browser: testInfo.project.name };
    try {
        const coldStartedAt = Date.now();
        await loginAndStartPackagedApp(page);
        performanceEvidence.cold_authenticated_start_ms = Date.now() - coldStartedAt;
        expect(performanceEvidence.cold_authenticated_start_ms).toBeLessThanOrEqual(
            PERFORMANCE_BUDGETS.browser.cold_authenticated_start_ms,
        );

        await expect(page).toHaveURL("/e2e_app");
        expect(new URL(page.url()).search).toBe("");
        await expect(page.locator("#static-import")).toHaveText("static-import-ok");
        await expect(page.locator("#dynamic-import")).toHaveText("dynamic-browser-file-ok");
        expect(await page.locator("#e2e-ready").evaluate(element => getComputedStyle(element).color)).toBe("rgb(12, 110, 72)");
        await expect(page.locator("#bff-error-contract")).not.toHaveText("");
        const proxyErrors = JSON.parse(await page.locator("#bff-error-contract").textContent());
        const expectedOperations = {
            async: "E2EData.async_call",
            stream: "E2EData.stream_call",
            sync: "E2EData.sync_call",
        };
        expect(Object.keys(proxyErrors).sort()).toEqual(["async", "stream", "sync"]);
        for (const [style, error] of Object.entries(proxyErrors)) {
            expect(error.type, style).toBe("PytinctureBFFError");
            expect(error.status_code, style).toBe(400);
            expect(error.operation, style).toBe(expectedOperations[style]);
            expect(error.correlation_id, style).toMatch(/^[A-Za-z0-9._:-]{1,128}$/);
            expect(error.message, style).not.toContain("missing required");
        }

        const lifecycle = await page.evaluate(() => window.__pytinctureLifecycle);
        const compatibility = lifecycle.find(event => event.type === "compatibility")?.compatibility;
        const health = await (await request.get("/healthz")).json();
        expect(compatibility.runtimeVersion).toBe(health.version);
        expect(compatibility.pyodideVersion).toBeTruthy();
        expect(compatibility.pythonVersion).toMatch(/^3\.13\./);
        expect(compatibility.widgetPackage).toBe("dhxpyt");
        expect(compatibility.widgetVersion).toBe("0.9.17");
        expect(compatibility.dhxAvailable).toBe(true);
        expect(compatibility.javascriptAssets).toBe(9);
        expect(compatibility.cssAssets).toBe(4);
        expect(compatibility.assetManifest).toContain("dhxpyt@0.9.17");
        expect(lifecycle.at(-1).type).toBe("ready");
        expect(await page.locator("style").evaluateAll(styles => (
            styles.some(style => style.textContent.includes("data:font/woff2;base64,"))
        ))).toBe(true);

        const warmStartedAt = Date.now();
        await page.goto("/e2e_app");
        await expect(page.locator("#e2e-ready")).toBeVisible();
        performanceEvidence.warm_authenticated_start_ms = Date.now() - warmStartedAt;
        expect(performanceEvidence.warm_authenticated_start_ms).toBeLessThanOrEqual(
            PERFORMANCE_BUDGETS.browser.warm_authenticated_start_ms,
        );

        const widgetRequest = diagnostics.requests.find(entry => new URL(entry.url).pathname.endsWith(WIDGET_WHEEL));
        expect(widgetRequest).toBeTruthy();
        expect(new URL(widgetRequest.url).hostname).toBe("127.0.0.1");
        expect(new URL(widgetRequest.url).searchParams.get("uuid")).toBeTruthy();
        const wheelHead = await request.head(`/e2e_app/appcode/${WIDGET_WHEEL}`);
        expect(wheelHead.ok()).toBe(true);
        expect(wheelHead.headers()["x-pytincture-sha256"]).toMatch(/^[a-f0-9]{64}$/);
        const wheelGet = await request.get(`/e2e_app/appcode/${WIDGET_WHEEL}`);
        expect(wheelGet.headers()["x-pytincture-sha256"]).toMatch(/^[a-f0-9]{64}$/);
        const wheelConditional = await request.get(`/e2e_app/appcode/${WIDGET_WHEEL}`, {
            headers: { "If-None-Match": wheelHead.headers().etag },
        });
        expect(wheelConditional.status()).toBe(304);

        const localFrontendRequests = diagnostics.requests.filter(entry => {
            const url = new URL(entry.url);
            return entry.method === "GET"
                && url.hostname === "127.0.0.1"
                && (url.pathname.includes("/frontend/") || url.pathname.includes("/appcode/"));
        });
        expect(localFrontendRequests.length).toBeGreaterThan(5);
        for (const request of localFrontendRequests) {
            const url = new URL(request.url);
            const isPyodideInternalRequest = url.pathname.includes("/frontend/pyodide/");
            expect(
                Boolean(url.searchParams.get("uuid")) || isPyodideInternalRequest,
                request.url,
            ).toBe(true);
        }

        const bff = await callAuthenticatedBff(page);
        expect(bff.sync.status).toBe(200);
        expect(JSON.parse(bff.sync.body)).toEqual({ kind: "sync", value: 11, email: "e2e@example.com" });
        expect(bff.asyncResult.status).toBe(200);
        expect(JSON.parse(bff.asyncResult.body)).toEqual({ kind: "async", value: 22, email: "e2e@example.com" });
        expect(bff.stream.status).toBe(200);
        expect(bff.stream.body.trim().split("\n").map(line => JSON.parse(line))).toEqual([
            { kind: "stream", value: 0 },
            { kind: "stream", value: 1 },
            { kind: "stream", value: 2 },
        ]);
        const bffRequests = diagnostics.requests.filter(entry => (
            new URL(entry.url).pathname.includes("/classcall/")
        ));
        expect(bffRequests.length).toBeGreaterThan(0);
        expect(bffRequests.every(entry => !new URL(entry.url).searchParams.has("uuid"))).toBe(true);

        const bffMeasurements = await measureAuthenticatedBff(
            page,
            PERFORMANCE_BUDGETS.browser.bff_samples,
        );
        performanceEvidence.authenticated_bff_samples_ms = bffMeasurements.map(value => (
            Math.round(value * 1000) / 1000
        ));
        performanceEvidence.authenticated_bff_p95_ms = Math.round(
            percentile(bffMeasurements, 0.95) * 1000,
        ) / 1000;
        expect(performanceEvidence.authenticated_bff_p95_ms).toBeLessThanOrEqual(
            PERFORMANCE_BUDGETS.browser.authenticated_bff_p95_ms,
        );

        const worker = await page.evaluate(async () => {
            const registration = await navigator.serviceWorker.getRegistration(
                "/e2e_app/",
            );
            return { scope: registration.scope, scriptURL: registration.active?.scriptURL || "" };
        });
        expect(worker.scope).toBe("http://127.0.0.1:8079/e2e_app/");
        expect(new URL(worker.scriptURL).pathname).toBe("/e2e_app/frontend/sw.js");
        expect(new URL(worker.scriptURL).searchParams.get("uuid")).toBeTruthy();
        const readCacheEvidence = () => page.evaluate(async () => {
            const names = await caches.keys();
            const ownedNames = names.filter(name => name.startsWith("pytincture:e2e_app:"));
            const requests = (await Promise.all(ownedNames.map(async name => (
                (await caches.open(name)).keys()
            )))).flat();
            return { names, urls: requests.map(request => request.url) };
        });
        if (testInfo.project.name !== "webkit") {
            await expect.poll(
                async () => (await readCacheEvidence()).urls.length,
                { timeout: 60000 },
            ).toBeGreaterThan(5);
        }
        const cacheEvidence = await readCacheEvidence();
        expect(cacheEvidence.names.some(name => name.startsWith("pytincture:e2e_app:"))).toBe(true);
        expect(cacheEvidence.urls.every(url => new URL(url).searchParams.get("uuid"))).toBe(true);

        const signedUrl = "https://api.example.test/report?X-Amz-Signature=abc123&part=1";
        let observedSignedUrl = "";
        await page.route("https://api.example.test/**", async route => {
            observedSignedUrl = route.request().url();
            await route.fulfill({
                status: 200,
                headers: {
                    "Access-Control-Allow-Origin": "http://127.0.0.1:8079",
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ ok: true }),
            });
        });
        expect(await page.evaluate(async url => (await fetch(url)).json(), signedUrl)).toEqual({ ok: true });
        expect(observedSignedUrl).toBe(signedUrl);
        await page.unroute("https://api.example.test/**");

        const upgradedCaches = await page.evaluate(async () => {
            await caches.open("foreign-library-cache");
            await caches.open("pytincture:other_app:current:other");
            await caches.open("pytincture:e2e_app:obsolete:old");
            const config = window.__pytinctureTesting.normalizeConfig({
                application: "e2e_app",
                requestUuid: "upgrade-test",
                enableServiceWorker: true,
                warmPyodideCache: false,
            });
            await window.__pytinctureTesting.DEFAULT_RUNTIME_OPERATIONS.ensureServiceWorker(config);
            return caches.keys();
        });
        expect(upgradedCaches).toContain("foreign-library-cache");
        expect(upgradedCaches).toContain("pytincture:other_app:current:other");
        expect(upgradedCaches).not.toContain("pytincture:e2e_app:obsolete:old");

        const unregisterEvidence = await page.evaluate(async () => {
            const removed = await window.__pytinctureTesting.unregisterOwnedServiceWorker({
                application: "e2e_app",
                serviceWorkerScope: "/e2e_app/",
                serviceWorkerUrl: "/e2e_app/frontend/sw.js",
            });
            return { removed, names: await caches.keys() };
        });
        expect(unregisterEvidence.removed).toBe(true);
        expect(unregisterEvidence.names.some(name => name.startsWith("pytincture:e2e_app:"))).toBe(false);
        expect(unregisterEvidence.names).toContain("foreign-library-cache");
        expect(unregisterEvidence.names).toContain("pytincture:other_app:current:other");

        await page.unrouteAll({ behavior: "wait" });
        await page.goto("/e2e_app/appcode/inline-e2e.html");
        await expect(page.locator("#inline-ready")).toBeVisible();
        expect(new URL(page.url()).search).toBe("");
        const inlineLifecycle = await page.evaluate(() => window.__pytinctureLifecycle);
        expect(inlineLifecycle.at(-1).type).toBe("ready");
        performanceEvidence.status = "passed";
    } finally {
        performanceEvidence.status ||= "failed";
        const performancePath = testInfo.outputPath("performance.json");
        const renderedPerformance = `${JSON.stringify(performanceEvidence, null, 2)}\n`;
        writeFileSync(performancePath, renderedPerformance);
        if (process.env.PYTINCTURE_ACCEPTANCE_RESULT) {
            writeFileSync(process.env.PYTINCTURE_ACCEPTANCE_RESULT, renderedPerformance);
        }
        await testInfo.attach("performance.json", {
            path: performancePath,
            contentType: "application/json",
        });
        await attachFailureDiagnostics(testInfo, diagnostics);
    }
});

test("widget manifests reject corrupt and non-owned executable assets", async ({ browserName, page }) => {
    test.skip(browserName !== "chromium", "Manifest enforcement is deterministic Python logic and is exercised once.");
    let manifest = null;
    await blockExternalWidgetIndex(page);
    await page.addInitScript(() => {
        window.__pytinctureLifecycle = [];
        window.addEventListener("pytincture:lifecycle", event => {
            window.__pytinctureLifecycle.push(event.detail);
        });
    });
    await page.route("**/e2e_app/appcode/inline-e2e.html", async route => {
        const response = await route.fetch();
        const original = await response.text();
        const body = original.replace(
            'loadingTitle: "Inline E2E"',
            `application: "e2e_app",\n      widgetAssetManifest: ${JSON.stringify(manifest)},\n      loadingTitle: "Inline E2E"`,
        );
        await route.fulfill({ response, body, headers: { ...response.headers(), "content-type": "text/html" } });
    });

    const expectManifestFailure = async (asset, expectedMessage) => {
        manifest = {
            schema: 1,
            package: "dhxpyt",
            version: "0.9.17",
            assets: [asset],
        };
        await page.goto("/e2e_app/appcode/inline-e2e.html");
        await expect(page.locator(".pytincture-loading__status")).toContainText(
            "Failed during widgetset-load",
        );
        const lifecycle = await page.evaluate(() => window.__pytinctureLifecycle);
        expect(lifecycle.find(event => event.type === "error")?.error.rootCause).toContain(expectedMessage);
        await expect(page.locator("#inline-ready")).toHaveCount(0);
    };

    await expectManifestFailure(
        {
            path: "dhxpyt/dhxsrc/suite.js",
            type: "javascript",
            sha256: "0".repeat(64),
        },
        "integrity check failed",
    );
    await expectManifestFailure(
        {
            path: "micropip/undeclared.js",
            type: "javascript",
            sha256: "0".repeat(64),
        },
        "is not owned by dhxpyt",
    );
});

test("direct SVG navigation cannot execute application-origin script", async ({ browserName, page }) => {
    test.skip(browserName !== "chromium", "Response policy is browser-independent and is exercised once.");

    const response = await page.goto("/e2e_app/appcode/active.svg");

    expect(response.status()).toBe(200);
    expect(response.headers()["content-security-policy"]).toContain(
        "sandbox; default-src 'none'",
    );
    expect(await page.evaluate(() => globalThis.__pytinctureSvgScriptRan === true)).toBe(false);
});

test("packaged entrypoint failure is rendered without fallback", async ({ browserName, page }, testInfo) => {
    test.skip(browserName !== "chromium", "The lifecycle failure stages are cross-browser unit tested separately.");
    const diagnostics = collectDiagnostics(page);
    try {
        await page.addInitScript(() => {
            window.__pytinctureLifecycle = [];
            window.addEventListener("pytincture:lifecycle", event => window.__pytinctureLifecycle.push(event.detail));
        });
        await blockExternalWidgetIndex(page);
        await page.goto("/failure_app/login");
        await page.getByPlaceholder("Email").fill("e2e@example.com");
        await page.getByPlaceholder("Password").fill("demo-password");
        await Promise.all([
            page.waitForURL(/\/failure_app$/),
            page.getByRole("button", { name: "Login with Email" }).click(),
        ]);
        await expect(page.locator(".pytincture-loading__status")).toContainText("Failed during entrypoint-execution");
        const lifecycle = await page.evaluate(() => window.__pytinctureLifecycle);
        expect(lifecycle.some(event => event.type === "fallback")).toBe(false);
        expect(lifecycle.some(event => event.type === "ready")).toBe(false);
        const error = lifecycle.find(event => event.type === "error" && event.stage === "entrypoint-execution");
        expect(error.error.rootCause).toContain("intentional e2e entrypoint failure");
    } finally {
        await attachFailureDiagnostics(testInfo, diagnostics);
    }
});
