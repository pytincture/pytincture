import assert from 'node:assert/strict';
import test from 'node:test';
import { MicroappClient, validateMicroappCatalog, PyodideMicroappAdapter } from '../microapps.js';
const wheel = new TextEncoder().encode('approved browser wheel');
const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', wheel)), b => b.toString(16).padStart(2, '0')).join('');
function app(release = 'a'.repeat(64)) {
    return {id: 'orders', release, version: '0.1.0', application: 'orders',
        bff_base: `/_microapps/releases/orders/${release}/backend/orders`,
        browser: {url: `/_microapps/releases/orders/${release}/browser/ui.whl`, sha256,
            bytes: wheel.length, entrypoint: 'orders_ui:mount', top_modules: ['orders_ui'], runtime: 'pyodide', runtime_version: '0.29.3'}};
}
function fixture() {
    let catalog = {schema: 1, revision: 0, apps: []};
    const calls = [], installed = [];
    const adapter = {install: async (item, data) => installed.push([item, data]),
        invoke: async (item, hook, context) => {
            calls.push(hook);
            if (hook === 'mount') {
                context.root.textContent = String(await context.callBff('services.py', 'Data', 'read'));
                context.addCleanup(() => calls.push('cleanup'));
            }
        }};
    const fetch = async (url, options) => {
        if (url.endsWith('/catalog')) return new Response(JSON.stringify(catalog));
        if (url.endsWith('/ui.whl')) return new Response(wheel);
        assert.match(url, /\/backend\/orders\/classcall\/services.py\/Data\/read$/);
        assert.equal(options.credentials, 'same-origin');
        return new Response('42');
    };
    const client = new MicroappClient({adapter, fetch, baseUrl: 'https://platform.test/', onError: error => { throw error; }});
    return {client, installed, calls, setCatalog: value => { catalog = value; }};
}

test('open client discovers and mounts a later installation without startup/reload', async () => {
    const f = fixture(); await f.client.refresh(); assert.equal(f.client.available.size, 0);
    f.setCatalog({schema: 1, revision: 1, apps: [app()]}); await f.client.refresh();
    const root = {replaceChildren() { this.textContent = ''; }};
    await f.client.mount('orders', root);
    assert.equal(root.textContent, '42'); assert.equal(f.installed.length, 1);
    await f.client.unmount('orders');
    assert.deepEqual(f.calls, ['mount', 'unmount', 'cleanup']); assert.equal(root.textContent, '');
});
test('hash mismatch rejects code before adapter execution', async () => {
    const f = fixture(), item = app(); item.browser.sha256 = '0'.repeat(64);
    f.setCatalog({schema: 1, revision: 1, apps: [item]}); await f.client.refresh();
    await assert.rejects(f.client.install('orders'), /integrity/); assert.equal(f.installed.length, 0);
});
test('upgrade preserves old release without silent replacement', async () => {
    const f = fixture(); f.setCatalog({schema: 1, revision: 1, apps: [app()]}); await f.client.refresh(); await f.client.install('orders');
    f.setCatalog({schema: 1, revision: 2, apps: [app('b'.repeat(64))]}); await f.client.refresh();
    await assert.rejects(f.client.install('orders'), /reload/);
    assert.equal(f.client.installed.get('orders').release, 'a'.repeat(64));
});
test('identical bundled release needs no reinstallation', async () => {
    const f = fixture(); f.client.bundledApps = {orders: app().release};
    f.setCatalog({schema: 1, revision: 1, apps: [app()]}); await f.client.refresh(); await f.client.install('orders');
    assert.equal(f.installed.length, 0);
});
test('catalog cannot redirect assets off origin', () => {
    const item = app(); item.browser.url = 'https://evil.test/ui.whl';
    assert.throws(() => validateMicroappCatalog({schema: 1, revision: 1, apps: [item]}, 'https://platform.test/'), /origin/);
});
test('duplicate ids are rejected', () => {
    assert.throws(() => validateMicroappCatalog({schema: 1, revision: 1, apps: [app(), app()]}, 'https://platform.test/'), /Invalid/);
});
test('incompatible runtime rejects before extraction', async () => {
    let unpacked = false;
    const adapter = new PyodideMicroappAdapter({version: 'wrong', unpackArchive() { unpacked = true; }, pyimport() {}});
    await assert.rejects(adapter.install(app(), wheel), /runtime/); assert.equal(unpacked, false);
});
test('loaded module conflicts reject before extraction', async () => {
    let unpacked = false;
    const adapter = new PyodideMicroappAdapter({version: '0.29.3', runPython() { return '["orders_ui"]'; }, unpackArchive() { unpacked = true; }, pyimport() {}});
    await assert.rejects(adapter.install(app(), wheel), /conflict/); assert.equal(unpacked, false);
});

test('cleanup errors still clear UI and release the mounted slot', async () => {
    const f = fixture(); const ran = [];
    f.client.adapter.invoke = async (item, hook, context) => {
        if (hook === 'mount') {
            context.addCleanup(() => ran.push('last'));
            context.addCleanup(() => { throw new Error('cleanup failed'); });
            context.root.textContent = 'mounted';
        }
    };
    f.setCatalog({schema: 1, revision: 1, apps: [app()]}); await f.client.refresh();
    const root = {replaceChildren() { this.textContent = ''; }};
    await f.client.mount('orders', root);
    await assert.rejects(f.client.unmount('orders'), /cleanup/i);
    assert.deepEqual(ran, ['last']); assert.equal(root.textContent, '');
    assert.equal(f.client.mounted.size, 0);
});


test('legacy runtime source remains a classic script; live loading is opt-in', async () => {
    const {readFile} = await import('node:fs/promises');
    const {Script} = await import('node:vm');
    const source = await readFile(new URL('../pytincture.js', import.meta.url), 'utf8');
    assert.doesNotThrow(() => new Script(source));
    assert.match(source, /createPytinctureMicroappClient/);
});
