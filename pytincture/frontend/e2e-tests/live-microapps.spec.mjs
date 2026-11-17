import {test, expect} from '@playwright/test';

test('an open platform installs a later microapp into the same real Pyodide runtime and calls its BFF', async ({page, request}) => {
    const wasm = []; const navigations = [];
    page.on('request', request => { if (request.url().endsWith('.wasm')) wasm.push(request.url()); });
    page.on('framenavigated', frame => { if (frame === page.mainFrame()) navigations.push(frame.url()); });
    await page.goto('/');
    await expect(page.locator('#status')).toHaveText('Platform ready');
    expect(await page.evaluate(() => client.available.size)).toBe(0);
    const initialWasm = wasm.length;
    expect(initialWasm).toBeGreaterThan(0);
    expect((await request.post('/test-only-install')).ok()).toBeTruthy();
    await expect(page.getByRole('button', {name: 'Open orders'})).toBeVisible();
    await page.getByRole('button', {name: 'Open orders'}).click();
    await expect(page.locator('#microapp')).toHaveText('Live value: 42');
    expect(await page.evaluate(() => window.failure)).toBeUndefined();
    expect(await page.evaluate(() => runtime.runPython('platform_startup_marker'))).toBe('original bundle still running');
    expect(wasm.length).toBe(initialWasm);
    expect(navigations).toHaveLength(1);
    await page.evaluate(async () => { await client.unmount('orders'); client.stop(); });
    await expect(page.locator('#microapp')).toBeEmpty();
});
