const CACHE_NAME = "pytincture-sw-v1";
const PYODIDE_CDN_PREFIX = "https://cdn.jsdelivr.net/pyodide/";
const CACHEABLE_EXTENSIONS = [
    ".wasm", ".data", ".js", ".json", ".css", ".whl", ".pyt", ".zip",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".ico", ".png", ".jpg",
    ".jpeg", ".gif", ".svg", ".webp", ".webmanifest", ".xml",
];
const REQUEST_UUID = new URL(self.location.href).searchParams.get("uuid");

function withRequestUuid(url) {
    const bustedUrl = new URL(url.href);
    bustedUrl.searchParams.set("uuid", REQUEST_UUID);
    return bustedUrl.href;
}

function isFrontendFileRequest(request, url) {
    return Boolean(request.destination) ||
        url.pathname.includes("/appcode/") ||
        CACHEABLE_EXTENSIONS.some(ext => url.pathname.toLowerCase().endsWith(ext));
}

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
    if (url.searchParams.has("uuid")) {
        return false;
    }
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
    if (url.searchParams.has("uuid") && isFrontendFileRequest(event.request, url)) {
        event.respondWith(fetch(event.request, { cache: "no-store" }));
        return;
    }
    if (REQUEST_UUID && isFrontendFileRequest(event.request, url)) {
        const bustedRequest = new Request(withRequestUuid(url), event.request);
        event.respondWith(fetch(bustedRequest, { cache: "no-store" }));
        return;
    }
    if (!shouldCache(url)) {
        return;
    }
    event.respondWith(cacheFirst(event.request));
});
