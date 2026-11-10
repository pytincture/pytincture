import {test, expect} from '@playwright/test';

test('startup uses automatic bundled inventory, BFF calls pull additions, and new clients use rebuilt snapshot', async ({page, context, request}) => {
    const wheels = [], bundles = [], wasm = [], navigations = [];
    page.on('request', req => {
        if(req.url().includes('/browser/') && req.url().endsWith('.whl')) wheels.push(req.url());
        if(req.url().includes('/bundles/')) bundles.push(req.url());
        if(req.url().endsWith('.wasm')) wasm.push(req.url());
    });
    page.on('framenavigated', frame => {if(frame===page.mainFrame()) navigations.push(frame.url());});
    await page.goto('/');
    await expect(page.locator('#status')).toHaveText('Platform ready');
    expect(await page.evaluate(()=>window.failure)).toBeUndefined();
    expect(await page.evaluate(()=>Object.keys(client.bundledApps))).toEqual(['orders']);
    expect(wheels).toHaveLength(0); expect(bundles).toHaveLength(1);
    const oldBundle = bundles[0], wasmCount=wasm.length;
    expect(wasmCount).toBeGreaterThan(0);
    await page.getByRole('button',{name:'Open orders'}).click();
    await expect(page.locator('#microapp')).toHaveText('42');
    expect(wheels).toHaveLength(0);
    await page.evaluate(()=>client.unmount('orders'));
    const response=await request.post('/_microapps/installations',{
        headers:{'X-Pytincture-Install':'1','Origin':'http://127.0.0.1:41840'},
        data:{app_id:'reports',installed:true,version:'0.1.0',expected_revision:0}});
    expect(response.status()).toBe(202);
    await expect.poll(async()=>{
        const status=await request.get('/_microapps/environment');
        const data=await status.json();return data.serving===data.desired;
    }).toBe(true);
    expect(await page.evaluate(()=>client.installed.has('reports'))).toBe(false);
    // A normal platform BFF call, not catalog polling, drives the background pull.
    await page.evaluate(()=>fetch('/shell/classcall/ping.py/Ping/read').then(r=>r.json()));
    await expect.poll(()=>page.evaluate(()=>client.installed.has('reports'))).toBe(true);
    await expect(page.getByRole('button',{name:'Open reports'})).toBeVisible();
    await page.getByRole('button',{name:'Open reports'}).click();
    await expect(page.locator('#microapp')).toHaveText('99');
    expect(wheels).toHaveLength(1); expect(bundles).toHaveLength(1);
    expect(wasm.length).toBe(wasmCount); expect(navigations).toHaveLength(1);
    expect(await page.evaluate(()=>runtime.runPython("__import__('shell').base_startup_marker"))).toBe('original shell');
    const fresh=await context.newPage();const freshWheels=[],freshBundles=[];
    fresh.on('request',req=>{if(req.url().includes('/browser/')&&req.url().endsWith('.whl'))freshWheels.push(req.url());if(req.url().includes('/bundles/'))freshBundles.push(req.url());});
    await fresh.goto('/');await expect(fresh.locator('#status')).toHaveText('Platform ready');
    expect(await fresh.evaluate(()=>Object.keys(client.bundledApps).sort())).toEqual(['orders','reports']);
    expect(freshWheels).toHaveLength(0);expect(freshBundles).toHaveLength(1);expect(freshBundles[0]).not.toBe(oldBundle);
    expect(await fresh.evaluate(()=>client.installed.has('dormant'))).toBe(false);
    await fresh.evaluate(()=>client.stop());await fresh.close();await page.evaluate(()=>client.stop());
});
