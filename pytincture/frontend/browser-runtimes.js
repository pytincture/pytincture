/* Experimental portable browser applications. Pyodide's existing package path
 * remains in pytincture.js; these adapters use an explicit client-only bundle. */
export const BROWSER_RUNTIMES = Object.freeze(["pyodide", "micropython"]);

function relativePath(value) {
    if (typeof value !== "string" || !/^[A-Za-z0-9_@./-]+$/.test(value)
        || value.split("/").some(part => !part || part.startsWith("."))) {
        throw new Error(`Invalid browser bundle path: ${value}`);
    }
    return value;
}

export function sameOriginUrl(value, base) {
    const url = new URL(value, base);
    if (url.origin !== new URL(base).origin || url.username || url.password
        || !["http:", "https:"].includes(url.protocol)) {
        throw new Error("Browser runtime assets must use the application origin");
    }
    return url.href;
}

export function validateRuntimeManifest(manifest, engine) {
    if (!BROWSER_RUNTIMES.includes(engine)) throw new Error(`Unknown browser runtime: ${engine}`);
    if (manifest?.schema !== 1 || !Array.isArray(manifest.runtimes)
        || !manifest.runtimes.length || new Set(manifest.runtimes).size !== manifest.runtimes.length
        || manifest.runtimes.some(name => !BROWSER_RUNTIMES.includes(name))) {
        throw new Error("Invalid browser runtime manifest");
    }
    if (!manifest.runtimes.includes(engine)) throw new Error(`Application does not support ${engine}`);
    relativePath(manifest.host);
    for (const kind of ["scripts", "styles"]) {
        if (!Array.isArray(manifest[kind]) || manifest[kind].length > 64) {
            throw new Error(`Manifest ${kind} must be an array of at most 64 paths`);
        }
        manifest[kind].forEach(relativePath);
    }
    relativePath(manifest.sources);
    if (!/^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$/.test(manifest.entrypoint || "")) {
        throw new Error("Manifest entrypoint must be a Python module name");
    }
    if (engine === "micropython") {
        relativePath(manifest.micropython?.module);
        relativePath(manifest.micropython?.wasm);
        const heap = manifest.micropython.heapBytes ?? 8 * 1024 * 1024;
        if (!Number.isInteger(heap) || heap < 1024 * 1024 || heap > 128 * 1024 * 1024) {
            throw new Error("MicroPython heapBytes must be between 1 and 128 MiB");
        }
    }
    return manifest;
}

async function fetchJson(url) {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) throw new Error(`Browser bundle request failed (${response.status}): ${url}`);
    return response.json();
}

function loadAsset(url, stylesheet = false) {
    return new Promise((resolve, reject) => {
        const node = document.createElement(stylesheet ? "link" : "script");
        if (stylesheet) { node.rel = "stylesheet"; node.href = url; }
        else { node.src = url; node.async = false; }
        node.onload = () => resolve();
        node.onerror = () => reject(new Error(`Unable to load browser asset: ${url}`));
        document.head.appendChild(node);
    });
}

function bffRequest(config, module, className, method, args, options = {}) {
    if (!/^[A-Za-z_]\w*$/.test(config.application || "")) throw new Error("A BFF application is required");
    if (!/^[A-Za-z_]\w*(\/[A-Za-z_]\w*)*$/.test(module)
        || !/^[A-Za-z_]\w*$/.test(className) || !/^[A-Za-z_]\w*$/.test(method)) {
        throw new Error("Invalid BFF target");
    }
    const cookies = document.cookie.split(";").map(value => value.trim());
    const cookieName = config.csrfCookieName || "pytincture-dev-csrf";
    const cookie = cookies.find(value => value.startsWith(`${cookieName}=`));
    const csrf = cookie ? decodeURIComponent(cookie.slice(cookieName.length + 1)) : "";
    const httpMethod = options.method || "POST";
    if (!["POST", "GET"].includes(httpMethod)) throw new Error("Unsupported browser BFF HTTP method");
    return {
        url: `/${config.application}/classcall/${module}/${className}/${method}`,
        init: {
            method: httpMethod, credentials: "same-origin",
            headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
            ...(httpMethod === "GET" ? {} : {body: JSON.stringify(args)}),
        },
    };
}

