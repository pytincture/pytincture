/* Opt-in live additions to an already running Pyodide platform.
 * Existing bundle startup is unchanged; no second interpreter or base WASM fetch.
 */
function sameOrigin(value, base) {
    const url = new URL(value, base);
    if (url.origin !== new URL(base).origin || !['http:', 'https:'].includes(url.protocol)
        || url.username || url.password || url.hash) throw new Error('Microapp URLs must use the platform origin');
    return url.href;
}

function assetUrl(value, base, origins = []) {
    const url = new URL(value, base);
    if (url.origin === new URL(base).origin) return sameOrigin(value, base);
    if (url.protocol !== 'https:' || url.username || url.password || url.hash || !origins.includes(url.origin)) {
        throw new Error('Unapproved microapp asset origin');
    }
    return url.href;
}

const bffObservers = new Set();
let restoreBffObserver = null;
export function observeBffResponses(listener) {
    bffObservers.add(listener);
    if (!restoreBffObserver) {
        const originalFetch = globalThis.fetch;
        const notify = response => {
            if (!response?.url || !response.url.includes('/classcall/')) return;
            for (const callback of bffObservers) callback(response);
        };
        const wrappedFetch = async (...args) => {
            const response = await originalFetch.apply(globalThis, args);
            notify(response);
            return response;
        };
        globalThis.fetch = wrappedFetch;
        const xhr = globalThis.XMLHttpRequest?.prototype;
        const open = xhr?.open;
        let xhrListener;
        if (xhr) {
            xhrListener = function (...args) {
                if (this.__pytinctureBffListener) this.removeEventListener('loadend', this.__pytinctureBffListener);
                this.__pytinctureBffListener = () => notify({url: this.responseURL,
                    headers: {get: name => this.getResponseHeader(name)}});
                this.addEventListener('loadend', this.__pytinctureBffListener);
                return open.apply(this, args);
            };
            xhr.open = xhrListener;
        }
        restoreBffObserver = () => {
            if (globalThis.fetch === wrappedFetch) globalThis.fetch = originalFetch;
            if (xhr && xhr.open === xhrListener) xhr.open = open;
            restoreBffObserver = null;
        };
    }
    return () => { bffObservers.delete(listener); if (!bffObservers.size) restoreBffObserver?.(); };
}

async function bytes(response, limit) {
    if (!response.ok) throw new Error(`Microapp request failed (${response.status})`);
    if (Number(response.headers.get('content-length') || 0) > limit) throw new Error('Microapp response exceeds byte limit');
    const chunks = []; let size = 0;
    const reader = response.body.getReader();
    try {
        while (true) {
            const {done, value} = await reader.read();
            if (done) break;
            size += value.byteLength;
            if (size > limit) throw new Error('Microapp response exceeds byte limit');
            chunks.push(value);
        }
    } catch (error) {
        await reader.cancel().catch(() => {});
        throw error;
    } finally { reader.releaseLock(); }
    const output = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.byteLength; }
    return output;
}

export function validateMicroappCatalog(document, base, origins = []) {
    if (document?.schema !== 1 || !Number.isSafeInteger(document.revision) || document.revision < 0
        || !Array.isArray(document.apps) || document.apps.length > 256) throw new Error('Invalid microapp catalog');
    const ids = new Set();
    let snapshot = null;
    if (document.snapshot) {
        const item = document.snapshot;
        if (!/^[a-f0-9]{64}$/.test(item.selection) || !/^[a-f0-9]{64}$/.test(item.sha256)
            || !Number.isSafeInteger(item.bytes) || item.bytes < 1 || item.bytes > 268435456
            || item.runtime?.engine !== 'pyodide' || item.runtime?.version !== '0.29.3') throw new Error('Invalid environment snapshot');
        snapshot = {...item, url: assetUrl(item.url, base, origins)};
    }
    return {schema: 1, revision: document.revision, snapshot, environment: document.environment,
        desired_revision: document.desired_revision, apps: document.apps.map(app => {
        const browser = app.browser;
        if (!/^[a-z][a-z0-9-]{0,63}$/.test(app.id) || ids.has(app.id)
            || !/^[a-f0-9]{64}$/.test(app.release) || typeof app.version !== 'string'
            || !/^[A-Za-z_]\w*$/.test(app.application)
            || browser?.runtime !== 'pyodide' || typeof browser.runtime_version !== 'string'
            || !/^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$/.test(browser.entrypoint)
            || !/^[a-f0-9]{64}$/.test(browser.sha256)
            || !Number.isSafeInteger(browser.bytes) || browser.bytes < 1 || browser.bytes > 134217728
            || !Array.isArray(browser.top_modules) || !browser.top_modules.length
            || browser.top_modules.some(name => !/^[A-Za-z_]\w*$/.test(name))) throw new Error('Invalid microapp release');
        ids.add(app.id);
        return {...app, bff_base: sameOrigin(app.bff_base, base),
            browser: {...browser, url: assetUrl(browser.url, base, origins)}};
    })};
}

