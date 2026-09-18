import asyncio

from pytincture.dataclass import backend_for_frontend, bff_stream, bff_external


@backend_for_frontend(include_session_methods_in_docs=True)
class E2EData:
    def __init__(self, _user):
        self._user = _user

    @bff_external
    def sync_call(self, value):
        return {"kind": "sync", "value": value, "email": self._user["email"]}

    async def async_call(self, value):
        await asyncio.sleep(0)
        return {"kind": "async", "value": value, "email": self._user["email"]}

    @bff_stream()
    async def stream_call(self, count):
        for index in range(count):
            await asyncio.sleep(0)
            yield {"kind": "stream", "value": index}
