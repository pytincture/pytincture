"""
pyTincture grid widget implementation
"""
from ast import Call
from re import A, T
from typing import Any, Callable, Dict, List, Optional, Union, TypeVar
from hashlib import new
from uuid import uuid4
import js
import json

from .grid import Grid, Column, Header, Footer
from .toolbar import Toolbar
from .sidebar import Sidebar

from pyodide.ffi import create_proxy

TLayout = TypeVar("TLayout", bound="Layout")

import js
import json


class Layout:
    def __init__(self, mainwindow=False, widget_config: Dict[str, Any] = {}, widget_parent: Any = None):
        """ similar to grid.py implementation implement layout api"""
        self.layout = js.dhx.Layout
        if mainwindow:
            if not widget_config:
                widget_config = {"css":"dhx_layout-cell--bordered", "type":"line", "rows" :[{"header":None,"height":"98vh", "id": "mainwindow"}]}
            self.layout = self.layout.new("maindiv", js.JSON.parse(json.dumps(widget_config)))
        else:
            self.layout = self.layout.new(widget_parent, js.JSON.parse(json.dumps(widget_config)))
            self.initialized = False

    """ Layout methods """

    def destructor(self):
        """ removes a Layout instance and releases occupied resources """
        self.layout.destructor()
    
    def progress_hide(self):
        """ hides the progress bar in the Layout container """
        self.layout.progressHide()

    def progress_show(self):
        """ shows the progress bar in the Layout container """
        self.layout.progressShow()

    def remove_cell(self, id: str):
        """ removes a specified cell """
        self.layout.getCell(id).removeCell()

    """ Layout events """

    def after_add(self, handler: Callable):
        """ fires after adding a new cell """
        self.layout.events.afterAdd = create_proxy(handler)

    def after_collapse(self, handler: Callable):
        """ fires after a cell is collapsed """
        self.layout.events.afterCollapse = create_proxy(handler)

    def after_expand(self, handler: Callable):
        """ fires after expanding a Layout cell """
        self.layout.events.afterExpand = create_proxy(handler)

    def after_hide(self, handler: Callable):
        """ fires after a cell is hidden """
        self.layout.events.afterHide = create_proxy(handler)

    def after_remove(self, handler: Callable):
        """ fires after removing a cell """
        self.layout.events.afterRemove = create_proxy(handler)

    def after_resize_end(self, handler: Callable):
        """ fires after resizing of a cell is ended """
        self.layout.events.afterResizeEnd = create_proxy(handler)

    def after_show(self, handler: Callable):
        """ fires after a cell is shown """
        self.layout.events.afterShow = create_proxy(handler)

    def before_add(self, handler: Callable):
        """ fires before adding a cell """
        self.layout.events.beforeAdd = create_proxy(handler)

    def before_collapse(self, handler: Callable):
        """ fires before a cell is collapsed """
        self.layout.events.beforeCollapse = create_proxy(handler)

    def before_expand(self, handler: Callable):
        """ fires before expanding a Layout cell """
        self.layout.events.beforeExpand = create_proxy(handler)

    def before_hide(self, handler: Callable):
        """ fires before a cell is hidden """
        self.layout.events.beforeHide = create_proxy(handler)

    def before_remove(self, handler: Callable):
        """ fires before removing a cell """
        self.layout.events.beforeRemove = create_proxy(handler)

    def before_resize_start(self, handler: Callable):
        """ fires before resizing of a cell has started """
        self.layout.events.beforeResizeStart = create_proxy(handler)

    def before_show(self, handler: Callable):
        """ fires before a cell is shown """
        self.layout.events.beforeShow = create_proxy(handler)

    def resize(self, handler: Callable):
        """ fires on resizing a cell """
        self.layout.events.resize = create_proxy(handler)

    """ Layout properties """

    @property
    def cols(self):
        """ Optional. An array of columns objects """
        return self.layout.cols
    
    @cols.setter
    def cols(self, value):
        self.layout.cols = value

    @property
    def css(self):
        """ Optional. The name of a CSS class(es) applied to Layout """
        return self.layout.css
    
    @css.setter
    def css(self, value):
        self.layout.css = value

    @property
    def rows(self):
        """ Optional. An array of rows objects """
        return self.layout.rows
    
    @rows.setter
    def rows(self, value):
        self.layout.rows = value

    @property
    def type(self):
        """ Optional. Defines the type of borders between cells inside a layout """
        return self.layout.type

    @type.setter
    def type(self, value):
        self.layout.type = value

    """ Cell API """

    def attach(self, id: str, widget: Any):
        """ attaches a DHTMLX component into a Layout cell """
        self.initialized = True
        self.layout.getCell(id).attach(widget)

    def add_grid(self, id: str = "mainwindow", grid_config: Dict[str, Any] = {}, columns: List[Dict[str, Any]] = [], data_url: str = "") -> Grid:
        """ adds a grid into a Layout cell """
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
        self.layout.getCell(id).attach(grid_widget.grid)
        return grid_widget

    def add_layout(self, id: str = "mainwindow", layout_config: Dict[str, Any] = {}) -> TLayout:
        """ adds a Layout into a Layout cell """
        layout_widget = Layout(
            widget_config=layout_config
        )
        self.layout.getCell(id).attach(layout_widget.layout)
        return layout_widget
    
    def add_toolbar(self, id: str = "mainwindow", toolbar_config: Dict[str, Any] = {}, data: Dict[str, Any] = None) -> Toolbar:
        """ adds a Toolbar into a Layout cell """
        toolbar_widget = Toolbar(widget_config=toolbar_config)
        self.layout.getCell(id).attach(toolbar_widget.toolbar)
        return toolbar_widget
                    
    def add_sidebar(self, id: str = "mainwindow", sidebar_config: Dict[str, Any] = {}) -> Sidebar:
        """ adds a Sidebar into a Layout cell """
        sidebar_widget = Sidebar(widget_config=sidebar_config)
        self.layout.getCell(id).attach(sidebar_widget.sidebar)
        return sidebar_widget

    def attach_html(self, id: str, html: str):
        """ adds an HTML content into a Layout cell """
        self.layout.attachHTML(id, html)

    def collapse(self, id: str):
        """ collapses a specified cell """
        self.layout.collapse(id)

    def detach(self, id: str):
        """ detaches an attached DHTMLX component or HTML content from a cell """
        self.layout.detach(id)

    def expand(self, id: str):
        """ expands a collapsed cell """
        self.layout.expand(id)

    def get_parent(self, id: str):
        """ returns the parent of a cell """
        return self.layout.getParent(id)

    def get_widget(self, id: str):
        """ returns the widget attached to a layout cell """
        return self.layout.getWidget(id)

    def hide(self, id: str):
        """ hides a specified cell """
        self.layout.hide(id)

    def is_visible(self, id: str):
        """ checks whether a cell is visible """
        return self.layout.isVisible(id)
    
    def paint(self):
        """ repaints Layout on a page """
        self.layout.paint()

    def progress_hide(self, id: str):
        """ hides the progress bar in a cell """
        self.layout.progressHide(id)

    def progress_show(self, id: str):
        """ shows the progress bar in a cell """
        self.layout.progressShow(id)

    def show(self, id: str):
        """ shows a hidden cell """
        self.layout.show(id)

    def toggle(self, id: str):
        """ expands/collapses a Layout cell """
        self.layout.toggle(id)

    """ Cell properties """

    @property
    def align(self):
        """ Optional. Sets the alignment of content inside a cell """
        return self.layout.align
    
    @align.setter
    def align(self, value):
        self.layout.align = value

    @property
    def collapsable(self):
        """ Optional. Defines whether a cell can be collapsed """
        return self.layout.collapsable
    
    @collapsable.setter
    def collapsable(self, value):
        self.layout.collapsable = value

    @property
    def collapsed(self):
        """ Optional. Defines whether a cell is collapsed """
        return self.layout.collapsed
    
    @collapsed.setter
    def collapsed(self, value):
        self.layout.collapsed = value

    @property
    def css(self):
        """ Optional. The name of a CSS class(es) applied to a cell of Layout """
        return self.layout.css
    
    @css.setter
    def css(self, value):
        self.layout.css = value

    @property
    def gravity(self):
        """ Optional. Sets the "weight" of a cell in relation to other cells placed in the same row and within one parent """
        return self.layout.gravity
    
    @gravity.setter
    def gravity(self, value):
        self.layout.gravity = value

    @property
    def header(self):
        """ Optional. Adds a header with text for a cell """
        return self.layout.header
    
    @header.setter
    def header(self, value):
        self.layout.header = value

    @property
    def header_height(self):
        """ Optional. Sets the height of the header of a Layout cell """
        return self.layout.headerHeight
    
    @header_height.setter
    def header_height(self, value):
        self.layout.headerHeight = value

    @property
    def header_icon(self):
        """ Optional. An icon used in the header of a cell """
        return self.layout.headerIcon
    
    @header_icon.setter
    def header_icon(self, value):
        self.layout.headerIcon = value

    @property
    def header_image(self):
        """ Optional. An image used in the header of a cell """
        return self.layout.headerImage
    
    @header_image.setter
    def header_image(self, value):
        self.layout.headerImage = value

    @property
    def height(self):
        """ Optional. Sets the height of a cell """
        return self.layout.height
    
    @height.setter
    def height(self, value):
        self.layout.height = value

    @property
    def hidden(self):
        """ Optional. Defines whether a cell is hidden """
        return self.layout.hidden
    
    @hidden.setter
    def hidden(self, value):
        self.layout.hidden = value

    @property
    def html(self):
        """ Optional. Sets HTML content for a cell """
        return self.layout.html
    
    @html.setter
    def html(self, value):
        self.layout.html = value

    @property
    def id(self):
        """ Optional. The id of a cell """
        return self.layout.id
    
    @id.setter
    def id(self, value):
        self.layout.id = value

    @property
    def max_height(self):
        """ Optional. The maximal height to be set for a cell """
        return self.layout.maxHeight
    
    @max_height.setter
    def max_height(self, value):
        self.layout.maxHeight = value

    @property
    def max_width(self):
        """ Optional. The maximal width to be set for a cell """
        return self.layout.maxWidth
    
    @max_width.setter
    def max_width(self, value):
        self.layout.maxWidth = value

    @property
    def min_height(self):
        """ Optional. The minimal height to be set for a cell """
        return self.layout.minHeight
    
    @min_height.setter
    def min_height(self, value):
        self.layout.minHeight = value

    @property
    def min_width(self):
        """ Optional. The minimal width to be set for a cell """
        return self.layout.minWidth
    
    @min_width.setter
    def min_width(self, value):
        self.layout.minWidth = value

    @property
    def padding(self):
        """ Optional. Defines the distance between a cell and the border of layout """
        return self.layout.padding
    
    @padding.setter
    def padding(self, value):
        self.layout.padding = value

    @property
    def progress_default(self):
        """ Optional. Defines whether the progress bar must be shown in a cell in the absence of the component/HTML content in the cell """
        return self.layout.progressDefault
    
    @progress_default.setter
    def progress_default(self, value):
        self.layout.progressDefault = value

    @property
    def resizable(self):
        """ Optional. Defines whether a cell can be resized """
        return self.layout.resizable
    
    @resizable.setter
    def resizable(self, value: bool):
        self.layout.resizable = value

    @property
    def type(self):
        """ Optional. Defines the type of borders between cells inside rows and columns of a layout """
        return self.layout.type

    @type.setter
    def type(self, value):
        self.layout.type = value

    @property
    def width(self):
        """ Optional. Sets the width of a cell """
        return self.layout.width
    
    @width.setter
    def width(self, value):
        self.layout.width = value

class MainWindow(Layout):
    def __init__(self):
        super().__init__(True)
        self.initialized = True

