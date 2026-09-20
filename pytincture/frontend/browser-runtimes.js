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
    if (manifest?.schema !== 2 || !Array.isArray(manifest.runtimes)
        || !manifest.runtimes.length || new Set(manifest.runtimes).size !== manifest.runtimes.length
        || manifest.runtimes.some(name => !BROWSER_RUNTIMES.includes(name))) {
        throw new Error("Invalid browser runtime manifest");
    }
    if (!manifest.runtimes.includes(engine)) throw new Error(`Application does not support ${engine}`);
    if (manifest.runtimeRequirements?.[engine] !== (engine === 'pyodide' ? '0.29.3' : '1.29.0-6')) throw new Error('Unsupported runtime version requirement');
    if (!Array.isArray(manifest.requiredBrowserApis) || manifest.requiredBrowserApis.some(name => typeof name !== 'string' || !/^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$/.test(name))) throw new Error('Invalid browser API requirements');
    relativePath(manifest.host);
    for (const kind of ["scripts", "styles"]) {
        if (!Array.isArray(manifest[kind]) || manifest[kind].length > 64) {
            throw new Error(`Manifest ${kind} must be an array of at most 64 paths`);
        }
        manifest[kind].forEach(relativePath);
    }
    if (manifest.styleIds && (typeof manifest.styleIds !== 'object' || Array.isArray(manifest.styleIds)
        || Object.entries(manifest.styleIds).some(([path, id]) => !manifest.styles.includes(path) || typeof id !== 'string' || !/^[A-Za-z][\w-]*$/.test(id)))) throw new Error('Invalid stylesheet identifiers');
    if (!/^[a-f0-9]{64}$/.test(manifest.bundleId || "") || manifest.assetBase !== `releases/${manifest.bundleId}/`) throw new Error("Invalid immutable bundle identifier");
    if (manifest.profile !== "pytincture-portable-1") throw new Error("Unsupported portable Python profile");
    const target = manifest.targets?.[engine];
    relativePath(target?.sources);
    relativePath(manifest.resources);
    const inventory = manifest.integrity;
    if (!inventory || Array.isArray(inventory) || Object.keys(inventory).length > 4096) throw new Error("Invalid bundle integrity inventory");
    let size = 0;
    for (const [path, entry] of Object.entries(inventory)) {
        relativePath(path);
        if (!/^[a-f0-9]{64}$/.test(entry.sha256 || "") || !Number.isSafeInteger(entry.bytes) || entry.bytes < 0 || entry.bytes > 33554432) throw new Error("Invalid bundle integrity entry");
        size += entry.bytes;
    }
    if (size > 134217728) throw new Error("Bundle exceeds 128 MiB");
    for (const path of [manifest.host, target.sources, manifest.resources, ...manifest.scripts, ...manifest.styles,
        ...(engine === "micropython" ? [manifest.micropython?.module, manifest.micropython?.wasm] : [])]) {
        if (!Object.hasOwn(inventory, path)) throw new Error(`Missing integrity lock: ${path}`);
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

async function fetchBytes(url, limit) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 35000);
    try {
        const response = await fetch(url, {credentials: "same-origin", signal: controller.signal});
        if (!response.ok) throw new Error(`Browser bundle request failed (${response.status}): ${url}`);
        const declared = response.headers.get('content-length');
        if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > limit)) throw new Error("Browser bundle response exceeds byte limit");
        // Native consumption completes the original network body. A bounded clone
        // monitors decoded bytes too, including responses compressed by a proxy.
        const reader = response.clone().body.getReader();
        const monitor = async () => {
            let size = 0;
            try {
                while (true) {
                    const {done, value} = await reader.read();
                    if (done) return;
                    size += value.byteLength;
                    if (size > limit) {
                        controller.abort();
                        throw new Error("Browser bundle response exceeds byte limit");
                    }
                }
            } finally { reader.releaseLock(); }
        };
        const [bytes] = await Promise.all([response.arrayBuffer(), monitor()]);
        return new Uint8Array(bytes);
    } catch (error) {
        controller.abort();
        throw error;
    } finally { clearTimeout(timer); }
}

