

from dhxpyt.form import FormTypes
from dhxpyt.window import Window
from py_ui_data import py_ui_data as pud


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
        self.pud = pud()
        self.load_ui()

    def load_ui(self):
        #self.add_form(self.pud.form_data, form_type=FormTy
        pass
