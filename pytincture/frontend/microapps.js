/* Opt-in live additions to an already running Pyodide platform.
 * Existing bundle startup is unchanged; no second interpreter or base WASM fetch.
 */
function sameOrigin(value, base) {
    const url = new URL(value, base);
    if (url.origin !== new URL(base).origin || !['http:', 'https:'].includes(url.protocol)
        || url.username || url.password || url.hash) throw new Error('Microapp URLs must use the platform origin');
    return url.href;
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

export function validateMicroappCatalog(document, base) {
    if (document?.schema !== 1 || !Number.isSafeInteger(document.revision) || document.revision < 0
        || !Array.isArray(document.apps) || document.apps.length > 256) throw new Error('Invalid microapp catalog');
    const ids = new Set();
    return {schema: 1, revision: document.revision, apps: document.apps.map(app => {
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
            browser: {...browser, url: sameOrigin(browser.url, base)}};
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
                 bundledApps = {}} = {}) {
        this.baseUrl = baseUrl;
        this.catalogUrl = sameOrigin(catalogUrl, baseUrl);
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
    async _fetch(url, limit) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 35000);
        try {
            return await bytes(await this.fetch(url, {credentials: 'same-origin', cache: 'no-store',
                redirect: 'error', signal: controller.signal}), limit);
        } finally { clearTimeout(timer); }
    }
    refresh() {
        if (this.refreshing) return this.refreshing;
        this.refreshing = (async () => {
            const raw = await this._fetch(this.catalogUrl, 1048576);
            const catalog = validateMicroappCatalog(JSON.parse(new TextDecoder().decode(raw)), this.baseUrl);
            this.available = new Map(catalog.apps.map(app => [app.id, app]));
            this.revision = catalog.revision;
            this.onCatalog(catalog);
            return catalog;
        })().finally(() => { this.refreshing = null; });
        return this.refreshing;
    }
    async start() {
        if (this.running) return;
        this.running = true;
        globalThis.addEventListener?.('focus', this.focus);
        const poll = async () => {
            if (!this.running) return;
            try { await this.refresh(); } catch (error) { this.onError(error); }
            if (this.running) this.timer = setTimeout(poll, this.pollMs);
        };
        await poll();
    }
    stop() {
        this.running = false;
        clearTimeout(this.timer);
        globalThis.removeEventListener?.('focus', this.focus);
    }
    install(id) {
        return this._serial(async () => {
            const app = this.available.get(id);
            if (!app) throw new Error('Microapp is not enabled in this environment');
            const current = this.installed.get(id);
            if (current?.release === app.release) return current;
            if (current || (this.bundledApps[id] && this.bundledApps[id] !== app.release)) throw new Error('Updating a loaded app requires a reload in this first version');
            if (this.bundledApps[id] !== app.release) {
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
