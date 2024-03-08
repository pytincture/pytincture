"""
sidebar widget
"""


from typing import Any, Callable, Dict, List, Optional, Union
import js
import json
from enum import Enum
from pyodide.ffi import create_proxy


class Sidebar:
    """
    Sidebar widget
    """

    def __init__(self, widget_config: Dict[str, Any]) -> None:
        self.sidebar = js.dhx.Sidebar
        self.widget_config = widget_config
        self.sidebar = self.sidebar.new(None, js.JSON.parse(json.dumps(widget_config)))

    def collapse(self) -> None:
        """
        Collapses the sidebar
        """
        self.sidebar.collapse()

    def destructor(self) -> None:
        """
        Removes the Sidebar instance and releases occupied resources
        """
        self.sidebar.destructor()

    def disable(self) -> None:
        """
        Disables and dims items of the Sidebar
        """
        self.sidebar.disable()

    def enable(self) -> None:
        """
        Enables disabled items of the Sidebar
        """
        self.sidebar.enable()

    def expand(self) -> None:
        """
        Expands the sidebar
        """
        self.sidebar.expand()

    def getSelected(self) -> List[str]:
        """
        Returns an array of IDs of selected items in the Sidebar
        """
        return self.sidebar.getSelected()

    def hide(self) -> None:
        """
        Hides items of the Sidebar
        """
        self.sidebar.hide()

    def isCollapsed(self) -> bool:
        """
        Checks whether the Sidebar is collapsed
        """
        return self.sidebar.isCollapsed()

    def isDisabled(self) -> bool:
        """
        Checks whether an item of the Sidebar is disabled
        """
        return self.sidebar.isDisabled()

    def isSelected(self) -> bool:
        """
        Checks whether a specified Sidebar item is selected
        """
        return self.sidebar.isSelected()

    def paint(self) -> None:
        """
        Repaints the Sidebar on a page
        """
        self.sidebar.paint()

    def select(self) -> None:
        """
        Selects a specified Sidebar item
        """
        self.sidebar.select()

    def show(self) -> None:
        """
        Shows items of the Sidebar
        """
        self.sidebar.show()

    def toggle(self) -> None:
        """
        Expands or collapses the Sidebar
        """
        self.sidebar.toggle()

    def unselect(self) -> None:
        """
        Unselects a selected Sidebar item
        """
        self.sidebar.unselect()

    # Sidebar events
    def afterCollapse(self, callback: Callable) -> None:
        """
        Fires after collapsing the Sidebar
        """
        self.sidebar.attachEvent("afterCollapse", callback)

    def afterExpand(self, callback: Callable) -> None:
        """
        Fires after expanding the Sidebar
        """
        self.sidebar.attachEvent("afterExpand", callback)

    def afterHide(self, callback: Callable) -> None:
        """
        Fires after hiding a sub-item of the Sidebar
        """
        self.sidebar.attachEvent("afterHide", callback)

    def beforeCollapse(self, callback: Callable) -> None:
        """
        Fires before collapsing the Sidebar
        """
        self.sidebar.attachEvent("beforeCollapse", callback)

    def beforeExpand(self, callback: Callable) -> None:
        """
        Fires before expanding the Sidebar
        """
        self.sidebar.attachEvent("beforeExpand", callback)

    def beforeHide(self, callback: Callable) -> None:
        """
        Fires before hiding a sub-item of the Sidebar
        """
        self.sidebar.attachEvent("beforeHide", callback)

    def click(self, callback: Callable) -> None:
        """
        Fires after a click on a control in the Sidebar
        """
        self.sidebar.attachEvent("click", callback)

    def inputBlur(self, callback: Callable) -> None:
        """
        Fires when a control in the Sidebar is blurred
        """
        self.sidebar.attachEvent("inputBlur", callback)

    def inputCreated(self, callback: Callable) -> None:
        """
        Fires when a new input is added to the Sidebar
        """
        self.sidebar.attachEvent("inputCreated", callback)

    def inputFocus(self, callback: Callable) -> None:
        """
        Fires when a control in the Sidebar is focused
        """
        self.sidebar.attachEvent("inputFocus", callback)

    def keydown(self, callback: Callable) -> None:
        """
        Fires when any key is pressed and a Sidebar option is in focus
        """
        self.sidebar.attachEvent("keydown", callback)

    def openMenu(self, callback: Callable) -> None:
        """
        Fires on expanding a menu control in the Sidebar
        """
        self.sidebar.attachEvent("openMenu", callback)

    # Sidebar properties
    @property
    def collapsed(self) -> Optional[bool]:
        """
        Defines whether the Sidebar is initialized in the collapsed state
        """
        return self.sidebar.config.collapsed

    @collapsed.setter
    def collapsed(self, value: Optional[bool]) -> None:
        self.sidebar.config.collapsed = value

    @property
    def css(self) -> Optional[str]:
        """
        Adds style classes to the Sidebar
        """
        return self.sidebar.config.css

    @css.setter
    def css(self, value: Optional[str]) -> None:
        self.sidebar.config.css = value

    @property
    def data(self) -> Optional[List[Dict[str, Any]]]:
        """
        Specifies an array of data objects to set into the Sidebar
        """
        return self.sidebar.data

    @data.setter
    def data(self, value: Optional[List[Dict[str, Any]]]) -> None:
        self.sidebar.data = value

    @property
    def menuCss(self) -> Optional[str]:
        """
        Adds style classes to all containers of Sidebar controls with nested items
        """
        return self.sidebar.config.menuCss

    @menuCss.setter
    def menuCss(self, value: Optional[str]) -> None:
        self.sidebar.config.menuCss = value

    @property
    def minWidth(self) -> Optional[Union[int, str]]:
        """
        Sets the minimal width of the Sidebar in the collapsed state
        """
        return self.sidebar.config.minWidth

    @minWidth.setter
    def minWidth(self, value: Optional[Union[int, str]]) -> None:
        self.sidebar.config.minWidth = value

    @property
    def width(self) -> Optional[Union[int, str]]:
        """
        Sets the width of the Sidebar
        """
        return self.sidebar.config.width

    @width.setter
    def width(self, value: Optional[Union[int, str]]) -> None:
        self.sidebar.config.width = value

    

    
