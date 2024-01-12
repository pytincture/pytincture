"""
header implementation
"""
from __future__ import annotations

from typing import TypeVar

Header = object


def header_menu(menu={}):
    options = {
        "menu_title": menu.get("menu_title"),
        "menu_dropdown": menu.get("menu_dropdown"),
    }
    return {"type": "menu", "options": options}

def header_item_text(text, text_id=""):
    options = {"text": text, "text_id": text_id}
    return {"type": "item_text", "options": options}


def header_icon(icon, size="a-md"):
    options = {"icon": icon, "size": size}
    return {"type": "icon", "options": options}


def navbar_icon(icon, size="", color="", dropup_items=[]):
    return f"{icon} {size} {color}"


def header_image(image_name):
    return f'<div><img src="/static/icons/{image_name}" width="40px" height="32px"></img><div>'

def header_user(name, initials, addons={}):
    options = {
        "name": name,
        "initials": initials,
        "list_items": addons.get("list_items"),
        "buttons": addons.get("buttons"),
    }
    return {"type": "user", "options": options}

class HeaderItemTypes:
    image = header_image
    icon = header_icon
    item_text = header_item_text
    divider = {"type": "divider"}
    user_info = header_user
    menu = header_menu


class IconColor:
    orange = "a-text-orange"
    red = "a-text-red"
    yellow = "a-text-yellow"
    green = "a-text-green"


class FontSize:
    font16 = "a-font-16"
    font24 = "a-font-24"
    font32 = "a-font-32"
    font40 = "a-font-40"


class Header:
    """Header widget implementation"""

    def __init__(self, header_id, parent=None, session_id=""):  
        self.parent = parent
        self.session_id = session_id or self.parent.session_id
        self.name = header_id
        self.initialized = False

        self._base_header = Header(
            header_id, session_id=self.session_id, parent=parent
        )

        self.on_nav_click_callable = None
        self.on_action_click_callable = None

        self.widget_config = {}
        
        from .layout import Layout
        self.layout = Layout("",None,"none")

    @property
    def raw_widget(self):
        """Returns protected base header class"""
        return self._base_header

    def init_widget(self):
        """Initialize the widget for the first time"""
        self._base_header.init_widget(self.raw_widget.raw_widget._unique_id)
        self.widget_config = self._base_header.config
        self.initialized = True

    def add_nav_buttons(self, button_list, position="left"):
        """Add a list of buttons to the navigation bar"""
        if not self.initialized:
            self._base_header.nav_bar_buttons = button_list
        else:
            self._base_header.add_nav_buttons(button_list)

    def add_header_items(self, header_item_list, position="left"):
        """Add a list of header items to the topbar"""
        for item in header_item_list:
            item["position"] = position
        self._base_header.add_header_items(header_item_list)

    def add_breadcrumbs(self, tree):
        """Add navigation breadcrumbs to the top bar"""
        self._base_header.add_breadcrumbs(tree)

    def change_header_text(self, text_id, new_text):
        """Change header text based on id"""
        self._base_header.change_header_text(text_id, new_text)

    def set_active(self, item_id):
        """Set a nav bar button as active"""
        self._base_header.set_active(item_id)

    def set_enabled(self, item_id, true_or_false):
        """Set a nav bar button as active"""
        self._base_header.set_enabled(item_id, true_or_false)

    def attach_widget(self, widget):
        """Attach a widget to the layout on a specific panel"""
        uid = widget.raw_widget._unique_id
        widget.widget_config
        if not widget.initialized:
            widget.init_widget()
        self.raw_widget.attach_widget(uid)

    def on_nav_click(self, event_callable, ret_widget_values=[], block_signal=False):
        """Handle on click event for grid widget"""
        # TODO implement block_signal if possible or remove
        self.on_nav_click_callable = event_callable
        self._base_header.on_nav_click(
            self.on_nav_click_return, ret_widget_values, block_signal
        )

    def on_nav_click_return(self, button_id):
        """Header navigation on click event return"""
        self.on_nav_click_callable(button_id)

    def on_menu_click(self, event_callable, ret_widget_values=None, block_signal=False):
        """Handle on click event for grid widget"""
        # TODO implement block_signal if possible or remove
        self.on_nav_click_callable = event_callable
        self._base_header.on_nav_click(
            self.on_nav_click_return, ret_widget_values, block_signal
        )

    def on_menu_click_return(self, button_id):
        """Header navigation on click event return"""
        self.on_nav_click_callable(button_id)

    def on_action_click(
        self, event_callable, ret_widget_values=None, block_signal=False
    ):
        """Handle on click event for action buttons"""
        self.on_action_click_callable = event_callable
        self._base_header.on_action_click(
            self.on_action_click_return, ret_widget_values, block_signal
        )

    def on_action_click_return(self, item_id):
        """Action on click event return"""
        self.on_action_click_callable(item_id)
