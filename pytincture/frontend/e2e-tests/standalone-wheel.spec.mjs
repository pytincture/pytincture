import { expect, test } from "@playwright/test";
import { writeFileSync } from "node:fs";


const REAL_WIDGET_WHEEL = "dhxpyt-0.9.16+backend-py3-none-any.whl";
const DEV_WIDGET_WHEEL = "dhxpyt-99.99.99-py3-none-any.whl";

function collectDiagnostics(page) {
    const consoleEntries = [];
    const requests = [];
    const failures = [];
    const responses = [];
    page.on("console", message => {
        consoleEntries.push({ type: message.type(), text: message.text() });
    });
    page.on("request", request => {
        requests.push({ method: request.method(), resourceType: request.resourceType(), url: request.url() });
    });
    page.on("requestfailed", request => {
        failures.push({ method: request.method(), failure: request.failure(), url: request.url() });
    });
    page.on("response", response => {
        responses.push({ status: response.status(), url: response.url() });
    });
    return { consoleEntries, failures, requests, responses };
}

async function attachEvidence(testInfo, evidence, diagnostics) {
    const evidencePath = testInfo.outputPath("standalone-acceptance.json");
    const renderedEvidence = `${JSON.stringify(evidence, null, 2)}\n`;
    writeFileSync(evidencePath, renderedEvidence);
    if (process.env.PYTINCTURE_ACCEPTANCE_RESULT) {
        writeFileSync(process.env.PYTINCTURE_ACCEPTANCE_RESULT, renderedEvidence);
    }
    await testInfo.attach("standalone-acceptance.json", {
        path: evidencePath,
        contentType: "application/json",
    });
    await testInfo.attach("standalone-console.json", {
        body: Buffer.from(JSON.stringify(diagnostics.consoleEntries, null, 2)),
        contentType: "application/json",
    });
    await testInfo.attach("standalone-network.json", {
        body: Buffer.from(JSON.stringify({
            requests: diagnostics.requests,
            responses: diagnostics.responses,
            failures: diagnostics.failures,
        }, null, 2)),
        contentType: "application/json",
    });
}

test("standalone app runs from the Python wheel runtime", async ({ page, request }, testInfo) => {
    const diagnostics = collectDiagnostics(page);
    const startedAt = Date.now();
    const evidence = { browser: testInfo.project.name };
    try {
        await page.route("**/*", async route => {
            const url = new URL(route.request().url());
            if (url.hostname !== "127.0.0.1" && url.href.toLowerCase().includes("dhxpyt")) {
                const isJsonIndex = url.pathname.endsWith("/json");
                await route.fulfill({
                    status: 200,
                    contentType: isJsonIndex ? "application/json" : "text/html",
                    body: isJsonIndex
                        ? JSON.stringify({ info: { version: "0.0.0" }, releases: {} })
                        : "<!doctype html><html><body></body></html>",
                });
                return;
            }
            await route.continue();
        });

        await page.goto("/standalone");
        await expect(page.locator("#standalone-ready")).toBeVisible();
        await expect(page.locator("#widget-proof")).toHaveText("dhxpyt layout loaded");
        await expect(page.locator(".dhx_layout")).toBeVisible();

        expect(page.url()).toBe("http://127.0.0.1:8082/standalone");
        expect(new URL(page.url()).search).toBe("");

        const health = await (await request.get("/healthz")).json();
        expect(health.status).toBe("ok");
        expect(health.runtime_source).toBe("installed-python-wheel");
        expect(health.version).toBeTruthy();
        expect(health.distribution_version).toBe(health.version);
        expect(health.runtime_sha256).toMatch(/^[a-f0-9]{64}$/);

        const lifecycle = await page.evaluate(() => window.__standaloneLifecycle);
        const ready = lifecycle.find(event => event.type === "ready");
        expect(ready).toBeTruthy();
        expect(ready.compatibility.runtimeVersion).toBe(health.version);
        expect(ready.compatibility.widgetPackage).toBe("dhxpyt");
        expect(ready.compatibility.widgetVersion).toBe("0.9.16");
        expect(ready.compatibility.dhxAvailable).toBe(true);
        expect(ready.compatibility.javascriptAssets).toBeGreaterThan(0);
        expect(ready.compatibility.cssAssets).toBeGreaterThan(0);

        const requestUuid = await page.evaluate(() => window.__standaloneRequestUuid);
        expect(requestUuid).toBe(health.instance_uuid);
        const localAssets = diagnostics.requests.filter(entry => {
            const url = new URL(entry.url);
            return url.hostname === "127.0.0.1"
                && (url.pathname.startsWith("/runtime/")
                    || url.pathname.startsWith("/standalone_fixture/appcode/"));
        });
        expect(localAssets.length).toBeGreaterThan(5);
        for (const asset of localAssets) {
            const url = new URL(asset.url);
            const isPyodideInternalRequest = url.pathname.startsWith("/runtime/pyodide/");
            expect(
                url.searchParams.get("uuid") === requestUuid || isPyodideInternalRequest,
                asset.url,
            ).toBe(true);
        }

        const realWheelRequests = localAssets.filter(entry => (
            new URL(entry.url).pathname.endsWith(REAL_WIDGET_WHEEL)
        ));
        expect(realWheelRequests.length).toBeGreaterThanOrEqual(2);
        expect(localAssets.some(entry => new URL(entry.url).pathname.endsWith(DEV_WIDGET_WHEEL))).toBe(false);

        const externalWidgetRequests = diagnostics.requests.filter(entry => {
            const url = new URL(entry.url);
            return url.hostname !== "127.0.0.1" && url.href.toLowerCase().includes("dhxpyt");
        });
        expect(externalWidgetRequests.length).toBeGreaterThan(0);
        for (const externalRequest of externalWidgetRequests) {
            expect(new URL(externalRequest.url).searchParams.has("uuid"), externalRequest.url).toBe(false);
        }

        const consoleErrors = diagnostics.consoleEntries.filter(entry => entry.type === "error");
        expect(consoleErrors).toEqual([]);
        const completedWheelProbeAborts = diagnostics.failures.filter(entry => (
            entry.method === "GET"
            && entry.failure?.errorText === "net::ERR_ABORTED"
            && new URL(entry.url).pathname.endsWith(REAL_WIDGET_WHEEL)
            && diagnostics.responses.some(response => (
                response.status === 200 && response.url === entry.url
            ))
        ));
        const unexpectedFailures = diagnostics.failures.filter(entry => {
            const url = new URL(entry.url);
            const completedProbeAbort = completedWheelProbeAborts.includes(entry);
            return !completedProbeAbort
                && (url.hostname === "127.0.0.1" || !url.href.toLowerCase().includes("dhxpyt"));
        });
        expect(unexpectedFailures).toEqual([]);

        evidence.duration_ms = Date.now() - startedAt;
        evidence.health = health;
        evidence.request_uuid = requestUuid;
        evidence.visible_url = page.url();
        evidence.backend_widget_wheel = REAL_WIDGET_WHEEL;
        evidence.backend_widget_requests = realWheelRequests.length;
        evidence.local_asset_requests = localAssets.length;
        evidence.external_widget_requests = externalWidgetRequests.length;
        evidence.console_error_count = consoleErrors.length;
        evidence.completed_widget_probe_abort_count = completedWheelProbeAborts.length;
        evidence.unexpected_request_failure_count = unexpectedFailures.length;
        evidence.compatibility = ready.compatibility;
        evidence.status = "passed";
    } finally {
        evidence.status ||= "failed";
        evidence.duration_ms ||= Date.now() - startedAt;
        await attachEvidence(testInfo, evidence, diagnostics);
    }
});
