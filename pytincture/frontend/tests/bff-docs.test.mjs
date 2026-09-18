import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../bff-docs.js", import.meta.url), "utf8");
function runtime() {
    let options;
    const bundle = config => { options = config; return {}; };
    bundle.presets = {};
    const context = {
        URL,
        SwaggerUIBundle: bundle,
        location: { href: "https://example.test/demo/bff-docs", origin: "https://example.test" },
        document: {
            cookie: "unrelated=ignore; exact-csrf=csrf-value",
            getElementById: () => ({ dataset: { openapiUrl: "/demo/bff-docs/openapi.json", csrfCookieName: "exact-csrf" } }),
        },
    };
    vm.runInNewContext(source, context);
    return options;
}

test("Swagger adds the configured CSRF cookie only to same-origin BFF writes", () => {
    const { requestInterceptor } = runtime();
    const request = requestInterceptor({ url: "/demo/classcall/catalog.py/Catalog/search", method: "POST", headers: { "Content-Type": "application/json" } });
    assert.equal(request.headers["X-CSRF-Token"], "csrf-value");
    assert.equal(request.headers["Content-Type"], "application/json");
    for (const [url, method] of [
        ["https://other.test/demo/classcall/catalog.py/Catalog/search", "POST"],
        ["/demo/classcall/catalog.py/Catalog/search", "GET"],
        ["/demo/bff-docs/openapi.json", "GET"],
        ["/demo/auth/user", "POST"],
    ]) {
        assert.equal(requestInterceptor({ url, method, headers: {} }).headers["X-CSRF-Token"], undefined);
    }
});
