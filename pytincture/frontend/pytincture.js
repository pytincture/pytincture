const FALLBACK_DEV_WIDGET_HOST = "http://127.0.0.1:8070";

const DEFAULT_CONFIG = {
    application: null,
    entrypoint: null,
    widgetlib: "dhxpyt",
    widgetSource: null,
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
};

let loggingInstalled = false;
const originalConsoleMethods = {};

function ensureTrailingSlash(value) {
    if (!value) {
        return "/";
    }
    return value.endsWith("/") ? value : `${value}/`;
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
        merged.entrypoint = merged.entrypoint || merged.application;
        merged.devWidgetHost = resolveDevWidgetHost(merged.devWidgetHost);
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
    config.devWidgetHost = resolveDevWidgetHost(config.devWidgetHost);
    config.enableBackendLogging = !!application;
    if (config.application && (config.pyodideBaseUrl.startsWith("frontend/") || config.pyodideBaseUrl.startsWith("./frontend/"))) {
        const cleanPath = config.pyodideBaseUrl.replace(/^\.\//, "");
        config.pyodideBaseUrl = ensureTrailingSlash(`${config.application}/${cleanPath}`);
    }
    return config;
}

function loadScript(url) {
    return new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = url;
        script.onload = resolve;
        script.onerror = () => reject(new Error(`Failed to load script: ${url}`));
        document.head.appendChild(script);
    });
}

