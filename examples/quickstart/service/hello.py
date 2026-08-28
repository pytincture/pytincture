import js
import widget
from dhxpyt.layout import MainWindow


class hello(MainWindow):
    def load_ui(self):
        js.document.getElementById("maindiv").innerHTML = (
            "<main><h1>Hello from Pytincture</h1>"
            "<p>Python is running in your browser.</p></main>"
        )
