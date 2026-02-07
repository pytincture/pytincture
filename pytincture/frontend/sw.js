const CACHE_NAME = "pytincture-sw-v1";
const PYODIDE_CDN_PREFIX = "https://cdn.jsdelivr.net/pyodide/";
const CACHEABLE_EXTENSIONS = [".wasm", ".data", ".js", ".json", ".css", ".whl", ".pyt"];

self.addEventListener("install", event => {
    self.skipWaiting();
});

self.addEventListener("activate", event => {
    event.waitUntil((async () => {
        const keys = await caches.keys();
        await Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)));
        await self.clients.claim();
    })());
});

function shouldCache(url) {
    if (url.href.startsWith(PYODIDE_CDN_PREFIX)) {
        return true;
    }
    if (url.origin === self.location.origin) {
        if (url.pathname.includes("/appcode/")) {
            return true;
        }
        return CACHEABLE_EXTENSIONS.some(ext => url.pathname.endsWith(ext));
    }
    return false;
}

async function cacheFirst(request) {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request);
    if (cached) {
        return cached;
    }
    const response = await fetch(request);
    if (response && (response.ok || response.type === "opaque")) {
        cache.put(request, response.clone());
    }
    return response;
}

self.addEventListener("fetch", event => {
    if (event.request.method !== "GET") {
        return;
    }
    const url = new URL(event.request.url);
    if (!shouldCache(url)) {
        return;
    }
    event.respondWith(cacheFirst(event.request));
});
