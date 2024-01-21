"""
toolbar widget
"""


from typing import Any, Callable, Dict, List, Optional, Union
import js
import json
from enum import Enum
from pyodide.ffi import create_proxy


class Toolbar:
    def __init__(self, widget_config: Dict[str, Any]) -> None:
        self.toolbar = js.dhx.Toolbar
        self.widget_config = widget_config
        self.toolbar = self.toolbar.new(None, js.JSON.parse(json.dumps(widget_config)))
        
    def destructor(self) -> None:
        """
        Removes a Toolbar instance and releases occupied resources
        """
        self.toolbar.destructor()

    def disable(self, item_ids: Union[str, List[str]]) -> None:
        """
        Disables and dims an item(s) of Toolbar
        """
        self.toolbar.disable(item_ids)

    def enable(self, item_ids: Union[str, List[str]]) -> None:
        """
        Enables a disabled item(s) of Toolbar
        """
        self.toolbar.enable(item_ids)

    def get_selected(self) -> List[str]:
        """
        Returns an array with IDs of selected items
        """
        return self.toolbar.getSelected()

    def get_state(self) -> Dict[str, Any]:
        """
        Gets current values/states of controls
        """
        return self.toolbar.getState()

    def hide(self, item_id: str) -> None:
        """
        Hides an item of Toolbar
        """
        self.toolbar.hide(item_id)

    def is_disabled(self, item_id: str) -> bool:
        """
        Checks whether an item of Toolbar is disabled
        """
        return self.toolbar.isDisabled(item_id)

    def is_selected(self, item_id: str) -> bool:
        """
        Checks whether a specified Toolbar item is selected
        """
        return self.toolbar.isSelected(item_id)

    def paint(self) -> None:
        """
        Repaints Toolbar on a page
        """
        self.toolbar.paint()

    def select(self, item_id: str) -> None:
        """
        Selects a specified item of Toolbar
        """
        self.toolbar.select(item_id)

    def set_focus(self, input_id: str) -> None:
        """
        Sets focus on an Input control by its ID
        """
        self.toolbar.setFocus(input_id)

    def set_state(self, state: Dict[str, Any]) -> None:
        """
        Sets values/states of controls
        """
        self.toolbar.setState(state)

    def show(self, item_id: str) -> None:
        """
        Shows an item of Toolbar
        """
        self.toolbar.show(item_id)

    def unselect(self, item_id: str) -> None:
        """
        Unselects a selected Toolbar item
        """
        self.toolbar.unselect(item_id)

    # Events
    def after_hide(self, callback: Callable) -> None:
        """
        Fires after hiding a sub-item of Toolbar
        """
        self.toolbar.events.on("afterHide", create_proxy(callback))

    def before_hide(self, callback: Callable) -> None:
        """
        Fires before hiding a sub-item of Toolbar
        """
        sself.toolbar.events.on("beforeHide", create_proxy(callback))

    def click(self, callback: Callable) -> None:
        """
        Fires after a click on a control
        """
        self.toolbar.events.on("click", create_proxy(callback))

    def input(self, callback: Callable) -> None:
        """
        Fires on entering a text into the input field
        """
        sself.toolbar.events.on("input", create_proxy(callback))

    def input_blur(self, callback: Callable) -> None:
        """
        Fires when a control is blurred
        """
        sself.toolbar.events.on("inputBlur", create_proxy(callback))

    def input_change(self, callback: Callable) -> None:
        """
        Fires on changing the value in the Input control of Toolbar
        """
        sself.toolbar.events.on("inputChange", create_proxy(callback))

    def input_created(self, callback: Callable) -> None:
        """
        Fires when a new input is added
        """
        sself.toolbar.events.on("inputCreated", create_proxy(callback))

    def input_focus(self, callback: Callable) -> None:
        """
        Fires when a control is focused
        """
        sself.toolbar.events.on("inputFocus", create_proxy(callback))

    def keydown(self, callback: Callable) -> None:
        """
        Fires when any key is pressed and a control of Toolbar is in focus
        """
        sself.toolbar.events.on("keydown", create_proxy(callback))

    def open_menu(self, callback: Callable) -> None:
        """
        Fires on expanding a menu control
        """
        sself.toolbar.events.on("openMenu", create_proxy(callback))

    # Properties
    @property
    def css(self) -> Optional[str]:
        """
        Optional. Adds style classes to Toolbar
        """
        return self.toolbar.css

    @css.setter
    def css(self, value: Optional[str]) -> None:
        self.toolbar.css = value

    @property
    def data(self) -> Optional[List[Dict[str, Any]]]:
        """
        Optional. Specifies an array of data objects to set into Toolbar
        """
        return self.toolbar.data

    @data.setter
    def data(self, value: Optional[List[Dict[str, Any]]]) -> None:
        self.toolbar.data = value

    @property
    def menu_css(self) -> Optional[str]:
        """
        Optional. Adds style classes to all containers of Toolbar controls with nested items
        """
        return self.toolbar.menuCss

    @menu_css.setter
    def menu_css(self, value: Optional[str]) -> None:
        self.toolbar.menuCss = value

