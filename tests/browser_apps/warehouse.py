APP_TITLE = 'Warehouse'
APP_RUNTIME_MANIFEST = 'browser/warehouse/manifest.json'
APP_ENTRYPOINT = 'main'

import js
import asyncio
from asyncio import ensure_future
from pyodide.ffi import create_proxy
from api.catalog import Catalog
from components import heading

callbacks = []


async def heartbeat():
    while True:
        await asyncio.sleep(1)


async def main():
    service = Catalog()
    result = await service.lookup_async('part-7')
    title = js.document.createElement('h1')
    title.textContent = heading(result)
    js.document.body.appendChild(title)
    button = js.document.createElement('button')
    button.textContent = 'Use the DOM'
    js.document.body.appendChild(button)

    def clicked(event):
        button.textContent = 'DOM callback worked'

    callbacks.append(create_proxy(clicked))
    button.addEventListener('click', callbacks[-1])
    combined = await service.combine_async(1, 2, 3, mode='sum')
    assert combined['total'] == 6 and combined['options']['mode'] == 'sum'
    assert (await service.ping_async())['ok']
    assert service.lookup('sync')['sku'] == 'sync'
    events = []
    async for event in service.events():
        events.append(event['text'])
    assert events == ['café', 'streamed']
    raw = ''
    async for chunk in service.raw_events():
        raw += chunk
    assert raw == 'raw café'
    response = await js.fetch(js.pytinctureAssetUrl('assets/public/settings.json'))
    settings = await response.text()
    assert 'warehouse-settings' in settings
    ensure_future(heartbeat())
