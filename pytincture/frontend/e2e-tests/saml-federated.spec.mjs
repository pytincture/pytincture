import { expect, test } from "@playwright/test";
import { inflateRawSync } from "node:zlib";
import { writeFileSync } from "node:fs";


const KEYCLOAK_IMAGE = "quay.io/keycloak/keycloak:26.7.2@sha256:9d1f1b2b7261ff53c66cb1092dfcdc34a5fb77e81f9e6a6e75b8b6a795de8067";

function collectDiagnostics(page) {
    const consoleEntries = [];
    const failures = [];
    const requests = [];
    const responses = [];
    page.on("console", message => consoleEntries.push({ type: message.type(), text: message.text() }));
    page.on("request", request => requests.push({ method: request.method(), url: request.url() }));
    page.on("requestfailed", request => failures.push({
        method: request.method(), failure: request.failure(), url: request.url(),
    }));
    page.on("response", response => responses.push({ status: response.status(), url: response.url() }));
    return { consoleEntries, failures, requests, responses };
}

function decodeAuthnRequest(urlValue) {
    const encoded = new URL(urlValue).searchParams.get("SAMLRequest");
    if (!encoded) throw new Error(`Missing SAMLRequest in ${urlValue}`);
    return inflateRawSync(Buffer.from(encoded, "base64")).toString("utf8");
}

async function callBff(page) {
    return page.evaluate(async () => {
        const csrfToken = document.cookie
            .split(";")
            .map(value => value.trim().split("="))
            .find(([name]) => [
                "__Host-pytincture-csrf",
                "pytincture-dev-csrf",
            ].includes(name))
            ?.slice(1).join("=") || "";
        const response = await fetch("/e2e_app/classcall/e2e_data.py/E2EData/sync_call", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
            body: JSON.stringify({ args: [], kwargs: { value: 41 } }),
        });
        return { status: response.status, body: await response.text() };
    });
}

async function attachEvidence(testInfo, evidence, diagnostics) {
    const evidencePath = testInfo.outputPath("saml-acceptance.json");
    const renderedEvidence = `${JSON.stringify(evidence, null, 2)}\n`;
    writeFileSync(evidencePath, renderedEvidence);
    if (process.env.PYTINCTURE_ACCEPTANCE_RESULT) {
        writeFileSync(process.env.PYTINCTURE_ACCEPTANCE_RESULT, renderedEvidence);
    }
    await testInfo.attach("saml-acceptance.json", { path: evidencePath, contentType: "application/json" });
    await testInfo.attach("saml-console.json", {
        body: Buffer.from(JSON.stringify(diagnostics.consoleEntries, null, 2)),
        contentType: "application/json",
    });
    await testInfo.attach("saml-network.json", {
        body: Buffer.from(JSON.stringify({
            requests: diagnostics.requests,
            responses: diagnostics.responses,
            failures: diagnostics.failures,
        }, null, 2)),
        contentType: "application/json",
    });
}

test("Keycloak SAML login authenticates the packaged app and BFF", async ({ page, request }, testInfo) => {
    const diagnostics = collectDiagnostics(page);
    const startedAt = Date.now();
    const evidence = {
        browser: testInfo.project.name,
        idp_image: KEYCLOAK_IMAGE,
        requested_authn_context: false,
    };
    try {
        await page.goto("/e2e_app");
        await page.waitForURL(/127\.0\.0\.1:8085\/realms\/pytincture-acceptance\/protocol\/saml/);

        const handshakeCookie = (await page.context().cookies()).find(
            cookie => cookie.name === "pytincture-dev-saml-handshake-e2e_app",
        );
        expect(handshakeCookie).toMatchObject({
            httpOnly: true,
            path: "/e2e_app/auth/saml",
        });

        const authnRequest = decodeAuthnRequest(page.url());
        expect(authnRequest).toContain("AuthnRequest");
        expect(authnRequest).toContain("http://127.0.0.1:8084/e2e_app/auth/saml/metadata");
        expect(authnRequest).not.toContain("RequestedAuthnContext");

        await page.getByLabel("Username or email").fill("saml-user");
        await page.getByLabel("Password", { exact: true }).fill("acceptance-password");
        await Promise.all([
            page.waitForURL("http://127.0.0.1:8084/e2e_app"),
            page.getByRole("button", { name: "Sign In" }).click(),
        ]);
        await expect(page.locator("#e2e-ready")).toBeVisible();
        expect(new URL(page.url()).search).toBe("");
        expect(await page.context().cookies()).not.toEqual(expect.arrayContaining([
            expect.objectContaining({ name: "pytincture-dev-saml-handshake-e2e_app" }),
        ]));

        const bff = await callBff(page);
        expect(bff.status).toBe(200);
        expect(JSON.parse(bff.body)).toEqual({ kind: "sync", value: 41, email: "saml@example.com" });

        await page.reload();
        await expect(page.locator("#e2e-ready")).toBeVisible();
        expect(page.url()).toBe("http://127.0.0.1:8084/e2e_app");

        const csrfCookie = (await page.context().cookies()).find(
            cookie => cookie.name === "pytincture-dev-csrf",
        );
        const logoutResponse = await page.request.post("/e2e_app/auth/logout", {
            headers: { "X-CSRF-Token": csrfCookie.value },
            maxRedirects: 0,
        });
        expect(logoutResponse.status()).toBe(302);
        expect(logoutResponse.headers().location).toBe("/e2e_app/login");
        expect(await page.context().cookies()).not.toEqual(expect.arrayContaining([
            expect.objectContaining({ name: "pytincture-dev-csrf" }),
        ]));

        const anonymousBff = await page.request.post("/e2e_app/classcall/e2e_data.py/E2EData/sync_call", {
            data: { args: [], kwargs: { value: 42 } },
        });
        expect(anonymousBff.status()).toBe(401);

        const consoleErrors = diagnostics.consoleEntries.filter(entry => entry.type === "error");
        expect(consoleErrors).toEqual([]);
        const completedWheelProbeAborts = diagnostics.failures.filter(entry => (
            ["HEAD", "GET"].includes(entry.method)
            && entry.failure?.errorText === "net::ERR_ABORTED"
            && entry.url.includes("dhxpyt-0.9.18+backend-py3-none-any.whl")
            && diagnostics.responses.some(response => (
                response.status === 200 && response.url === entry.url
            ))
        ));
        const unexpectedFailures = diagnostics.failures.filter(entry => (
            !completedWheelProbeAborts.includes(entry)
        ));
        expect(unexpectedFailures).toEqual([]);

        const health = await (await request.get("/healthz")).json();
        evidence.status = "passed";
        evidence.duration_ms = Date.now() - startedAt;
        evidence.pytincture_version = health.version;
        evidence.sp_entity_id = "http://127.0.0.1:8084/e2e_app/auth/saml/metadata";
        evidence.acs_url = "http://127.0.0.1:8084/e2e_app/auth/saml/acs";
        evidence.authenticated_email = "saml@example.com";
        evidence.browser_bound_handshake_cookie = true;
        evidence.handshake_cookie_cleared_after_use = true;
        evidence.bff_status = bff.status;
        evidence.anonymous_bff_status_after_logout = anonymousBff.status();
        evidence.console_error_count = consoleErrors.length;
        evidence.completed_widget_probe_abort_count = completedWheelProbeAborts.length;
        evidence.unexpected_request_failure_count = unexpectedFailures.length;
    } finally {
        evidence.status ||= "failed";
        evidence.duration_ms ||= Date.now() - startedAt;
        await attachEvidence(testInfo, evidence, diagnostics);
    }
});
