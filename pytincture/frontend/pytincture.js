const FALLBACK_DEV_WIDGET_HOST = "http://127.0.0.1:8070";

const DEFAULT_CONFIG = {
    application: null,
    entrypoint: null,
    widgetlib: "dhxpyt",
    widgetSource: null,
    mode: "auto", // 'package', 'inline', or 'auto'
    pyodideBaseUrl: "https://cdn.jsdelivr.net/pyodide/v0.28.0/full/",
    loadMaterialIcons: true,
    materialIconsUrl: "https://cdnjs.cloudflare.com/ajax/libs/MaterialDesign-Webfont/7.4.47/css/materialdesignicons.css",
    enableBackendLogging: false,
    logEndpoint: "/logs",
    inlineSelector: 'script[type="text/python"]',
    libsSelector: '#micropip-libs',
    devWidgetHost: null,
    devWheelVersion: "99.99.99",
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

    if (config.enableBackendLogging) {
        enableBackendLogging(config.logEndpoint);
    }
    if (config.loadMaterialIcons) {
        ensureMaterialIcons(config.materialIconsUrl);
    }

    await ensurePyodideLoaded(config);

    const pyodide = await loadPyodide({ indexURL: config.pyodideBaseUrl });
    await pyodide.loadPackage("micropip");
    await installExtraMicropipLibs(pyodide, config.libsSelector);

    const widgetSource = await resolveWidgetSource(config);
    await installAndLoadWidgetset(pyodide, widgetSource);

    if (config.mode === "inline") {
        await runInlineApp(pyodide, config);
        return;
    }

    if (config.mode === "package" || config.application) {
        try {
            await runPackagedApp(pyodide, config);
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
