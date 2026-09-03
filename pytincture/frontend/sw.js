const WORKER_URL = new URL(self.location.href);
const REQUEST_UUID = (WORKER_URL.searchParams.get("uuid") || "unversioned")
    .replace(/[^A-Za-z0-9_.-]/g, "_")
    .slice(0, 64);
const APPLICATION = (WORKER_URL.searchParams.get("application") || "standalone")
    .replace(/[^A-Za-z0-9_]/g, "_")
    .slice(0, 64);
const RELEASE = (WORKER_URL.searchParams.get("release") || "unknown")
    .replace(/[^A-Za-z0-9_.-]/g, "_")
    .slice(0, 64);
const OWNED_CACHE_PREFIX = `pytincture:${encodeURIComponent(APPLICATION)}:`;
const CACHE_NAME = `${OWNED_CACHE_PREFIX}${RELEASE}:${REQUEST_UUID}`;
const FRAMEWORK_ASSET_PATHS = new Set([
    "pytincture.js",
    "vendor/materialdesignicons/materialdesignicons.css",
    "vendor/materialdesignicons/materialdesignicons.css.map",
    "vendor/materialdesignicons/fonts/materialdesignicons-webfont.woff2",
    "pyodide/0.29.3/full/pyodide.js",
    "pyodide/0.29.3/full/pyodide.asm.js",
    "pyodide/0.29.3/full/pyodide.asm.wasm",
    "pyodide/0.29.3/full/pyodide-lock.json",
    "pyodide/0.29.3/full/python_stdlib.zip",
    "pyodide/0.29.3/full/micropip-0.11.0-py3-none-any.whl",
    "pyodide/0.29.3/full/micropip-0.11.0-py3-none-any.whl.metadata",
]);
const ASSET_BASE_PATH = new URL("./", WORKER_URL).pathname;

function isManifestRequest(request, url) {
    if (request.method !== "GET" || url.origin !== WORKER_URL.origin) {
        return false;
    }
    if (!url.pathname.startsWith(ASSET_BASE_PATH)) {
        return false;
    }
    const relativePath = url.pathname.slice(ASSET_BASE_PATH.length);
    const requestUuid = url.searchParams.get("uuid");
    return FRAMEWORK_ASSET_PATHS.has(relativePath)
        && (!requestUuid || requestUuid === REQUEST_UUID)
        && !url.searchParams.has("pytincture_warm")
        && !request.headers.get("authorization");
}

function canonicalAssetRequest(url) {
    const canonicalUrl = new URL(url.pathname, WORKER_URL.origin);
    canonicalUrl.searchParams.set("uuid", REQUEST_UUID);
    return new Request(canonicalUrl.href, {
        method: "GET",
        credentials: "omit",
    });
}

const OWNED_ASSET_URLS = new Set(
    [...FRAMEWORK_ASSET_PATHS].map(relativePath => canonicalAssetRequest(
        new URL(relativePath, WORKER_URL),
    ).url),
);

async function pruneOwnedCaches() {
    const cacheNames = await caches.keys();
    await Promise.all(
        cacheNames
            .filter(name => name.startsWith(OWNED_CACHE_PREFIX) && name !== CACHE_NAME)
            .map(name => caches.delete(name)),
    );
    const currentCache = await caches.open(CACHE_NAME);
    const entries = await currentCache.keys();
    await Promise.all(
        entries
            .filter(request => !OWNED_ASSET_URLS.has(request.url))
            .map(request => currentCache.delete(request)),
    );
}

function responseIsPublicImmutable(response) {
    if (!response?.ok || response.type === "opaque") {
        return false;
    }
    const cacheControl = response.headers.get("cache-control") || "";
    const vary = response.headers.get("vary") || "";
    return !/(?:^|,)\s*(?:private|no-store)(?:\s|,|$)/i.test(cacheControl)
        && !/(?:^|,)\s*(?:cookie|authorization)(?:\s|,|$)/i.test(vary)
        && !response.headers.get("set-cookie");
}

self.addEventListener("install", event => {
    event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", event => {
    event.waitUntil((async () => {
        await pruneOwnedCaches();
        await self.clients.claim();
    })());
});

async function cacheFirst(request) {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request);
    if (cached) {
        return cached;
    }
    const response = await fetch(request, { cache: "no-store" });
    if (responseIsPublicImmutable(response)) {
        await cache.put(request, response.clone());
    }
    return response;
}

self.addEventListener("fetch", event => {
    const url = new URL(event.request.url);
    if (isManifestRequest(event.request, url)) {
        event.respondWith(cacheFirst(canonicalAssetRequest(url)));
    }
});
