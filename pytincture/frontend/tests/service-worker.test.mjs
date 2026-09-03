import assert from "node:assert/strict";
import test from "node:test";


test("service worker canonicalizes asset keys and prunes only its application namespace", async () => {
    const previousSelf = globalThis.self;
    const previousCaches = globalThis.caches;
    const previousFetch = globalThis.fetch;
    const listeners = {};
    const matched = [];
    const stored = [];
    const deletedEntries = [];
    const deletedCaches = [];
    const canonical = new Request(
        "https://app.example.test/alpha/frontend/pytincture.js?uuid=instance-a",
    );
    const polluted = new Request(
        "https://app.example.test/alpha/frontend/pytincture.js?uuid=instance-a&junk=one",
    );
    const currentCache = {
        match: async request => {
            matched.push(request.url);
            return null;
        },
        put: async (request, response) => {
            stored.push(request.url);
        },
        keys: async () => [canonical, polluted],
        delete: async request => {
            deletedEntries.push(request.url);
            return true;
        },
    };

    globalThis.self = {
        location: {
            href: (
                "https://app.example.test/alpha/frontend/sw.js"
                + "?uuid=instance-a&application=alpha&release=1.0.0rc4"
            ),
        },
        clients: { claim: async () => undefined },
        skipWaiting: async () => undefined,
        addEventListener: (name, listener) => {
            listeners[name] = listener;
        },
    };
    globalThis.caches = {
        open: async () => currentCache,
        keys: async () => [
            "pytincture:alpha:1.0.0rc3:old-instance",
            "pytincture:alpha:1.0.0rc4:instance-a",
            "pytincture:beta:1.0.0rc4:instance-a",
            "foreign-cache",
        ],
        delete: async name => {
            deletedCaches.push(name);
            return true;
        },
    };
    globalThis.fetch = async request => new Response("asset", {
        headers: { "Cache-Control": "public, max-age=31536000, immutable" },
    });

    try {
        await import(`../sw.js?test=${Date.now()}`);

        const dispatchFetch = requestUrl => {
            let responsePromise;
            listeners.fetch({
                request: new Request(requestUrl),
                respondWith: promise => {
                    responsePromise = promise;
                },
            });
            return responsePromise;
        };
        await dispatchFetch(
            "https://app.example.test/alpha/frontend/pytincture.js"
            + "?uuid=instance-a&junk=one&junk=two",
        );
        await dispatchFetch(
            "https://app.example.test/alpha/frontend/pytincture.js"
            + "?unrelated=different&uuid=instance-a",
        );

        assert.deepEqual(matched, [canonical.url, canonical.url]);
        assert.deepEqual(stored, [canonical.url, canonical.url]);

        let activation;
        listeners.activate({ waitUntil: promise => { activation = promise; } });
        await activation;
        assert.deepEqual(deletedEntries, [polluted.url]);
        assert.deepEqual(deletedCaches, [
            "pytincture:alpha:1.0.0rc3:old-instance",
        ]);
    } finally {
        globalThis.self = previousSelf;
        globalThis.caches = previousCaches;
        globalThis.fetch = previousFetch;
    }
});
