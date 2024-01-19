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

    def getSelected(self) -> List[str]:
        """
        Returns an array with IDs of selected items
        """
        return self.toolbar.getSelected()

    def getState(self) -> Dict[str, Any]:
        """
        Gets current values/states of controls
        """
        return self.toolbar.getState()

    def hide(self, item_id: str) -> None:
        """
        Hides an item of Toolbar
        """
        self.toolbar.hide(item_id)

    def isDisabled(self, item_id: str) -> bool:
        """
        Checks whether an item of Toolbar is disabled
        """
        return self.toolbar.isDisabled(item_id)

    def isSelected(self, item_id: str) -> bool:
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

    def setFocus(self, input_id: str) -> None:
        """
        Sets focus on an Input control by its ID
        """
        self.toolbar.setFocus(input_id)

    def setState(self, state: Dict[str, Any]) -> None:
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
    def afterHide(self, callback: Callable) -> None:
        """
        Fires after hiding a sub-item of Toolbar
        """
        self.toolbar.attachEvent("afterHide", callback)

    def beforeHide(self, callback: Callable) -> None:
        """
        Fires before hiding a sub-item of Toolbar
        """
        self.toolbar.attachEvent("beforeHide", callback)

    def click(self, callback: Callable) -> None:
        """
        Fires after a click on a control
        """
        self.toolbar.attachEvent("click", callback)

    def input(self, callback: Callable) -> None:
        """
        Fires on entering a text into the input field
        """
        self.toolbar.attachEvent("input", callback)

    def inputBlur(self, callback: Callable) -> None:
        """
        Fires when a control is blurred
        """
        self.toolbar.attachEvent("inputBlur", callback)

    def inputChange(self, callback: Callable) -> None:
        """
        Fires on changing the value in the Input control of Toolbar
        """
        self.toolbar.attachEvent("inputChange", callback)

    def inputCreated(self, callback: Callable) -> None:
        """
        Fires when a new input is added
        """
        self.toolbar.attachEvent("inputCreated", callback)

    def inputFocus(self, callback: Callable) -> None:
        """
        Fires when a control is focused
        """
        self.toolbar.attachEvent("inputFocus", callback)

    def keydown(self, callback: Callable) -> None:
        """
        Fires when any key is pressed and a control of Toolbar is in focus
        """
        self.toolbar.attachEvent("keydown", callback)

    def openMenu(self, callback: Callable) -> None:
        """
        Fires on expanding a menu control
        """
        self.toolbar.attachEvent("openMenu", callback)

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
    def menuCss(self) -> Optional[str]:
        """
        Optional. Adds style classes to all containers of Toolbar controls with nested items
        """
        return self.toolbar.menuCss

    @menuCss.setter
    def menuCss(self, value: Optional[str]) -> None:
        self.toolbar.menuCss = value

