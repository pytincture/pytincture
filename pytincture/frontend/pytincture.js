const FALLBACK_DEV_WIDGET_HOST = "http://127.0.0.1:8070";
const PYTINCTURE_RUNTIME_VERSION = "0.10.7";

const DEFAULT_CONFIG = {
    application: null,
    entrypoint: null,
    widgetlib: "dhxpyt",
    widgetSource: null,
    requestUuid: null,
    mode: "auto", // 'package', 'inline', or 'auto'
    pyodideBaseUrl: "./frontend/pyodide/0.29.3/full/",
    loadMaterialIcons: true,
    materialIconsUrl: "https://cdnjs.cloudflare.com/ajax/libs/MaterialDesign-Webfont/7.4.47/css/materialdesignicons.css",
    enableBackendLogging: false,
    logEndpoint: "/logs",
    inlineSelector: 'script[type="text/python"]',
    libsSelector: '#micropip-libs',
    devWidgetHost: null,
    devWheelVersion: "99.99.99",
    enableServiceWorker: false,
    serviceWorkerUrl: "sw.js",
    serviceWorkerScope: "./",
    warmPyodideCache: true,
    showLoadingOverlay: true,
    loadingOverlayId: "pytincture-loading",
    loadingTitle: "Starting PyTincture",
    onLifecycleEvent: null,
};

const LIFECYCLE_STAGES = Object.freeze({
    PREFLIGHT: "preflight",
    RUNTIME_LOAD: "runtime-load",
    PACKAGE_INSTALL: "package-install",
    WIDGETSET_INSTALL: "widgetset-install",
    WIDGETSET_LOAD: "widgetset-load",
    ARCHIVE_DOWNLOAD: "archive-download",
    ARCHIVE_UNPACK: "archive-unpack",
    ENTRYPOINT_EXECUTION: "entrypoint-execution",
    READY: "ready",
});

let loggingInstalled = false;
const originalConsoleMethods = {};
let nativeFetch = null;
let activeRequestUuid = null;
let cacheBustingSuspensionDepth = 0;

function sanitizeDiagnostic(value) {
    if (value === null || value === undefined) {
        return "";
    }
    return String(value)
        .replace(/([?&](?:token|secret|password|authorization|code)=)[^&#\s]*/gi, "$1[redacted]")
        .replace(/\b(token|secret|password|authorization|api[_-]?key|access[_-]?token|id[_-]?token)(\s*[:=]\s*)[^\s,;]+/gi, "$1$2[redacted]")
        .replace(/\b(Bearer\s+)[A-Za-z0-9._~+/=-]+/gi, "$1[redacted]")
        .slice(0, 800);
}

function sanitizeResource(value) {
    if (!value) {
        return null;
    }
    const rawValue = String(value);
    if (rawValue.startsWith("#") || (!rawValue.includes("/") && !rawValue.includes(":"))) {
        return sanitizeDiagnostic(rawValue);
    }
    try {
        const base = typeof window !== "undefined" && window.location
            ? window.location.href
            : "http://localhost/";
        const parsed = new URL(rawValue, base);
        parsed.search = "";
        parsed.hash = "";
        return parsed.toString();
    } catch (_error) {
        return sanitizeDiagnostic(value).replace(/\?.*$/, "");
    }
}

class PytinctureLifecycleError extends Error {
    constructor({ stage, code = "startup_failed", resource = null, requestId = null, correlationId = null, cause = null }) {
        const rootCause = sanitizeDiagnostic(cause?.message || cause || "Unknown startup failure");
        const safeResource = sanitizeResource(resource);
        super(`Pytincture startup failed during ${stage}${safeResource ? ` (${safeResource})` : ""}: ${rootCause}`);
        this.name = "PytinctureLifecycleError";
        this.stage = stage;
        this.code = code;
        this.resource = safeResource;
        this.requestId = requestId || null;
        this.correlationId = correlationId || null;
        this.rootCause = rootCause;
    }

    toJSON() {
        return {
            name: this.name,
            message: this.message,
            stage: this.stage,
            code: this.code,
            resource: this.resource,
            requestId: this.requestId,
            correlationId: this.correlationId,
            rootCause: this.rootCause,
        };
    }
}

function emitLifecycleEvent(config, type, stage, details = {}) {
    const event = Object.freeze({
        type,
        stage,
        requestId: config.requestUuid || null,
        timestamp: new Date().toISOString(),
        ...details,
    });
    if (typeof config.onLifecycleEvent === "function") {
        try {
            config.onLifecycleEvent(event);
        } catch (callbackError) {
            console.warn("Pytincture lifecycle callback failed:", callbackError);
        }
    }
    if (typeof window !== "undefined" && typeof window.dispatchEvent === "function" && typeof CustomEvent === "function") {
        window.dispatchEvent(new CustomEvent("pytincture:lifecycle", { detail: event }));
    }
    return event;
}

async function runLifecycleStage(config, stage, resource, callback, metadata = {}) {
    const safeResource = sanitizeResource(resource);
    emitLifecycleEvent(config, "stage-start", stage, { resource: safeResource });
    try {
        const result = await callback();
        emitLifecycleEvent(config, "stage-complete", stage, { resource: safeResource });
        return result;
    } catch (error) {
        const lifecycleError = error instanceof PytinctureLifecycleError
            ? error
            : new PytinctureLifecycleError({
                stage,
                resource,
                requestId: config.requestUuid,
                correlationId: metadata.correlationId || null,
                cause: error,
            });
        emitLifecycleEvent(config, "error", lifecycleError.stage, {
            resource: lifecycleError.resource,
            error: lifecycleError.toJSON(),
        });
        throw lifecycleError;
    }
}

function ensureTrailingSlash(value) {
    if (!value) {
        return "/";
    }
    return value.endsWith("/") ? value : `${value}/`;
}

function makeRequestId() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
        return crypto.randomUUID();
    }
    const rand = Math.random().toString(16).slice(2, 10);
    return `${Date.now().toString(16)}-${rand}`;
}