export class PyodideMicroappAdapter {
    constructor(runtime) {
        if (!runtime?.unpackArchive || !runtime?.pyimport) throw new Error('A running Pyodide runtime is required');
        this.runtime = runtime;
    }
    async install(app, wheelBytes) {
        const runtime = this.runtime;
        if (runtime.version !== app.browser.runtime_version) throw new Error('Microapp requires a different runtime; reload or isolate it');
        // Avoid modifying a package already used by the fast initial bundle.
        const names = JSON.stringify(app.browser.top_modules);
        const conflicts = runtime.runPython(`__import__('json').dumps([n for n in ${names} if n in __import__('sys').modules or __import__('importlib.util', fromlist=['find_spec']).find_spec(n) is not None])`);
        if (JSON.parse(conflicts).length) throw new Error('Microapp module conflicts with the running bundle; use a unique namespace');
        const directory = `/pytincture_microapps/${app.id}/${app.release}`;
        runtime.FS.mkdirTree(directory);
        runtime.unpackArchive(wheelBytes, 'zip', {extractDir: directory});
        await runtime.runPythonAsync(`__import__('sys').path.insert(0, ${JSON.stringify(directory)})\n__import__('importlib').invalidate_caches()\n__import__('importlib').import_module(${JSON.stringify(app.browser.entrypoint.split(':')[0])})`);
    }
    async installSnapshot(catalog, data) {
        const snapshot = catalog.snapshot;
        if (this.runtime.version !== snapshot.runtime.version) throw new Error('Snapshot runtime mismatch');
        const directory = `/pytincture_environment/${snapshot.selection}`;
        this.runtime.FS.mkdirTree(directory);
        this.runtime.unpackArchive(data, 'zip', {extractDir: directory});
        const inventory = JSON.parse(this.runtime.FS.readFile(directory + '/pytincture-environment.json', {encoding: 'utf8'}));
        if (inventory.schema !== 1 || inventory.selection !== snapshot.selection
            || Object.keys(inventory.apps).length !== catalog.apps.length) throw new Error('Invalid embedded bundle inventory');
        for (const app of catalog.apps) {
            const bundled = inventory.apps[app.id];
            if (bundled?.release !== app.release || bundled.version !== app.version || bundled.sha256 !== app.browser.sha256) {
                throw new Error('Bundled app version does not match catalog');
            }
        }
        await this.runtime.runPythonAsync(`__import__('sys').path.insert(0, ${JSON.stringify(directory)})\n__import__('importlib').invalidate_caches()`);
        return inventory;
    }
    async invoke(app, hook, context, optional = false) {
        if (this.runtime.version !== app.browser.runtime_version) throw new Error('Microapp requires a different runtime');
        const module = this.runtime.pyimport(app.browser.entrypoint.split(':')[0]);
        let fn;
        try {
            const name = hook === 'mount' ? app.browser.entrypoint.split(':')[1] : hook;
            fn = module[name];
            if (typeof fn !== 'function') {
                if (optional) return;
                throw new Error(`Microapp is missing ${name}`);
            }
            const result = await fn(context);
            result?.destroy?.();
        } finally { fn?.destroy?.(); module.destroy?.(); }
    }
}

function defaultCsrfToken() {
    const name = globalThis.__pytinctureCsrfCookieName;
    if (!name || typeof document === 'undefined') return null;
    const entry = document.cookie.split(';').map(value => value.trim()).find(value => value.startsWith(name + '='));
    return entry ? decodeURIComponent(entry.slice(name.length + 1)) : null;
}

async function cleanupAll(cleanups) {
    const errors = [];
    for (const cleanup of [...cleanups].reverse()) {
        try { await cleanup(); } catch (error) { errors.push(error); }
        finally { cleanup.destroy?.(); }
    }
    if (errors.length) throw new AggregateError(errors, 'Microapp cleanup failed');
}

