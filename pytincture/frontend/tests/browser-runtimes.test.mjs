import assert from 'node:assert/strict';
import test from 'node:test';
import {createHash} from 'node:crypto';
import {BROWSER_RUNTIMES, createBffCaller, sameOriginUrl, validateRuntimeManifest} from '../browser-runtimes.js';

const manifest = {
    schema:2, runtimes:['pyodide','micropython'], host:'host.js',
    runtimeRequirements:{pyodide:'0.29.3',micropython:'1.29.0-6'},
    scripts:['suite.js'], styles:['suite.css'], sources:'sources.json',
    entrypoint:'client', resources:'resources.json', profile:'pytincture-portable-1',
    requiredBrowserApis:['fetch', 'WebAssembly', 'crypto.subtle'],
    bundleId:'a'.repeat(64), assetBase:'releases/'+ 'a'.repeat(64)+'/',
    targets:{pyodide:{sources:'sources.json'},micropython:{sources:'sources.json'}},
    integrity:Object.fromEntries(['host.js','suite.js','suite.css','sources.json','resources.json','micropython.mjs','micropython.wasm'].map(name=>[name,{sha256:'b'.repeat(64),bytes:0}])) ,
    micropython:{module:'micropython.mjs',wasm:'micropython.wasm'},
};

test('bundle verification rejects changed bytes and changed manifests before execution', async () => {
    const {verifyBundle} = await import('../browser-runtimes.js');
    const hash = value => createHash('sha256').update(value).digest('hex');
    const sorted = value => Array.isArray(value) ? value.map(sorted) : value && typeof value === 'object'
        ? Object.fromEntries(Object.keys(value).sort().map(key => [key,sorted(value[key])])) : value;
    const content = 'window.executionCount = 1;';
    const unsigned = {schema:2,integrity:{'app.js':{sha256:hash(content),bytes:content.length}}};
    const id = hash(JSON.stringify(sorted(unsigned))+'\n');
    const signed = {...unsigned,bundleId:id,assetBase:`releases/${id}/`};
    const priorFetch = globalThis.fetch;
    try {
        globalThis.fetch = async () => new Response(content);
        assert.equal((await verifyBundle(signed, path=>'https://app.test/'+path)).size, 1);
        globalThis.fetch = async () => new Response(content.replace('1','2'));
        await assert.rejects(verifyBundle(signed,path=>'https://app.test/'+path), /integrity mismatch/);
        await assert.rejects(verifyBundle({...signed,profile:'changed'},path=>path), /identifier/);
        globalThis.fetch = async () => new Response(content+'oversized');
        await assert.rejects(verifyBundle(signed,path=>'https://app.test/'+path), /byte limit/);
        globalThis.fetch = async () => new Response(content,{headers:{'content-length':'999999'}});
        await assert.rejects(verifyBundle(signed,path=>'https://app.test/'+path), /byte limit/);
    } finally {globalThis.fetch=priorFetch;}
});

test('widget asset ownership registry identifies exact successfully loaded assets', async () => {
    const {publishLoadedAssets} = await import('../browser-runtimes.js');
    publishLoadedAssets({...manifest,widgetPackages:['examplewidgets']},path=>'https://app.test/'+path);
    const registry = globalThis.pytinctureAssets;
    assert.equal(registry.isPackageReady('examplewidgets'), true);
    assert.equal(registry.isPackageReady('otherwidgets'), false);
    assert.equal(registry.isLoaded('suite.js','b'.repeat(64)),true);
    assert.equal(registry.isLoaded('suite.js','c'.repeat(64)),false);
    assert.equal(registry.getInfo().owner,'portable-bundle');
    assert.throws(()=>registry.getInfo().assets.push({}),TypeError);
    delete globalThis.pytinctureAssets;
});