function ensureMaterialIcons(url) {
    if (!url) {
        return;
    }
    const existing = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).some(link => link.href === url);
    if (existing) {
        return;
    }
    const link = document.createElement("link");
    link.href = url;
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
        fetch(logEndpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
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
        await navigator.serviceWorker.register(config.serviceWorkerUrl, {
            scope: config.serviceWorkerScope,
        });
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
                const request = new Request(url, { mode: "cors", credentials: "omit" });
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
    if (typeof loadPyodide === "function") {
        return;
    }
    window.languagePluginUrl = config.pyodideBaseUrl;
    await loadScript(`${config.pyodideBaseUrl}pyodide.js`);
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
        console.warn("Failed to parse micropip-libs JSON:", err);
        return;
    }

    for (const lib of libs) {
        const libLiteral = JSON.stringify(lib);
        await pyodide.runPythonAsync(`
import micropip
await micropip.install(${libLiteral})
        `);
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

async function resolveWidgetSource(config) {
    if (config.widgetSource) {
        return config.widgetSource;
    }
    if (config.application) {
        const match = (config.widgetlib || "").match(/^[A-Za-z0-9_\-]+/);
        const widgetPackage = match ? match[0] : DEFAULT_CONFIG.widgetlib;
        const widgetUrl = `${config.devWidgetHost}/${config.application}/appcode/${widgetPackage}-${config.devWheelVersion}-py3-none-any.whl`;
        if (await urlExists(widgetUrl)) {
            return widgetUrl;
        }
    }
    return config.widgetlib;
}

async function runPackagedApp(pyodide, config) {
    if (!config.application) {
        throw new Error("No application supplied for packaged mode.");
    }
    const archiveUrl = `${config.application}/appcode/appcode.pyt`;
    const response = await fetch(archiveUrl);
    if (!response.ok) {
        throw new Error(`Failed to fetch packaged app from ${archiveUrl}`);
    }
    const appBinary = await response.arrayBuffer();
    pyodide.unpackArchive(appBinary, "zip");
    const entrypoint = config.entrypoint || config.application;
    pyodide.runPython(`from ${config.application} import ${entrypoint} as app\napp()`);
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

async function installAndLoadWidgetset(pyodide, widgetlib) {
    if (!widgetlib) {
        return;
    }
    const escapedLib = widgetlib.replace(/'/g, "\\'");
    try {
        await pyodide.runPythonAsync(`
import micropip
await micropip.install("python-dotenv")
await micropip.install('${escapedLib}')
        `);

        const loadFilesCode = `
import os
import pyodide
import js
import site
import base64
import re

def replace_font_urls(css_content, font_folder_path):
    if not os.path.isdir(font_folder_path):
        return css_content

    font_files = [f for f in os.listdir(font_folder_path) if os.path.isfile(os.path.join(font_folder_path, f))]

    for font_file in font_files:
        font_extension = font_file.split('.')[-1].lower()
        if font_extension not in ['woff', 'woff2']:
            continue
        font_file_path = os.path.join(font_folder_path, font_file)
        with open(font_file_path, "rb") as f:
            font_data = base64.b64encode(f.read()).decode("utf-8")
        mime_type = f"font/{font_extension}"
        css_content = re.sub(
            rf"""url\\(['"]?\\./fonts/{re.escape(font_file)}['"]?\\)""",
            f"url(data:{mime_type};charset=utf-8;base64,{font_data})",
            css_content
        )
    return css_content

package_path = site.getsitepackages()[0]

for root, _, files in os.walk(package_path):
    for file in files:
        file_path = os.path.join(root, file)
        file_extension = os.path.splitext(file_path)[1].lower()
        if file_extension == '.js':
            with open(file_path) as f:
                js.eval(f.read())
        elif file_extension == '.css':
            with open(file_path) as f:
                style_content = replace_font_urls(f.read(), os.path.join(os.path.dirname(file_path), "fonts"))
            style = js.document.createElement('style')
            style.innerHTML = style_content
            js.document.head.appendChild(style)
        `;
        await pyodide.runPythonAsync(loadFilesCode);
    } catch (error) {
        console.error(`Error installing and loading ${widgetlib}:`, error);
    }
}

async function runTinctureApp(arg1, widgetlib, entrypoint) {
    const config = normalizeConfig(arg1, widgetlib, entrypoint);
    const loadingOverlay = ensureLoadingOverlay(config);

    if (config.enableBackendLogging) {
        enableBackendLogging(config.logEndpoint);
    }
    if (config.loadMaterialIcons) {
        ensureMaterialIcons(config.materialIconsUrl);
    }

    try {
        updateLoadingStatus(loadingOverlay, "Preparing runtime…");
        await ensureServiceWorker(config);
        warmPyodideCache(config);
        updateLoadingStatus(loadingOverlay, "Loading Pyodide…");
        await ensurePyodideLoaded(config);

        const pyodide = await loadPyodide({ indexURL: config.pyodideBaseUrl });
        updateLoadingStatus(loadingOverlay, "Loading micropip…");
        await pyodide.loadPackage("micropip");
        updateLoadingStatus(loadingOverlay, "Installing extra packages…");
        await installExtraMicropipLibs(pyodide, config.libsSelector);

        updateLoadingStatus(loadingOverlay, "Loading widgetset…");
        const widgetSource = await resolveWidgetSource(config);
        await installAndLoadWidgetset(pyodide, widgetSource);

        updateLoadingStatus(loadingOverlay, "Starting app…");
        if (config.mode === "inline") {
            await runInlineApp(pyodide, config);
            removeLoadingOverlay(loadingOverlay);
            return;
        }

        if (config.mode === "package" || config.application) {
            try {
                await runPackagedApp(pyodide, config);
                removeLoadingOverlay(loadingOverlay);
                return;
            } catch (err) {
                console.error("Failed to run packaged app:", err);
                if (config.mode === "package") {
                    throw err;
                }
                console.warn("Falling back to inline mode.");
            }
        }

        const inlineStarted = await runInlineApp(pyodide, config);
        if (!inlineStarted) {
            throw new Error("No application could be started: packaged app missing and no inline scripts found.");
        }
        removeLoadingOverlay(loadingOverlay);
    } catch (err) {
        updateLoadingStatus(loadingOverlay, "Failed to start. Check console for details.");
        throw err;
    }
}

window.runTinctureApp = runTinctureApp;

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

if (document.readyState === "complete" || document.readyState === "interactive") {
    setTimeout(autoStartInlineApp, 0);
} else {
    document.addEventListener("DOMContentLoaded", autoStartInlineApp);
}