function withRequestUuid(value, requestUuid) {
    if (!value || !requestUuid || typeof value !== "string") {
        return value;
    }
    if (/^(?:data|blob|javascript):/i.test(value) || value.startsWith("#")) {
        return value;
    }

    const hashIndex = value.indexOf("#");
    const hash = hashIndex >= 0 ? value.slice(hashIndex) : "";
    let base = hashIndex >= 0 ? value.slice(0, hashIndex) : value;
    const encodedUuid = encodeURIComponent(requestUuid);
    if (/(?:^|[?&])uuid=/.test(base)) {
        base = base.replace(/([?&])uuid=[^&]*/g, `$1uuid=${encodedUuid}`);
    } else {
        base = `${base}${base.includes("?") ? "&" : "?"}uuid=${encodedUuid}`;
    }
    return `${base}${hash}`;
}

function installCacheBustingFetch(requestUuid) {
    if (!requestUuid || typeof globalThis === "undefined" || typeof globalThis.fetch !== "function") {
        return;
    }
    activeRequestUuid = requestUuid;
    if (nativeFetch) {
        return;
    }

    nativeFetch = globalThis.fetch.bind(globalThis);
    globalThis.fetch = function (resource, options) {
        // micropip validates package and wheel URLs. Query-string cache tokens
        // can make otherwise valid package sources unresolvable, so installs
        // temporarily use the original fetch implementation.
        if (cacheBustingSuspensionDepth > 0) {
            return nativeFetch(resource, options);
        }
        const requestMethod = String(
            options?.method || (typeof Request !== "undefined" && resource instanceof Request ? resource.method : "GET"),
        ).toUpperCase();
        if (requestMethod !== "GET" && requestMethod !== "HEAD") {
            return nativeFetch(resource, options);
        }

        if (typeof Request !== "undefined" && resource instanceof Request) {
            const bustedRequest = new Request(withRequestUuid(resource.url, activeRequestUuid), resource);
            return nativeFetch(bustedRequest, options);
        }
        return nativeFetch(withRequestUuid(String(resource), activeRequestUuid), options);
    };
}

async function withoutCacheBusting(callback) {
    cacheBustingSuspensionDepth += 1;
    try {
        return await callback();
    } finally {
        cacheBustingSuspensionDepth -= 1;
    }
}

