import { test, expect } from "@playwright/test";

async function openBlobFrame(page) {
    await page.evaluate(() => {
        window.frameViolations = [];
        document.addEventListener("securitypolicyviolation", (event) => {
            window.frameViolations.push(event.effectiveDirective);
        });
        const iframe = document.createElement("iframe");
        iframe.id = "artifact-preview";
        iframe.src = URL.createObjectURL(new Blob(
            ['<!doctype html><p id="artifact">Artifact preview</p>'],
            { type: "text/html" },
        ));
        document.body.appendChild(iframe);
    });
}

test("default policy still blocks blob iframe navigation", async ({ page }) => {
    const response = await page.goto("/frame-policy/preview");
    expect(response.status()).toBe(200);
    expect(response.headers()["content-security-policy"]).not.toContain("frame-src");
    await openBlobFrame(page);
    await expect.poll(() => page.evaluate(() => window.frameViolations)).toContain("frame-src");
});

test("explicit blob opt-in permits an artifact iframe without changing other guards", async ({ page, browserName }) => {
    test.skip(browserName === "webkit", "WebKit also enforces inherited frame-ancestors on blob HTML; the opt-in intentionally preserves that guard.");
    const response = await page.goto("/frame-policy-enabled/frame-policy/preview");
    expect(response.status()).toBe(200);
    const csp = response.headers()["content-security-policy"];
    expect(csp).toContain("frame-src 'self' blob:;");
    expect(csp).toContain("frame-ancestors 'none';");
    expect(csp).toContain("object-src 'none';");
    await openBlobFrame(page);
    await expect(page.frameLocator("#artifact-preview").locator("#artifact")).toHaveText("Artifact preview");
    expect(await page.evaluate(() => window.frameViolations)).toEqual([]);
});
