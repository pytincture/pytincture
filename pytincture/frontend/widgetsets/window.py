"""
window widget
"""

from .grid import Grid, Column, Header, Footer
from .toolbar import Toolbar
from .sidebar import Sidebar
from .layout import Layout

from typing import Any, Callable, Dict, List, Optional, Union
import js
import json
from enum import Enum
from pyodide.ffi import create_proxy


class Window:
    def __init__(self, widget_config: Dict[str, Any]) -> None:
        self.toolbar = js.dhx.Window
        self.widget_config = widget_config
        self.window = self.toolbar.new(js.JSON.parse(json.dumps(widget_config)))
    
    def attach(self, name: str, config: Optional[Dict[str, Any]] = None) -> None:
        """
        attaches a DHTMLX component to a DHTMLX Window
        """
        self.window.attach(name, config)

    def add_grid(self, grid_config: Dict[str, Any] = {}, columns: List[Dict[str, Any]] = [], data_url: str = "") -> Grid:
        """ adds a grid into a Window cell """
        for column in columns:
            if "header" in column:
                newheader = Header()
                for head in column["header"]:
                    newheader.append(**head)
                column["header"] = newheader
            if "footer" in column:
                newfooter = Footer()
                for foot in column["footer"]:
                    newfooter.append(**foot)
                column["footer"] = newfooter
            else:
                column["footer"] = None

        grid_widget = Grid(grid_config, columns, data_url)
        self.window.attach(grid_widget.grid)
        return grid_widget

    def add_layout(self, layout_config: Dict[str, Any] = {}) -> Layout:
        """ adds a Layout into a Layout cell """
        layout_widget = Layout(
            widget_config=layout_config
        )
        self.window.attach(layout_widget.layout)
        return layout_widget
    
    def add_toolbar(self, toolbar_config: Dict[str, Any] = {}, data: Dict[str, Any] = None) -> Toolbar:
        """ adds a Toolbar into a Layout cell """
        toolbar_widget = Toolbar(widget_config=toolbar_config)
        self.window.attach(toolbar_widget.toolbar)
        if data:
            dparse = js.JSON.parse(json.dumps(data))
            toolbar_widget.data.parse(dparse)
        return toolbar_widget
    
    def add_sidebar(self, sidebar_config: Dict[str, Any] = {}, data: Dict[str, Any] = None) -> Sidebar:
        """ adds a Sidebar into a Layout cell """
        sidebar_widget = Sidebar(widget_config=sidebar_config)
        self.window.attach(sidebar_widget.sidebar)
        if data:
            dparse = js.JSON.parse(json.dumps(data))
            sidebar_widget.data.parse(dparse)
        return sidebar_widget

    def attach_html(self, html: str) -> None:
        """
        adds an HTML content into a DHTMLX Window
        """
        self.window.attachHTML(html)

    def destructor(self) -> None:
        """
        removes a window instance and releases occupied resources
        """
        self.window.destructor()

    def get_container(self) -> Any:
        """
        returns the HTML element of Window
        """
        return self.window.getContainer()
    
    def get_position(self) -> Dict[str, Any]:
        """
        gets the position of a window
        """
        return self.window.getPosition()
    
    def get_size(self) -> Dict[str, Any]:
        """
        gets the size of window
        """
        return self.window.getSize()
    
    def get_widget(self) -> Any:
        """
        returns the widget attached to Window
        """
        return self.window.getWidget()
    
    def hide(self) -> None:
        """
        hides a window
        """
        self.window.hide()

    def is_full_screen(self) -> bool:
        """
        checks whether the window is in the full screen mode
        """
        return self.window.isFullScreen()
    
    def is_visible(self) -> bool:
        """
        checks whether a window is visible
        """
        return self.window.isVisible()
    
    def paint(self) -> None:
        """
        repaints a window on a page
        """
        self.window.paint()

    def set_full_screen(self) -> None:
        """
        switches the window into the full screen mode
        """
        self.window.setFullScreen()

    def set_position(self, left: int, top: int) -> None:
        """
        sets the position of a window
        """
        self.window.setPosition(left, top)

    def set_size(self, width: int, height: int) -> None:
        """
        sets the size of a window
        """
        self.window.setSize(width, height)

    def show(self, left: Optional[int] = None, top: Optional[int] = None) -> None:
        """
        shows a window on a page
        """
        self.window.show(left, top)

    def unset_full_screen(self) -> None:
        """
        switches the window from the full screen mode into the windowed mode
        """
        self.window.unsetFullScreen()

    def after_hide_event(self, callback: Callable[[Dict[str, Any], Any], None]) -> None:
        """
        fires after a window is hidden
        """
        self.window.events.on("AfterHide", callback)

    def after_show_event(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """
        fires after a window is shown
        """
        self.window.events.on("AfterShow", callback)

    def before_hide_event(self, callback: Callable[[Dict[str, Any], Any], None]) -> None:
        """
        fires before a window is hidden
        """
        self.window.events.on("BeforeHide", callback)

    def before_show_event(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """
        fires before a window is shown
        """
        self.window.events.on("BeforeShow", callback)

    def header_double_click_event(self, callback: Callable[[Any], None]) -> None:
        """
        fires on double clicking the header of a window
        """
        self.window.events.on("HeaderDoubleClick", callback)

    def move_event(self, callback: Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], None]) -> None:
        """
        fires on moving a window
        """
        self.window.events.on("Move", callback)

    def resize_event(self, callback: Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], None]) -> None:
        """
        fires on resizing a window
        """
        self.window.events.on("Resize", callback)

    @property
    def closable(self) -> Optional[bool]:
        """
        defines whether a window can be closed
        """
        return self.window.config.closable
    
    @closable.setter
    def closable(self, value: Optional[bool]) -> None:
        self.window.config.closable = value

    @property
    def css(self) -> Optional[str]:
        """
        adds style classes for the component
        """
        return self.window.config.css
    
    @css.setter
    def css(self, value: Optional[str]) -> None:
        self.window.config.css = value

    @property
    def footer(self) -> Optional[bool]:
        """
        adds a footer to a window
        """
        return self.window.config.footer
    
    @footer.setter
    def footer(self, value: Optional[bool]) -> None:
        self.window.config.footer = value

    @property
    def header(self) -> Optional[bool]:
        """
        adds a header to a window
        """
        return self.window.config.header
    
    @header.setter
    def header(self, value: Optional[bool]) -> None:
        self.window.config.header = value

    @property
    def height(self) -> Optional[int]:
        """
        sets the height of a window
        """
        return self.window.config.height
    
    @height.setter
    def height(self, value: Optional[int]) -> None:
        self.window.config.height = value

    @property
    def html(self) -> Optional[str]:
        """
        sets an HTML content into a window on initialization
        """
        return self.window.config.html
    
    @html.setter
    def html(self, value: Optional[str]) -> None:
        self.window.config.html = value

    @property
    def left(self) -> Optional[int]:
        """
        the left coordinate of a window position
        """
        return self.window.config.left
    
    @left.setter
    def left(self, value: Optional[int]) -> None:
        self.window.config.left = value

    @property
    def min_height(self) -> Optional[int]:
        """
        sets the minimal height of a window
        """
        return self.window.config.minHeight
    

    
    