function normalizeConfig(arg1, widgetlib, entrypoint) {
    const resolveDevWidgetHost = host => {
        if (host) {
            return host;
        }
        if (typeof window !== "undefined" && window.location) {
            if (window.location.origin) {
                return window.location.origin;
            }
            return `${window.location.protocol}//${window.location.host}`;
        }
        return FALLBACK_DEV_WIDGET_HOST;
    };

    if (typeof arg1 === "object" && arg1 !== null) {
        const merged = { ...DEFAULT_CONFIG, ...arg1 };
        merged.pyodideBaseUrl = ensureTrailingSlash(merged.pyodideBaseUrl);
        merged.requestUuid = merged.requestUuid || makeRequestId();
        merged.entrypoint = merged.entrypoint || merged.application;
        merged.devWidgetHost = resolveDevWidgetHost(merged.devWidgetHost);
        if (merged.application && merged.serviceWorkerUrl === DEFAULT_CONFIG.serviceWorkerUrl) {
            merged.serviceWorkerUrl = "/frontend/sw.js";
        }
        if (!("enableBackendLogging" in arg1)) {
            merged.enableBackendLogging = !!merged.application;
        }
        if (merged.application && (merged.pyodideBaseUrl.startsWith("frontend/") || merged.pyodideBaseUrl.startsWith("./frontend/"))) {
            const cleanPath = merged.pyodideBaseUrl.replace(/^\.\//, "");
            merged.pyodideBaseUrl = ensureTrailingSlash(`${merged.application}/${cleanPath}`);
        }
        return merged;
    }

    const application = arg1 || null;
    const config = {
        ...DEFAULT_CONFIG,
        application,
        widgetlib: widgetlib || DEFAULT_CONFIG.widgetlib,
        entrypoint: entrypoint || application,
    };
    config.pyodideBaseUrl = ensureTrailingSlash(config.pyodideBaseUrl);
    config.requestUuid = config.requestUuid || makeRequestId();
    config.devWidgetHost = resolveDevWidgetHost(config.devWidgetHost);
    if (config.application && config.serviceWorkerUrl === DEFAULT_CONFIG.serviceWorkerUrl) {
        config.serviceWorkerUrl = "/frontend/sw.js";
    }
    config.enableBackendLogging = !!application;
    if (config.application && (config.pyodideBaseUrl.startsWith("frontend/") || config.pyodideBaseUrl.startsWith("./frontend/"))) {
        const cleanPath = config.pyodideBaseUrl.replace(/^\.\//, "");
        config.pyodideBaseUrl = ensureTrailingSlash(`${config.application}/${cleanPath}`);
    }
    return config;
}

function preflightConfig(config) {
    if (typeof document === "undefined" || typeof fetch !== "function") {
        throw new Error("A browser document and Fetch API are required.");
    }
    if (!new Set(["auto", "package", "inline"]).has(config.mode)) {
        throw new Error(`Unsupported startup mode: ${config.mode}`);
    }
    if (config.mode === "package" && !config.application) {
        throw new Error("Packaged mode requires an application name.");
    }
    const pythonIdentifier = /^[A-Za-z_]\w*$/;
    if (config.application && !pythonIdentifier.test(config.application)) {
        throw new Error("Application must be a valid Python module identifier.");
    }
    if (config.entrypoint && !pythonIdentifier.test(config.entrypoint)) {
        throw new Error("Entrypoint must be a valid Python identifier.");
    }
    if (!config.pyodideBaseUrl) {
        throw new Error("pyodideBaseUrl is required.");
    }
    return {
        runtime: "pytincture",
        runtimeVersion: PYTINCTURE_RUNTIME_VERSION,
        pyodideBaseUrl: sanitizeResource(config.pyodideBaseUrl),
        widgetset: sanitizeDiagnostic(config.widgetSource || config.widgetlib || "none"),
    };
}

function preflightPyodide(pyodide) {
    const requiredMethods = ["loadPackage", "runPython", "runPythonAsync", "unpackArchive"];
    const missing = requiredMethods.filter(name => typeof pyodide?.[name] !== "function");
    if (!pyodide?.FS || missing.length) {
        throw new Error(`Incompatible Pyodide runtime; missing: ${[...missing, ...(!pyodide?.FS ? ["FS"] : [])].join(", ")}`);
    }
    return {
        pyodideVersion: sanitizeDiagnostic(pyodide.version || "unknown"),
        pythonVersion: sanitizeDiagnostic(
            typeof pyodide.runPython === "function"
                ? pyodide.runPython("import platform; platform.python_version()")
                : "unknown",
        ),
    };
}

function loadScript(url, requestUuid) {
    return new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = withRequestUuid(url, requestUuid);
        script.onload = resolve;
        script.onerror = () => reject(new Error(`Failed to load script: ${url}`));
        document.head.appendChild(script);
    });
}

function ensureMaterialIcons(url, requestUuid) {
    if (!url) {
        return;
    }
    const stylesheetUrl = withRequestUuid(url, requestUuid);
    const existing = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).some(link => link.href === stylesheetUrl);
    if (existing) {
        return;
    }
    const link = document.createElement("link");
    link.href = stylesheetUrl;
    link.rel = "stylesheet";
    link.type = "text/css";
    link.media = "all";
    document.head.appendChild(link);
}

function enableBackendLogging(endpoint) {
    if (loggingInstalled) {
        return;
    }

    const logEndpoint = endpoint || "/logs";
    const levels = ["log", "warn", "error", "info", "debug"];

    levels.forEach(level => {
        if (typeof console[level] === "function") {
            originalConsoleMethods[level] = console[level].bind(console);
        }
    });

    function sendToBackend(level, message) {
        const csrfToken = String(document.cookie || "")
            .split(";")
            .map(cookie => cookie.trim().split("="))
            .find(([name]) => name === "pytincture_csrf")?.slice(1).join("=") || "";
        const headers = { "Content-Type": "application/json" };
        if (csrfToken) {
            headers["X-CSRF-Token"] = csrfToken;
        }
        fetch(logEndpoint, {
            method: "POST",
            headers,
            body: JSON.stringify({
                level,
                message,
                timestamp: new Date().toISOString(),
            }),
        }).catch(err => {
            const fallbackError = originalConsoleMethods.error || console.error.bind(console);
            fallbackError("Failed to send log to backend:", err);
        });
    }

    levels.forEach(level => {
        if (typeof console[level] !== "function" || !originalConsoleMethods[level]) {
            return;
        }
        console[level] = function (...args) {
            const message = args.map(arg => (typeof arg === "object" ? JSON.stringify(arg) : arg)).join(" ");
            sendToBackend(level, message);
            originalConsoleMethods[level](...args);
        };
    });

    loggingInstalled = true;
}

