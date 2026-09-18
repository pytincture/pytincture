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

    const csrfToken = () => {
        const name = root.dataset.csrfCookieName;
        const cookie = name && document.cookie.split(";").map(value => value.trim())
            .find(value => value.startsWith(`${name}=`));
        return cookie ? decodeURIComponent(cookie.slice(name.length + 1)) : "";
    };

    globalThis.ui = globalThis.SwaggerUIBundle({
        url: openapiUrl,
        dom_id: "#swagger-ui",
        deepLinking: true,
        showExtensions: true,
        showCommonExtensions: true,
        requestInterceptor(request) {
            const url = new URL(request.url, globalThis.location.href);
            const method = String(request.method || "GET").toUpperCase();
            if (url.origin === globalThis.location.origin
                && url.pathname.includes("/classcall/")
                && !["GET", "HEAD", "OPTIONS"].includes(method)) {
                const token = csrfToken();
                if (token) {
                    request.headers ||= {};
                    request.headers["X-CSRF-Token"] = token;
                }
            }
            return request;
        },
        presets: [
            globalThis.SwaggerUIBundle.presets.apis,
            globalThis.SwaggerUIBundle.SwaggerUIStandalonePreset,
        ],
        layout: "BaseLayout",
    });

    if (!root.dataset.authBase) return;
    const authBase = root.dataset.authBase;
    const status = document.getElementById("auth-status");
    const login = document.getElementById("api-login");
    const controls = document.getElementById("api-token-controls");
    const result = document.getElementById("api-token-result");
    const tokenField = document.getElementById("api-access-token");
    const requestJson = async (path, body) => {
        const response = await fetch(`${authBase}/${path}`, {
            method: body === undefined ? "GET" : "POST",
            credentials: "same-origin",
            cache: "no-store",
            headers: body === undefined ? {} : { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
            ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "API authentication failed");
        return data;
    };
    const refresh = async () => {
        const data = await requestJson("bff-token");
        login.hidden = data.authenticated || !data.password_login;
        controls.hidden = !data.authenticated || !data.tokens_enabled;
        status.textContent = data.authenticated
            ? `Signed in as ${data.email}. Execute can use this browser session.${data.tokens_enabled ? " You can also generate an API token below." : " API token generation is disabled by the server."}`
            : `Not signed in. ${data.public_token_required ? "This server requires an API token or browser sign-in for public methods too." : "Methods marked public allow anonymous calls."} Sign in to call private methods or generate an API token.`;
    };
    const action = async (button, work) => {
        button.disabled = true;
        try { await work(); }
        catch (error) { status.textContent = error.message; }
        finally { button.disabled = false; }
    };
    login.addEventListener("submit", event => {
        event.preventDefault();
        action(login.querySelector("button"), async () => {
            const transaction = await requestJson("mcp");
            try {
                await requestJson("mcp", {
                    email: login.elements.email.value,
                    password: login.elements.password.value,
                    login_csrf_token: transaction.login_csrf_token,
                });
            } finally { login.elements.password.value = ""; }
            await refresh();
        });
    });
    const refreshButton = document.getElementById("refresh-auth");
    refreshButton.addEventListener("click", () => action(refreshButton, refresh));
    const generate = document.getElementById("generate-token");
    const useToken = (data, access) => {
        tokenField.value = data.access_token;
        globalThis.ui.preauthorizeApiKey("BffBearer", data.access_token);
        document.getElementById("api-token-expiry").textContent = `Ready for Execute. Expires in ${data.expires_in} seconds. Access: ${access}. Copy this token for API clients; keep it private.`;
        result.hidden = false;
    };
    generate.addEventListener("click", () => action(generate, async () => {
        const data = await requestJson("bff-token", { scope: document.getElementById("api-token-scope").value });
        useToken(data, data.scope);
    }));
    const clientLogin = document.getElementById("api-client-login");
    if (clientLogin) clientLogin.addEventListener("submit", event => {
        event.preventDefault();
        action(clientLogin.querySelector("button"), async () => {
            try {
                const data = await requestJson("client-token", {
                    grant_type: "client_credentials",
                    client_id: clientLogin.elements.client_id.value,
                    client_secret: clientLogin.elements.client_secret.value,
                });
                useToken(data, `registered grants for ${data.client_id}`);
                status.textContent = "Application token ready. Execute uses this client's permissions.";
            } finally { clientLogin.elements.client_secret.value = ""; }
        });
    });
    document.getElementById("clear-token").addEventListener("click", () => {
        globalThis.ui.authActions.logout(["BffBearer"]);
        tokenField.value = "";
        result.hidden = true;
    });
    refresh().catch(error => { status.textContent = error.message; });
})();
