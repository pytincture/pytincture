"""
Appkit-Python stacked widget implementation
"""
from __future__ import annotations

from uuid import uuid4
from typing import TypeVar

from .raw_widgets.appkit_stackedwidget import StackedWidget as AppkitStackedWidget
from pdd.decorators import buffer_for_init


class StackedWidget:
    """StackedWidget widget implementation"""

    def __init__(self, stackedwidget_id, parent=None, session_id=""):    
        self.parent = parent
        self.session_id = session_id or self.parent.session_id
        self.name = stackedwidget_id
        self.initialized = False

        self._base_stackedwidget = AppkitStackedWidget(
            stackedwidget_id, session_id=self.session_id, parent=parent
        )

        self.on_index_change_callable = None
        self.on_render_callable = None

        self.widget_config = {}
        from .layout import Layout
        self.layouts = [
            Layout("",None,"none"),
            Layout("",None,"none"),
            Layout("",None,"none"),
            Layout("",None,"none"),
            Layout("",None,"none")
        ]

    @property
    def raw_widget(self):
        """Returns protected base stackedwidget class"""
        return self._base_stackedwidget

    def init_widget(self):
        """Initialize the widget for the first time"""
        self._base_stackedwidget.init_widget()
        self.widget_config = self._base_stackedwidget.config
        self.initialized = True

    @buffer_for_init
    def attach_widget(self, index, widget):
        """Attach a widget to the layout on a specific panel"""
        uid = widget.raw_widget._unique_id
        widget.widget_config
        if not widget.initialized:
            widget.init_widget()
        self.raw_widget.attach_widget(index, uid)

    def widget_return_values(self):
        return {
            "property": "activeIndex",
            "getType": "none",
            "widget_id": self.base_widget._raw_stackedwidget._unique_id,
        }

    @property
    def activeIndex(self):
        activeIndex = self._base_stackedwidget.active_index or 0
        return activeIndex

    @property
    def pages(self):
        return self._base_stackedwidget.pages
    
    @pages.setter
    def pages(self, pages):
        self._base_stackedwidget.pages = pages

    @buffer_for_init
    def set_index(self, index, target=""):
        """Set the index of the stacked widget"""
        self._base_stackedwidget.set_index(index, target)

    @buffer_for_init
    def on_index_change(self, event_callable, ret_widget_values=None, block_signal=False):
        """Handle on click event for grid widget"""
        # TODO implement block_signal if possible or remove
        self.on_index_change_callable = event_callable
        self._base_stackedwidget.on_index_change(self.on_index_change_return, ret_widget_values, block_signal)
        
    def on_index_change_return(self, index):
        """Stackedwidget index change event return"""
        self.on_index_change_callable(index)

    @buffer_for_init
    def on_render(self, event_callable, ret_widget_values=None, block_signal=False):
        """Handle on click event for grid widget"""
        # TODO implement block_signal if possible or remove
        self.on_render_callable = event_callable
        self._base_stackedwidget.on_render(self.on_render_return, ret_widget_values, block_signal)
        
    def on_render_return(self, index):
        """Stackedwidget index change event return"""
        self.on_render_callable(index)