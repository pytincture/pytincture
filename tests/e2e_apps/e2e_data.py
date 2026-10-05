import asyncio

from pytincture.dataclass import backend_for_frontend, bff_stream, bff_external, bff_http_methods


@backend_for_frontend(include_session_methods_in_docs=True)
class E2EData:
    count: int = 3

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

    async def fetch(self):
        return "fetch"

    def fetch_sync(self):
        return "fetch_sync"

    @bff_stream()
    async def fetch_stream(self):
        for value in ["hello", "123", "true", "", "a\nb", "café"]:
            yield value

    @bff_http_methods("PUT", "PATCH", "DELETE")
    def update(self, value):
        return {"value": value}

    def model_result(self):
        from pydantic import BaseModel, Field, field_serializer

        class Item(BaseModel):
            name: str = Field(serialization_alias="displayName")

            @field_serializer("name", when_used="json")
            def serialize_name(self, value):
                return value.upper()

        return Item(name="example")