const decodeJson = bytes => JSON.parse(new TextDecoder().decode(bytes));
const digest = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), b => b.toString(16).padStart(2, "0")).join("");
function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
    return value;
}

export async function verifyBundle(manifest, asset, engine = null) {
    const {bundleId, assetBase, ...identity} = manifest;
    if (await digest(new TextEncoder().encode(JSON.stringify(canonical(identity))+"\n")) !== bundleId) throw new Error("Bundle identifier does not match manifest contents");
    const excluded = new Set(engine ? ['build.json', 'compatibility.json'] : []);
    if (engine) {
        for (const [target, options] of Object.entries(manifest.targets || {})) {
            if (target !== engine) excluded.add(options.sources);
        }
        if (engine === 'pyodide') {
            excluded.add(manifest.micropython?.module);
            excluded.add(manifest.micropython?.wasm);
        }
    }
    const entries = Object.entries(manifest.integrity).filter(([path]) => !excluded.has(path));
    const contents = new Map();
    let cursor = 0;
    await Promise.all(Array.from({length: 4}, async () => {
        while (cursor < entries.length) {
            const [path, entry] = entries[cursor++];
            const bytes = await fetchBytes(asset(path), entry.bytes);
            if (bytes.length !== entry.bytes || await digest(bytes) !== entry.sha256) throw new Error(`Bundle integrity mismatch: ${path}`);
            contents.set(path, bytes);
        }
    }));
    return contents;
}

function sri(entry) {
    return "sha256-" + btoa(String.fromCharCode(...entry.sha256.match(/../g).map(byte => parseInt(byte, 16))));
}

export function publishLoadedAssets(manifest, asset) {
    const records = [...manifest.scripts, ...manifest.styles].map(path => Object.freeze({
        path, url: asset(path), sha256: manifest.integrity[path].sha256,
    }));
    const packages = Object.freeze([...(manifest.widgetPackages || [])]);
    globalThis.pytinctureAssets = Object.freeze({
        isPackageReady: name => packages.includes(name),
        isLoaded: (path, hash) => records.some(record => (record.path === path || record.url === path) && (!hash || hash === record.sha256)),
        getInfo: () => Object.freeze({owner: 'portable-bundle', bundleId: manifest.bundleId, packages, assets: Object.freeze([...records])}),
    });
}

function installResources(runtime, bundle) {
    if (!bundle.files || typeof bundle.files !== "object" || Array.isArray(bundle.files)) throw new Error("Invalid package resources");
    for (const [path, encoded] of Object.entries(bundle.files)) {
        relativePath(path);
        if (path.endsWith('.py') || path.endsWith('.pyc') || typeof encoded !== 'string') throw new Error("Invalid package resource");
        const directory = path.slice(0, path.lastIndexOf('/'));
        if (path.includes('/')) runtime.FS.mkdirTree('/'+directory);
        runtime.FS.writeFile('/'+path, Uint8Array.from(atob(encoded), value => value.charCodeAt(0)));
    }
}

