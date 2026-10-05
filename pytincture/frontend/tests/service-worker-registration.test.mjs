import assert from "node:assert/strict";
import test from "node:test";

await import("../pytincture.js");
const { normalizeConfig, DEFAULT_RUNTIME_OPERATIONS } = globalThis.__pytinctureTesting;

async function fixture(run, { path = "/alpha/", active = true } = {}) {
    const originals = Object.fromEntries(["window", "navigator", "caches", "setTimeout", "clearTimeout"]
        .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    const listeners = new Map();
    const timers = new Map();
    const worker = {
        scriptURL: "https://example.test/alpha/frontend/sw.js",
        state: active ? "activated" : "installing",
        addEventListener: (type, callback) => listeners.set(type, callback),
        removeEventListener: type => listeners.delete(type),
    };
    const registration = { scope: "https://example.test/alpha/", active: active ? worker : null,
        installing: active ? null : worker };
    const serviceWorker = {
        controller: null,
        register: async () => registration,
        addEventListener: (type, callback) => listeners.set(type, callback),
        removeEventListener: type => listeners.delete(type),
    };
    let cacheOpens = 0;
    const values = {
        window: { location: new URL(`https://example.test${path}`) },
        navigator: { serviceWorker },
        caches: { keys: async () => [], open: async () => { cacheOpens++; return { keys: async () => [] }; } },
        setTimeout: (callback, ms) => { const id = {}; timers.set(id, { callback, ms }); return id; },
        clearTimeout: id => timers.delete(id),
    };
    for (const [key, value] of Object.entries(values)) {
        Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
    }
    const config = normalizeConfig({ application: "alpha", requestUuid: "instance", enableServiceWorker: true });
    const settle = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
    try {
        await run({ config, worker, registration, serviceWorker, listeners, timers, settle,
            cacheOpens: () => cacheOpens });
    } finally {
        for (const [key, descriptor] of Object.entries(originals)) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete globalThis[key];
        }
    }
}

for (const path of ["/alpha", "/alpha2/"]) {
    test(`out-of-scope page ${path} does not wait or warm an unused cache`, async () => {
        await fixture(async ({ config, listeners, timers, cacheOpens }) => {
            await DEFAULT_RUNTIME_OPERATIONS.ensureServiceWorker(config);
            await DEFAULT_RUNTIME_OPERATIONS.warmPyodideCache(config);
            assert.equal(listeners.size, 0);
            assert.equal(timers.size, 0);
            assert.equal(cacheOpens(), 0);
        }, { path });
    });
}

test("an unrelated controller cannot satisfy the application worker wait", async () => {
    await fixture(async ({ config, serviceWorker, worker, listeners, timers, settle }) => {
        serviceWorker.controller = { scriptURL: "https://example.test/root-sw.js" };
        const waiting = DEFAULT_RUNTIME_OPERATIONS.ensureServiceWorker(config);
        await settle();
        assert.equal(timers.size, 1);
        listeners.get("controllerchange")();
        assert.equal(timers.size, 1);
        serviceWorker.controller = worker;
        listeners.get("controllerchange")();
        await waiting;
        assert.equal(listeners.size, 0);
        assert.equal(timers.size, 0);
    });
});

test("control timeout removes its listener and timer", async () => {
    await fixture(async ({ config, listeners, timers, settle }) => {
        const waiting = DEFAULT_RUNTIME_OPERATIONS.ensureServiceWorker(config);
        await settle();
        const timer = [...timers.values()][0];
        assert.equal(timer.ms, 5000);
        timer.callback();
        await waiting;
        assert.equal(listeners.size, 0);
        assert.equal(timers.size, 0);
    });
});

for (const timeout of [false, true]) {
    test(`activation ${timeout ? "timeout" : "success"} cleans up its listener and timer`, async () => {
        await fixture(async ({ config, worker, registration, serviceWorker, listeners, timers, settle }) => {
            const waiting = DEFAULT_RUNTIME_OPERATIONS.ensureServiceWorker(config);
            await settle();
            assert.equal(timers.size, 1);
            if (timeout) {
                [...timers.values()][0].callback();
            } else {
                worker.state = "activated";
                registration.active = worker;
                serviceWorker.controller = worker;
                listeners.get("statechange")();
            }
            await waiting;
            assert.equal(listeners.size, 0);
            assert.equal(timers.size, 0);
        }, { active: false, path: timeout ? "/alpha" : "/alpha/" });
    });
}
