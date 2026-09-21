/* pytincture runtime */
var PytinctureRuntime = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __esm = (fn, res, err) => function __init() {
    if (err) throw err[0];
    try {
      return fn && (res = (0, fn[__getOwnPropNames(fn)[0]])(fn = 0)), res;
    } catch (e) {
      throw err = [e], e;
    }
  };
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };

  // widget-assets.js
  var widget_assets_exports = {};
  __export(widget_assets_exports, {
    createWidgetAssetBridge: () => createWidgetAssetBridge
  });
  function createWidgetAssetBridge(root, assets) {
    const scripts = new Set(assets.filter((item) => item.type === "script").map((item) => item.text.trim()));
    const styles = new Set(assets.filter((item) => item.type === "style").map((item) => item.text.trim()));
    const scriptUrls = new Set(assets.filter((item) => item.type === "script").map((item) => item.url));
    const styleUrls = new Set(assets.filter((item) => item.type === "style").map((item) => item.url));
    const proxies = /* @__PURE__ */ new WeakMap();
    const unwrap = /* @__PURE__ */ new WeakMap();
    const duplicate = (node) => {
      var _a;
      const tag = (_a = node == null ? void 0 : node.tagName) == null ? void 0 : _a.toLowerCase();
      if (tag === "script") return node.src ? scriptUrls.has(node.src) : scripts.has((node.textContent || "").trim());
      if (tag === "style") return styles.has((node.textContent || "").trim());
      return tag === "link" && node.rel === "stylesheet" && styleUrls.has(node.href);
    };
    const completed = (node) => {
      root.queueMicrotask(() => node.dispatchEvent(new root.Event("load")));
      return node;
    };
    const wrap = (target) => {
      if (!target || !["object", "function"].includes(typeof target)) return target;
      if (proxies.has(target)) return proxies.get(target);
      const proxy = new Proxy(target, {
        get(object, key) {
          var _a, _b, _c;
          if (object === root && key === "eval") return (code) => {
            if (typeof code === "string" && scripts.has(code.trim())) return void 0;
            return root.eval(code);
          };
          const value = Reflect.get(object, key, object);
          if (value === root || value === root.document || value === ((_a = root.document) == null ? void 0 : _a.head) || value === ((_b = root.document) == null ? void 0 : _b.body) || value === ((_c = root.document) == null ? void 0 : _c.documentElement)) return wrap(value);
          if (typeof value !== "function") return value;
          return new Proxy(value, {
            apply(fn, receiver, args) {
              var _a2, _b2;
              receiver = unwrap.get(receiver) || receiver;
              if (["appendChild", "insertBefore", "replaceChild"].includes(key) && duplicate(args[0])) {
                completed(args[0]);
                return key === "replaceChild" ? receiver.removeChild(args[1]) : args[0];
              }
              if (["append", "prepend"].includes(key)) {
                args = args.filter((node) => {
                  if (!duplicate(node)) return true;
                  completed(node);
                  return false;
                });
              }
              const result = Reflect.apply(fn, receiver, args.map((arg) => unwrap.get(arg) || arg));
              if (result === ((_a2 = root.document) == null ? void 0 : _a2.head) || result === ((_b2 = root.document) == null ? void 0 : _b2.body)) return wrap(result);
              return result;
            }
          });
        },
        set(object, key, value) {
          return Reflect.set(object, key, value, object);
        }
      });
      proxies.set(target, proxy);
      unwrap.set(proxy, target);
      return proxy;
    };
    return wrap(root);
  }
  var init_widget_assets = __esm({
    "widget-assets.js"() {
    }
  });

  // browser-runtimes.js
  var browser_runtimes_exports = {};
  __export(browser_runtimes_exports, {
    BROWSER_RUNTIMES: () => BROWSER_RUNTIMES,
    createBffCaller: () => createBffCaller,
    createBffStreamCaller: () => createBffStreamCaller,
    createBffSyncCaller: () => createBffSyncCaller,
    createEventCallback: () => createEventCallback,
    createOnceCallable: () => createOnceCallable,
    evaluatePythonJavascript: () => evaluatePythonJavascript,
    installResources: () => installResources,
    preparePackageResources: () => preparePackageResources,
    publishLoadedAssets: () => publishLoadedAssets,
    runBrowserApplication: () => runBrowserApplication,
    sameOriginUrl: () => sameOriginUrl,
    validateRuntimeManifest: () => validateRuntimeManifest,
    verifyBundle: () => verifyBundle
  });
  function relativePath(value) {
    if (typeof value !== "string" || !/^[A-Za-z0-9_@./-]+$/.test(value) || value.split("/").some((part) => !part || part.startsWith("."))) {
      throw new Error(`Invalid browser bundle path: ${value}`);
    }
    return value;
  }
  function sameOriginUrl(value, base) {
    const url = new URL(value, base);
    if (url.origin !== new URL(base).origin || url.username || url.password || !["http:", "https:"].includes(url.protocol)) {
      throw new Error("Browser runtime assets must use the application origin");
    }
    return url.href;
  }
  function validateRuntimeManifest(manifest, engine) {
    var _a, _b, _c, _d, _e, _f, _g;
    if (!BROWSER_RUNTIMES.includes(engine)) throw new Error(`Unknown browser runtime: ${engine}`);
    if ((manifest == null ? void 0 : manifest.schema) !== 2 || !Array.isArray(manifest.runtimes) || !manifest.runtimes.length || new Set(manifest.runtimes).size !== manifest.runtimes.length || manifest.runtimes.some((name) => !BROWSER_RUNTIMES.includes(name))) {
      throw new Error("Invalid browser runtime manifest");
    }
    if (!manifest.runtimes.includes(engine)) throw new Error(`Application does not support ${engine}`);
    if (((_a = manifest.runtimeRequirements) == null ? void 0 : _a[engine]) !== (engine === "pyodide" ? "0.29.3" : "1.29.0-6")) throw new Error("Unsupported runtime version requirement");
    if (!Array.isArray(manifest.requiredBrowserApis) || manifest.requiredBrowserApis.some((name) => typeof name !== "string" || !/^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$/.test(name))) throw new Error("Invalid browser API requirements");
    relativePath(manifest.host);
    for (const kind of ["scripts", "styles"]) {
      if (!Array.isArray(manifest[kind]) || manifest[kind].length > 64) {
        throw new Error(`Manifest ${kind} must be an array of at most 64 paths`);
      }
      manifest[kind].forEach(relativePath);
    }
    if (manifest.styleIds && (typeof manifest.styleIds !== "object" || Array.isArray(manifest.styleIds) || Object.entries(manifest.styleIds).some(([path, id]) => !manifest.styles.includes(path) || typeof id !== "string" || !/^[A-Za-z][\w-]*$/.test(id)))) throw new Error("Invalid stylesheet identifiers");
    if (!/^[a-f0-9]{64}$/.test(manifest.bundleId || "") || manifest.assetBase !== `releases/${manifest.bundleId}/`) throw new Error("Invalid immutable bundle identifier");
    if (!["pytincture-portable-1", "pytincture-portable-2"].includes(manifest.profile)) throw new Error("Unsupported portable Python profile");
    const target = (_b = manifest.targets) == null ? void 0 : _b[engine];
    relativePath(target == null ? void 0 : target.sources);
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
    for (const path of [
      manifest.host,
      target.sources,
      manifest.resources,
      ...manifest.scripts,
      ...manifest.styles,
      ...engine === "micropython" ? [(_c = manifest.micropython) == null ? void 0 : _c.module, (_d = manifest.micropython) == null ? void 0 : _d.wasm] : []
    ]) {
      if (!Object.hasOwn(inventory, path)) throw new Error(`Missing integrity lock: ${path}`);
    }
    relativePath(manifest.sources);
    if (!/^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$/.test(manifest.entrypoint || "")) {
      throw new Error("Manifest entrypoint must be a Python module name");
    }
    if (engine === "micropython") {
      relativePath((_e = manifest.micropython) == null ? void 0 : _e.module);
      relativePath((_f = manifest.micropython) == null ? void 0 : _f.wasm);
      const heap = (_g = manifest.micropython.heapBytes) != null ? _g : 8 * 1024 * 1024;
      if (!Number.isInteger(heap) || heap < 1024 * 1024 || heap > 128 * 1024 * 1024) {
        throw new Error("MicroPython heapBytes must be between 1 and 128 MiB");
      }
    }
    return manifest;
  }
  async function fetchBytes(url, limit) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 35e3);
    try {
      const response = await fetch(url, { credentials: "same-origin", signal: controller.signal });
      if (!response.ok) throw new Error(`Browser bundle request failed (${response.status}): ${url}`);
      const declared = response.headers.get("content-length");
      if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > limit)) throw new Error("Browser bundle response exceeds byte limit");
      const reader = response.clone().body.getReader();
      const monitor = async () => {
        let size = 0;
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) return;
            size += value.byteLength;
            if (size > limit) {
              controller.abort();
              throw new Error("Browser bundle response exceeds byte limit");
            }
          }
        } finally {
          reader.releaseLock();
        }
      };
      const [bytes] = await Promise.all([response.arrayBuffer(), monitor()]);
      return new Uint8Array(bytes);
    } catch (error) {
      controller.abort();
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }
  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
    return value;
  }
  async function verifyBundle(manifest, asset, engine = null) {
    var _a, _b;
    const { bundleId, assetBase, ...identity } = manifest;
    if (await digest(new TextEncoder().encode(JSON.stringify(canonical(identity)) + "\n")) !== bundleId) throw new Error("Bundle identifier does not match manifest contents");
    const excluded = new Set(engine ? ["build.json", "compatibility.json"] : []);
    if (engine) {
      for (const [target, options] of Object.entries(manifest.targets || {})) {
        if (target !== engine) excluded.add(options.sources);
      }
      if (engine === "pyodide") {
        excluded.add((_a = manifest.micropython) == null ? void 0 : _a.module);
        excluded.add((_b = manifest.micropython) == null ? void 0 : _b.wasm);
      }
    }
    const entries = Object.entries(manifest.integrity).filter(([path]) => !excluded.has(path));
    const contents = /* @__PURE__ */ new Map();
    let cursor = 0;
    await Promise.all(Array.from({ length: 4 }, async () => {
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
    return "sha256-" + btoa(String.fromCharCode(...entry.sha256.match(/../g).map((byte) => parseInt(byte, 16))));
  }
  function publishLoadedAssets(manifest, asset) {
    const records = [...manifest.scripts, ...manifest.styles].map((path) => Object.freeze({
      path,
      url: asset(path),
      sha256: manifest.integrity[path].sha256
    }));
    const packages = Object.freeze([...manifest.widgetPackages || []]);
    globalThis.pytinctureAssets = Object.freeze({
      isPackageReady: (name) => packages.includes(name),
      isLoaded: (path, hash) => records.some((record) => (record.path === path || record.url === path) && (!hash || hash === record.sha256)),
      getInfo: () => Object.freeze({ owner: "portable-bundle", bundleId: manifest.bundleId, packages, assets: Object.freeze([...records]) })
    });
  }
  function preparePackageResources(bundle, contents) {
    if (!bundle || !bundle.files || typeof bundle.files !== "object" || Array.isArray(bundle.files) || bundle.format !== void 0 && bundle.format !== "asset-references-v1") throw new Error("Invalid package resources");
    const files = /* @__PURE__ */ new Map();
    for (const [path, entry] of Object.entries(bundle.files)) {
      relativePath(path);
      if (path.endsWith(".py") || path.endsWith(".pyc")) throw new Error("Invalid package resource");
      if (typeof entry === "string" && bundle.format === void 0) files.set(path, entry);
      else if (bundle.format === "asset-references-v1" && entry && typeof entry === "object" && !Array.isArray(entry) && Object.keys(entry).length === 1 && entry.asset === "vendor/" + path && contents.get(entry.asset) instanceof Uint8Array) files.set(path, contents.get(entry.asset));
      else throw new Error(`Invalid or unverified package resource: ${path}`);
    }
    const get = (path) => {
      if (!files.has(path)) throw new Error(`Unknown package resource: ${path}`);
      return files.get(path);
    };
    return {
      paths: [...files.keys()],
      readBytes(path) {
        const value = get(path);
        return typeof value === "string" ? Uint8Array.from(atob(value), (ch) => ch.charCodeAt(0)) : value;
      },
      readBase64(path) {
        const value = get(path);
        if (typeof value === "string") return value;
        const chunks = [];
        for (let start = 0; start < value.length; start += 32768) chunks.push(String.fromCharCode(...value.subarray(start, start + 32768)));
        return btoa(chunks.join(""));
      }
    };
  }
  function installResources(runtime, resources) {
    for (const path of resources.paths) {
      const directory = path.slice(0, path.lastIndexOf("/"));
      if (path.includes("/")) runtime.FS.mkdirTree("/" + directory);
      runtime.FS.writeFile("/" + path, resources.readBytes(path));
    }
  }
  function loadAsset(url, stylesheet = false, integrity = null, id = null) {
    return new Promise((resolve, reject) => {
      const node = document.createElement(stylesheet ? "link" : "script");
      if (stylesheet) {
        node.rel = "stylesheet";
        node.href = url;
      } else {
        node.src = url;
        node.async = false;
      }
      if (integrity) {
        node.integrity = integrity;
        node.crossOrigin = "anonymous";
      }
      if (id) node.id = id;
      node.onload = () => resolve();
      node.onerror = () => reject(new Error(`Unable to load browser asset: ${url}`));
      document.head.appendChild(node);
    });
  }
  function bffRequest(config, module, className, method, args, options = {}) {
    if (!/^[A-Za-z_]\w*$/.test(config.application || "")) throw new Error("A BFF application is required");
    if (!/^[A-Za-z_]\w*(\/[A-Za-z_]\w*)*$/.test(module) || !/^[A-Za-z_]\w*$/.test(className) || !/^[A-Za-z_]\w*$/.test(method)) {
      throw new Error("Invalid BFF target");
    }
    const cookies = document.cookie.split(";").map((value) => value.trim());
    const cookieName = config.csrfCookieName || "pytincture-dev-csrf";
    const cookie = cookies.find((value) => value.startsWith(`${cookieName}=`));
    const csrf = cookie ? decodeURIComponent(cookie.slice(cookieName.length + 1)) : "";
    const httpMethod = options.method || "POST";
    if (!["POST", "GET"].includes(httpMethod)) throw new Error("Unsupported browser BFF HTTP method");
    return {
      url: `/${config.application}/classcall/${module}/${className}/${method}`,
      init: {
        method: httpMethod,
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        ...httpMethod === "GET" ? {} : { body: JSON.stringify(args) }
      }
    };
  }
  function createBffCaller(config) {
    if (!/^[A-Za-z_]\w*$/.test(config.application || "")) throw new Error("A BFF application is required");
    return async (module, className, method, args = {}, options = {}) => {
      const request = bffRequest(config, module, className, method, args, options);
      const response = await fetch(request.url, { ...request.init, signal: AbortSignal.timeout(35e3) });
      if (!response.ok) throw new Error(`BFF ${className}.${method} failed (${response.status})`);
      return response.json();
    };
  }
  function createBffSyncCaller(config) {
    return (module, className, method, args = {}, options = {}) => {
      var _a;
      const { url, init } = bffRequest(config, module, className, method, args, options);
      const request = new XMLHttpRequest();
      request.open(init.method, url, false);
      for (const [name, value] of Object.entries(init.headers)) request.setRequestHeader(name, value);
      request.send((_a = init.body) != null ? _a : null);
      if (request.status < 200 || request.status >= 300) throw new Error(`BFF ${className}.${method} failed (${request.status})`);
      return JSON.parse(request.responseText);
    };
  }
  function createBffStreamCaller(config) {
    return async (module, className, method, args = {}, options = {}) => {
      const { url, init } = bffRequest(config, module, className, method, args, options);
      const controller = new AbortController();
      const response = await fetch(url, { ...init, signal: controller.signal });
      if (!response.ok) {
        controller.abort();
        throw new Error(`BFF ${className}.${method} failed (${response.status})`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "", done = false, closed = false;
      const close = async () => {
        if (closed) return;
        closed = true;
        try {
          await reader.cancel();
        } finally {
          controller.abort();
          reader.releaseLock();
        }
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
                  if (line.trim()) return JSON.stringify({ done: false, value: JSON.parse(line) });
                  continue;
                }
              }
              if (done) {
                await close();
                break;
              }
              const chunk = await reader.read();
              done = chunk.done;
              const text = decoder.decode(chunk.value, { stream: !done });
              if (options.raw) {
                if (text) return JSON.stringify({ done: false, value: text });
              } else {
                buffer += text;
                if (buffer.length > 8 * 1024 * 1024) throw new Error("BFF stream record exceeds 8 MiB");
              }
            }
            return JSON.stringify({ done: true });
          } catch (error) {
            await close();
            throw error;
          }
        }
      };
    };
  }
  function installSources(runtime, bundle) {
    const files = bundle == null ? void 0 : bundle.files;
    if (!files || typeof files !== "object" || Array.isArray(files) || Object.keys(files).length > 256) throw new Error("Invalid Python source bundle");
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
  function createOnceCallable(callback) {
    if (typeof callback !== "function") throw new TypeError("Expected a callable");
    const once = (...args) => {
      if (callback === null) throw new Error("Once callable has already been called or destroyed");
      const invoke = callback;
      callback = null;
      return invoke(...args);
    };
    once.destroy = () => {
      callback = null;
    };
    return once;
  }
  function createEventCallback(callback) {
    if (typeof callback !== "function") throw new TypeError("Expected a callable");
    const listener = (...args) => callback === null ? void 0 : callback(...args);
    listener.destroy = () => {
      callback = null;
    };
    return listener;
  }
  function evaluatePythonJavascript(code, widget = false) {
    try {
      if (typeof code !== "string") throw new TypeError("run_js requires a string");
      const scope = widget ? globalThis.pytinctureWidgetBridge : globalThis;
      return { ok: true, value: scope.eval(code) };
    } catch (error) {
      return { ok: false, error: String(error) };
    }
  }
  async function runBrowserApplication(config, status = () => {
  }) {
    var _a;
    globalThis.pytinctureRunJavascript = evaluatePythonJavascript;
    globalThis.pytinctureCreateOnceCallable = createOnceCallable;
    globalThis.pytinctureCreateEventCallback = createEventCallback;
    const engine = config.runtime || "pyodide";
    if (config.deliveryMode !== "portable-bundle") throw new Error("Portable loader requires deliveryMode=portable-bundle");
    const phase = config._measurePhase || (async (_stage, _resource, callback) => callback());
    if (!config.runtimeManifestUrl) throw new Error(`${engine} requires an application runtime manifest`);
    const manifestUrl = sameOriginUrl(config.runtimeManifestUrl, location.href);
    status(`Preparing ${engine}\u2026`);
    const manifest = validateRuntimeManifest(decodeJson(await phase("bundle-manifest", manifestUrl, () => fetchBytes(manifestUrl, 1048576))), engine);
    for (const name of manifest.requiredBrowserApis) {
      let value = globalThis;
      for (const part of name.split(".")) value = value == null ? void 0 : value[part];
      if (value == null) throw new Error(`Required browser API unavailable: ${name}`);
    }
    const assetBase = new URL(manifest.assetBase, manifestUrl).href;
    const asset = (path) => sameOriginUrl(relativePath(path), assetBase);
    (_a = config._runtimeInfo) == null ? void 0 : _a.call(config, { bundleId: manifest.bundleId, compatibilityProfile: manifest.profile });
    const contents = await phase("bundle-download", assetBase, () => verifyBundle(manifest, asset, engine));
    status("Loading application assets\u2026");
    await phase("asset-loading", assetBase, async () => {
      await Promise.all(manifest.styles.map((path) => {
        var _a2;
        return loadAsset(asset(path), true, sri(manifest.integrity[path]), (_a2 = manifest.styleIds) == null ? void 0 : _a2[path]);
      }));
      for (const path of manifest.scripts) await loadAsset(asset(path), false, sri(manifest.integrity[path]));
    });
    publishLoadedAssets(manifest, asset);
    const { createWidgetAssetBridge: createWidgetAssetBridge2 } = await Promise.resolve().then(() => (init_widget_assets(), widget_assets_exports));
    globalThis.pytinctureWidgetBridge = createWidgetAssetBridge2(
      globalThis,
      [
        ...manifest.scripts.map((path) => ({ path, type: "script" })),
        ...manifest.styles.map((path) => ({ path, type: "style" }))
      ].map((item) => ({
        ...item,
        url: asset(item.path),
        text: new TextDecoder().decode(contents.get(item.path))
      }))
    );
    let invoke;
    let capturedOutput = null;
    let consoleOutput = [];
    let queue = Promise.resolve();
    const handle = {
      engine,
      runtime: null,
      call(name, payload) {
        if (!/^[A-Za-z_]\w*$/.test(name)) return Promise.reject(new Error("Invalid client function name"));
        queue = queue.catch(() => {
        }).then(() => {
          if (!invoke) throw new Error("Browser runtime is not ready");
          return invoke(name, payload);
        });
        return queue;
      }
    };
    const host = await phase("host-import", asset(manifest.host), () => import(asset(manifest.host)));
    if (typeof host.setup !== "function") throw new Error("Browser host must export setup(context)");
    await host.setup({
      engine,
      runtimes: [...manifest.runtimes],
      application: config.application,
      callBff: createBffCaller(config),
      callBffSync: createBffSyncCaller(config),
      streamBff: createBffStreamCaller(config),
      invoke: handle.call,
      assetUrl: asset
    });
    const resourceBundle = preparePackageResources(decodeJson(contents.get(manifest.resources)), contents);
    globalThis.pytinctureResourcePaths = JSON.stringify(resourceBundle.paths);
    globalThis.pytinctureReadResourceBytes = (path) => resourceBundle.readBase64(path);
    status(`Loading ${engine}\u2026`);
    let runtime;
    if (engine === "micropython") {
      const { loadMicroPython } = await phase("runtime-download", asset(manifest.micropython.module), () => import(asset(manifest.micropython.module)));
      runtime = await phase("runtime-initialization", asset(manifest.micropython.wasm), () => {
        var _a2;
        return loadMicroPython({
          url: asset(manifest.micropython.wasm),
          heapsize: (_a2 = manifest.micropython.heapBytes) != null ? _a2 : 8 * 1024 * 1024,
          linebuffer: false,
          stdout: (bytes) => {
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
          }
        });
      });
    } else {
      const base = sameOriginUrl(config.pyodideBaseUrl, location.href);
      if (typeof globalThis.loadPyodide !== "function") await phase("runtime-download", new URL("pyodide.js", base).href, () => loadAsset(new URL("pyodide.js", base).href));
      runtime = await phase("runtime-initialization", base, () => globalThis.loadPyodide({ indexURL: base }));
    }
    if (engine === "pyodide" && runtime.version !== manifest.runtimeRequirements.pyodide) throw new Error("Pyodide version does not match the portable profile");
    handle.runtime = runtime;
    handle.captureOutput = (source) => {
      if (typeof source !== "string" || source.length > 1024 * 1024) throw new Error("Invalid Python snippet");
      if (engine === "micropython") {
        if (capturedOutput !== null) throw new Error("Nested output capture is not supported");
        capturedOutput = [];
        try {
          runtime.runPython(`exec(${JSON.stringify(source)}, {})`);
          if (capturedOutput.length > 1024 * 1024) throw new Error("Python output exceeds 1 MiB");
          return new TextDecoder().decode(Uint8Array.from(capturedOutput));
        } finally {
          capturedOutput = null;
        }
      }
      const program = `import io, contextlib
with contextlib.redirect_stdout(io.StringIO()) as output:
    exec(${JSON.stringify(source)}, {})
output.getvalue()`;
      return runtime.runPython(program);
    };
    globalThis.pytinctureBrowserRuntime = handle;
    status("Installing application bundle\u2026");
    await phase("bundle-installation", asset(manifest.targets[engine].sources), async () => {
      installSources(runtime, decodeJson(contents.get(manifest.targets[engine].sources)));
      installResources(runtime, resourceBundle);
    });
    status("Importing application\u2026");
    await phase("module-import", manifest.entrypoint, () => runtime.runPythonAsync(`import sys
sys.path.insert(0, '/')
import ${manifest.entrypoint} as _pytincture_client`));
    invoke = async (name, payload) => {
      if (payload !== void 0 && typeof payload !== "string") throw new Error("Interpreter callback payload must be a JSON string");
      runtime.globals.set("_pytincture_payload", payload != null ? payload : "");
      await runtime.runPythonAsync(`await _pytincture_client.${name}(${payload === void 0 ? "" : "_pytincture_payload"})`);
    };
    status("Running application entrypoint\u2026");
    await phase("application-entrypoint", manifest.entrypoint, () => handle.call("main"));
    return handle;
  }
  var BROWSER_RUNTIMES, decodeJson, digest;
  var init_browser_runtimes = __esm({
    "browser-runtimes.js"() {
      BROWSER_RUNTIMES = Object.freeze(["pyodide", "micropython"]);
      decodeJson = (bytes) => JSON.parse(new TextDecoder().decode(bytes));
      digest = async (bytes) => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (b) => b.toString(16).padStart(2, "0")).join("");
    }
  });

  // pytincture.js
  var FALLBACK_DEV_WIDGET_HOST = "http://127.0.0.1:8070";
  var PYTINCTURE_RUNTIME_VERSION = "1.0.0rc10";
  var BUILTIN_WIDGET_WHEEL_LOCKS = Object.freeze({
    "dhxpyt==0.9.18": "https://files.pythonhosted.org/packages/0c/e7/b48e045156c7b4bf20778597991d7dfe591fd46ada5267b747e2d5977244/dhxpyt-0.9.18-py3-none-any.whl#sha256=acd8db34547c6b61c83a01958e9545ee724564859e5bcb53713ae3c872234fbe"
  });
  var BUILTIN_WIDGET_ASSET_MANIFESTS = Object.freeze({
    "dhxpyt@0.9.18": {
      schema: 1,
      package: "dhxpyt",
      version: "0.9.18",
      assets: [
        { path: "dhxpyt/dhxsrc/suite.css", type: "css", sha256: "bbfb8928fce1a99acf5a6f610c99625eea8b31e0f62b4cb5b31b6ccb684a1719" },
        { path: "dhxpyt/dhxsrc/fonts/inter.css", type: "css", sha256: "868d674eb57814ea39415af15132b4077ccdc081b1c8e15cf31e652e12bf3cc9" },
        { path: "dhxpyt/dhxsrc/dhx_custom.css", type: "css", sha256: "43091047578499088323dc062805c71327c273c2840512b7700f9ed10c9c6a61" },
        { path: "dhxpyt/dhxsrc/suite.js", type: "javascript", sha256: "e4d99a233660aae3afeb62d2aba546269bf5fe59ed4489481848ac0de53f9d2c" },
        { path: "dhxpyt/dhxsrc/cardflow.js", type: "javascript", sha256: "a589ff5e7adb642b70c06b018f06789bff8da4c41520915830dd4c7aefc8658b" },
        { path: "dhxpyt/dhxsrc/cardpanel.js", type: "javascript", sha256: "25e1befe91f86b1de5f738b934f6887c094b1c35d1a634a58c3cdb98c0e41715" },
        { path: "dhxpyt/dhxsrc/chat.js", type: "javascript", sha256: "fa7420e2955db78d62317fa566209fefbf90eba042894bff6841754f32223c10" },
        { path: "dhxpyt/dhxsrc/kanban.css", type: "css", sha256: "155a5ea4b5589b1fa0c2b51ea7c9007e8939b1484d037f7ea04eaea9a03d1abb" },
        { path: "dhxpyt/dhxsrc/kanban.js", type: "javascript", sha256: "186950aa79b1a09525b78c808a6a62e4cfc052e1de6afe834eab64cbb94b9acb" },
        { path: "dhxpyt/dhxsrc/kanban_board.js", type: "javascript", sha256: "053a4d55b2c43171ce41b4888b0a89f123afbfe49ef5adbf540491b156626c55" },
        { path: "dhxpyt/dhxsrc/ragwidget.js", type: "javascript", sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" },
        { path: "dhxpyt/dhxsrc/theme.js", type: "javascript", sha256: "1afd7bb65f81d3fc1e474edc81d3f6c23ffa2caa5feb2220153616137f117634" },
        { path: "dhxpyt/dhxsrc/webgpu.js", type: "javascript", sha256: "f4825014ced0cb326995fa4ad9ecc6cccb6efa947ad8e6bf5b26a4e9c7759e89" }
      ]
    }
  });
  var CSRF_COOKIE_NAMES = Object.freeze([
    "__Host-pytincture-csrf",
    "pytincture-dev-csrf"
  ]);
  var DEFAULT_CONFIG = {
    runtime: "pyodide",
    deliveryMode: "legacy-package",
    runtimeManifestUrl: null,
    application: null,
    entrypoint: null,
    widgetlib: "dhxpyt==0.9.18",
    widgetSource: null,
    widgetAssetManifest: null,
    backendWidgetSources: null,
    allowPublicWidgetIndex: null,
    requestUuid: null,
    csrfCookieName: null,
    mode: "auto",
    // 'package', 'inline', or 'auto'
    pyodideBaseUrl: "./frontend/pyodide/0.29.3/full/",
    pyodideScriptIntegrity: null,
    allowUnverifiedExternalPyodide: false,
    loadMaterialIcons: true,
    materialIconsUrl: "./frontend/vendor/materialdesignicons/materialdesignicons.css",
    materialIconsIntegrity: null,
    enableBackendLogging: false,
    logEndpoint: "/logs",
    inlineSelector: 'script[type="text/python"]',
    libsSelector: "#micropip-libs",
    devWidgetHost: null,
    devWheelVersion: "99.99.99",
    enableServiceWorker: false,
    serviceWorkerUrl: "sw.js",
    serviceWorkerScope: "./",
    warmPyodideCache: true,
    showLoadingOverlay: true,
    loadingOverlayId: "pytincture-loading",
    loadingTitle: "Starting PyTincture",
    onLifecycleEvent: null
  };
  var LIFECYCLE_STAGES = Object.freeze({
    PREFLIGHT: "preflight",
    RUNTIME_LOAD: "runtime-load",
    PACKAGE_INSTALL: "package-install",
    WIDGETSET_INSTALL: "widgetset-install",
    WIDGETSET_LOAD: "widgetset-load",
    ARCHIVE_DOWNLOAD: "archive-download",
    ARCHIVE_UNPACK: "archive-unpack",
    ENTRYPOINT_EXECUTION: "entrypoint-execution",
    READY: "ready"
  });
  var loggingInstalled = false;
  var originalConsoleMethods = {};
  var MAX_BROWSER_DIAGNOSTIC_CHARS = 800;
  var MAX_BROWSER_DIAGNOSTIC_DEPTH = 3;
  var MAX_BROWSER_DIAGNOSTIC_ENTRIES = 20;
  var SENSITIVE_DIAGNOSTIC_KEY = /(?:api.?key|assertion|authorization|cookie|credential|csrf|password|private.?key|secret|session|token)/i;
  function sanitizeDiagnostic(value) {
    if (value === null || value === void 0) {
      return "";
    }
    return String(value).replace(/([?&](?:token|secret|password|authorization|code)=)[^&#\s]*/gi, "$1[redacted]").replace(/\b(token|secret|password|authorization|api[_-]?key|access[_-]?token|id[_-]?token|refresh[_-]?token|client[_-]?secret|cookie|session|csrf)(\s*[:=]\s*)[^\s,;]+/gi, "$1$2[redacted]").replace(/\b(Bearer\s+)[A-Za-z0-9._~+/=-]+/gi, "$1[redacted]").slice(0, MAX_BROWSER_DIAGNOSTIC_CHARS);
  }
  function summarizeConsoleValue(value, depth = 0, seen = /* @__PURE__ */ new WeakSet()) {
    if (value === null || value === void 0) {
      return value === null ? null : "[undefined]";
    }
    if (typeof value === "string") {
      return sanitizeDiagnostic(value);
    }
    if (["number", "boolean"].includes(typeof value)) {
      return value;
    }
    if (["bigint", "function", "symbol"].includes(typeof value)) {
      return sanitizeDiagnostic(String(value));
    }
    if (value instanceof Error) {
      return {
        name: sanitizeDiagnostic(value.name),
        message: sanitizeDiagnostic(value.message)
      };
    }
    if (depth >= MAX_BROWSER_DIAGNOSTIC_DEPTH) {
      return "[truncated]";
    }
    if (seen.has(value)) {
      return "[circular]";
    }
    seen.add(value);
    if (Array.isArray(value)) {
      const summarized2 = value.slice(0, MAX_BROWSER_DIAGNOSTIC_ENTRIES).map((item) => summarizeConsoleValue(item, depth + 1, seen));
      if (value.length > MAX_BROWSER_DIAGNOSTIC_ENTRIES) {
        summarized2.push("[truncated]");
      }
      return summarized2;
    }
    const summarized = {};
    let keys;
    try {
      keys = Object.keys(value);
    } catch (_error) {
      return "[unreadable object]";
    }
    for (const key of keys.slice(0, MAX_BROWSER_DIAGNOSTIC_ENTRIES)) {
      if (SENSITIVE_DIAGNOSTIC_KEY.test(key)) {
        summarized[key] = "[redacted]";
        continue;
      }
      try {
        summarized[key] = summarizeConsoleValue(value[key], depth + 1, seen);
      } catch (_error) {
        summarized[key] = "[unreadable]";
      }
    }
    if (keys.length > MAX_BROWSER_DIAGNOSTIC_ENTRIES) {
      summarized.__truncated__ = true;
    }
    return summarized;
  }
  function sanitizeConsoleMessage(args) {
    const parts = args.map((value) => {
      try {
        const summarized = summarizeConsoleValue(value);
        return typeof summarized === "string" ? summarized : JSON.stringify(summarized);
      } catch (_error) {
        return "[unreadable]";
      }
    });
    return sanitizeDiagnostic(parts.join(" "));
  }
  function sanitizeResource(value) {
    if (!value) {
      return null;
    }
    const rawValue = String(value);
    if (rawValue.startsWith("#") || !rawValue.includes("/") && !rawValue.includes(":")) {
      return sanitizeDiagnostic(rawValue);
    }
    try {
      const base = typeof window !== "undefined" && window.location ? window.location.href : "http://localhost/";
      const parsed = new URL(rawValue, base);
      parsed.search = "";
      parsed.hash = "";
      return parsed.toString();
    } catch (_error) {
      return sanitizeDiagnostic(value).replace(/\?.*$/, "");
    }
  }
  var PytinctureLifecycleError = class extends Error {
    constructor({ stage, code = "startup_failed", resource = null, requestId = null, correlationId = null, cause = null }) {
      const rootCause = sanitizeDiagnostic((cause == null ? void 0 : cause.message) || cause || "Unknown startup failure");
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
        rootCause: this.rootCause
      };
    }
  };
  var runtimeDiagnostics = /* @__PURE__ */ new WeakMap();
  var now = () => {
    var _a, _b, _c;
    return (_c = (_b = (_a = globalThis.performance) == null ? void 0 : _a.now) == null ? void 0 : _b.call(_a)) != null ? _c : Date.now();
  };
  function initializeRuntimeDiagnostics(config) {
    const state = {
      schema: 1,
      engine: config.runtime || "pyodide",
      deliveryMode: config.deliveryMode || "legacy-package",
      runtimeVersion: null,
      pythonImplementation: null,
      pythonVersion: null,
      bundleId: null,
      compatibilityProfile: null,
      startupTimings: []
    };
    runtimeDiagnostics.set(config, state);
    delete globalThis.pytinctureAssets;
    delete globalThis.pytinctureWidgetBridge;
    delete globalThis.pytinctureBrowserRuntime;
    const snapshot = () => Object.freeze({
      ...state,
      startupTimings: Object.freeze(state.startupTimings.map((entry) => Object.freeze({ ...entry })))
    });
    globalThis.pytinctureRuntime = Object.freeze({ getInfo: snapshot });
    if (typeof document !== "undefined" && document.body) {
      document.body.dataset.browserRuntime = state.engine;
      document.body.dataset.deliveryMode = state.deliveryMode;
    }
    config._runtimeInfo = (values) => Object.assign(state, values);
    config._measurePhase = (stage, resource, callback) => measureRuntimePhase(config, stage, resource, callback);
  }
  function resourceCacheStatus(resource) {
    var _a;
    if (!resource || !((_a = globalThis.performance) == null ? void 0 : _a.getEntriesByName) || typeof location === "undefined") return "unknown";
    try {
      const entry = performance.getEntriesByName(new URL(resource, location.href).href).at(-1);
      if (!entry || !entry.decodedBodySize) return "unknown";
      return entry.transferSize === 0 ? "cache" : "network";
    } catch (_) {
      return "unknown";
    }
  }
  async function measureRuntimePhase(config, stage, resource, callback) {
    const started = now();
    emitLifecycleEvent(config, "stage-start", stage, { resource: sanitizeResource(resource) });
    try {
      const result = await callback();
      emitLifecycleEvent(config, "stage-complete", stage, {
        resource: sanitizeResource(resource),
        durationMs: Math.max(0, now() - started),
        cacheStatus: resourceCacheStatus(resource)
      });
      return result;
    } catch (error) {
      emitLifecycleEvent(config, "stage-failed", stage, {
        resource: sanitizeResource(resource),
        durationMs: Math.max(0, now() - started),
        cacheStatus: resourceCacheStatus(resource)
      });
      throw error;
    }
  }
  function recordRuntimeIdentity(config, runtime) {
    var _a;
    let identity = {};
    try {
      runtime.runPython("import sys as _pt_sys, json as _pt_json, js as _pt_js\n_pt_js.globalThis._pytinctureIdentityResult = _pt_json.dumps({'pythonImplementation': _pt_sys.implementation.name, 'pythonVersion': _pt_sys.version.split()[0], 'runtimeVersion': '.'.join(str(v) for v in _pt_sys.implementation.version[:3])})");
      const result = JSON.parse(globalThis._pytinctureIdentityResult);
      if (result && typeof result === "object") identity = result;
    } catch (_) {
    } finally {
      delete globalThis._pytinctureIdentityResult;
    }
    (_a = config._runtimeInfo) == null ? void 0 : _a.call(config, { ...identity, runtimeVersion: runtime.version || identity.runtimeVersion || null });
  }
  async function firstApplicationFrame(config) {
    await measureRuntimePhase(config, "first-render", null, async () => {
      if (typeof requestAnimationFrame === "function") {
        await new Promise((resolve) => {
          const timer = setTimeout(resolve, 250);
          requestAnimationFrame(() => requestAnimationFrame(() => {
            clearTimeout(timer);
            resolve();
          }));
        });
      }
    });
  }
  function emitLifecycleEvent(config, type, stage, details = {}) {
    var _a;
    const event = Object.freeze({
      type,
      stage,
      engine: config.runtime || "pyodide",
      deliveryMode: config.deliveryMode || "legacy-package",
      durationMs: null,
      resource: null,
      cacheStatus: "unknown",
      requestId: config.requestUuid || null,
      timestamp: (/* @__PURE__ */ new Date()).toISOString(),
      ...details
    });
    if (["stage-complete", "stage-failed"].includes(type)) {
      (_a = runtimeDiagnostics.get(config)) == null ? void 0 : _a.startupTimings.push({
        stage,
        durationMs: event.durationMs,
        resource: event.resource,
        cacheStatus: event.cacheStatus,
        status: type
      });
    }
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
    const started = now();
    const safeResource = sanitizeResource(resource);
    emitLifecycleEvent(config, "stage-start", stage, { resource: safeResource });
    try {
      const result = await callback();
      emitLifecycleEvent(config, "stage-complete", stage, { resource: safeResource, durationMs: Math.max(0, now() - started), cacheStatus: resourceCacheStatus(resource) });
      return result;
    } catch (error) {
      const lifecycleError = error instanceof PytinctureLifecycleError ? error : new PytinctureLifecycleError({
        stage,
        resource,
        requestId: config.requestUuid,
        correlationId: metadata.correlationId || null,
        cause: error
      });
      emitLifecycleEvent(config, "error", lifecycleError.stage, {
        resource: lifecycleError.resource,
        error: lifecycleError.toJSON()
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
  function withSameOriginRequestUuid(value, requestUuid) {
    if (!value || !requestUuid || typeof window === "undefined" || !window.location) {
      return value;
    }
    try {
      const resolved = new URL(value, window.location.href);
      if (resolved.origin !== window.location.origin) {
        return value;
      }
      return withRequestUuid(resolved.href, requestUuid);
    } catch (_error) {
      return value;
    }
  }
  function isExternalAssetUrl(value) {
    var _a;
    if (!value) {
      return false;
    }
    try {
      const base = typeof window !== "undefined" && window.location ? window.location.href : "http://pytincture.invalid/";
      const resolved = new URL(value, base);
      if (typeof window === "undefined" || !((_a = window.location) == null ? void 0 : _a.origin)) {
        return resolved.origin !== "http://pytincture.invalid";
      }
      return resolved.origin !== window.location.origin;
    } catch (_error) {
      return false;
    }
  }
  function isValidSubresourceIntegrity(value) {
    if (typeof value !== "string") {
      return false;
    }
    const expectedBytes = { sha256: 32, sha384: 48, sha512: 64 };
    const tokens = value.trim().split(/\s+/);
    if (!tokens.length || !tokens[0] || typeof globalThis.atob !== "function") {
      return false;
    }
    return tokens.every((token) => {
      const match = /^(sha256|sha384|sha512)-([A-Za-z0-9+/]+={0,2})$/.exec(token);
      if (!match) {
        return false;
      }
      try {
        return globalThis.atob(match[2]).length === expectedBytes[match[1]];
      } catch (_error) {
        return false;
      }
    });
  }
  function serviceFrontendUrl(application, value) {
    if (!application || typeof value !== "string") {
      return value;
    }
    if (value.startsWith("frontend/") || value.startsWith("./frontend/")) {
      return `/${application}/${value.replace(/^\.\//, "")}`;
    }
    return value;
  }
  function alignDefaultMaterialIconsUrl(config) {
    if (config.application || config.materialIconsUrl !== DEFAULT_CONFIG.materialIconsUrl || isExternalAssetUrl(config.pyodideBaseUrl)) {
      return config.materialIconsUrl;
    }
    const marker = config.pyodideBaseUrl.indexOf("pyodide/");
    if (marker < 0) {
      return config.materialIconsUrl;
    }
    return `${config.pyodideBaseUrl.slice(0, marker)}vendor/materialdesignicons/materialdesignicons.css`;
  }
  function defaultCsrfCookieName() {
    var _a;
    if (typeof window !== "undefined" && ((_a = window.location) == null ? void 0 : _a.protocol) === "https:") {
      return "__Host-pytincture-csrf";
    }
    return "pytincture-dev-csrf";
  }
  function normalizeCsrfCookieName(cookieName) {
    const selected = cookieName || defaultCsrfCookieName();
    if (!CSRF_COOKIE_NAMES.includes(selected)) {
      throw new Error("Unsupported Pytincture CSRF cookie name.");
    }
    return selected;
  }
  function readCookieValue(cookieText, cookieName) {
    const selected = normalizeCsrfCookieName(cookieName);
    for (const cookie of String(cookieText || "").split(";")) {
      const [name, ...value] = cookie.trim().split("=");
      if (name === selected) {
        return value.join("=");
      }
    }
    return "";
  }
  function normalizeConfig(arg1, widgetlib, entrypoint) {
    const resolveDevWidgetHost = (host) => {
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
      merged.csrfCookieName = normalizeCsrfCookieName(merged.csrfCookieName);
      merged.entrypoint = merged.entrypoint || merged.application;
      merged.devWidgetHost = resolveDevWidgetHost(merged.devWidgetHost);
      if (merged.allowPublicWidgetIndex === null) {
        merged.allowPublicWidgetIndex = !merged.application;
      }
      if (merged.application) {
        if (merged.serviceWorkerUrl === DEFAULT_CONFIG.serviceWorkerUrl) {
          merged.serviceWorkerUrl = `/${merged.application}/frontend/sw.js`;
        }
        if (merged.serviceWorkerScope === DEFAULT_CONFIG.serviceWorkerScope) {
          merged.serviceWorkerScope = `/${merged.application}/`;
        }
      }
      if (!("enableBackendLogging" in arg1)) {
        merged.enableBackendLogging = !!merged.application;
      }
      merged.pyodideBaseUrl = ensureTrailingSlash(
        serviceFrontendUrl(merged.application, merged.pyodideBaseUrl)
      );
      merged.materialIconsUrl = serviceFrontendUrl(
        merged.application,
        merged.materialIconsUrl
      );
      merged.materialIconsUrl = alignDefaultMaterialIconsUrl(merged);
      return merged;
    }
    const application = arg1 || null;
    const config = {
      ...DEFAULT_CONFIG,
      application,
      widgetlib: widgetlib || DEFAULT_CONFIG.widgetlib,
      entrypoint: entrypoint || application
    };
    config.pyodideBaseUrl = ensureTrailingSlash(config.pyodideBaseUrl);
    config.requestUuid = config.requestUuid || makeRequestId();
    config.csrfCookieName = normalizeCsrfCookieName(config.csrfCookieName);
    config.devWidgetHost = resolveDevWidgetHost(config.devWidgetHost);
    config.allowPublicWidgetIndex = !config.application;
    if (config.application) {
      config.serviceWorkerUrl = `/${config.application}/frontend/sw.js`;
      config.serviceWorkerScope = `/${config.application}/`;
    }
    config.enableBackendLogging = !!application;
    config.pyodideBaseUrl = ensureTrailingSlash(
      serviceFrontendUrl(config.application, config.pyodideBaseUrl)
    );
    config.materialIconsUrl = serviceFrontendUrl(
      config.application,
      config.materialIconsUrl
    );
    config.materialIconsUrl = alignDefaultMaterialIconsUrl(config);
    return config;
  }
  function preflightConfig(config) {
    var _a;
    if (typeof document === "undefined" || typeof fetch !== "function") {
      throw new Error("A browser document and Fetch API are required.");
    }
    if (!(/* @__PURE__ */ new Set(["auto", "package", "inline"])).has(config.mode)) {
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
    if (isExternalAssetUrl(config.pyodideBaseUrl)) {
      if (config.allowUnverifiedExternalPyodide !== true) {
        throw new Error(
          "External Pyodide requires allowUnverifiedExternalPyodide=true; self-host the verified runtime for production."
        );
      }
      for (const filename of ["pyodide.js", "pyodide.asm.js"]) {
        if (!isValidSubresourceIntegrity((_a = config.pyodideScriptIntegrity) == null ? void 0 : _a[filename])) {
          throw new Error(
            `External Pyodide requires pyodideScriptIntegrity[${JSON.stringify(filename)}].`
          );
        }
      }
    }
    if (config.loadMaterialIcons && isExternalAssetUrl(config.materialIconsUrl) && !isValidSubresourceIntegrity(config.materialIconsIntegrity)) {
      throw new Error("External Material Icons require materialIconsIntegrity.");
    }
    validatePackageRequirement(config.widgetSource || config.widgetlib, {
      label: "Widgetset",
      allowPackagePin: !config.widgetSource
    });
    if (typeof config.allowPublicWidgetIndex !== "boolean") {
      throw new Error("allowPublicWidgetIndex must be a boolean.");
    }
    if (config.backendWidgetSources !== null && (!Array.isArray(config.backendWidgetSources) || config.backendWidgetSources.some((source) => typeof source !== "string" || !source))) {
      throw new Error("backendWidgetSources must be null or an array of wheel URLs.");
    }
    return {
      runtime: "pytincture",
      runtimeVersion: PYTINCTURE_RUNTIME_VERSION,
      pyodideBaseUrl: sanitizeResource(config.pyodideBaseUrl),
      widgetset: sanitizeDiagnostic(config.widgetSource || config.widgetlib || "none")
    };
  }
  function isHashPinnedWheelUrl(value) {
    try {
      const parsed = new URL(value, "http://pytincture.invalid/");
      return parsed.pathname.toLowerCase().endsWith(".whl") && /^sha256=[a-f0-9]{64}$/i.test(parsed.hash.slice(1));
    } catch (_error) {
      return false;
    }
  }
  function isExactPackagePin(value) {
    return /^[A-Za-z0-9_.-]+==[A-Za-z0-9][A-Za-z0-9_.+!-]*$/.test(value || "");
  }
  function validatePackageRequirement(value, { label = "Browser package", allowPackagePin = true } = {}) {
    if (!value) {
      return;
    }
    if (allowPackagePin && isExactPackagePin(value) || isHashPinnedWheelUrl(value)) {
      return;
    }
    throw new Error(
      `${label} must use an exact name==version pin or a wheel URL with a #sha256=<64 hex> fragment.`
    );
  }
  function preflightPyodide(pyodide) {
    const requiredMethods = ["loadPackage", "runPython", "runPythonAsync", "unpackArchive"];
    const missing = requiredMethods.filter((name) => typeof (pyodide == null ? void 0 : pyodide[name]) !== "function");
    if (!(pyodide == null ? void 0 : pyodide.FS) || missing.length) {
      throw new Error(`Incompatible Pyodide runtime; missing: ${[...missing, ...!(pyodide == null ? void 0 : pyodide.FS) ? ["FS"] : []].join(", ")}`);
    }
    return {
      pyodideVersion: sanitizeDiagnostic(pyodide.version || "unknown"),
      pythonVersion: sanitizeDiagnostic(
        typeof pyodide.runPython === "function" ? pyodide.runPython("import platform; platform.python_version()") : "unknown"
      )
    };
  }
  function loadScript(url, requestUuid, integrity = null) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = withSameOriginRequestUuid(url, requestUuid);
      if (integrity) {
        script.integrity = integrity;
        script.crossOrigin = "anonymous";
      }
      script.onload = resolve;
      script.onerror = () => reject(new Error(`Failed to load script: ${url}`));
      document.head.appendChild(script);
    });
  }
  function ensureMaterialIcons(url, requestUuid, integrity = null) {
    if (!url) {
      return Promise.resolve();
    }
    const stylesheetUrl = withSameOriginRequestUuid(url, requestUuid);
    const existing = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).find(
      (link) => link.href === stylesheetUrl
    );
    if (existing) {
      if (integrity && (existing.integrity !== integrity || isExternalAssetUrl(url) && existing.crossOrigin !== "anonymous")) {
        return Promise.reject(new Error(
          `Existing stylesheet was not loaded with the required integrity: ${url}`
        ));
      }
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      const link = document.createElement("link");
      link.href = stylesheetUrl;
      link.rel = "stylesheet";
      link.type = "text/css";
      link.media = "all";
      if (integrity) {
        link.integrity = integrity;
        link.crossOrigin = "anonymous";
      }
      link.onload = resolve;
      link.onerror = () => reject(new Error(`Failed to load stylesheet: ${url}`));
      document.head.appendChild(link);
    });
  }
  function enableBackendLogging(endpoint, csrfCookieName) {
    if (loggingInstalled) {
      return;
    }
    const logEndpoint = endpoint || "/logs";
    const levels = ["log", "warn", "error", "info", "debug"];
    levels.forEach((level) => {
      if (typeof console[level] === "function") {
        originalConsoleMethods[level] = console[level].bind(console);
      }
    });
    function sendToBackend(level, message) {
      const csrfToken = readCookieValue(document.cookie, csrfCookieName);
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
          timestamp: (/* @__PURE__ */ new Date()).toISOString()
        })
      }).catch((err) => {
        const fallbackError = originalConsoleMethods.error || console.error.bind(console);
        fallbackError("Failed to send log to backend:", err);
      });
    }
    levels.forEach((level) => {
      if (typeof console[level] !== "function" || !originalConsoleMethods[level]) {
        return;
      }
      console[level] = function(...args) {
        const message = sanitizeConsoleMessage(args);
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
    const overlayId = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(config.loadingOverlayId) ? config.loadingOverlayId : DEFAULT_CONFIG.loadingOverlayId;
    overlay.id = overlayId;
    const card = document.createElement("div");
    card.className = "pytincture-loading__card";
    const title = document.createElement("div");
    title.className = "pytincture-loading__title";
    title.textContent = String(config.loadingTitle || "Loading application");
    const status = document.createElement("div");
    status.className = "pytincture-loading__status";
    status.textContent = "Loading\u2026";
    const bar = document.createElement("div");
    bar.className = "pytincture-loading__bar";
    const barInner = document.createElement("div");
    barInner.className = "pytincture-loading__bar-inner";
    bar.appendChild(barInner);
    card.append(title, status, bar);
    overlay.appendChild(card);
    const style = document.createElement("style");
    style.textContent = `
      #${overlayId} {
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
      await unregisterOwnedServiceWorker(config);
      return;
    }
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
      return;
    }
    try {
      const workerUrl = new URL(config.serviceWorkerUrl, window.location.href);
      workerUrl.searchParams.set("uuid", config.requestUuid);
      workerUrl.searchParams.set("application", config.application || "standalone");
      workerUrl.searchParams.set("release", PYTINCTURE_RUNTIME_VERSION);
      const registration = await navigator.serviceWorker.register(workerUrl.href, {
        scope: config.serviceWorkerScope
      });
      await waitForServiceWorkerActivation(registration);
      await waitForServiceWorkerControl(registration);
      await deleteStaleOwnedCaches(config);
    } catch (err) {
      console.warn("Service worker registration failed:", err);
    }
  }
  async function waitForServiceWorkerControl(registration) {
    if (navigator.serviceWorker.controller) {
      return registration;
    }
    await Promise.race([
      new Promise((resolve) => {
        const handleControllerChange = () => {
          navigator.serviceWorker.removeEventListener("controllerchange", handleControllerChange);
          resolve(registration);
        };
        navigator.serviceWorker.addEventListener("controllerchange", handleControllerChange);
      }),
      new Promise((resolve) => setTimeout(() => resolve(registration), 5e3))
    ]);
    return registration;
  }
  async function waitForServiceWorkerActivation(registration) {
    if (registration.active) {
      return registration;
    }
    const worker = registration.installing || registration.waiting;
    if (!worker) {
      return registration;
    }
    await Promise.race([
      new Promise((resolve) => {
        const handleStateChange = () => {
          if (worker.state === "activated" || worker.state === "redundant") {
            worker.removeEventListener("statechange", handleStateChange);
            resolve(registration);
          }
        };
        worker.addEventListener("statechange", handleStateChange);
        handleStateChange();
      }),
      new Promise((resolve) => setTimeout(() => resolve(registration), 5e3))
    ]);
    return registration;
  }
  function ownedCachePrefix(config) {
    const application = encodeURIComponent(config.application || "standalone");
    return `pytincture:${application}:`;
  }
  function frameworkCacheName(config) {
    return `${ownedCachePrefix(config)}${PYTINCTURE_RUNTIME_VERSION}:${config.requestUuid}`;
  }
  async function deleteStaleOwnedCaches(config) {
    if (typeof caches === "undefined") {
      return;
    }
    const currentCache = frameworkCacheName(config);
    const keys = await caches.keys();
    await Promise.all(
      keys.filter((key) => key.startsWith(ownedCachePrefix(config)) && key !== currentCache).map((key) => caches.delete(key))
    );
  }
  function frameworkAssetUrls(config) {
    if (typeof window === "undefined" || !window.location) {
      return [];
    }
    const assetBaseUrl = config.application ? new URL(`/${config.application}/frontend/`, window.location.href) : new URL(config.serviceWorkerScope, window.location.href);
    const relativePaths = [
      "pytincture.js",
      "vendor/materialdesignicons/materialdesignicons.css",
      "vendor/materialdesignicons/fonts/materialdesignicons-webfont.woff2",
      "pyodide/0.29.3/full/pyodide.js",
      "pyodide/0.29.3/full/pyodide.asm.js",
      "pyodide/0.29.3/full/pyodide.asm.wasm",
      "pyodide/0.29.3/full/pyodide-lock.json",
      "pyodide/0.29.3/full/python_stdlib.zip",
      "pyodide/0.29.3/full/micropip-0.11.0-py3-none-any.whl",
      "pyodide/0.29.3/full/micropip-0.11.0-py3-none-any.whl.metadata"
    ];
    return relativePaths.map((path) => withRequestUuid(new URL(path, assetBaseUrl).href, config.requestUuid));
  }
  function responseIsPublicImmutable(response) {
    var _a, _b, _c, _d, _e, _f;
    if (!(response == null ? void 0 : response.ok) || response.type === "opaque") {
      return false;
    }
    const cacheControl = ((_b = (_a = response.headers) == null ? void 0 : _a.get) == null ? void 0 : _b.call(_a, "cache-control")) || "";
    const vary = ((_d = (_c = response.headers) == null ? void 0 : _c.get) == null ? void 0 : _d.call(_c, "vary")) || "";
    return !/(?:^|,)\s*(?:private|no-store)(?:\s|,|$)/i.test(cacheControl) && !/(?:^|,)\s*(?:cookie|authorization)(?:\s|,|$)/i.test(vary) && !((_f = (_e = response.headers) == null ? void 0 : _e.get) == null ? void 0 : _f.call(_e, "set-cookie"));
  }
  async function unregisterOwnedServiceWorker(config) {
    var _a, _b, _c;
    if (!config.application || typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
      return false;
    }
    const scopeUrl = new URL(config.serviceWorkerScope, window.location.href);
    const expectedWorker = new URL(config.serviceWorkerUrl, window.location.href);
    const registration = await navigator.serviceWorker.getRegistration(scopeUrl.href);
    const activeUrl = ((_a = registration == null ? void 0 : registration.active) == null ? void 0 : _a.scriptURL) || ((_b = registration == null ? void 0 : registration.waiting) == null ? void 0 : _b.scriptURL) || ((_c = registration == null ? void 0 : registration.installing) == null ? void 0 : _c.scriptURL) || "";
    let removed = false;
    if (registration && activeUrl && new URL(activeUrl).pathname === expectedWorker.pathname) {
      removed = await registration.unregister();
    }
    if (typeof caches !== "undefined") {
      const keys = await caches.keys();
      await Promise.all(
        keys.filter((key) => key.startsWith(ownedCachePrefix(config))).map((key) => caches.delete(key))
      );
    }
    return removed;
  }
  async function warmPyodideCache(config) {
    if (!config.enableServiceWorker || !config.warmPyodideCache) {
      return;
    }
    if (typeof caches === "undefined") {
      return;
    }
    const resources = frameworkAssetUrls(config);
    try {
      const cacheName = frameworkCacheName(config);
      const cache = await caches.open(cacheName);
      const cachedUrls = new Set((await cache.keys()).map((request) => request.url));
      for (const url of resources) {
        const cacheRequest = new Request(url, { credentials: "omit" });
        if (!cachedUrls.has(cacheRequest.url)) {
          const networkUrl = new URL(url);
          networkUrl.searchParams.set("pytincture_warm", "1");
          const networkRequest = new Request(networkUrl.href, {
            credentials: "omit"
          });
          const response = await fetch(networkRequest, { cache: "no-store" });
          if (responseIsPublicImmutable(response)) {
            await cache.put(cacheRequest, response.clone());
            cachedUrls.add(cacheRequest.url);
          }
        }
      }
    } catch (err) {
      console.warn("Pyodide cache warm failed:", err);
    }
  }
  async function ensurePyodideLoaded(config) {
    var _a, _b;
    if (typeof loadPyodide !== "function") {
      window.languagePluginUrl = config.pyodideBaseUrl;
      await loadScript(
        `${config.pyodideBaseUrl}pyodide.js`,
        config.requestUuid,
        (_a = config.pyodideScriptIntegrity) == null ? void 0 : _a["pyodide.js"]
      );
    }
    if (typeof _createPyodideModule !== "function") {
      await loadScript(
        `${config.pyodideBaseUrl}pyodide.asm.js`,
        config.requestUuid,
        (_b = config.pyodideScriptIntegrity) == null ? void 0 : _b["pyodide.asm.js"]
      );
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
    if (!Array.isArray(libs) || libs.some((lib) => typeof lib !== "string" || !lib.trim())) {
      throw new Error("micropip-libs must be a JSON array of non-empty package strings.");
    }
    for (const lib of libs) {
      validatePackageRequirement(lib, { label: "micropip-libs entry" });
      const libLiteral = JSON.stringify(lib);
      await pyodide.runPythonAsync(`
import micropip
await micropip.install(${libLiteral}, deps=False)
        `);
    }
  }
  async function probeBackendWheel(url) {
    var _a, _b, _c, _d, _e, _f, _g;
    let response;
    try {
      response = await fetch(url, { method: "HEAD" });
    } catch (err) {
      console.warn(`Failed to check URL: ${url}`, err);
      return null;
    }
    if (!response.ok) {
      try {
        await ((_a = response.body) == null ? void 0 : _a.cancel());
      } catch (_error) {
      }
      return null;
    }
    let sha256 = ((_c = (_b = response.headers) == null ? void 0 : _b.get) == null ? void 0 : _c.call(_b, "x-pytincture-sha256")) || "";
    if (!/^[a-f0-9]{64}$/i.test(sha256)) {
      try {
        response = await fetch(url);
      } catch (err) {
        console.warn(`Failed to check URL: ${url}`, err);
        return null;
      }
      if (!response.ok) {
        try {
          await ((_d = response.body) == null ? void 0 : _d.cancel());
        } catch (_error) {
        }
        return null;
      }
      sha256 = ((_f = (_e = response.headers) == null ? void 0 : _e.get) == null ? void 0 : _f.call(_e, "x-pytincture-sha256")) || "";
    }
    try {
      await ((_g = response.body) == null ? void 0 : _g.cancel());
    } catch (_error) {
    }
    if (!/^[a-f0-9]{64}$/i.test(sha256)) {
      throw new Error("Backend wheel response is missing a valid X-Pytincture-SHA256 header.");
    }
    return { url, sha256: sha256.toLowerCase() };
  }
  async function resolveBackendWidgetSources(config) {
    if (!config.application) {
      return [];
    }
    if (Array.isArray(config.backendWidgetSources)) {
      return config.backendWidgetSources.map((source) => withRequestUuid(source, config.requestUuid));
    }
    const match = (config.widgetlib || "").match(/^[A-Za-z0-9_.\-]+/);
    const widgetPackage = match ? match[0] : DEFAULT_CONFIG.widgetlib;
    const pinnedMatch = (config.widgetlib || "").match(
      /^[A-Za-z0-9_.\-]+==([A-Za-z0-9_.+!\-]+)$/
    );
    const candidateVersions = [];
    if (pinnedMatch) {
      candidateVersions.push(pinnedMatch[1]);
    }
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
    var _a, _b;
    const archiveUrl = withRequestUuid(`${config.application}/appcode/appcode.pyt`, config.requestUuid);
    const response = await fetch(archiveUrl);
    const correlationId = ((_b = (_a = response.headers) == null ? void 0 : _a.get) == null ? void 0 : _b.call(_a, "x-request-id")) || null;
    if (!response.ok) {
      throw new PytinctureLifecycleError({
        stage: LIFECYCLE_STAGES.ARCHIVE_DOWNLOAD,
        code: [404, 410].includes(response.status) ? "package_unavailable" : "archive_download_failed",
        resource: archiveUrl,
        requestId: config.requestUuid,
        correlationId,
        cause: `HTTP ${response.status}`
      });
    }
    try {
      return {
        binary: await response.arrayBuffer(),
        resource: archiveUrl,
        correlationId
      };
    } catch (error) {
      throw new PytinctureLifecycleError({
        stage: LIFECYCLE_STAGES.ARCHIVE_DOWNLOAD,
        code: "archive_read_failed",
        resource: archiveUrl,
        requestId: config.requestUuid,
        correlationId,
        cause: error
      });
    }
  }
  function unpackPackagedApp(pyodide, downloaded) {
    pyodide.unpackArchive(downloaded.binary, "zip");
  }
  async function executePackagedApp(pyodide, config) {
    await pyodide.runPythonAsync(`
import importlib as _pytincture_importlib
import inspect as _pytincture_inspect
_pytincture_module = _pytincture_importlib.import_module(${JSON.stringify(config.application)})
_pytincture_entry_name = ${JSON.stringify(config.entrypoint || "")}
if _pytincture_entry_name == ${JSON.stringify(config.application)} and not hasattr(_pytincture_module, _pytincture_entry_name):
    _pytincture_entry_name = ''
if not _pytincture_entry_name:
    _pytincture_conventional = getattr(_pytincture_module, ${JSON.stringify(config.application)}, None)
    if callable(_pytincture_conventional):
        _pytincture_entry_name = ${JSON.stringify(config.application)}
    else:
        _pytincture_candidates = []
        for _name, _value in vars(_pytincture_module).items():
            if _pytincture_inspect.isclass(_value) and any(
                _base.__name__ == 'MainWindow' for _base in getattr(_value, '__mro__', ())[1:]
            ):
                _pytincture_candidates.append((_name, _value))
        _pytincture_local = [item for item in _pytincture_candidates if item[1].__module__ == _pytincture_module.__name__]
        if _pytincture_local or _pytincture_candidates:
            _pytincture_entry_name = (_pytincture_local or _pytincture_candidates)[0][0]
if not _pytincture_entry_name:
    raise RuntimeError('No MainWindow subclass, alias, or application entrypoint found')
_pytincture_result = getattr(_pytincture_module, _pytincture_entry_name)()
if _pytincture_inspect.isawaitable(_pytincture_result):
    await _pytincture_result
`);
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
            if any(base.__name__ == 'MainWindow' for base in getattr(obj, '__mro__', ())[1:]):
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
  async function installWidgetsetSource(pyodide, source, requestUuid = null) {
    const installSource = requestUuid ? withRequestUuid(source, requestUuid) : source;
    const sourceLiteral = JSON.stringify(installSource);
    await pyodide.runPythonAsync(`
import micropip
await micropip.install(${sourceLiteral}, deps=False)
    `);
  }
  async function installWidgetset(pyodide, config) {
    const primarySource = config.widgetSource || config.widgetlib;
    if (!primarySource) {
      return null;
    }
    validatePackageRequirement(primarySource, {
      label: "Widgetset",
      allowPackagePin: !config.widgetSource
    });
    if (config.widgetSource) {
      await installWidgetsetSource(pyodide, primarySource);
      return primarySource;
    }
    let lastInstallError = null;
    if (config.application) {
      const backendSources = await resolveBackendWidgetSources(config);
      for (const source of backendSources) {
        const backendWheel = await probeBackendWheel(source);
        if (!backendWheel) {
          continue;
        }
        try {
          const lockedSource = `${backendWheel.url}#sha256=${backendWheel.sha256}`;
          await installWidgetsetSource(pyodide, lockedSource, config.requestUuid);
          return lockedSource;
        } catch (error) {
          lastInstallError = error;
          console.warn(`Failed to install widgetset from ${sanitizeResource(source)}.`);
        }
      }
    }
    const builtinLockedSource = BUILTIN_WIDGET_WHEEL_LOCKS[primarySource];
    if (builtinLockedSource) {
      await installWidgetsetSource(pyodide, builtinLockedSource);
      return builtinLockedSource;
    }
    if (config.allowPublicWidgetIndex) {
      try {
        await installWidgetsetSource(pyodide, primarySource);
        return primarySource;
      } catch (error) {
        lastInstallError = error;
      }
    }
    throw lastInstallError || new Error(
      `No trusted backend wheel is available for ${primarySource}; configure the backend wheel or explicitly allow this exact public-index package.`
    );
  }
  async function loadWidgetsetAssets(pyodide, config, installedSource) {
    var _a, _b;
    if (!installedSource) {
      return { installedSource: null, javascriptAssets: 0, cssAssets: 0 };
    }
    const widgetPackageLiteral = JSON.stringify(
      ((_a = String(config.widgetlib || "").match(/^[A-Za-z0-9_.\-]+/)) == null ? void 0 : _a[0]) || ""
    );
    const configuredManifestLiteral = JSON.stringify(
      encodeURIComponent(JSON.stringify(config.widgetAssetManifest || null))
    );
    const builtinManifestsLiteral = JSON.stringify(BUILTIN_WIDGET_ASSET_MANIFESTS);
    const loadFilesCode = `
import js
import base64
import re
import json
import hashlib
import importlib.metadata
import posixpath
from urllib.parse import unquote

CONFIGURED_MANIFEST = json.loads(unquote(${configuredManifestLiteral}))
BUILTIN_MANIFESTS = ${builtinManifestsLiteral}
widget_package = ${widgetPackageLiteral}

if not widget_package:
    raise RuntimeError("An installed widget package is required to load assets")

distribution = importlib.metadata.distribution(widget_package)
widget_version = distribution.version
owned_files = {str(path).replace('\\\\', '/') for path in (distribution.files or ())}
manifest_paths = sorted(
    path for path in owned_files if path.endswith('/pytincture-assets.json')
)
if len(manifest_paths) > 1:
    raise RuntimeError("Widgetset owns multiple pytincture-assets.json manifests")
manifest_path = manifest_paths[0] if manifest_paths else None

if CONFIGURED_MANIFEST is not None:
    manifest = CONFIGURED_MANIFEST
    manifest_source = "runtime configuration"
elif manifest_path:
    manifest = json.loads(distribution.locate_file(manifest_path).read_text(encoding="utf-8"))
    manifest_source = manifest_path
else:
    manifest_key = f"{widget_package.lower().replace('-', '_')}@{widget_version}"
    manifest = BUILTIN_MANIFESTS.get(manifest_key)
    manifest_source = f"Pytincture compatibility lock {manifest_key}"

if not isinstance(manifest, dict):
    raise RuntimeError(
        f"Widgetset {widget_package}=={widget_version} must provide an owned, "
        "explicitly hashed pytincture-assets.json manifest"
    )
if manifest.get("schema") != 1:
    raise RuntimeError("Widget asset manifest schema must be 1")
normalize_name = lambda value: re.sub(r'[-_.]+', '_', str(value).lower())
manifest_package = normalize_name(manifest.get("package") or "")
if manifest_package != normalize_name(widget_package):
    raise RuntimeError("Widget asset manifest package does not match the installed widgetset")
manifest_version = str(manifest.get("version") or "")
if manifest_version != widget_version:
    raise RuntimeError("Widget asset manifest version does not match the installed widgetset")
assets = manifest.get("assets")
if not isinstance(assets, list) or not assets or len(assets) > 128:
    raise RuntimeError("Widget asset manifest must declare between 1 and 128 assets")

def replace_font_urls(css_content, css_asset):
    mime_types = {
        'woff': 'font/woff',
        'woff2': 'font/woff2',
        'ttf': 'font/ttf',
        'otf': 'font/otf',
        'eot': 'application/vnd.ms-fontobject',
    }

    def try_inline(url_value):
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
        if path.startswith('/') or '\\\\' in path:
            return None
        candidate = posixpath.normpath(posixpath.join(posixpath.dirname(css_asset), path))
        if candidate.startswith('../') or candidate not in owned_files:
            return None
        ext = posixpath.splitext(candidate)[1].lstrip('.').lower()
        if ext not in mime_types:
            return None
        try:
            with open(distribution.locate_file(candidate), "rb") as f:
                font_data = base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            return None
        return f"url(data:{mime_types[ext]};base64,{font_data})"

    def repl(match):
        raw = match.group(1)
        if not raw:
            return match.group(0)
        cleaned = raw.strip().strip("'").strip('"')
        data_uri = try_inline(cleaned)
        if data_uri:
            return f"url('{data_uri[4:-1]}')" if data_uri.startswith('url(') else data_uri
        return f"url('{cleaned}')"

    return re.sub(r"url\\(([^)]+)\\)", repl, css_content, flags=re.IGNORECASE)

javascript_assets = 0
css_assets = 0

seen_paths = set()
for asset in assets:
    if not isinstance(asset, dict):
        raise RuntimeError("Every widget asset declaration must be an object")
    asset_path = str(asset.get("path") or "")
    asset_type = str(asset.get("type") or "")
    expected_hash = str(asset.get("sha256") or "").lower()
    normalized_path = posixpath.normpath(asset_path)
    if (
        not asset_path
        or normalized_path != asset_path
        or asset_path.startswith('/')
        or '\\\\' in asset_path
        or normalized_path.startswith('../')
        or asset_path in seen_paths
    ):
        raise RuntimeError(f"Invalid or duplicate widget asset path: {asset_path!r}")
    if asset_path not in owned_files:
        raise RuntimeError(f"Widget asset is not owned by {widget_package}: {asset_path}")
    if asset_type not in {'javascript', 'css'}:
        raise RuntimeError(f"Unsupported widget asset type for {asset_path}")
    expected_extension = '.js' if asset_type == 'javascript' else '.css'
    if not asset_path.lower().endswith(expected_extension):
        raise RuntimeError(f"Widget asset type does not match {asset_path}")
    if not re.fullmatch(r'[a-f0-9]{64}', expected_hash):
        raise RuntimeError(f"Widget asset is missing a SHA-256 lock: {asset_path}")

    asset_bytes = distribution.locate_file(asset_path).read_bytes()
    actual_hash = hashlib.sha256(asset_bytes).hexdigest()
    if actual_hash != expected_hash:
        raise RuntimeError(f"Widget asset integrity check failed: {asset_path}")
    asset_content = asset_bytes.decode('utf-8')
    seen_paths.add(asset_path)

    if asset_type == 'javascript':
        js.eval(asset_content)
        javascript_assets += 1
    else:
        style_content = replace_font_urls(asset_content, asset_path)
        style = js.document.createElement('style')
        style.textContent = style_content
        js.document.head.appendChild(style)
        css_assets += 1

json.dumps({
    "widgetPackage": widget_package or None,
    "widgetVersion": widget_version,
    "javascriptAssets": javascript_assets,
    "cssAssets": css_assets,
    "assetManifest": manifest_source,
    "dhxAvailable": bool(hasattr(js, "dhx")),
})
    `;
    const rawReport = await pyodide.runPythonAsync(loadFilesCode);
    const report = typeof rawReport === "string" ? JSON.parse(rawReport) : rawReport;
    if (((_b = report == null ? void 0 : report.widgetPackage) == null ? void 0 : _b.replace(/-/g, "_")) === "dhxpyt" && !report.dhxAvailable) {
      throw new Error("dhxpyt installed but its DHTMLX JavaScript assets did not expose window.dhx.");
    }
    return { installedSource: sanitizeResource(installedSource) || installedSource, ...report };
  }
  var DEFAULT_RUNTIME_OPERATIONS = Object.freeze({
    preflightConfig,
    ensureServiceWorker,
    ensureMaterialIcons,
    warmPyodideCache,
    ensurePyodideLoaded,
    loadPyodideRuntime: (options) => globalThis.loadPyodide(options),
    preflightPyodide,
    installExtraMicropipLibs,
    installWidgetset,
    loadWidgetsetAssets,
    downloadPackagedApp,
    unpackPackagedApp,
    executePackagedApp,
    runInlineApp
  });
  async function runStartup(config, loadingOverlay, operations = DEFAULT_RUNTIME_OPERATIONS) {
    if (config.runtime && !["pyodide", "micropython"].includes(config.runtime)) throw new Error("Unknown browser runtime");
    initializeRuntimeDiagnostics(config);
    const delivery = config.deliveryMode || "legacy-package";
    if (!["legacy-package", "portable-bundle"].includes(delivery)) throw new Error("Unknown application delivery mode");
    if (delivery === "legacy-package" && config.runtime && config.runtime !== "pyodide") {
      throw new Error("MicroPython requires deliveryMode=portable-bundle");
    }
    if (delivery === "portable-bundle") {
      const handle = await runLifecycleStage(
        config,
        LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
        config.runtimeManifestUrl,
        async () => {
          const { runBrowserApplication: runBrowserApplication2 } = await Promise.resolve().then(() => (init_browser_runtimes(), browser_runtimes_exports));
          return runBrowserApplication2(config, (message) => updateLoadingStatus(loadingOverlay, message));
        }
      );
      recordRuntimeIdentity(config, handle.runtime);
      await firstApplicationFrame(config);
      emitLifecycleEvent(config, "ready", LIFECYCLE_STAGES.READY, { runtime: handle.engine });
      return handle;
    }
    updateLoadingStatus(loadingOverlay, "Checking compatibility\u2026");
    const configReport = await runLifecycleStage(
      config,
      LIFECYCLE_STAGES.PREFLIGHT,
      config.pyodideBaseUrl,
      () => operations.preflightConfig(config)
    );
    await operations.ensureServiceWorker(config);
    if (config.loadMaterialIcons) {
      await operations.ensureMaterialIcons(
        config.materialIconsUrl,
        config.requestUuid,
        config.materialIconsIntegrity
      );
    }
    Promise.resolve(operations.warmPyodideCache(config)).catch((error) => {
      console.warn("Pyodide cache warm failed:", sanitizeDiagnostic((error == null ? void 0 : error.message) || error));
    });
    updateLoadingStatus(loadingOverlay, "Loading Pyodide\u2026");
    const runtimeResult = await runLifecycleStage(
      config,
      LIFECYCLE_STAGES.RUNTIME_LOAD,
      `${config.pyodideBaseUrl}pyodide.js`,
      async () => {
        await measureRuntimePhase(config, "runtime-download", `${config.pyodideBaseUrl}pyodide.js`, () => operations.ensurePyodideLoaded(config));
        const pyodide2 = await measureRuntimePhase(
          config,
          "runtime-initialization",
          config.pyodideBaseUrl,
          () => operations.loadPyodideRuntime({ indexURL: config.pyodideBaseUrl })
        );
        recordRuntimeIdentity(config, pyodide2);
        const report = await operations.preflightPyodide(pyodide2);
        return { pyodide: pyodide2, report };
      }
    );
    const { pyodide } = runtimeResult;
    globalThis.pytinctureBrowserRuntime = {
      engine: "pyodide",
      runtime: pyodide,
      captureOutput(source) {
        if (typeof source !== "string" || source.length > 1024 * 1024) throw new Error("Invalid Python snippet");
        return pyodide.runPython(`import io, contextlib
with contextlib.redirect_stdout(io.StringIO()) as output:
    exec(${JSON.stringify(source)}, {})
output.getvalue()`);
      }
    };
    updateLoadingStatus(loadingOverlay, "Installing packages\u2026");
    await runLifecycleStage(
      config,
      LIFECYCLE_STAGES.PACKAGE_INSTALL,
      "micropip",
      async () => {
        await pyodide.loadPackage("micropip");
        await operations.installExtraMicropipLibs(pyodide, config.libsSelector);
      }
    );
    updateLoadingStatus(loadingOverlay, "Installing widgetset\u2026");
    const installedSource = await runLifecycleStage(
      config,
      LIFECYCLE_STAGES.WIDGETSET_INSTALL,
      config.widgetSource || config.widgetlib,
      () => operations.installWidgetset(pyodide, config)
    );
    updateLoadingStatus(loadingOverlay, "Loading widget assets\u2026");
    const widgetReport = await runLifecycleStage(
      config,
      LIFECYCLE_STAGES.WIDGETSET_LOAD,
      installedSource,
      () => operations.loadWidgetsetAssets(pyodide, config, installedSource)
    );
    emitLifecycleEvent(config, "compatibility", LIFECYCLE_STAGES.WIDGETSET_LOAD, {
      compatibility: { ...configReport, ...runtimeResult.report, ...widgetReport }
    });
    updateLoadingStatus(loadingOverlay, "Starting app\u2026");
    if (config.mode === "inline") {
      await runLifecycleStage(
        config,
        LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
        config.inlineSelector,
        async () => {
          if (!await operations.runInlineApp(pyodide, config)) {
            throw new PytinctureLifecycleError({
              stage: LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
              code: "inline_app_unavailable",
              resource: config.inlineSelector,
              requestId: config.requestUuid,
              cause: "No inline application scripts were found."
            });
          }
        }
      );
    } else if (config.mode === "package" || config.application) {
      let downloaded;
      try {
        downloaded = await runLifecycleStage(
          config,
          LIFECYCLE_STAGES.ARCHIVE_DOWNLOAD,
          `${config.application}/appcode/appcode.pyt`,
          () => operations.downloadPackagedApp(config)
        );
      } catch (error) {
        if (!(error instanceof PytinctureLifecycleError) || error.code !== "package_unavailable" || config.mode === "package") {
          throw error;
        }
        emitLifecycleEvent(config, "fallback", LIFECYCLE_STAGES.ARCHIVE_DOWNLOAD, {
          from: "package",
          to: "inline",
          reason: error.toJSON()
        });
        await runLifecycleStage(
          config,
          LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
          config.inlineSelector,
          async () => {
            if (!await operations.runInlineApp(pyodide, config)) {
              throw new PytinctureLifecycleError({
                stage: LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
                code: "inline_app_unavailable",
                resource: config.inlineSelector,
                requestId: config.requestUuid,
                cause: "The package was unavailable and no inline application scripts were found."
              });
            }
          }
        );
        downloaded = null;
      }
      if (downloaded) {
        await runLifecycleStage(
          config,
          LIFECYCLE_STAGES.ARCHIVE_UNPACK,
          downloaded.resource,
          () => operations.unpackPackagedApp(pyodide, downloaded),
          { correlationId: downloaded.correlationId }
        );
        await runLifecycleStage(
          config,
          LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
          `${config.application}:${config.entrypoint || config.application}`,
          () => operations.executePackagedApp(pyodide, config),
          { correlationId: downloaded.correlationId }
        );
      }
    } else {
      await runLifecycleStage(
        config,
        LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
        config.inlineSelector,
        async () => {
          if (!await operations.runInlineApp(pyodide, config)) {
            throw new PytinctureLifecycleError({
              stage: LIFECYCLE_STAGES.ENTRYPOINT_EXECUTION,
              code: "application_unavailable",
              resource: config.inlineSelector,
              requestId: config.requestUuid,
              cause: "No packaged or inline application was available."
            });
          }
        }
      );
    }
    await firstApplicationFrame(config);
    emitLifecycleEvent(config, "ready", LIFECYCLE_STAGES.READY, {
      compatibility: { ...configReport, ...runtimeResult.report, ...widgetReport }
    });
    return pyodide;
  }
  async function runTinctureApp(arg1, widgetlib, entrypoint) {
    const config = normalizeConfig(arg1, widgetlib, entrypoint);
    globalThis.__pytinctureCsrfCookieName = config.csrfCookieName;
    const loadingOverlay = ensureLoadingOverlay(config);
    if (config.enableBackendLogging) {
      enableBackendLogging(config.logEndpoint, config.csrfCookieName);
    }
    try {
      const pyodide = await runStartup(config, loadingOverlay);
      removeLoadingOverlay(loadingOverlay);
      return pyodide;
    } catch (error) {
      const lifecycleError = error instanceof PytinctureLifecycleError ? error : new PytinctureLifecycleError({
        stage: LIFECYCLE_STAGES.PREFLIGHT,
        requestId: config.requestUuid,
        cause: error
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
      ...window.pytinctureAutoStartConfig || {}
    };
    runTinctureApp(inlineConfig).catch((error) => {
      console.error("Auto-start inline app failed:", error);
      const container = document.getElementById("maindiv");
      if (container) {
        const message = document.createElement("p");
        message.style.color = "red";
        message.textContent = `Error: ${error.message}`;
        container.replaceChildren(message);
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
    isExternalAssetUrl,
    isValidSubresourceIntegrity,
    responseIsPublicImmutable,
    sanitizeConsoleMessage,
    sanitizeDiagnostic,
    unregisterOwnedServiceWorker,
    validatePackageRequirement,
    withSameOriginRequestUuid,
    runLifecycleStage,
    runStartup,
    runTinctureApp
  });
})();
//# sourceMappingURL=pytincture.js.map
