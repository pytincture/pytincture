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
from .form import Form

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

    def destructor(self) -> None:
        """ removes a Layout instance and releases occupied resources """
        self.layout.destructor()
    
    def progress_hide(self) -> None:
        """ hides the progress bar in the Layout container """
        self.layout.progressHide()

    def progress_show(self) -> None:
        """ shows the progress bar in the Layout container """
        self.layout.progressShow()

    def remove_cell(self, id: str) -> None:
        """ removes a specified cell """
        self.layout.getCell(id).removeCell()

    """ Layout events """

    def after_add(self, handler: Callable) -> None:
        """ fires after adding a new cell """
        self.layout.events.afterAdd = create_proxy(handler)

    def after_collapse(self, handler: Callable) -> None:
        """ fires after a cell is collapsed """
        self.layout.events.afterCollapse = create_proxy(handler)

    def after_expand(self, handler: Callable) -> None:
        """ fires after expanding a Layout cell """
        self.layout.events.afterExpand = create_proxy(handler)

    def after_hide(self, handler: Callable) -> None:
        """ fires after a cell is hidden """
        self.layout.events.afterHide = create_proxy(handler)

    def after_remove(self, handler: Callable) -> None:
        """ fires after removing a cell """
        self.layout.events.afterRemove = create_proxy(handler)

    def after_resize_end(self, handler: Callable) -> None:
        """ fires after resizing of a cell is ended """
        self.layout.events.afterResizeEnd = create_proxy(handler)

    def after_show(self, handler: Callable) -> None:
        """ fires after a cell is shown """
        self.layout.events.afterShow = create_proxy(handler)

    def before_add(self, handler: Callable) -> None:
        """ fires before adding a cell """
        self.layout.events.beforeAdd = create_proxy(handler)

    def before_collapse(self, handler: Callable) -> None:
        """ fires before a cell is collapsed """
        self.layout.events.beforeCollapse = create_proxy(handler)

    def before_expand(self, handler: Callable) -> None:
        """ fires before expanding a Layout cell """
        self.layout.events.beforeExpand = create_proxy(handler)

    def before_hide(self, handler: Callable) -> None:
        """ fires before a cell is hidden """
        self.layout.events.beforeHide = create_proxy(handler)

    def before_remove(self, handler: Callable) -> None:
        """ fires before removing a cell """
        self.layout.events.beforeRemove = create_proxy(handler)

    def before_resize_start(self, handler: Callable) -> None:
        """ fires before resizing of a cell has started """
        self.layout.events.beforeResizeStart = create_proxy(handler)

    def before_show(self, handler: Callable) -> None:
        """ fires before a cell is shown """
        self.layout.events.beforeShow = create_proxy(handler)

    def resize(self, handler: Callable) -> None:
        """ fires on resizing a cell """
        self.layout.events.resize = create_proxy(handler)

    """ Layout properties """

    @property
    def cols(self) -> List[Dict[Any, Any]]:
        """ Optional. An array of columns objects """
        return self.layout.cols
    
    @cols.setter
    def cols(self, value: List[Dict[Any, Any]]) -> None:
        self.layout.cols = value

    @property
    def css(self) -> str:
        """ Optional. The name of a CSS class(es) applied to Layout """
        return self.layout.css
    
    @css.setter
    def css(self, value: str) -> None:
        self.layout.css = value

    @property
    def rows(self) -> List[Dict[Any, Any]]:
        """ Optional. An array of rows objects """
        return self.layout.rows
    
    @rows.setter
    def rows(self, value: List[Dict[Any, Any]]) -> None:
        self.layout.rows = value

    @property
    def type(self) -> str:
        """ Optional. Defines the type of borders between cells inside a layout """
        return self.layout.type

    @type.setter
    def type(self, value: str) -> None:
        self.layout.type = value

    """ Cell API """

    def attach(self, id: str, widget: Any) -> None:
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
        if data:
            dparse = js.JSON.parse(json.dumps(data))
            toolbar_widget.data.parse(dparse)
        return toolbar_widget
                    
    def add_sidebar(self, id: str = "mainwindow", sidebar_config: Dict[str, Any] = {}, data: Dict[str, Any] = None) -> Sidebar:
        """ adds a Sidebar into a Layout cell """
        sidebar_widget = Sidebar(widget_config=sidebar_config)
        self.layout.getCell(id).attach(sidebar_widget.sidebar)
        if data:
            dparse = js.JSON.parse(json.dumps(data))
            sidebar_widget.data.parse(dparse)
        return sidebar_widget
    
    def add_form(self, id: str = "mainwindow", form_config: Dict[str, Any] = {}, data: Dict[str, Any] = None) -> Form:
        """ adds a Form into a Layout cell """
        form_widget = Form(widget_config=form_config)
        self.layout.getCell(id).attach(form_widget.form)
        if data:
            dparse = js.JSON.parse(json.dumps(data))
            form_widget.data.parse(dparse)
        return form_widget

    def attach_html(self, id: str, html: str) -> None:
        """ adds an HTML content into a Layout cell """
        self.layout.getCell(id).attachHTML(html)

    def collapse(self, id: str) -> None:
        """ collapses a specified cell """
        self.layout.getCell(id).collapse()

    def detach(self, id: str) -> None:
        """ detaches an attached DHTMLX component or HTML content from a cell """
        self.layout.getCell(id).detach()

    def expand(self, id: str) -> None:
        """ expands a collapsed cell """
        self.layout.getCell(id).expand()

    def get_parent(self, id: str) -> Dict[str, Any]:
        """ returns the parent of a cell """
        return self.layout.getParent(id)

    def get_widget(self, id: str) -> Callable:
        """ returns the widget attached to a layout cell """
        return self.layout.getCell(id).getWidget()

    def hide(self, id: str) -> None:
        """ hides a specified cell """
        self.layout.getCell(id).hide()

    def is_visible(self, id: str) -> bool:
        """ checks whether a cell is visible """
        return self.layout.getCell(id).isVisible()
    
    def paint(self) -> None:
        """ repaints Layout on a page """
        self.layout.paint()

    def progress_hide(self, id: str) -> None:
        """ hides the progress bar in a cell """
        self.layout.getCell(id).progressHide()

    def progress_show(self, id: str) -> None:
        """ shows the progress bar in a cell """
        self.layout.getCell(id).progressShow()

    def show(self, id: str) -> None:
        """ shows a hidden cell """
        self.layout.getCell(id).show()

    def toggle(self, id: str) -> None:
        """ expands/collapses a Layout cell """
        self.layout.getCell(id).toggle()

    """ Cell properties """

    @property
    def align(self) -> str:
        """ Optional. Sets the alignment of content inside a cell """
        return self.layout.align
    
    @align.setter
    def align(self, value: str) -> None:
        self.layout.align = value

    @property
    def collapsable(self) -> bool:
        """ Optional. Defines whether a cell can be collapsed """
        return self.layout.collapsable
    
    @collapsable.setter
    def collapsable(self, value: bool) -> None:
        self.layout.collapsable = value

    @property
    def collapsed(self) -> bool:
        """ Optional. Defines whether a cell is collapsed """
        return self.layout.collapsed
    
    @collapsed.setter
    def collapsed(self, value: bool) -> None:
        self.layout.collapsed = value

    @property
    def css(self) -> str:
        """ Optional. The name of a CSS class(es) applied to a cell of Layout """
        return self.layout.css
    
    @css.setter
    def css(self, value: str) -> None:
        self.layout.css = value

    @property
    def gravity(self) -> Union[int, bool]:
        """ Optional. Sets the "weight" of a cell in relation to other cells placed in the same row and within one parent """
        return self.layout.gravity
    
    @gravity.setter
    def gravity(self, value: Union[int, bool]) -> None:
        self.layout.gravity = value

    @property
    def header(self) -> str:
        """ Optional. Adds a header with text for a cell """
        return self.layout.header
    
    @header.setter
    def header(self, value: str) -> None:
        self.layout.header = value

    @property
    def header_height(self) -> int:
        """ Optional. Sets the height of the header of a Layout cell """
        return self.layout.headerHeight
    
    @header_height.setter
    def header_height(self, value: int) -> None:
        self.layout.headerHeight = value

    @property
    def header_icon(self) -> str:
        """ Optional. An icon used in the header of a cell """
        return self.layout.headerIcon
    
    @header_icon.setter
    def header_icon(self, value: str) -> None:
        self.layout.headerIcon = value

    @property
    def header_image(self) -> str:
        """ Optional. An image used in the header of a cell """
        return self.layout.headerImage
    
    @header_image.setter
    def header_image(self, value: str) -> None:
        self.layout.headerImage = value

    @property
    def height(self) -> Union[int, str]:
        """ Optional. Sets the height of a cell """
        return self.layout.height
    
    @height.setter
    def height(self, value: Union[int, str]) -> None:
        self.layout.height = value

    @property
    def hidden(self) -> bool:
        """ Optional. Defines whether a cell is hidden """
        return self.layout.hidden
    
    @hidden.setter
    def hidden(self, value: bool) -> None:
        self.layout.hidden = value

    @property
    def html(self) -> str:
        """ Optional. Sets HTML content for a cell """
        return self.layout.html
    
    @html.setter
    def html(self, value: str) -> None:
        self.layout.html = value

    @property
    def id(self) -> str:
        """ Optional. The id of a cell """
        return self.layout.id
    
    @id.setter
    def id(self, value: str) -> None:
        self.layout.id = value

    @property
    def max_height(self) -> Union[int, str]:
        """ Optional. The maximal height to be set for a cell """
        return self.layout.maxHeight
    
    @max_height.setter
    def max_height(self, value: Union[int, str]) -> None:
        self.layout.maxHeight = value

    @property
    def max_width(self) -> Union[int, str]:
        """ Optional. The maximal width to be set for a cell """
        return self.layout.maxWidth
    
    @max_width.setter
    def max_width(self, value: Union[int, str]) -> None:
        self.layout.maxWidth = value

    @property
    def min_height(self) -> Union[int, str]:
        """ Optional. The minimal height to be set for a cell """
        return self.layout.minHeight
    
    @min_height.setter
    def min_height(self, value: Union[int, str]) -> None:
        self.layout.minHeight = value

    @property
    def min_width(self) -> Union[int, str]:
        """ Optional. The minimal width to be set for a cell """
        return self.layout.minWidth
    
    @min_width.setter
    def min_width(self, value: Union[int, str]) -> None:
        self.layout.minWidth = value

    @property
    def padding(self) -> Union[int, str]:
        """ Optional. Defines the distance between a cell and the border of layout """
        return self.layout.padding
    
    @padding.setter
    def padding(self, value: Union[int, str]) -> None:
        self.layout.padding = value

    @property
    def progress_default(self) -> bool:
        """ Optional. Defines whether the progress bar must be shown in a cell in the absence of the component/HTML content in the cell """
        return self.layout.progressDefault
    
    @progress_default.setter
    def progress_default(self, value: bool) -> None:
        self.layout.progressDefault = value

    @property
    def resizable(self) -> bool:
        """ Optional. Defines whether a cell can be resized """
        return self.layout.resizable
    
    @resizable.setter
    def resizable(self, value: bool) -> None:
        self.layout.resizable = value

    @property
    def type(self) -> str:
        """ Optional. Defines the type of borders between cells inside rows and columns of a layout """
        return self.layout.type

    @type.setter
    def type(self, value: str) -> None:
        self.layout.type = value

    @property
    def width(self) -> Union[int, str]:
        """ Optional. Sets the width of a cell """
        return self.layout.width
    
    @width.setter
    def width(self, value: Union[int, str]) -> None:
        self.layout.width = value

class MainWindow(Layout):
    def __init__(self) -> None:
        super().__init__(True)
        self.initialized = True

    def set_theme(self, theme: str) -> None:
        """ sets the layout theme """
        js.dhx.setTheme(theme)

