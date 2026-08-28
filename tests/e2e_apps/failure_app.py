import e2e_widget

from dhxpyt.layout import MainWindow


class failure_app(MainWindow):
    def load_ui(self):
        raise RuntimeError("intentional e2e entrypoint failure")
