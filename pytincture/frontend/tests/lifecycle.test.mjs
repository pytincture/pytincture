import assert from "node:assert/strict";
import test from "node:test";

await import("../pytincture.js");

const {
    BUILTIN_WIDGET_ASSET_MANIFESTS,
    BUILTIN_WIDGET_WHEEL_LOCKS,
    DEFAULT_RUNTIME_OPERATIONS,
    LIFECYCLE_STAGES,
    PytinctureLifecycleError,
    frameworkAssetUrls,
    frameworkCacheName,
    normalizeConfig,
    normalizeCsrfCookieName,
    readCookieValue,
    responseIsPublicImmutable,
    runStartup,
    sanitizeConsoleMessage,
    unregisterOwnedServiceWorker,
    validatePackageRequirement,
    withSameOriginRequestUuid,
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
        pyodideScriptIntegrity: null,
        loadMaterialIcons: false,
        materialIconsUrl: null,
        materialIconsIntegrity: null,
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

test("browser package requirements must be exact or hash-pinned wheels", () => {
    assert.doesNotThrow(() => validatePackageRequirement("dhxpyt==0.9.18"));
    assert.doesNotThrow(() => validatePackageRequirement(
        "https://widgets.example/widget-1.0-py3-none-any.whl#sha256=" + "a".repeat(64),
        { allowPackagePin: false },
    ));
    assert.throws(() => validatePackageRequirement("dhxpyt"), /exact name==version/);
    assert.throws(
        () => validatePackageRequirement("https://widgets.example/widget.whl"),
        /#sha256/,
    );
});

test("public widget index defaults to standalone-only", () => {
    assert.equal(normalizeConfig({ widgetlib: "custom==1.0.0" }).allowPublicWidgetIndex, true);
    assert.equal(normalizeConfig({
        application: "sample",
        widgetlib: "custom==1.0.0",
    }).allowPublicWidgetIndex, false);
});

test("CSRF cookie selection uses one explicit runtime mode", () => {
    const cookies = [
        "pytincture-dev-csrf=sibling-value",
        "__Host-pytincture-csrf=production-value",
    ].join("; ");

    assert.equal(
        readCookieValue(cookies, "__Host-pytincture-csrf"),
        "production-value",
    );
    assert.equal(
        readCookieValue(cookies, "pytincture-dev-csrf"),
        "sibling-value",
    );
    assert.equal(normalizeCsrfCookieName("__Host-pytincture-csrf"), "__Host-pytincture-csrf");
    assert.throws(
        () => normalizeCsrfCookieName("attacker-selected-cookie"),
        /Unsupported Pytincture CSRF cookie name/,
    );
});

test("the built-in dhxpyt release verifies the complete PyPI wheel", async () => {
    const calls = [];
    const pyodide = fakePyodide();
    pyodide.runPythonAsync = async source => calls.push(source);

    const installedSource = await DEFAULT_RUNTIME_OPERATIONS.installWidgetset(pyodide, {
        widgetlib: "dhxpyt==0.9.18",
        widgetSource: null,
        requestUuid: "backend-instance",
    });

    assert.equal(installedSource, BUILTIN_WIDGET_WHEEL_LOCKS["dhxpyt==0.9.18"]);
    assert.match(installedSource, /^https:\/\/files\.pythonhosted\.org\//);
    assert.match(installedSource, /#sha256=[a-f0-9]{64}$/);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].includes("backend-instance"), false);
    assert.match(calls[0], /deps=False/);
});

test("service metadata skips probes for backend wheels that do not exist", async () => {
    const originalFetch = globalThis.fetch;
    const calls = [];
    const pyodide = fakePyodide();
    pyodide.runPythonAsync = async source => calls.push(source);
    globalThis.fetch = async () => {
        throw new Error("generated service metadata must suppress missing-wheel probes");
    };
    try {
        const source = await DEFAULT_RUNTIME_OPERATIONS.installWidgetset(pyodide, {
            application: "sample",
            widgetlib: "dhxpyt==0.9.18",
            widgetSource: null,
            backendWidgetSources: [],
            requestUuid: "backend-instance",
            allowPublicWidgetIndex: false,
        });
        assert.equal(source, BUILTIN_WIDGET_WHEEL_LOCKS["dhxpyt==0.9.18"]);
        assert.equal(calls.length, 1);
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test("explicit widget sources require caller-provided wheel integrity", () => {
    const config = normalizeConfig({
        application: "sample",
        widgetlib: "custom==1.0.0",
        widgetSource: "https://widgets.example/custom.whl",
    });
    const originalDocument = globalThis.document;
    globalThis.document = {};
    try {
        assert.throws(
            () => DEFAULT_RUNTIME_OPERATIONS.preflightConfig(config),
            /#sha256/,
        );
    } finally {
        if (originalDocument === undefined) {
            delete globalThis.document;
        } else {
            globalThis.document = originalDocument;
        }
    }
});

test("widget asset loader is manifest-only and verifies ownership and hashes", async () => {
    let generatedPython = "";
    const pyodide = fakePyodide();
    pyodide.runPythonAsync = async source => {
        generatedPython = source;
        return JSON.stringify({
            widgetPackage: "dhxpyt",
            widgetVersion: "0.9.18",
            javascriptAssets: 9,
            cssAssets: 4,
            assetManifest: "Pytincture compatibility lock dhxpyt@0.9.18",
            dhxAvailable: true,
        });
    };
    const report = await DEFAULT_RUNTIME_OPERATIONS.loadWidgetsetAssets(
        pyodide,
        normalizeConfig({ application: "sample", widgetlib: "dhxpyt==0.9.18" }),
        "dhxpyt==0.9.18",
    );

    assert.equal(report.javascriptAssets, 9);
    assert.equal(generatedPython.includes("os.walk"), false);
    assert.match(generatedPython, /asset_path not in owned_files/);
    assert.match(generatedPython, /Widget asset integrity check failed/);
    assert.match(generatedPython, /hashlib\.sha256/);
    assert.equal(BUILTIN_WIDGET_ASSET_MANIFESTS["dhxpyt@0.9.18"].assets.length, 13);
});

test("backend wheel wins before a matching public-index package", async () => {
    const originalFetch = globalThis.fetch;
    const calls = [];
    const pyodide = fakePyodide();
    pyodide.runPythonAsync = async source => {
        calls.push(source);
        if (source.includes('"dhxpyt==1.2.3"')) {
            throw new Error("package index unavailable");
        }
    };
    globalThis.fetch = async () => ({
        ok: true,
        headers: { get: name => name === "x-pytincture-sha256" ? "a".repeat(64) : null },
    });
    try {
        const source = await DEFAULT_RUNTIME_OPERATIONS.installWidgetset(pyodide, {
            application: "sample",
            widgetlib: "dhxpyt==1.2.3",
            widgetSource: null,
            devWidgetHost: "https://widgets.example",
            devWheelVersion: "99.99.99",
            requestUuid: "instance-id",
            allowPublicWidgetIndex: true,
        });
        assert.match(source, /#sha256=a{64}$/);
        assert.equal(calls.length, 1);
        assert.equal(calls[0].includes('"dhxpyt==1.2.3"'), false);
        assert.match(calls.at(-1), /sha256=a{64}/);
        assert.match(calls.at(-1), /deps=False/);
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test("service custom widgets fail closed instead of silently using PyPI", async () => {
    const originalFetch = globalThis.fetch;
    const calls = [];
    const pyodide = fakePyodide();
    pyodide.runPythonAsync = async source => calls.push(source);
    globalThis.fetch = async () => ({
        ok: false,
        headers: { get: () => null },
        body: { cancel: async () => undefined },
    });
    try {
        await assert.rejects(
            DEFAULT_RUNTIME_OPERATIONS.installWidgetset(pyodide, {
                application: "sample",
                widgetlib: "corp-widget==1.2.3",
                widgetSource: null,
                devWidgetHost: "https://widgets.example",
                devWheelVersion: "99.99.99",
                requestUuid: "instance-id",
                allowPublicWidgetIndex: false,
            }),
            /No trusted backend wheel/,
        );
        assert.equal(calls.length, 0);
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test("an exact service widget may use PyPI only after explicit allowlisting", async () => {
    const originalFetch = globalThis.fetch;
    const calls = [];
    const pyodide = fakePyodide();
    pyodide.runPythonAsync = async source => calls.push(source);
    globalThis.fetch = async () => ({
        ok: false,
        headers: { get: () => null },
        body: { cancel: async () => undefined },
    });
    try {
        const source = await DEFAULT_RUNTIME_OPERATIONS.installWidgetset(pyodide, {
            application: "sample",
            widgetlib: "corp-widget==1.2.3",
            widgetSource: null,
            devWidgetHost: "https://widgets.example",
            devWheelVersion: "99.99.99",
            requestUuid: "instance-id",
            allowPublicWidgetIndex: true,
        });
        assert.equal(source, "corp-widget==1.2.3");
        assert.equal(calls.length, 1);
        assert.match(calls[0], /corp-widget==1\.2\.3/);
        assert.equal(calls[0].includes("instance-id"), false);
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test("backend wheel probe uses metadata first and hashes only an uncached wheel", async () => {
    const originalFetch = globalThis.fetch;
    const methods = [];
    const pyodide = fakePyodide();
    pyodide.runPythonAsync = async source => {
        if (source.includes('"custom-widgets==1.0.0"')) {
            throw new Error("package index unavailable");
        }
    };
    globalThis.fetch = async (_url, options = {}) => {
        const method = options.method || "GET";
        methods.push(method);
        return {
            ok: true,
            headers: {
                get: name => name === "x-pytincture-sha256" && method === "GET"
                    ? "b".repeat(64)
                    : null,
            },
            body: { cancel: async () => undefined },
        };
    };
    try {
        const source = await DEFAULT_RUNTIME_OPERATIONS.installWidgetset(pyodide, {
            application: "sample",
            widgetlib: "custom-widgets==1.0.0",
            widgetSource: null,
            devWidgetHost: "https://widgets.example",
            devWheelVersion: "99.99.99",
            requestUuid: "instance-id",
        });
        assert.deepEqual(methods, ["HEAD", "GET"]);
        assert.match(source, /#sha256=b{64}$/);
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test("backend fallback refuses a wheel without server integrity metadata", async () => {
    const originalFetch = globalThis.fetch;
    const pyodide = fakePyodide();
    pyodide.runPythonAsync = async () => {
        throw new Error("package index unavailable");
    };
    globalThis.fetch = async () => ({ ok: true, headers: { get: () => null } });
    try {
        await assert.rejects(
            DEFAULT_RUNTIME_OPERATIONS.installWidgetset(pyodide, {
                application: "sample",
                widgetlib: "dhxpyt==1.2.3",
                widgetSource: null,
                devWidgetHost: "https://widgets.example",
                devWheelVersion: "99.99.99",
                requestUuid: "instance-id",
            }),
            /X-Pytincture-SHA256/,
        );
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test("package bootstrap no longer installs an unpinned framework dependency", async () => {
    const fixture = startupFixture();
    const pythonCalls = [];
    fixture.pyodide.runPythonAsync = async source => {
        pythonCalls.push(source);
    };
    await runStartup(fixture.config, null, fixture.operations);
    assert.equal(pythonCalls.some(source => source.includes("python-dotenv")), false);
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

test("service applications isolate the worker under their application scope", () => {
    const config = normalizeConfig({ application: "sample" });
    assert.equal(config.serviceWorkerUrl, "/sample/frontend/sw.js");
    assert.equal(config.serviceWorkerScope, "/sample/");
    assert.equal(config.pyodideBaseUrl, "/sample/frontend/pyodide/0.29.3/full/");
    assert.equal(
        config.materialIconsUrl,
        "/sample/frontend/vendor/materialdesignicons/materialdesignicons.css",
    );
});

test("default icons follow an explicit same-origin Pyodide frontend root", () => {
    const config = normalizeConfig({
        mode: "inline",
        pyodideBaseUrl: "/frontend/pyodide/0.29.3/full/",
    });
    assert.equal(
        config.materialIconsUrl,
        "/frontend/vendor/materialdesignicons/materialdesignicons.css",
    );
});

test("external browser assets fail closed without explicit SRI", () => {
    const previousDocument = globalThis.document;
    const previousWindow = globalThis.window;
    globalThis.document = {};
    globalThis.window = {
        location: {
            href: "https://app.example.test/",
            origin: "https://app.example.test",
        },
    };
    try {
        const externalPyodide = normalizeConfig({
            mode: "inline",
            pyodideBaseUrl: "https://cdn.example.test/pyodide/0.29.3/full/",
        });
        assert.throws(
            () => DEFAULT_RUNTIME_OPERATIONS.preflightConfig(externalPyodide),
            /allowUnverifiedExternalPyodide=true/,
        );

        const optedInWithoutIntegrity = normalizeConfig({
            mode: "inline",
            pyodideBaseUrl: "https://cdn.example.test/pyodide/0.29.3/full/",
            allowUnverifiedExternalPyodide: true,
        });
        assert.throws(
            () => DEFAULT_RUNTIME_OPERATIONS.preflightConfig(optedInWithoutIntegrity),
            /External Pyodide requires pyodideScriptIntegrity/,
        );

        const externalIcons = normalizeConfig({
            mode: "inline",
            loadMaterialIcons: true,
            materialIconsUrl: "https://cdn.example.test/materialdesignicons.css",
        });
        assert.throws(
            () => DEFAULT_RUNTIME_OPERATIONS.preflightConfig(externalIcons),
            /External Material Icons require materialIconsIntegrity/,
        );
    } finally {
        globalThis.document = previousDocument;
        globalThis.window = previousWindow;
    }
});

test("external script and stylesheet loaders apply SRI with anonymous CORS", async () => {
    const previousDocument = globalThis.document;
    const previousWindow = globalThis.window;
    const previousLoadPyodide = globalThis.loadPyodide;
    const previousCreatePyodideModule = globalThis._createPyodideModule;
    const appended = [];
    const integrity = "sha384-" + "A".repeat(64);
    globalThis.window = {
        location: {
            href: "https://app.example.test/",
            origin: "https://app.example.test",
        },
    };
    globalThis.document = {
        createElement: tagName => ({ tagName }),
        querySelectorAll: () => [],
        head: {
            appendChild: element => {
                appended.push(element);
                queueMicrotask(() => element.onload());
            },
        },
    };
    delete globalThis.loadPyodide;
    delete globalThis._createPyodideModule;
    try {
        const config = normalizeConfig({
            mode: "inline",
            pyodideBaseUrl: "https://cdn.example.test/pyodide/0.29.3/full/",
            allowUnverifiedExternalPyodide: true,
            pyodideScriptIntegrity: {
                "pyodide.js": integrity,
                "pyodide.asm.js": integrity,
            },
            materialIconsUrl: "https://cdn.example.test/materialdesignicons.css",
            materialIconsIntegrity: integrity,
        });
        assert.doesNotThrow(() => DEFAULT_RUNTIME_OPERATIONS.preflightConfig(config));
        await DEFAULT_RUNTIME_OPERATIONS.ensurePyodideLoaded(config);
        await DEFAULT_RUNTIME_OPERATIONS.ensureMaterialIcons(
            config.materialIconsUrl,
            config.requestUuid,
            config.materialIconsIntegrity,
        );
        assert.equal(appended.length, 3);
        assert.equal(appended.every(element => element.integrity === integrity), true);
        assert.equal(appended.every(element => element.crossOrigin === "anonymous"), true);
    } finally {
        globalThis.document = previousDocument;
        globalThis.window = previousWindow;
        if (previousLoadPyodide === undefined) {
            delete globalThis.loadPyodide;
        } else {
            globalThis.loadPyodide = previousLoadPyodide;
        }
        if (previousCreatePyodideModule === undefined) {
            delete globalThis._createPyodideModule;
        } else {
            globalThis._createPyodideModule = previousCreatePyodideModule;
        }
    }
});

test("framework caches are namespaced by application, release, and instance uuid", () => {
    const alpha = normalizeConfig({ application: "alpha", requestUuid: "instance-a" });
    const beta = normalizeConfig({ application: "beta", requestUuid: "instance-a" });
    const restarted = normalizeConfig({ application: "alpha", requestUuid: "instance-b" });
    assert.match(frameworkCacheName(alpha), /^pytincture:alpha:/);
    assert.notEqual(frameworkCacheName(alpha), frameworkCacheName(beta));
    assert.notEqual(frameworkCacheName(alpha), frameworkCacheName(restarted));
});

test("only same-origin framework URLs receive the instance uuid", () => {
    const previousWindow = globalThis.window;
    globalThis.window = {
        location: {
            href: "https://app.example.test/sample",
            origin: "https://app.example.test",
        },
    };
    try {
        const signed = "https://storage.example.test/object?X-Amz-Signature=abc123";
        assert.equal(withSameOriginRequestUuid(signed, "instance-a"), signed);
        assert.equal(
            withSameOriginRequestUuid("/sample/frontend/pytincture.js", "instance-a"),
            "https://app.example.test/sample/frontend/pytincture.js?uuid=instance-a",
        );
        const assets = frameworkAssetUrls(normalizeConfig({
            application: "sample",
            requestUuid: "instance-a",
        }));
        assert.equal(assets.length, 10);
        assert.equal(assets.every(url => (
            new URL(url).pathname.startsWith("/sample/frontend/")
            && new URL(url).searchParams.get("uuid") === "instance-a"
        )), true);
    } finally {
        globalThis.window = previousWindow;
    }
});

test("private, credential-varying, and cookie-setting responses are never cacheable", () => {
    const response = headers => ({
        ok: true,
        type: "basic",
        headers: { get: name => headers[name.toLowerCase()] || null },
    });
    assert.equal(responseIsPublicImmutable(response({ "cache-control": "public, max-age=31536000" })), true);
    assert.equal(responseIsPublicImmutable(response({ "cache-control": "private, max-age=60" })), false);
    assert.equal(responseIsPublicImmutable(response({ "cache-control": "no-store" })), false);
    assert.equal(responseIsPublicImmutable(response({ vary: "Accept-Encoding, Cookie" })), false);
    assert.equal(responseIsPublicImmutable(response({ "set-cookie": "session=secret" })), false);
});

test("disabling a service worker removes only the owning application's state", async () => {
    const previousWindow = globalThis.window;
    const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
    const cachesDescriptor = Object.getOwnPropertyDescriptor(globalThis, "caches");
    const removed = [];
    let unregistered = false;
    globalThis.window = {
        location: {
            href: "https://app.example.test/alpha",
            origin: "https://app.example.test",
        },
    };
    Object.defineProperty(globalThis, "navigator", {
        configurable: true,
        value: {
            serviceWorker: {
                getRegistration: async () => ({
                    active: { scriptURL: "https://app.example.test/alpha/frontend/sw.js?uuid=old" },
                    unregister: async () => {
                        unregistered = true;
                        return true;
                    },
                }),
            },
        },
    });
    Object.defineProperty(globalThis, "caches", {
        configurable: true,
        value: {
            keys: async () => [
                "pytincture:alpha:old-release:old",
                "pytincture:beta:current-release:new",
                "foreign-library-cache",
            ],
            delete: async key => {
                removed.push(key);
                return true;
            },
        },
    });
    try {
        const result = await unregisterOwnedServiceWorker(normalizeConfig({ application: "alpha" }));
        assert.equal(result, true);
        assert.equal(unregistered, true);
        assert.deepEqual(removed, ["pytincture:alpha:old-release:old"]);
    } finally {
        globalThis.window = previousWindow;
        if (navigatorDescriptor) {
            Object.defineProperty(globalThis, "navigator", navigatorDescriptor);
        } else {
            delete globalThis.navigator;
        }
        if (cachesDescriptor) {
            Object.defineProperty(globalThis, "caches", cachesDescriptor);
        } else {
            delete globalThis.caches;
        }
    }
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

test("browser console forwarding redacts secrets and bounds complex messages", () => {
    const circular = { label: "safe" };
    circular.self = circular;
    const message = sanitizeConsoleMessage([
        "request?token=url-secret Bearer bearer-secret",
        {
            password: "object-secret",
            nested: {
                client_secret: "nested-secret",
                detail: "authorization=inline-secret",
            },
            circular,
        },
        "x".repeat(2000),
    ]);

    for (const secret of [
        "url-secret",
        "bearer-secret",
        "object-secret",
        "nested-secret",
        "inline-secret",
    ]) {
        assert.equal(message.includes(secret), false);
    }
    assert.equal(message.includes("[circular]"), true);
    assert.equal(message.length <= 800, true);
});