export class MicroappClient {
    constructor({runtime, adapter, catalogUrl = '/_microapps/catalog', baseUrl = globalThis.location?.href,
                 fetch: fetcher = globalThis.fetch.bind(globalThis), pollMs = 15000,
                 onCatalog = () => {}, onError = console.error, csrfToken = defaultCsrfToken,
                 bundledApps = {}, assetOrigins = [], autoSync = false,
                 onUpdateRequired = () => {}} = {}) {
        this.baseUrl = baseUrl;
        this.catalogUrl = sameOrigin(catalogUrl, baseUrl);
        for (const origin of assetOrigins) {
            if (new URL(origin).origin !== origin || !origin.startsWith('https://')) throw new Error('Asset origins must be exact HTTPS origins');
        }
        this.assetOrigins = [...assetOrigins]; this.autoSync = autoSync;
        this.onUpdateRequired = onUpdateRequired;
        this.observedSelection = null; this.observedRevision = -1;
        this.fetch = fetcher;
        this.adapter = adapter || new PyodideMicroappAdapter(runtime);
        if (!Number.isFinite(pollMs) || pollMs < 1000) throw new Error('pollMs must be at least 1000');
        this.pollMs = pollMs;
        this.onCatalog = onCatalog; this.onError = onError; this.csrfToken = csrfToken;
        this.available = new Map(); this.installed = new Map(); this.mounted = new Map();
        this.bundledApps = bundledApps;
        this.queue = Promise.resolve(); this.running = false; this.timer = null;
        this.refreshing = null;
        this.focus = () => { if (this.running) this.refresh().catch(this.onError); };
    }
    _serial(operation) {
        const result = this.queue.catch(() => {}).then(operation);
        this.queue = result;
        return result;
    }
    async _fetch(url, limit, cache = 'no-store') {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 35000);
        try {
            return await bytes(await this.fetch(url, {credentials: new URL(url).origin === new URL(this.baseUrl).origin ? 'same-origin' : 'omit', cache,
                redirect: 'error', signal: controller.signal}), limit);
        } finally { clearTimeout(timer); }
    }
    refresh() {
        if (this.refreshing) return this.refreshing;
        this.refreshing = (async () => {
            const raw = await this._fetch(this.catalogUrl, 1048576);
            const catalog = validateMicroappCatalog(JSON.parse(new TextDecoder().decode(raw)), this.baseUrl, this.assetOrigins);
            this.available = new Map(catalog.apps.map(app => [app.id, app]));
            this.revision = catalog.revision;
            this.catalog = catalog;
            this.observedRevision = Math.max(this.observedRevision, catalog.revision);
            this.observedSelection = catalog.snapshot?.selection || this.observedSelection;
            if (this.autoSync) await this.syncAvailable();
            this.onCatalog(catalog);
            return catalog;
        })().finally(() => { this.refreshing = null; });
        return this.refreshing;
    }
    async start({refresh = true} = {}) {
        if (this.running) return;
        this.running = true;
        if (this.autoSync) this._unobserve = observeBffResponses(response => this.noticeBffResponse(response));
        globalThis.addEventListener?.('focus', this.focus);
        const poll = async () => {
            if (!this.running) return;
            try { await this.refresh(); } catch (error) { this.onError(error); }
            if (this.running) this.timer = setTimeout(poll, this.pollMs);
        };
        if (refresh) await poll();
        else this.timer = setTimeout(poll, this.pollMs);
    }
    stop() {
        this.running = false;
        this._unobserve?.(); this._unobserve = null;
        clearTimeout(this.timer);
        globalThis.removeEventListener?.('focus', this.focus);
    }
    noticeBffResponse(response) {
        if (response.url && new URL(response.url, this.baseUrl).origin !== new URL(this.baseUrl).origin) return;
        const selected = response.headers?.get('x-pytincture-bundle');
        const revision = Number(response.headers?.get('x-pytincture-revision'));
        if (!this.autoSync || !selected || selected === this.observedSelection || revision < this.observedRevision) return;
        this.refresh().catch(this.onError);
    }
    async syncAvailable() {
        for (const app of this.available.values()) {
            const installed = this.installed.get(app.id);
            if (installed && installed.release !== app.release) {
                this.onUpdateRequired(app, installed);
                continue;
            }
            if (!installed) await this.install(app.id);
        }
    }
    async initializeSnapshot() {
        const catalog = await this.refresh();
        if (catalog.snapshot) {
            const snapshot = catalog.snapshot;
            const data = await this._fetch(snapshot.url, snapshot.bytes, 'no-cache');
            const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', data)), b => b.toString(16).padStart(2, '0')).join('');
            if (data.byteLength !== snapshot.bytes || hash !== snapshot.sha256) throw new Error('Environment bundle integrity check failed');
            const inventory = await this.adapter.installSnapshot(catalog, data);
            this.bundledApps = Object.fromEntries(Object.entries(inventory.apps).map(([id, app]) => [id, app.release]));
            for (const app of catalog.apps) this.installed.set(app.id, app);
            this.bundleInventory = inventory;
        } else {
            await this.syncAvailable();
        }
        this.autoSync = true;
        return catalog;
    }
    install(id) {
        return this._serial(async () => {
            const app = this.available.get(id);
            if (!app) throw new Error('Microapp is not enabled in this environment');
            const current = this.installed.get(id);
            const bundled = Object.hasOwn(this.bundledApps, id) ? this.bundledApps[id] : null;
            if (current?.release === app.release) return current;
            if (current || (bundled && bundled !== app.release)) throw new Error('Updating a loaded app requires a reload in this first version');
            if (bundled !== app.release) {
                const wheel = await this._fetch(app.browser.url, app.browser.bytes);
                if (wheel.byteLength !== app.browser.bytes) throw new Error('Microapp wheel size mismatch');
                const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', wheel)), b => b.toString(16).padStart(2, '0')).join('');
                if (hash !== app.browser.sha256) throw new Error('Microapp wheel integrity check failed');
                await this.adapter.install(app, wheel);
            }
            this.installed.set(id, app);
            return app;
        });
    }
    async mount(id, root) {
        await this.install(id);
        return this._serial(async () => {
            if (this.mounted.has(id)) throw new Error('Microapp is already mounted');
            const app = this.installed.get(id);
            const cleanups = [];
            const context = {root, id, version: app.version, release: app.release,
                addCleanup: fn => { if (typeof fn !== 'function') throw new Error('Cleanup must be callable'); cleanups.push(typeof fn.copy === 'function' ? fn.copy() : fn); },
                callBff: async (module, className, method, args = [], kwargs = {}) => {
                    if (!/^[A-Za-z0-9_./-]+\.py$/.test(module) || module.split('/').some(part => part === '..')
                        || !/^[A-Za-z_]\w*$/.test(className) || !/^[A-Za-z_]\w*$/.test(method)) throw new Error('Invalid BFF target');
                    // Convert Pyodide collections before serializing JSON.
                    const plain = value => value?.toJs ? value.toJs({dict_converter: Object.fromEntries}) : value;
                    const headers = {'Content-Type': 'application/json'};
                    const csrf = this.csrfToken();
                    if (csrf) headers['X-CSRF-Token'] = csrf;
                    const url = `${app.bff_base}/classcall/${module.split('/').map(encodeURIComponent).join('/')}/${encodeURIComponent(className)}/${encodeURIComponent(method)}`;
                    const response = await this.fetch(url, {method: 'POST', credentials: 'same-origin', redirect: 'error',
                        headers, body: JSON.stringify({args: plain(args), kwargs: plain(kwargs)})});
                    this.noticeBffResponse(response);
                    if (!response.ok) throw new Error(`Microapp BFF call failed (${response.status})`);
                    return response.json();
                }};
            try {
                await this.adapter.invoke(app, 'mount', context);
                this.mounted.set(id, {app, context, cleanups});
            } catch (error) {
                try { await cleanupAll(cleanups); }
                finally { root?.replaceChildren?.(); }
                throw error;
            }
            return context;
        });
    }
    unmount(id) {
        return this._serial(async () => {
            const mounted = this.mounted.get(id);
            if (!mounted) return;
            try { await this.adapter.invoke(mounted.app, 'unmount', mounted.context, true); }
            finally {
                try { await cleanupAll(mounted.cleanups); }
                finally {
                    mounted.context.root?.replaceChildren?.();
                    this.mounted.delete(id);
                }
            }
        });
    }
}


export async function bootstrapEnvironment(runtime, options) {
    if (runtime.__pytinctureEnvironmentClient) return runtime.__pytinctureEnvironmentClient;
    const client = new MicroappClient({...options, runtime,
        onCatalog: options.onCatalog || (catalog => globalThis.dispatchEvent?.(new CustomEvent('pytincture:microapps', {detail: catalog}))),
        onUpdateRequired: options.onUpdateRequired || (app => globalThis.dispatchEvent?.(new CustomEvent('pytincture:microapp-update', {detail: app})))});
    await client.initializeSnapshot();
    runtime.__pytinctureEnvironmentClient = client;
    globalThis.pytinctureMicroapps = client;
    await client.start({refresh: false});
    return client;
}