export function createBffCaller(config) {
    if (!/^[A-Za-z_]\w*$/.test(config.application || "")) throw new Error("A BFF application is required");
    return async (module, className, method, args = {}, options = {}) => {
        const request = bffRequest(config, module, className, method, args, options);
        const response = await fetch(request.url, {...request.init, signal: AbortSignal.timeout(35000)});
        if (!response.ok) throw new Error(`BFF ${className}.${method} failed (${response.status})`);
        return response.json();
    };
}

export function createBffSyncCaller(config) {
    return (module, className, method, args = {}, options = {}) => {
        const {url, init} = bffRequest(config, module, className, method, args, options);
        const request = new XMLHttpRequest();
        request.open(init.method, url, false);
        for (const [name, value] of Object.entries(init.headers)) request.setRequestHeader(name, value);
        request.send(init.body ?? null);
        if (request.status < 200 || request.status >= 300) throw new Error(`BFF ${className}.${method} failed (${request.status})`);
        return JSON.parse(request.responseText);
    };
}

export function createBffStreamCaller(config) {
    return async (module, className, method, args = {}, options = {}) => {
        const {url, init} = bffRequest(config, module, className, method, args, options);
        const controller = new AbortController();
        const response = await fetch(url, {...init, signal: controller.signal});
        if (!response.ok) { controller.abort(); throw new Error(`BFF ${className}.${method} failed (${response.status})`); }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "", done = false, closed = false;
        const close = async () => {
            if (closed) return;
            closed = true;
            try { await reader.cancel(); } finally { controller.abort(); reader.releaseLock(); }
        };
        return {
            close,
            async next() {
                try {
                    while (!closed) {
                        if (!options.raw) {
                            const newline = buffer.indexOf("\n");
                            if (newline >= 0 || done && buffer.trim()) {
                                const line = newline >= 0 ? buffer.slice(0, newline) : buffer;
                                buffer = newline >= 0 ? buffer.slice(newline + 1) : "";
                                if (line.trim()) return JSON.stringify({done: false, value: JSON.parse(line)});
                                continue;
                            }
                        }
                        if (done) { await close(); break; }
                        const chunk = await reader.read();
                        done = chunk.done;
                        const text = decoder.decode(chunk.value, {stream: !done});
                        if (options.raw) {
                            if (text) return JSON.stringify({done: false, value: text});
                        } else {
                            buffer += text;
                            if (buffer.length > 8 * 1024 * 1024) throw new Error("BFF stream record exceeds 8 MiB");
                        }
                    }
                    return JSON.stringify({done: true});
                } catch (error) { await close(); throw error; }
            },
        };
    };
}

function installSources(runtime, bundle) {
    const files = bundle?.files;
    if (!files || typeof files !== "object" || Array.isArray(files)
        || Object.keys(files).length > 256) throw new Error("Invalid Python source bundle");
    let size = 0;
    for (const [path, source] of Object.entries(files)) {
        relativePath(path);
        if (!path.endsWith(".py") || typeof source !== "string") throw new Error("Invalid Python source file");
        size += new TextEncoder().encode(source).length;
        if (size > 8 * 1024 * 1024) throw new Error("Python source bundle exceeds 8 MiB");
        const directory = path.slice(0, path.lastIndexOf("/"));
        if (path.includes("/")) runtime.FS.mkdirTree(`/${directory}`);
        runtime.FS.writeFile(`/${path}`, source);
    }
}

