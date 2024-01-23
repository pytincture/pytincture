

from pytincture.frontend.widgetsets.form import FormTypes
from pytincture.frontend.widgetsets.window import Window


class FormExample(Window):
    def __init__(self, ):
        widget_config = {
            "id": "form_example",
            "css": "dhx_widget--bordered dhx_widget--bg_white",
            "width": 400,
            "height": 400,
            "left": 100,
            "top": 100,
            "text": "Form Example",
            "modal": True,
            "resizable": True,
            "movable": True,
            "closeable": True
        }
        super().__init__(widget_config=widget_config)
        self.load_ui()

    def load_ui(self):
        pass