function ensureLoadingOverlay(config) {
    if (!config.showLoadingOverlay || typeof document === "undefined") {
        return null;
    }
    const existing = document.getElementById(config.loadingOverlayId);
    if (existing) {
        return existing;
    }
    const overlay = document.createElement("div");
    overlay.id = config.loadingOverlayId;
    overlay.innerHTML = `
      <div class="pytincture-loading__card">
        <div class="pytincture-loading__title">${config.loadingTitle}</div>
        <div class="pytincture-loading__status">Loading…</div>
        <div class="pytincture-loading__bar">
          <div class="pytincture-loading__bar-inner"></div>
        </div>
      </div>
    `;

    const style = document.createElement("style");
    style.textContent = `
      #${config.loadingOverlayId} {
        position: fixed;
        inset: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        background: radial-gradient(circle at 20% 20%, #f5f6f7, #e8ebef);
        z-index: 99999;
        font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      }
      #${config.loadingOverlayId} .pytincture-loading__card {
        background: #ffffff;
        border-radius: 14px;
        padding: 20px 24px;
        box-shadow: 0 12px 30px rgba(10, 22, 70, 0.12);
        min-width: 260px;
        max-width: 360px;
        text-align: left;
      }
      #${config.loadingOverlayId} .pytincture-loading__title {
        font-weight: 600;
        font-size: 16px;
        margin-bottom: 6px;
        color: #1f2937;
      }
      #${config.loadingOverlayId} .pytincture-loading__status {
        font-size: 13px;
        color: #4b5563;
        margin-bottom: 12px;
      }
      #${config.loadingOverlayId} .pytincture-loading__bar {
        height: 8px;
        background: #e5e7eb;
        border-radius: 999px;
        overflow: hidden;
      }
      #${config.loadingOverlayId} .pytincture-loading__bar-inner {
        height: 100%;
        width: 40%;
        background: linear-gradient(90deg, #2563eb, #38bdf8);
        animation: pytincture-loading 1.4s ease-in-out infinite;
      }
      @keyframes pytincture-loading {
        0% { transform: translateX(-120%); }
        50% { transform: translateX(10%); }
        100% { transform: translateX(220%); }
      }
    `;
    overlay.appendChild(style);
    document.body.appendChild(overlay);
    return overlay;
}

function updateLoadingStatus(overlay, status) {
    if (!overlay) {
        return;
    }
    const statusEl = overlay.querySelector(".pytincture-loading__status");
    if (statusEl) {
        statusEl.textContent = status;
    }
}

function removeLoadingOverlay(overlay) {
    if (!overlay) {
        return;
    }
    overlay.remove();
}

async function ensureServiceWorker(config) {
    if (!config.enableServiceWorker) {
        return;
    }
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
        return;
    }
    try {
        await navigator.serviceWorker.register(withRequestUuid(config.serviceWorkerUrl, config.requestUuid), {
            scope: config.serviceWorkerScope,
        });
        await navigator.serviceWorker.ready;
    } catch (err) {
        console.warn("Service worker registration failed:", err);
    }
}

async function warmPyodideCache(config) {
    if (!config.enableServiceWorker || !config.warmPyodideCache) {
        return;
    }
    if (typeof caches === "undefined") {
        return;
    }
    const base = ensureTrailingSlash(config.pyodideBaseUrl);
    const resources = [
        `${base}pyodide.js`,
        `${base}pyodide.asm.js`,
        `${base}pyodide.asm.wasm`,
        `${base}pyodide-lock.json`,
        `${base}python_stdlib.zip`,
    ];
    try {
        const cache = await caches.open("pytincture-preload");
        await Promise.all(
            resources.map(async url => {
                const request = new Request(withRequestUuid(url, config.requestUuid), { mode: "cors", credentials: "omit" });
                const existing = await cache.match(request);
                if (!existing) {
                    const response = await fetch(request);
                    if (response && (response.ok || response.type === "opaque")) {
                        await cache.put(request, response.clone());
                    }
                }
            }),
        );
    } catch (err) {
        console.warn("Pyodide cache warm failed:", err);
    }
}

async function ensurePyodideLoaded(config) {
    if (typeof loadPyodide !== "function") {
        window.languagePluginUrl = config.pyodideBaseUrl;
        await loadScript(`${config.pyodideBaseUrl}pyodide.js`, config.requestUuid);
    }
    if (typeof _createPyodideModule !== "function") {
        await loadScript(`${config.pyodideBaseUrl}pyodide.asm.js`, config.requestUuid);
    }
}