test('each built-in runtime validates its required assets', () => {
    assert.deepEqual(BROWSER_RUNTIMES, ['pyodide', 'micropython']);
    for (const engine of manifest.runtimes) assert.equal(validateRuntimeManifest(manifest,engine),manifest);
    assert.throws(()=>validateRuntimeManifest(manifest,'other'),/Unknown/);
    assert.throws(()=>validateRuntimeManifest({...manifest,runtimes:['pyodide']},'micropython'),/does not support/);
    assert.throws(()=>validateRuntimeManifest(manifest,'transcrypt'),/Unknown/);
    assert.throws(()=>validateRuntimeManifest({...manifest,runtimes:['pyodide','transcrypt']},'pyodide'),/Invalid/);
    assert.throws(()=>validateRuntimeManifest({...manifest,sources:null},'pyodide'),/path/);
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

test('streaming BFF decodes split UTF-8 JSON lines and releases readers', async () => {
    const {createBffStreamCaller} = await import('../browser-runtimes.js');
    const priorFetch = globalThis.fetch, priorDocument = globalThis.document;
    try {
        globalThis.document = {cookie:'pytincture-dev-csrf=stream-token'};
        const bytes = new TextEncoder().encode('{"text":"café"}\n\n{"last":true}');
        let signal;
        globalThis.fetch = async (url, init) => {
            assert.equal(init.headers['X-CSRF-Token'], 'stream-token');
            assert.equal(init.credentials, 'same-origin');
            signal = init.signal;
            return new Response(new ReadableStream({start(controller) {
                for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
                controller.close();
            }}));
        };
        const stream = await createBffStreamCaller({application:'chat'})('api/data','Data','events');
        assert.deepEqual(JSON.parse(await stream.next()), {done:false,value:{text:'café'}});
        assert.deepEqual(JSON.parse(await stream.next()), {done:false,value:{last:true}});
        assert.deepEqual(JSON.parse(await stream.next()), {done:true});
        assert.ok(signal.aborted);
        await stream.close();
    } finally { globalThis.fetch=priorFetch; globalThis.document=priorDocument; }
});

test('raw streams preserve content, cancellation and malformed JSON close the body', async () => {
    const {createBffStreamCaller} = await import('../browser-runtimes.js');
    const priorFetch = globalThis.fetch, priorDocument = globalThis.document;
    try {
        globalThis.document = {cookie:''};
        let cancelled = false, signal;
        globalThis.fetch = async (_, init) => {
            signal = init.signal;
            return new Response(new ReadableStream({start(controller) {
                controller.enqueue(new TextEncoder().encode('not JSON\n'));
            }, cancel() {cancelled=true;}}));
        };
        const caller = createBffStreamCaller({application:'chat'});
        const stream = await caller('data','Data','events',{}, {raw:true});
        assert.deepEqual(JSON.parse(await stream.next()), {done:false,value:'not JSON\n'});
        await stream.close();
        assert.ok(cancelled && signal.aborted);
        cancelled = false;
        const invalid = await caller('data','Data','events');
        await assert.rejects(invalid.next(), SyntaxError);
        assert.ok(cancelled && signal.aborted);
        globalThis.fetch=async()=>new Response('denied',{status:403});
        await assert.rejects(caller('data','Data','events'), /403/);
    } finally { globalThis.fetch=priorFetch; globalThis.document=priorDocument; }
});

test('synchronous compatibility requests enforce the same target, session and CSRF', async () => {
    const {createBffSyncCaller} = await import('../browser-runtimes.js');
    const priorXhr=globalThis.XMLHttpRequest, priorDocument=globalThis.document;
    try {
        globalThis.document={cookie:'pytincture-dev-csrf=token'};
        let request;
        globalThis.XMLHttpRequest=class {
            constructor() {request=this;this.headers={};this.status=200;this.responseText='{"ok":true}';}
            open(method,url,async) {Object.assign(this,{method,url,async});}
            setRequestHeader(name,value) {this.headers[name]=value;}
            send(body) {this.body=body;}
        };
        const call=createBffSyncCaller({application:'chat'});
        assert.deepEqual(call('data','Data','lookup',{id:7}),{ok:true});
        assert.equal(request.url,'/chat/classcall/data/Data/lookup');
        assert.equal(request.async,false);
        assert.equal(request.headers['X-CSRF-Token'],'token');
        assert.equal(request.body,'{"id":7}');
        assert.throws(()=>call('../data','Data','lookup'),/Invalid BFF target/);
    } finally {globalThis.XMLHttpRequest=priorXhr;globalThis.document=priorDocument;}
});
