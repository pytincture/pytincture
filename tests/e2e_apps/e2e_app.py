import asyncio
import importlib
import json

import e2e_widget
import js
from dhxpyt.layout import MainWindow
from e2e_data import E2EData  # noqa: F401 - packaged BFF proxy/audience declaration

from static_helper import STATIC_VALUE


APP_TITLE = "Pytincture E2E"


class e2e_app(MainWindow):
    def load_ui(self):
        dynamic = importlib.import_module("dynamic_module")
        with open("e2e.css", encoding="utf-8") as stylesheet:
            style = js.document.createElement("style")
            style.textContent = stylesheet.read()
            js.document.head.appendChild(style)

        container = js.document.getElementById("maindiv")
        container.innerHTML = (
            '<main id="e2e-ready" class="e2e-ready">'
            '<h1>Packaged Pytincture ready</h1>'
            f'<p id="static-import">{STATIC_VALUE}</p>'
            f'<p id="dynamic-import">{dynamic.DYNAMIC_VALUE}</p>'
            '<output id="bff-error-contract"></output>'
            "</main>"
        )

        def describe_error(error):
            return {
                "type": type(error).__name__,
                "status_code": getattr(error, "status_code", None),
                "operation": getattr(error, "operation", None),
                "correlation_id": getattr(error, "correlation_id", None),
                "message": str(error),
            }

        async def verify_proxies():
            service = E2EData()
            errors = {}
            try:
                service.sync_call()
            except Exception as error:
                errors["sync"] = describe_error(error)
            try:
                await service.async_call()
            except Exception as error:
                errors["async"] = describe_error(error)
            try:
                async for _ in service.stream_call():
                    pass
            except Exception as error:
                errors["stream"] = describe_error(error)
            js.document.getElementById("bff-error-contract").textContent = json.dumps(
                errors
            )

        if bool(getattr(js.window, "__pytinctureTestBffErrors", False)):
            asyncio.create_task(verify_proxies())