async function installExtraMicropipLibs(pyodide, selector) {
    if (!selector) {
        return;
    }
    const script = document.querySelector(selector);
    if (!script) {
        return;
    }
    let libs = [];
    try {
        libs = JSON.parse(script.textContent || script.text || "[]");
    } catch (err) {
        throw new Error(`micropip-libs must contain valid JSON: ${sanitizeDiagnostic(err.message)}`);
    }
    if (!Array.isArray(libs) || libs.some(lib => typeof lib !== "string" || !lib.trim())) {
        throw new Error("micropip-libs must be a JSON array of non-empty package strings.");
    }

    for (const lib of libs) {
        const libLiteral = JSON.stringify(lib);
        await withoutCacheBusting(() => pyodide.runPythonAsync(`
import micropip
await micropip.install(${libLiteral})
        `));
    }
}

async function urlExists(url) {
    try {
        const response = await fetch(url, { method: "HEAD" });
        return response.ok;
    } catch (err) {
        console.warn(`Failed to check URL: ${url}`, err);
        return false;
    }
}

async function resolveBackendWidgetSources(config) {
    if (!config.application) {
        return [];
    }

    const match = (config.widgetlib || "").match(/^[A-Za-z0-9_.\-]+/);
    const widgetPackage = match ? match[0] : DEFAULT_CONFIG.widgetlib;
    const pinnedMatch = (config.widgetlib || "").match(
        /^[A-Za-z0-9_.\-]+==([A-Za-z0-9_.+!\-]+)$/,
    );
    const candidateVersions = [];

    // Prefer the deployed wheel matching the requested widgetset version.
    if (pinnedMatch) {
        candidateVersions.push(pinnedMatch[1]);
    }
    // The development wheel is the final backend fallback.
    if (!candidateVersions.includes(config.devWheelVersion)) {
        candidateVersions.push(config.devWheelVersion);
    }

    const sources = [];
    for (const version of candidateVersions) {
        let widgetUrl = `${config.devWidgetHost}/${config.application}/appcode/${widgetPackage}-${version}-py3-none-any.whl`;
        widgetUrl = withRequestUuid(widgetUrl, config.requestUuid);
        sources.push(widgetUrl);
    }
    return sources;
}

async function downloadPackagedApp(config) {
    const archiveUrl = withRequestUuid(`${config.application}/appcode/appcode.pyt`, config.requestUuid);
    const response = await fetch(archiveUrl);
    const correlationId = response.headers?.get?.("x-request-id") || null;
    if (!response.ok) {
        throw new PytinctureLifecycleError({
            stage: LIFECYCLE_STAGES.ARCHIVE_DOWNLOAD,
            code: [404, 410].includes(response.status) ? "package_unavailable" : "archive_download_failed",
            resource: archiveUrl,
            requestId: config.requestUuid,
            correlationId,
            cause: `HTTP ${response.status}`,
        });
    }
    try {
        return {
            binary: await response.arrayBuffer(),
            resource: archiveUrl,
            correlationId,
        };
    } catch (error) {
        throw new PytinctureLifecycleError({
            stage: LIFECYCLE_STAGES.ARCHIVE_DOWNLOAD,
            code: "archive_read_failed",
            resource: archiveUrl,
            requestId: config.requestUuid,
            correlationId,
            cause: error,
        });
    }
}

function unpackPackagedApp(pyodide, downloaded) {
    pyodide.unpackArchive(downloaded.binary, "zip");
}

async function executePackagedApp(pyodide, config) {
    const entrypoint = config.entrypoint || config.application;
    await pyodide.runPythonAsync(`from ${config.application} import ${entrypoint} as app\napp()`);
}

async function runInlineApp(pyodide, config) {
    const scripts = Array.from(document.querySelectorAll(config.inlineSelector));
    if (!scripts.length) {
        return false;
    }

    const appDir = "/appcode";
    const dirInfo = pyodide.FS.analyzePath(appDir);
    if (!dirInfo.exists) {
        pyodide.FS.mkdir(appDir);
    }

    scripts.forEach((script, index) => {
        const filename = script.getAttribute("data-filename") || (index === 0 ? "__init__.py" : `module_${index}.py`);
        const content = script.textContent || script.text || "";
        pyodide.FS.writeFile(`${appDir}/${filename}`, content);
    });

    const entrypointLiteral = config.entrypoint ? JSON.stringify(config.entrypoint) : "None";
    const runner = `
import sys
import importlib
import inspect
import traceback

sys.path.insert(0, '/')
module_name = 'appcode'
entrypoint_name = ${entrypointLiteral}

def find_main_window(module):
    for name, obj in inspect.getmembers(module):
        if inspect.isclass(obj):
            try:
                bases = getattr(obj, '__bases__', ())
            except Exception:
                bases = ()
            if any(base.__name__ == 'MainWindow' for base in bases):
                return name
    return None

try:
    module = importlib.import_module(module_name)
    target_name = entrypoint_name or find_main_window(module)
    if not target_name:
        raise RuntimeError("No MainWindow subclass or entrypoint found in inline scripts.")
    target = getattr(module, target_name)
    if inspect.isclass(target):
        target()
    else:
        target()
except Exception as exc:
    print("Error running inline application:", exc)
    print("".join(traceback.format_exception(exc)))
    raise
    `;

    await pyodide.runPythonAsync(runner);
    return true;
}

