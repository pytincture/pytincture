import assert from 'node:assert/strict';
import test from 'node:test';
import {createBffCaller, sameOriginUrl, validateRuntimeManifest} from '../browser-runtimes.js';

const manifest = {
    schema:1, runtimes:['pyodide','micropython','transcrypt'], host:'host.js',
    scripts:['suite.js'], styles:['suite.css'], sources:'sources.json',
    entrypoint:'client', compiled:'compiled/client.js',
    micropython:{module:'micropython.mjs',wasm:'micropython.wasm'},
};

test('each built-in runtime validates its required assets', () => {
    for (const engine of manifest.runtimes) assert.equal(validateRuntimeManifest(manifest,engine),manifest);
    assert.throws(()=>validateRuntimeManifest(manifest,'other'),/Unknown/);
    assert.throws(()=>validateRuntimeManifest({...manifest,runtimes:['pyodide']},'micropython'),/does not support/);
    assert.throws(()=>validateRuntimeManifest({...manifest,compiled:null},'transcrypt'),/path/);
    assert.throws(()=>validateRuntimeManifest({...manifest,micropython:{...manifest.micropython,heapBytes:-1}},'micropython'),/heapBytes/);
});

test('manifest paths cannot escape or substitute external code', () => {
    for (const host of ['../secret.js','/secret.js','https://example.com/a.js','%2e%2e/a.js','a\\b.js','.hidden/a.js']) {
        assert.throws(()=>validateRuntimeManifest({...manifest,host},'pyodide'),/path/);
    }
    assert.throws(()=>validateRuntimeManifest({...manifest,entrypoint:'client; import os'},'pyodide'),/module name/);
    assert.throws(()=>sameOriginUrl('https://elsewhere.test/m.js','https://app.test/manifest.json'),/origin/);
    assert.throws(()=>sameOriginUrl('https://user:pass@app.test/m.js','https://app.test/manifest.json'),/origin/);
    assert.equal(sameOriginUrl('host.js','https://app.test/bundle/manifest.json'),'https://app.test/bundle/host.js');
});

test('BFF calls retain session credentials, named arguments and configured CSRF', async () => {
    const priorFetch = globalThis.fetch;
    const priorDocument = globalThis.document;
    try {
        globalThis.document = {cookie:'other=1; __Host-pytincture-csrf=csrf%20value'};
        let actual;
        globalThis.fetch = async(url, options) => {
            actual={url,options};
            return {ok:true,json:async()=>({ok:true})};
        };
        const call = createBffCaller({application:'books',csrfCookieName:'__Host-pytincture-csrf'});
        assert.deepEqual(await call('data/books','Library','update',{book_id:7}),{ok:true});
        assert.equal(actual.url,'/books/classcall/data/books/Library/update');
        assert.equal(actual.options.credentials,'same-origin');
        assert.equal(actual.options.headers['X-CSRF-Token'],'csrf value');
        assert.deepEqual(JSON.parse(actual.options.body),{book_id:7});
        await assert.rejects(call('../private','Library','read'),/Invalid BFF target/);
        globalThis.fetch = async()=>({ok:false,status:401});
        await assert.rejects(call('books','Library','read'),/401/);
    } finally {
        globalThis.fetch=priorFetch;
        if(priorDocument === undefined) delete globalThis.document;
        else globalThis.document=priorDocument;
    }
});

test('GET-only BFF requests omit the body and reject unexpected verbs', async () => {
    const priorFetch = globalThis.fetch;
    const priorDocument = globalThis.document;
    try {
        globalThis.document = {cookie:''};
        let options;
        globalThis.fetch = async (url, received) => {
            options = received;
            return {ok:true, json:async()=>({ready:true})};
        };
        const call = createBffCaller({application:'warehouse'});
        assert.deepEqual(await call('api/catalog', 'Catalog', 'ping', {}, {method:'GET'}), {ready:true});
        assert.equal(options.method, 'GET');
        assert.equal('body' in options, false);
        assert.ok(options.signal);
        await assert.rejects(call('api/catalog', 'Catalog', 'ping', {}, {method:'DELETE'}), /Unsupported/);
    } finally {
        globalThis.fetch = priorFetch;
        if (priorDocument === undefined) delete globalThis.document;
        else globalThis.document = priorDocument;
    }
});
