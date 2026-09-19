APP_TITLE = 'Widget Workspace'
APP_RUNTIME_MANIFEST = 'browser/widgets/manifest.json'
APP_ENTRYPOINT = 'Workspace'

import js
from dhxpyt.layout import MainWindow, Layout, LayoutConfig, CellConfig
from dhxpyt.cardpanel import CardPanel, CardPanelConfig, CardPanelCardConfig
from dhxpyt.kanban import Kanban, KanbanConfig, KanbanColumnConfig, KanbanCardConfig
from dhxpyt.chat import Chat, ChatConfig, ChatMessageConfig


class Child(Layout):
    def __init__(self):
        super().__init__(config=LayoutConfig(rows=[CellConfig(id='inside')]))
        self.constructed = True
        self.loads = 0

    def load_ui(self):
        assert self.constructed
        self.loads += 1
        self.attach_html('inside', '<p>Nested layout initialized</p>')


class Workspace(MainWindow):
    def load_ui(self):
        self.child = Child()
        assert self.child.loads == 1
        self.attach('mainwindow', self.child.layout)
        self.widgets = []
        for kind in ('cards', 'board', 'chat'):
            root = js.document.createElement('div')
            root.id = kind
            root.style.height = '400px'
            js.document.body.appendChild(root)
            if kind == 'cards':
                widget = CardPanel(container=root, config=CardPanelConfig(
                    title='Reusable cards', cards=[CardPanelCardConfig(id='c1', title='First card')]))
            elif kind == 'board':
                widget = Kanban(container=root, config=KanbanConfig(
                    title='Reusable board', columns=[KanbanColumnConfig(id='todo', title='To do')],
                    cards=[KanbanCardConfig(id='k1', title='First task', status='todo')]))
            else:
                widget = Chat(container=root, config=ChatConfig(
                    messages=[ChatMessageConfig(role='assistant', content='Chat ready')],
                    persistence=False, extra={'models': ['Local test']}))
            self.widgets.append(widget)