async function installWidgetsetSource(pyodide, source, cacheBust = false) {
    // Only wheels served by this Pytincture backend share the server-instance
    // cache namespace. PyPI and user-supplied micropip sources stay canonical.
    const installSource = cacheBust ? withRequestUuid(source, activeRequestUuid) : source;
    const sourceLiteral = JSON.stringify(installSource);
    await withoutCacheBusting(() => pyodide.runPythonAsync(`
import micropip
await micropip.install(${sourceLiteral})
    `));
}

async function installWidgetset(pyodide, config) {
    const primarySource = config.widgetSource || config.widgetlib;
    if (!primarySource) {
        return null;
    }

    let installedSource = null;
    let lastInstallError = null;
    try {
        // PyPI (or an explicit widgetSource) remains the primary source.
        await installWidgetsetSource(pyodide, primarySource);
        installedSource = primarySource;
    } catch (error) {
        lastInstallError = error;
        if (config.widgetSource) {
            throw error;
        }
        console.info(
            `Widgetset ${primarySource} is not available from PyPI; checking backend wheels.`,
        );
    }

    if (!installedSource) {
        // Backend order is the real pinned version first, then 99.99.99.
        const backendSources = await resolveBackendWidgetSources(config);
        for (const source of backendSources) {
            if (!(await urlExists(source))) {
                continue;
            }
            try {
                await installWidgetsetSource(pyodide, source, true);
                installedSource = source;
                break;
            } catch (error) {
                lastInstallError = error;
                console.warn(`Failed to install widgetset from ${sanitizeResource(source)}.`);
            }
        }
    }

    if (!installedSource) {
        throw lastInstallError || new Error(`No installable widgetset source found for ${primarySource}.`);
    }
    return installedSource;
}

async function loadWidgetsetAssets(pyodide, config, installedSource) {
    if (!installedSource) {
        return { installedSource: null, javascriptAssets: 0, cssAssets: 0 };
    }
    const requestUuidLiteral = JSON.stringify(config.requestUuid);
    const widgetPackageLiteral = JSON.stringify(
        String(config.widgetlib || "").match(/^[A-Za-z0-9_.\-]+/)?.[0] || "",
    );
    const loadFilesCode = `
import os
import pyodide
import js
import site
import base64
import re
import json
import importlib.metadata

REQUEST_UUID = ${requestUuidLiteral}

def cache_bust_url(url_value):
    if not url_value or not REQUEST_UUID:
        return url_value
    if url_value.startswith(('data:', 'blob:', '#')):
        return url_value
    base, separator, fragment = url_value.partition('#')
    encoded_uuid = str(REQUEST_UUID)
    if re.search(r'(^|[?&])uuid=', base):
        base = re.sub(
            r'([?&])uuid=[^&]*',
            lambda match: match.group(1) + 'uuid=' + encoded_uuid,
            base,
        )
    else:
        base = base + ('&' if '?' in base else '?') + 'uuid=' + encoded_uuid
    return f"{base}{separator}{fragment}"

def replace_font_urls(css_content, search_dirs):
    if not search_dirs:
        return css_content

    mime_types = {
        'woff': 'font/woff',
        'woff2': 'font/woff2',
        'ttf': 'font/ttf',
        'otf': 'font/otf',
        'eot': 'application/vnd.ms-fontobject',
    }

    def try_inline(url_value, base_dir):
        if not url_value:
            return None
        if url_value.startswith('data:'):
            return None
        if re.match(r'^(https?:)?//', url_value):
            return None
        path = url_value
        if '?' in path:
            path = path.split('?', 1)[0]
        if '#' in path:
            path = path.split('#', 1)[0]
        path = path.lstrip('/')
        candidate = os.path.normpath(os.path.join(base_dir, path))
        if not os.path.isfile(candidate):
            return None
        ext = os.path.splitext(candidate)[1].lstrip('.').lower()
        if ext not in mime_types:
            return None
        try:
            with open(candidate, "rb") as f:
                font_data = base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            return None
        return f"url(data:{mime_types[ext]};base64,{font_data})"

    def repl(match):
        raw = match.group(1)
        if not raw:
            return match.group(0)
        cleaned = raw.strip().strip("'").strip('"')
        for base_dir in search_dirs:
            data_uri = try_inline(cleaned, base_dir)
            if data_uri:
                return f"url('{data_uri[4:-1]}')" if data_uri.startswith('url(') else data_uri
        return f"url('{cache_bust_url(cleaned)}')"

    return re.sub(r"url\(([^)]+)\)", repl, css_content, flags=re.IGNORECASE)

package_path = site.getsitepackages()[0]
javascript_assets = 0
css_assets = 0

for root, _, files in os.walk(package_path):
    for file in files:
        file_path = os.path.join(root, file)
        file_extension = os.path.splitext(file_path)[1].lower()
        if file_extension == '.js':
            with open(file_path) as f:
                js.eval(f.read())
            javascript_assets += 1
        elif file_extension == '.css':
            with open(file_path) as f:
                css_dir = os.path.dirname(file_path)
                search_dirs = [css_dir, os.path.join(css_dir, "fonts")]
                style_content = replace_font_urls(f.read(), search_dirs)
            style = js.document.createElement('style')
            style.innerHTML = style_content
            js.document.head.appendChild(style)
            css_assets += 1

widget_package = ${widgetPackageLiteral}
try:
    widget_version = importlib.metadata.version(widget_package) if widget_package else None
except importlib.metadata.PackageNotFoundError:
    widget_version = None

json.dumps({
    "widgetPackage": widget_package or None,
    "widgetVersion": widget_version,
    "javascriptAssets": javascript_assets,
    "cssAssets": css_assets,
    "dhxAvailable": bool(hasattr(js, "dhx")),
})
    `;
    const rawReport = await pyodide.runPythonAsync(loadFilesCode);
    const report = typeof rawReport === "string" ? JSON.parse(rawReport) : rawReport;
    if (report?.widgetPackage?.replace(/-/g, "_") === "dhxpyt" && !report.dhxAvailable) {
        throw new Error("dhxpyt installed but its DHTMLX JavaScript assets did not expose window.dhx.");
    }
    return { installedSource: sanitizeResource(installedSource) || installedSource, ...report };
}

