import { expect, test } from "@playwright/test";


const WIDGET_WHEEL = "dhxpyt-0.9.16+backend-py3-none-any.whl";

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
            await route.abort("failed");
            return;
        }
        await route.continue();
    });
}

async function loginAndStartPackagedApp(page) {
    await page.addInitScript(() => {
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
            const response = await fetch(`/classcall/e2e_data.py/E2EData/${method}`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrfToken,
                },
                body: JSON.stringify({ kwargs }),
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

test("authenticated packaged and inline apps run through real Pyodide", async ({ page, request }, testInfo) => {
    const diagnostics = collectDiagnostics(page);
    try {
        await loginAndStartPackagedApp(page);

        await expect(page).toHaveURL("/e2e_app");
        expect(new URL(page.url()).search).toBe("");
        await expect(page.locator("#static-import")).toHaveText("static-import-ok");
        await expect(page.locator("#dynamic-import")).toHaveText("dynamic-browser-file-ok");
        expect(await page.locator("#e2e-ready").evaluate(element => getComputedStyle(element).color)).toBe("rgb(12, 110, 72)");

        const lifecycle = await page.evaluate(() => window.__pytinctureLifecycle);
        const compatibility = lifecycle.find(event => event.type === "compatibility")?.compatibility;
        const health = await (await request.get("/healthz")).json();
        expect(compatibility.runtimeVersion).toBe(health.version);
        expect(compatibility.pyodideVersion).toBeTruthy();
        expect(compatibility.pythonVersion).toMatch(/^3\.13\./);
        expect(compatibility.widgetPackage).toBe("dhxpyt");
        expect(compatibility.widgetVersion).toBe("0.9.16");
        expect(compatibility.dhxAvailable).toBe(true);
        expect(compatibility.javascriptAssets).toBeGreaterThan(0);
        expect(compatibility.cssAssets).toBeGreaterThan(0);
        expect(lifecycle.at(-1).type).toBe("ready");
        expect(await page.locator("style").evaluateAll(styles => (
            styles.some(style => style.textContent.includes("data:font/woff2;base64,"))
        ))).toBe(true);

        const widgetRequest = diagnostics.requests.find(entry => new URL(entry.url).pathname.endsWith(WIDGET_WHEEL));
        expect(widgetRequest).toBeTruthy();
        expect(new URL(widgetRequest.url).hostname).toBe("127.0.0.1");
        expect(new URL(widgetRequest.url).searchParams.get("uuid")).toBeTruthy();

        const localFrontendRequests = diagnostics.requests.filter(entry => {
            const url = new URL(entry.url);
            return entry.method === "GET"
                && url.hostname === "127.0.0.1"
                && (url.pathname.includes("/frontend/") || url.pathname.includes("/appcode/"));
        });
        expect(localFrontendRequests.length).toBeGreaterThan(5);
        for (const request of localFrontendRequests) {
            expect(new URL(request.url).searchParams.get("uuid"), request.url).toBeTruthy();
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

        const worker = await page.evaluate(async () => {
            const registration = await navigator.serviceWorker.ready;
            return { scope: registration.scope, scriptURL: registration.active?.scriptURL || "" };
        });
        expect(worker.scope).toBe("http://127.0.0.1:8079/");
        expect(new URL(worker.scriptURL).pathname).toBe("/frontend/sw.js");
        expect(new URL(worker.scriptURL).searchParams.get("uuid")).toBeTruthy();

        await page.unrouteAll({ behavior: "wait" });
        await page.goto("/e2e_app/appcode/inline-e2e.html");
        await expect(page.locator("#inline-ready")).toBeVisible();
        expect(new URL(page.url()).search).toBe("");
        const inlineLifecycle = await page.evaluate(() => window.__pytinctureLifecycle);
        expect(inlineLifecycle.at(-1).type).toBe("ready");
    } finally {
        await attachFailureDiagnostics(testInfo, diagnostics);
    }
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
        await page.goto("/e2e_app/login");
        await page.getByPlaceholder("Email").fill("e2e@example.com");
        await page.getByPlaceholder("Password").fill("demo-password");
        await Promise.all([
            page.waitForURL(/\/e2e_app$/),
            page.getByRole("button", { name: "Login with Email" }).click(),
        ]);
        await page.goto("/failure_app");
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