function loadAsset(url, stylesheet = false, integrity = null, id = null) {
    return new Promise((resolve, reject) => {
        const node = document.createElement(stylesheet ? "link" : "script");
        if (stylesheet) { node.rel = "stylesheet"; node.href = url; }
        else { node.src = url; node.async = false; }
        if (integrity) { node.integrity = integrity; node.crossOrigin = "anonymous"; }
        if (id) node.id = id;
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
    if (config.deliveryMode !== "portable-bundle") throw new Error("Portable loader requires deliveryMode=portable-bundle");
    const phase = config._measurePhase || (async (_stage, _resource, callback) => callback());
    if (!config.runtimeManifestUrl) throw new Error(`${engine} requires an application runtime manifest`);
    const manifestUrl = sameOriginUrl(config.runtimeManifestUrl, location.href);
    status(`Preparing ${engine}…`);
    const manifest = validateRuntimeManifest(decodeJson(await phase("bundle-manifest", manifestUrl, () => fetchBytes(manifestUrl, 1048576))), engine);
    for (const name of manifest.requiredBrowserApis) {
        let value = globalThis;
        for (const part of name.split('.')) value = value?.[part];
        if (value == null) throw new Error(`Required browser API unavailable: ${name}`);
    }
    const assetBase = new URL(manifest.assetBase, manifestUrl).href;
    const asset = path => sameOriginUrl(relativePath(path), assetBase);
    config._runtimeInfo?.({bundleId: manifest.bundleId, compatibilityProfile: manifest.profile});
    const contents = await phase("bundle-download", assetBase, () => verifyBundle(manifest, asset, engine));
    status("Loading application assets…");
    await phase("asset-loading", assetBase, async () => {
        await Promise.all(manifest.styles.map(path => loadAsset(asset(path), true, sri(manifest.integrity[path]), manifest.styleIds?.[path])));
        for (const path of manifest.scripts) await loadAsset(asset(path), false, sri(manifest.integrity[path]));
    });
    // Publish only after every asset succeeded; widget hooks must not infer readiness from a partial load.
    publishLoadedAssets(manifest, asset);

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
    const host = await phase("host-import", asset(manifest.host), () => import(asset(manifest.host)));
    if (typeof host.setup !== "function") throw new Error("Browser host must export setup(context)");
    await host.setup({
        engine, runtimes: [...manifest.runtimes], application: config.application,
        callBff: createBffCaller(config), callBffSync: createBffSyncCaller(config),
        streamBff: createBffStreamCaller(config), invoke: handle.call, assetUrl: asset,
    });
    const resourceBundle = decodeJson(contents.get(manifest.resources));
    globalThis.pytinctureResourcePaths = JSON.stringify(Object.keys(resourceBundle.files));
    globalThis.pytinctureReadResourceBytes = path => {
        if (!Object.hasOwn(resourceBundle.files, path)) throw new Error(`Unknown package resource: ${path}`);
        return resourceBundle.files[path];
    };

    status(`Loading ${engine}…`);
    let runtime;
    if (engine === "micropython") {
        const { loadMicroPython } = await phase("runtime-download", asset(manifest.micropython.module), () => import(asset(manifest.micropython.module)));
        runtime = await phase("runtime-initialization", asset(manifest.micropython.wasm), () => loadMicroPython({
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
        }));
    } else {
        const base = sameOriginUrl(config.pyodideBaseUrl, location.href);
        if (typeof globalThis.loadPyodide !== "function") await phase("runtime-download", new URL("pyodide.js", base).href, () => loadAsset(new URL("pyodide.js", base).href));
        runtime = await phase("runtime-initialization", base, () => globalThis.loadPyodide({ indexURL: base }));
    }
    if (engine === 'pyodide' && runtime.version !== manifest.runtimeRequirements.pyodide) throw new Error('Pyodide version does not match the portable profile');
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
    status("Installing application bundle…");
    await phase("bundle-installation", asset(manifest.targets[engine].sources), async () => {
        installSources(runtime, decodeJson(contents.get(manifest.targets[engine].sources)));
        if (engine === "pyodide") installResources(runtime, resourceBundle);
    });
    status("Importing application…");
    await phase("module-import", manifest.entrypoint, () => runtime.runPythonAsync(`import sys\nsys.path.insert(0, '/')\nimport ${manifest.entrypoint} as _pytincture_client`));
    invoke = async (name, payload) => {
        if (payload !== undefined && typeof payload !== "string") throw new Error("Interpreter callback payload must be a JSON string");
        runtime.globals.set("_pytincture_payload", payload ?? "");
        await runtime.runPythonAsync(`await _pytincture_client.${name}(${payload === undefined ? "" : "_pytincture_payload"})`);
    };
    status("Running application entrypoint…");
    await phase("application-entrypoint", manifest.entrypoint, () => handle.call("main"));
    return handle;
}