const DEFAULT_RUNTIME_OPERATIONS = Object.freeze({
    preflightConfig,
    ensureServiceWorker,
    ensureMaterialIcons,
    warmPyodideCache,
    ensurePyodideLoaded,
    loadPyodideRuntime: options => globalThis.loadPyodide(options),
    preflightPyodide,
    installExtraMicropipLibs,
    installWidgetset,
    loadWidgetsetAssets,
    downloadPackagedApp,
    unpackPackagedApp,
    executePackagedApp,
    runInlineApp,
});

async function runStartup(config, loadingOverlay, operations = DEFAULT_RUNTIME_OPERATIONS) {
    updateLoadingStatus(loadingOverlay, "Checking compatibility…");
    const configReport = await runLifecycleStage(
        config,
        LIFECYCLE_STAGES.PREFLIGHT,
        config.pyodideBaseUrl,
        () => operations.preflightConfig(config),
    );

    await operations.ensureServiceWorker(config);
    if (config.loadMaterialIcons) {
        operations.ensureMaterialIcons(config.materialIconsUrl, config.requestUuid);
    }
    Promise.resolve(operations.warmPyodideCache(config)).catch(error => {
        console.warn("Pyodide cache warm failed:", sanitizeDiagnostic(error?.message || error));
    });

    updateLoadingStatus(loadingOverlay, "Loading Pyodide…");
    const runtimeResult = await runLifecycleStage(
        config,
        LIFECYCLE_STAGES.RUNTIME_LOAD,
        `${config.pyodideBaseUrl}pyodide.js`,
        async () => {
            await operations.ensurePyodideLoaded(config);
            const pyodide = await operations.loadPyodideRuntime({ indexURL: config.pyodideBaseUrl });
            const report = await operations.preflightPyodide(pyodide);
            return { pyodide, report };
        },
    );
    const { pyodide } = runtimeResult;

    updateLoadingStatus(loadingOverlay, "Installing packages…");
    await runLifecycleStage(
        config,
        LIFECYCLE_STAGES.PACKAGE_INSTALL,
        "micropip",
        async () => {
            await pyodide.loadPackage("micropip");
            await withoutCacheBusting(() => pyodide.runPythonAsync(`
import micropip
await micropip.install("python-dotenv")
            `));
            await operations.installExtraMicropipLibs(pyodide, config.libsSelector);
        },
    );

    updateLoadingStatus(loadingOverlay, "Installing widgetset…");
    const installedSource = await runLifecycleStage(
        config,
        LIFECYCLE_STAGES.WIDGETSET_INSTALL,
        config.widgetSource || config.widgetlib,
        () => operations.installWidgetset(pyodide, config),
    );
    updateLoadingStatus(loadingOverlay, "Loading widget assets…");
    const widgetReport = await runLifecycleStage(
        config,
        LIFECYCLE_STAGES.WIDGETSET_LOAD,
        installedSource,
        () => operations.loadWidgetsetAssets(pyodide, config, installedSource),
    );
    emitLifecycleEvent(config, "compatibility", LIFECYCLE_STAGES.WIDGETSET_LOAD, {
        compatibility: { ...configReport, ...runtimeResult.report, ...widgetReport },
    });

    updateLoadingStatus(loadingOverlay, "Starting app…");
    if (config.mode === "inline") {
        await runLifecycleStage(
            config,
            LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
            config.inlineSelector,
            async () => {
                if (!(await operations.runInlineApp(pyodide, config))) {
                    throw new PytinctureLifecycleError({
                        stage: LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
                        code: "inline_app_unavailable",
                        resource: config.inlineSelector,
                        requestId: config.requestUuid,
                        cause: "No inline application scripts were found.",
                    });
                }
            },
        );
    } else if (config.mode === "package" || config.application) {
        let downloaded;
        try {
            downloaded = await runLifecycleStage(
                config,
                LIFECYCLE_STAGES.ARCHIVE_DOWNLOAD,
                `${config.application}/appcode/appcode.pyt`,
                () => operations.downloadPackagedApp(config),
            );
        } catch (error) {
            if (!(error instanceof PytinctureLifecycleError)
                || error.code !== "package_unavailable"
                || config.mode === "package") {
                throw error;
            }
            emitLifecycleEvent(config, "fallback", LIFECYCLE_STAGES.ARCHIVE_DOWNLOAD, {
                from: "package",
                to: "inline",
                reason: error.toJSON(),
            });
            await runLifecycleStage(
                config,
                LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
                config.inlineSelector,
                async () => {
                    if (!(await operations.runInlineApp(pyodide, config))) {
                        throw new PytinctureLifecycleError({
                            stage: LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
                            code: "inline_app_unavailable",
                            resource: config.inlineSelector,
                            requestId: config.requestUuid,
                            cause: "The package was unavailable and no inline application scripts were found.",
                        });
                    }
                },
            );
            downloaded = null;
        }
        if (downloaded) {
            await runLifecycleStage(
                config,
                LIFECYCLE_STAGES.ARCHIVE_UNPACK,
                downloaded.resource,
                () => operations.unpackPackagedApp(pyodide, downloaded),
                { correlationId: downloaded.correlationId },
            );
            await runLifecycleStage(
                config,
                LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
                `${config.application}:${config.entrypoint || config.application}`,
                () => operations.executePackagedApp(pyodide, config),
                { correlationId: downloaded.correlationId },
            );
        }
    } else {
        await runLifecycleStage(
            config,
            LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
            config.inlineSelector,
            async () => {
                if (!(await operations.runInlineApp(pyodide, config))) {
                    throw new PytinctureLifecycleError({
                        stage: LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
                        code: "application_unavailable",
                        resource: config.inlineSelector,
                        requestId: config.requestUuid,
                        cause: "No packaged or inline application was available.",
                    });
                }
            },
        );
    }

    emitLifecycleEvent(config, "ready", LIFECYCLE_STAGES.READY, {
        compatibility: { ...configReport, ...runtimeResult.report, ...widgetReport },
    });
    return pyodide;
}