export async function runBrowserApplication(config, status = () => {}) {
    const engine = config.runtime || "pyodide";
    if (!config.runtimeManifestUrl) throw new Error(`${engine} requires an application runtime manifest`);
    const manifestUrl = sameOriginUrl(config.runtimeManifestUrl, location.href);
    status(`Preparing ${engine}…`);
    const manifest = validateRuntimeManifest(await fetchJson(manifestUrl), engine);
    const asset = path => sameOriginUrl(relativePath(path), manifestUrl);
    await Promise.all(manifest.styles.map(path => loadAsset(asset(path), true)));
    for (const path of manifest.scripts) await loadAsset(asset(path));

    let invoke;
    let capturedOutput = null;
    let consoleOutput = [];
    let queue = Promise.resolve();
    const handle = {
        engine, runtime: null,
        call(name, payload) {
            if (!/^[A-Za-z_]\w*$/.test(name)) return Promise.reject(new Error("Invalid client function name"));
            queue = queue.catch(() => {}).then(() => {
                if (!invoke) throw new Error("Browser runtime is not ready");
                return invoke(name, payload);
            });
            return queue;
        },
    };
    const host = await import(asset(manifest.host));
    if (typeof host.setup !== "function") throw new Error("Browser host must export setup(context)");
    await host.setup({
        engine, runtimes: [...manifest.runtimes], application: config.application,
        callBff: createBffCaller(config), callBffSync: createBffSyncCaller(config),
        streamBff: createBffStreamCaller(config), invoke: handle.call, assetUrl: asset,
    });

    status(`Loading ${engine}…`);
    let runtime;
    if (engine === "micropython") {
        const { loadMicroPython } = await import(asset(manifest.micropython.module));
        runtime = await loadMicroPython({
            url: asset(manifest.micropython.wasm),
            heapsize: manifest.micropython.heapBytes ?? 8 * 1024 * 1024,
            linebuffer: false,
            stdout: bytes => {
                if (capturedOutput !== null) {
                    if (capturedOutput.length <= 1024 * 1024) capturedOutput.push(...bytes);
                } else {
                    for (const byte of bytes) {
                        if (byte === 10) {
                            console.log(new TextDecoder().decode(Uint8Array.from(consoleOutput)));
                            consoleOutput = [];
                        } else consoleOutput.push(byte);
                    }
                }
            },
        });
    } else {
        const base = sameOriginUrl(config.pyodideBaseUrl, location.href);
        if (typeof globalThis.loadPyodide !== "function") await loadAsset(new URL("pyodide.js", base).href);
        runtime = await globalThis.loadPyodide({ indexURL: base });
    }
    handle.runtime = runtime;
    // Widgets can run short synchronous Python snippets without loading a second interpreter.
    handle.captureOutput = source => {
        if (typeof source !== "string" || source.length > 1024 * 1024) throw new Error("Invalid Python snippet");
        if (engine === "micropython") {
            if (capturedOutput !== null) throw new Error("Nested output capture is not supported");
            capturedOutput = [];
            try {
                runtime.runPython(`exec(${JSON.stringify(source)}, {})`);
                if (capturedOutput.length > 1024 * 1024) throw new Error("Python output exceeds 1 MiB");
                return new TextDecoder().decode(Uint8Array.from(capturedOutput));
            } finally { capturedOutput = null; }
        }
        const program = `import io, contextlib\nwith contextlib.redirect_stdout(io.StringIO()) as output:\n    exec(${JSON.stringify(source)}, {})\noutput.getvalue()`;
        return runtime.runPython(program);
    };
    globalThis.pytinctureBrowserRuntime = handle;
    installSources(runtime, await fetchJson(asset(manifest.sources)));
    await runtime.runPythonAsync(`import sys\nsys.path.insert(0, '/')\nimport ${manifest.entrypoint} as _pytincture_client`);
    invoke = async (name, payload) => {
        if (payload !== undefined && typeof payload !== "string") throw new Error("Interpreter callback payload must be a JSON string");
        runtime.globals.set("_pytincture_payload", payload ?? "");
        await runtime.runPythonAsync(`await _pytincture_client.${name}(${payload === undefined ? "" : "_pytincture_payload"})`);
    };
    await handle.call("main");
    return handle;
}
