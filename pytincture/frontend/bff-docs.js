(() => {
    "use strict";

    const root = document.getElementById("swagger-ui");
    if (!root || typeof globalThis.SwaggerUIBundle !== "function") {
        throw new Error("The packaged Swagger UI runtime is unavailable");
    }

    const openapiUrl = root.dataset.openapiUrl;
    if (!openapiUrl || !openapiUrl.startsWith("/")) {
        throw new Error("The BFF OpenAPI URL is invalid");
    }

    globalThis.ui = globalThis.SwaggerUIBundle({
        url: openapiUrl,
        dom_id: "#swagger-ui",
        deepLinking: true,
        showExtensions: true,
        showCommonExtensions: true,
        presets: [
            globalThis.SwaggerUIBundle.presets.apis,
            globalThis.SwaggerUIBundle.SwaggerUIStandalonePreset,
        ],
        layout: "BaseLayout",
    });
})();
