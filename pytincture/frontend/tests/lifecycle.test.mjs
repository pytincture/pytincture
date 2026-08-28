import assert from "node:assert/strict";
import test from "node:test";

await import("../pytincture.js");

const {
    DEFAULT_RUNTIME_OPERATIONS,
    LIFECYCLE_STAGES,
    PytinctureLifecycleError,
    normalizeConfig,
    runStartup,
} = globalThis.__pytinctureTesting;


function fakePyodide() {
    return {
        FS: {},
        loadPackage: async () => undefined,
        runPython: () => "3.13.0",
        runPythonAsync: async () => undefined,
        unpackArchive: () => undefined,
    };
}

function startupFixture(overrides = {}) {
    const events = [];
    const pyodide = fakePyodide();
    const config = {
        application: "sample",
        entrypoint: "Sample",
        widgetlib: "dhxpyt==1.2.3",
        widgetSource: null,
        requestUuid: "request-123",
        mode: "package",
        pyodideBaseUrl: "/pyodide/",
        loadMaterialIcons: false,
        materialIconsUrl: null,
        libsSelector: null,
        inlineSelector: 'script[type="text/python"]',
        onLifecycleEvent: event => events.push(event),
        ...overrides,
    };
    const operations = {
        preflightConfig: () => ({ runtime: "pytincture" }),
        ensureServiceWorker: async () => undefined,
        ensureMaterialIcons: () => undefined,
        warmPyodideCache: async () => undefined,
        ensurePyodideLoaded: async () => undefined,
        loadPyodideRuntime: async () => pyodide,
        preflightPyodide: () => ({ pyodideVersion: "0.29.3", pythonVersion: "3.13.0" }),
        installExtraMicropipLibs: async () => undefined,
        installWidgetset: async () => "dhxpyt==1.2.3",
        loadWidgetsetAssets: async () => ({ widgetVersion: "1.2.3", javascriptAssets: 1, cssAssets: 1 }),
        downloadPackagedApp: async () => ({
            binary: new ArrayBuffer(1),
            resource: "/sample/appcode/appcode.pyt?uuid=request-123",
            correlationId: "correlation-456",
        }),
        unpackPackagedApp: () => undefined,
        executePackagedApp: async () => undefined,
        runInlineApp: async () => true,
    };
    return { config, events, operations, pyodide };
}

const failureCases = [
    [LIFECYCLE_STAGES.PREFLIGHT, "preflightConfig"],
    [LIFECYCLE_STAGES.RUNTIME_LOAD, "ensurePyodideLoaded"],
    [LIFECYCLE_STAGES.WIDGETSET_INSTALL, "installWidgetset"],
    [LIFECYCLE_STAGES.WIDGETSET_LOAD, "loadWidgetsetAssets"],
    [LIFECYCLE_STAGES.ARCHIVE_DOWNLOAD, "downloadPackagedApp"],
    [LIFECYCLE_STAGES.ARCHIVE_UNPACK, "unpackPackagedApp"],
    [LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION, "executePackagedApp"],
];

for (const [stage, operationName] of failureCases) {
    test(`reports ${stage} failures with stage and resource`, async () => {
        const fixture = startupFixture();
        fixture.operations[operationName] = async () => {
            throw new Error(`failure in ${operationName}`);
        };

        await assert.rejects(
            runStartup(fixture.config, null, fixture.operations),
            error => {
                assert.equal(error instanceof PytinctureLifecycleError, true);
                assert.equal(error.stage, stage);
                assert.match(error.rootCause, new RegExp(operationName));
                assert.equal(error.requestId, "request-123");
                if ([LIFECYCLE_STAGES.ARCHIVE_UNPACK, LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION].includes(stage)) {
                    assert.equal(error.correlationId, "correlation-456");
                }
                return true;
            },
        );
        assert.equal(
            fixture.events.some(event => event.type === "error" && event.stage === stage),
            true,
        );
    });
}

test("reports package-install failures", async () => {
    const fixture = startupFixture();
    fixture.pyodide.loadPackage = async () => {
        throw new Error("micropip unavailable");
    };

    await assert.rejects(
        runStartup(fixture.config, null, fixture.operations),
        error => error.stage === LIFECYCLE_STAGES.PACKAGE_INSTALL
            && error.resource === "micropip",
    );
});

