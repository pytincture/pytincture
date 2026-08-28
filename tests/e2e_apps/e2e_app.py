import importlib

import e2e_widget
import js
from dhxpyt.layout import MainWindow

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
            "</main>"
        )