async function runTinctureApp(arg1, widgetlib, entrypoint) {
    const config = normalizeConfig(arg1, widgetlib, entrypoint);
    installCacheBustingFetch(config.requestUuid);
    const loadingOverlay = ensureLoadingOverlay(config);

    if (config.enableBackendLogging) {
        enableBackendLogging(config.logEndpoint);
    }
    try {
        const pyodide = await runStartup(config, loadingOverlay);
        removeLoadingOverlay(loadingOverlay);
        return pyodide;
    } catch (error) {
        const lifecycleError = error instanceof PytinctureLifecycleError
            ? error
            : new PytinctureLifecycleError({
                stage: LIFECYCLE_STAGES.PREFLIGHT,
                requestId: config.requestUuid,
                cause: error,
            });
        updateLoadingStatus(loadingOverlay, `Failed during ${lifecycleError.stage}. Check console for details.`);
        throw lifecycleError;
    }
}

if (typeof window !== "undefined") {
    window.runTinctureApp = runTinctureApp;
    window.PytinctureLifecycleError = PytinctureLifecycleError;
}

function autoStartInlineApp() {
    if (typeof window === "undefined") {
        return;
    }
    if (window.pytinctureAutoStartDisabled) {
        return;
    }
    const inlineScripts = document.querySelectorAll(DEFAULT_CONFIG.inlineSelector);
    if (!inlineScripts.length) {
        return;
    }
    const inlineConfig = {
        mode: "inline",
        enableBackendLogging: false,
        ...(window.pytinctureAutoStartConfig || {}),
    };
    runTinctureApp(inlineConfig).catch(error => {
        console.error("Auto-start inline app failed:", error);
        const container = document.getElementById("maindiv");
        if (container) {
            container.innerHTML = `<p style="color: red;">Error: ${error.message}</p>`;
        }
    });
}

if (typeof document !== "undefined") {
    if (document.readyState === "complete" || document.readyState === "interactive") {
        setTimeout(autoStartInlineApp, 0);
    } else {
        document.addEventListener("DOMContentLoaded", autoStartInlineApp);
    }
}

globalThis.__pytinctureTesting = Object.freeze({
    DEFAULT_RUNTIME_OPERATIONS,
    LIFECYCLE_STAGES,
    PytinctureLifecycleError,
    normalizeConfig,
    runLifecycleStage,
    runStartup,
    runTinctureApp,
});
