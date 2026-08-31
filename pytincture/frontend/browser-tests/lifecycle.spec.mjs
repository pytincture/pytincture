import { expect, test } from "@playwright/test";


async function openHarness(page) {
    await page.goto("/tests/fixtures/lifecycle.html");
    await page.waitForFunction(() => Boolean(window.PytinctureTestRuntime));
}

async function runFailure(page, targetStage) {
    return page.evaluate(async stage => {
        const runtime = window.PytinctureTestRuntime;
        const events = [];
        let inlineCalls = 0;
        const pyodide = {
            FS: {},
            loadPackage: async () => {
                if (stage === runtime.LIFECYCLE_STAGES.PACKAGE_INSTALL) {
                    throw new Error("browser package failure");
                }
            },
            runPython: () => "3.13.0",
            runPythonAsync: async () => undefined,
            unpackArchive: () => undefined,
        };
        const config = {
            application: "sample",
            entrypoint: "Sample",
            widgetlib: "dhxpyt==1.2.3",
            requestUuid: "browser-request",
            mode: "auto",
            pyodideBaseUrl: "/pyodide/",
            loadMaterialIcons: false,
            libsSelector: null,
            inlineSelector: 'script[type="text/python"]',
            onLifecycleEvent: event => events.push(event),
        };
        const fail = name => async () => {
            throw new Error(`browser ${name} failure`);
        };
        const operations = {
            preflightConfig: stage === runtime.LIFECYCLE_STAGES.PREFLIGHT
                ? fail("preflight")
                : () => ({ runtime: "pytincture" }),
            ensureServiceWorker: async () => undefined,
            ensureMaterialIcons: () => undefined,
            warmPyodideCache: async () => undefined,
            ensurePyodideLoaded: stage === runtime.LIFECYCLE_STAGES.RUNTIME_LOAD
                ? fail("runtime")
                : async () => undefined,
            loadPyodideRuntime: async () => pyodide,
            preflightPyodide: () => ({ pyodideVersion: "test" }),
            installExtraMicropipLibs: async () => undefined,
            installWidgetset: stage === runtime.LIFECYCLE_STAGES.WIDGETSET_INSTALL
                ? fail("widget install")
                : async () => "dhxpyt==1.2.3",
            loadWidgetsetAssets: stage === runtime.LIFECYCLE_STAGES.WIDGETSET_LOAD
                ? fail("widget load")
                : async () => ({ widgetVersion: "1.2.3" }),
            downloadPackagedApp: stage === runtime.LIFECYCLE_STAGES.ARCHIVE_DOWNLOAD
                ? fail("download")
                : async () => ({ binary: new ArrayBuffer(1), resource: "/app.pyt" }),
            unpackPackagedApp: stage === runtime.LIFECYCLE_STAGES.ARCHIVE_UNPACK
                ? fail("unpack")
                : () => undefined,
            executePackagedApp: stage === runtime.LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION
                ? fail("entrypoint")
                : async () => undefined,
            runInlineApp: async () => {
                inlineCalls += 1;
                return true;
            },
        };
        try {
            await runtime.runStartup(config, null, operations);
            return { error: null, events, inlineCalls };
        } catch (error) {
            return { error: error.toJSON(), events, inlineCalls };
        }
    }, targetStage);
}

for (const stage of [
    "preflight",
    "runtime-load",
    "package-install",
    "widgetset-install",
    "widgetset-load",
    "archive-download",
    "archive-unpack",
    "entrypoint-execution",
]) {
    test(`browser reports ${stage} failure without masking it`, async ({ page }) => {
        await openHarness(page);
        const result = await runFailure(page, stage);
        expect(result.error.stage).toBe(stage);
        expect(result.error.requestId).toBe("browser-request");
        expect(result.events.some(event => event.type === "error" && event.stage === stage)).toBe(true);
        if (stage === "entrypoint-execution") {
            expect(result.inlineCalls).toBe(0);
            expect(result.events.some(event => event.type === "fallback")).toBe(false);
        }
    });
}

test("browser emits an explicit fallback and ready event for a missing package", async ({ page }) => {
    await openHarness(page);
    const result = await page.evaluate(async () => {
        const runtime = window.PytinctureTestRuntime;
        const events = [];
        const pyodide = {
            FS: {},
            loadPackage: async () => undefined,
            runPython: () => "3.13.0",
            runPythonAsync: async () => undefined,
            unpackArchive: () => undefined,
        };
        const config = {
            application: "sample",
            entrypoint: "Sample",
            widgetlib: "dhxpyt",
            requestUuid: "browser-request",
            mode: "auto",
            pyodideBaseUrl: "/pyodide/",
            loadMaterialIcons: false,
            libsSelector: null,
            inlineSelector: "#inline",
            onLifecycleEvent: event => events.push(event),
        };
        const operations = {
            preflightConfig: () => ({ runtime: "pytincture" }),
            ensureServiceWorker: async () => undefined,
            ensureMaterialIcons: () => undefined,
            warmPyodideCache: async () => undefined,
            ensurePyodideLoaded: async () => undefined,
            loadPyodideRuntime: async () => pyodide,
            preflightPyodide: () => ({ pyodideVersion: "test" }),
            installExtraMicropipLibs: async () => undefined,
            installWidgetset: async () => "dhxpyt",
            loadWidgetsetAssets: async () => ({ widgetVersion: "test" }),
            downloadPackagedApp: async () => {
                throw new runtime.PytinctureLifecycleError({
                    stage: runtime.LIFECYCLE_STAGES.ARCHIVE_DOWNLOAD,
                    code: "package_unavailable",
                    resource: "/app.pyt?token=private",
                    requestId: "browser-request",
                    cause: "HTTP 404",
                });
            },
            unpackPackagedApp: () => undefined,
            executePackagedApp: async () => undefined,
            runInlineApp: async () => true,
        };
        await runtime.runStartup(config, null, operations);
        return events;
    });

    expect(result.some(event => event.type === "fallback")).toBe(true);
    expect(result.at(-1).type).toBe("ready");
    expect(JSON.stringify(result)).not.toContain("private");
});

test("browser rejects an asset whose SRI does not match", async ({ page }) => {
    await openHarness(page);
    const result = await page.evaluate(async () => {
        try {
            await window.PytinctureTestRuntime.DEFAULT_RUNTIME_OPERATIONS.ensureMaterialIcons(
                "/tests/fixtures/integrity.css",
                "integrity-test",
                `sha384-${"A".repeat(64)}`,
            );
            return null;
        } catch (error) {
            return error.message;
        }
    });
    expect(result).toContain("Failed to load stylesheet");
});
