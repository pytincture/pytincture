"""
Appkit-Python appkit card_panel implementation
"""

from .raw_widgets.appkit_cardpanel import CardPanel as AppkitCardPanel
from pdd.decorators import buffer_for_init


class CardPanel:
    """CardPanel widget implementation"""

    widget_set = None

    def __init__(self, card_panel_id, parent=None, session_id=""):
        self.parent = parent
        self.session_id = session_id or self.parent.session_id
        self.name = card_panel_id
        self.initialized = False

        self._base_card_panel = AppkitCardPanel(
            card_panel_id, session_id=self.session_id, parent=parent
        )

        self.on_card_click_callable = None

        self.widget_config = {}

    @property
    def raw_widget(self):
        """Returns protected base card_panel class"""
        return self._base_card_panel

    def init_widget(self):
        """Initialize the widget for the first time"""
        self._base_card_panel.init_widget()
        self.widget_config = self._base_card_panel.config
        self.initialized = True

    @buffer_for_init
    def on_card_click(self, event_callable, ret_widget_values=[], block_signal=False):
        """Handle on click event for grid widget"""
        # TODO implement block_signal if possible or remove
        self.on_card_click_callable = event_callable
        self._base_card_panel.on_card_click(
            self.on_card_click_return, ret_widget_values, block_signal
        )

    def on_card_click_return(self, card_id):
        """Header navigation on click event return"""
        self.on_card_click_callable(card_id)