test("a packaged execution failure never falls back to inline mode", async () => {
    const fixture = startupFixture({ mode: "auto" });
    let inlineCalls = 0;
    fixture.operations.executePackagedApp = async () => {
        throw new Error("application crashed");
    };
    fixture.operations.runInlineApp = async () => {
        inlineCalls += 1;
        return true;
    };

    await assert.rejects(
        runStartup(fixture.config, null, fixture.operations),
        error => error.stage === LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
    );
    assert.equal(inlineCalls, 0);
    assert.equal(fixture.events.some(event => event.type === "fallback"), false);
});

test("auto mode explicitly falls back only when the package is unavailable", async () => {
    const fixture = startupFixture({ mode: "auto" });
    let inlineCalls = 0;
    fixture.operations.downloadPackagedApp = async () => {
        throw new PytinctureLifecycleError({
            stage: LIFECYCLE_STAGES.ARCHIVE_DOWNLOAD,
            code: "package_unavailable",
            resource: "/sample/appcode/appcode.pyt?token=private",
            requestId: "request-123",
            correlationId: "correlation-456",
            cause: "HTTP 404",
        });
    };
    fixture.operations.runInlineApp = async () => {
        inlineCalls += 1;
        return true;
    };

    await runStartup(fixture.config, null, fixture.operations);

    assert.equal(inlineCalls, 1);
    const fallback = fixture.events.find(event => event.type === "fallback");
    assert.equal(fallback.from, "package");
    assert.equal(fallback.to, "inline");
    assert.equal(fallback.reason.resource.includes("private"), false);
    assert.equal(fixture.events.at(-1).type, "ready");
});

test("network and server errors do not trigger auto fallback", async () => {
    const fixture = startupFixture({ mode: "auto" });
    let inlineCalls = 0;
    fixture.operations.downloadPackagedApp = async () => {
        throw new Error("network failed");
    };
    fixture.operations.runInlineApp = async () => {
        inlineCalls += 1;
        return true;
    };

    await assert.rejects(runStartup(fixture.config, null, fixture.operations));
    assert.equal(inlineCalls, 0);
});

test("the archive downloader classifies only 404 and 410 as unavailable", async () => {
    const originalFetch = globalThis.fetch;
    try {
        for (const status of [404, 410, 401, 500]) {
            globalThis.fetch = async () => ({
                ok: false,
                status,
                headers: { get: name => name === "x-request-id" ? "backend-request" : null },
            });
            await assert.rejects(
                DEFAULT_RUNTIME_OPERATIONS.downloadPackagedApp({
                    application: "sample",
                    requestUuid: "browser-request",
                }),
                error => {
                    assert.equal(
                        error.code,
                        [404, 410].includes(status) ? "package_unavailable" : "archive_download_failed",
                    );
                    assert.equal(error.correlationId, "backend-request");
                    return true;
                },
            );
        }
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test("service applications register the shared root service worker", () => {
    const config = normalizeConfig({ application: "sample" });
    assert.equal(config.serviceWorkerUrl, "/frontend/sw.js");
    assert.equal(config.serviceWorkerScope, "./");
});

test("lifecycle diagnostics redact credentials and expose compatibility", async () => {
    const secretError = new PytinctureLifecycleError({
        stage: LIFECYCLE_STAGES.ARCHIVE_DOWNLOAD,
        resource: "https://example.test/app.pyt?token=super-secret",
        cause: "request failed?password=hunter2 Bearer abc.def",
    });
    assert.equal(secretError.message.includes("super-secret"), false);
    assert.equal(secretError.message.includes("hunter2"), false);
    assert.equal(secretError.message.includes("abc.def"), false);

    const fixture = startupFixture();
    await runStartup(fixture.config, null, fixture.operations);
    const compatibility = fixture.events.find(event => event.type === "compatibility");
    assert.deepEqual(compatibility.compatibility, {
        runtime: "pytincture",
        pyodideVersion: "0.29.3",
        pythonVersion: "3.13.0",
        widgetVersion: "1.2.3",
        javascriptAssets: 1,
        cssAssets: 1,
    });
    assert.equal(fixture.events.at(-1).stage, LIFECYCLE_STAGES.READY);
});
