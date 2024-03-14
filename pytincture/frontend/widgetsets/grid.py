"""
pyTincture grid widget implementation
"""
from typing import Any, Callable, Dict, List, Optional, Union
import js
import json
from enum import Enum
from pyodide.ffi import create_proxy

class ColumnType:
    string = "string"
    number = "number"
    boolean = "boolean"
    date = "date"
    percent = "percent"

class EditorType:
    input = "input"
    select = "select"
    datePicker = "datePicker"
    combobox = "combobox"
    multiselect = "multiselect"
    textarea = "textarea"

class AdjustType:
    data = "data"
    header = "header"
    footer = "footer"

class Alignment:
    left = "left"
    center = "center"
    right = "right"

class FilterConfig:
    def __init__(
        self, 
        filter: Optional[Callable] = None, 
        multiselection: bool = False, 
        readonly: bool = True, 
        placeholder: str = "", 
        virtual: bool = False, 
        template: Optional[str] = None
    ) -> None:
        self.filter: create_proxy = create_proxy(filter) if filter else None
        self.multiselection: bool = multiselection
        self.readonly: bool = readonly
        self.placeholder: str = placeholder
        self.virtual: bool = virtual
        self.template: Optional[str] = template
    
    @property
    def config(self) -> Dict[str, Any]:
        return {
            "filter": self.filter,
            "multiselection": self.multiselection,
            "readonly": self.readonly,
            "placeholder": self.placeholder,
            "virtual": self.virtual,
            "template": self.template
        }

class ContentType:
    text = "text"
    input = "input"
    select = "select"
    combo = "combo"
    avg = "avg"
    sum = "sum"
    max = "max"
    min = "min"
    count = "count"

class Header:
    def __init__(self) -> None:
        self.text: str = ""
        self.tooltip: Optional[str] = None
        self.tooltip_template: Optional[str] = None
        self.align: str = "left"
        self.colspan: Optional[str] = None
        self.rowspan: Optional[str] = None
        self.css: str = ""
        self.content: Optional[str] = None
        self.filter: Optional[str] = None
        self.filter_config: Optional[str] = None
        self.custom_filter: Optional[str] = None
        self.header_sort: bool = False
        self.sort_as: Optional[str] = None
        self.html_enable: bool = False
            
    def append(
        self,
        text: str = "text",
        tooltip: Optional[str] = None,
        tooltip_template: Optional[str] = None,
        align: str = Alignment.left,
        colspan: Optional[int] = None,
        rowspan: Optional[int] = None,
        css: str = "",
        content: Optional[str] = None,
        filter: Optional[Callable] = None,
        filter_config: Optional[FilterConfig] = None,
        custom_filter: Optional[Callable] = None,
        header_sort: bool = False,
        sort_as: Optional[str] = None,
        html_enable: bool = False
    ) -> None:
        self.text = text
        self.tooltip = tooltip
        self.tooltip_template = tooltip_template
        self.align = align
        self.colspan = colspan
        self.rowspan = rowspan
        self.css = css
        self.content = content
        self.filter = create_proxy(filter) if filter else None
        self.filter_config = filter_config.config if filter_config else None
        self.custom_filter = create_proxy(custom_filter) if custom_filter else None
        self.header_sort = header_sort
        self.sort_as = sort_as
        self.html_enable = html_enable
        
    @property
    def config(self) -> Dict[str, Any]:
        return [{
            "text": self.text,
            "tooltip": self.tooltip,
            "tooltipTemplate": self.tooltip_template,
            "align": self.align,
            "colspan": self.colspan,
            "rowspan": self.rowspan,
            "css": self.css,
            "content": self.content,
            "filter": self.filter,
            "filterConfig": self.filter_config,
            "customFilter": self.custom_filter,
            "headerSort": self.header_sort,
            "sortAs": self.sort_as,
            "htmlEnable": self.html_enable
        }]

class Footer:
    def __init__(self) -> None:
        self.text = ""
        self.tooltip = None
        self.tooltip_template = None
        self.align = "left"
        self.colspan = None
        self.rowspan = None
        self.css = ""
        self.content = None
        self.sort_as = None
        self.html_enable = False

    def append(
        self,
        text: str = "text",
        tooltip: Optional[str] = None,
        tooltip_template: Optional[str] = None,
        align: str = Alignment.left,
        colspan: Optional[int] = None,
        rowspan: Optional[int] = None,
        css: str = "",
        content: Optional[str] = None,
        sort_as: Optional[str] = None,
        html_enable: bool = False
    ) -> None:
        self.text = text
        self.tooltip = tooltip
        self.tooltip_template = tooltip_template
        self.align = align
        self.colspan = colspan
        self.rowspan = rowspan
        self.css = css
        self.content = content
        self.sort_as = sort_as
        self.html_enable = html_enable

    @property
    def config(self) -> Dict[str, Any]:
        return [{
            "text": self.text,
            "tooltip": self.tooltip,
            "tooltipTemplate": self.tooltip_template,
            "align": self.align,
            "colspan": self.colspan,
            "rowspan": self.rowspan,
            "css": self.css,
            "content": self.content,
            "sortAs": self.sort_as,
            "htmlEnable": self.html_enable
        }]

class Column:
    def __init__(
        self,
        id: Any,
        header: Header = Header(),
        footer: Footer = Footer(),
        width: int = 100,
        min_width: int = 20,
        max_width: Optional[int] = None,
        auto_width: bool = False,
        type: str = ColumnType.string,
        editor_type: str = EditorType.input,
        format: str = "",
        adjust: str = AdjustType.data,
        align: str = Alignment.left,
        html_enable: bool = False,
        hidden: bool = False,
        draggable: bool = False,
        editable: bool = False,
        resizable: bool = False,
        sortable: bool = True,
        tooltip: bool = False,
        tooltip_template: Optional[str] = None
    ) -> None:
        self.id = id
        self.header = header
        self.footer = footer
        self.width = width
        self.min_width = min_width
        self.max_width = max_width
        self.auto_width = auto_width
        self.type = type
        self.editor_type = editor_type
        self.format = format
        self.adjust = adjust
        self.align = align
        self.html_enable = html_enable
        self.hidden = hidden
        self.draggable = draggable
        self.editable = editable
        self.resizable = resizable
        self.sortable = sortable
        self.tooltip = tooltip
        self.tooltip_template = tooltip_template

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "header": self.header.config if self.header else None,
            "footer": self.footer.config if self.footer else None,
            "width": self.width,
            "minWidth": self.min_width,
            #"maxWidth": self.max_width,
            "autoWidth": self.auto_width,
            "type": self.type,
            "editorType": self.editor_type,
            "format": self.format,
            "adjust": self.adjust,
            "align": self.align,
            "htmlEnable": self.html_enable,
            "hidden": self.hidden,
            "draggable": self.draggable,
            "editable": self.editable,
            "resizable": self.resizable,
            "sortable": self.sortable,
            "tooltip": self.tooltip,
            "tooltipTemplate": self.tooltip_template
        }

class Grid:
    "Grid widget implemenataion"
    def __init__(self, widget_config: Dict[str, Any], columns: List[Dict[str, Any]], data_url: str, **kwargs) -> None:
        self.grid = js.dhx.Grid
        self.widget_config = widget_config
        col_data = []
        for col in columns:
            col_data.append(Column(**col).config)
        self.widget_config["columns"] = col_data
        self.grid = self.grid.new(None, js.JSON.parse(json.dumps(self.widget_config))) 
        self.grid.data.load(data_url)
        self.initialized = False

    """ Grid methods """
    # Modules
    def add_cell_css(self, row_id: Union[str, int], col_id: Union[str, int], css: str) -> None:
        """adds a style to a cell"""
        self.grid.addCellCss(row_id, col_id, css)

    def add_row_css(self, row_id: Union[str, int], css: str) -> None:
        """adds a style to a row"""
        self.grid.addRowCss(row_id, css)

    def add_span(self, row_id: Union[str, int], col_id: Union[str, int], width, height, value) -> None:
        """adds a rows/cols span"""
        self.grid.addSpan(row_id, col_id, width, height, value)

    def adjust_column_width(self, col_id: Union[str, int]) -> None:
        """adjusts the width of a column to make all its content visible"""
        self.grid.adjustColumnWidth(col_id)
    
    def destructor(self) -> None:
        """removes a Grid instance and releases occupied resources"""
        self.grid.destructor()

    def edit_cell(self, row_id: Union[str, int], col_id: Union[str, int], editor_type: str) -> None:
        """enables editing of a Grid cell"""
        self.grid.editCell(row_id, col_id, editor_type)

    def edit_end(self, preserve: bool, preserve_value: Union[str, int]) -> None:
        """finishes editing in a cell"""
        self.grid.editEnd(preserve, preserve_value)

    def get_cell_rect(self, row_id: Union[str, int], col_id: Union[str, int]) -> None:
        """returns the parameters of a cell"""
        self.grid.getCellRect(row_id, col_id)

    def get_column(self, col_id: Union[str, int]) -> None:
        """returns an object with attributes of a column"""
        self.grid.getColumn(col_id)

    def get_header_filter(self, col_id: Union[str, int]) -> None:
        """returns an object with a set of methods for the header filter of the specified column"""
        self.grid.getHeaderFilter(col_id)   

    def get_scroll_state(self) -> None:
        """returns the coordinates of a position a grid has been scrolled to"""
        self.grid.getScrollState()

    def get_sorting_state(self) -> None:
        """returns the current state of sorting data in Grid"""
        self.grid.getSortingState()

    def get_span(self, row_id: Union[str, int], col_id: Union[str, int]) -> None:
        """returns an object with spans"""
        self.grid.getSpan(row_id, col_id)

    def hide_column(self, col_id: Union[str, int]) -> None:
        """hides a column of Grid"""
        self.grid.hideColumn(col_id)

    def hide_row(self, row_id: Union[str, int]) -> None:
        """hides a row of Grid"""
        self.grid.hideRow(row_id)

    def is_column_hidden(self, col_id: Union[str, int]) -> None:
        """checks whether a column is hidden"""
        self.grid.isColumnHidden(col_id)
    
    def is_row_hidden(self, row_id: Union[str, int]) -> None:
        """checks whether a row is hidden"""
        self.grid.isRowHidden(row_id)

    def paint(self) -> None:
        """repaints a grid on a page"""
        self.grid.paint()

    def remove_cell_css(self, row_id: Union[str, int], col_id: Union[str, int], css: str) -> None:
        """removes a style from a cell"""
        self.grid.removeCellCss(row_id, col_id, css)

    def remove_row_css(self, row_id: Union[str, int], css: str) -> None:
        """removes a style from a row"""
        self.grid.removeRowCss(row_id, css)

    def remove_span(self, row_id: Union[str, int], col_id: Union[str, int]) -> None:
        """removes a cols/rows span"""
        self.grid.removeSpan(row_id, col_id)

    def scroll(self, x: int, y: int) -> None:
        """scrolls a grid according to specified coordinates"""
        self.grid.scroll(x, y)

    def scroll_to(self, row_id: Union[str, int], col_id: Union[str, int]) -> None:
        """scrolls a grid to a specified cell"""
        self.grid.scrollTo(row_id, col_id)

    def set_columns(self, columns: List[Dict]) -> None:
        """sets configuration for Grid columns"""
        self.grid.setColumns(columns)

    def show_column(self, col_id: Union[str, int]) -> None:
        """makes a specified column visible on a page"""
        self.grid.showColumn(col_id)

    def show_row(self, row_id: Union[str, int]) -> None:
        """makes a specified row visible on a page"""
        self.grid.showRow(row_id)

    """ Grid events """
    # Editing

    def after_edit_end(self, handler: Callable) -> None:
        """fires after editing of a cell is ended"""
        self.grid.events.on("afterEditEnd", create_proxy(handler))

    def after_edit_start(self, handler: Callable) -> None:
        """fires after editing of a cell has started"""
        self.grid.events.on("afterEditStart", create_proxy(handler))

    def before_edit_end(self, handler: Callable) -> None:
        """fires before editing of a cell is completed"""
        self.grid.events.on("beforeEditEnd", create_proxy(handler))

    def before_edit_start(self, handler: Callable) -> None:
        """fires before editing of a cell has started"""
        self.grid.events.on("beforeEditStart", create_proxy(handler))

    # Mouse
    
    def cell_click(self, handler: Callable) -> None:
        """fires on click on a grid cell"""
        self.grid.events.on("cellClick", create_proxy(handler))

    def cell_dbl_click(self, handler: Callable) -> None:
        """fires on double-click on a grid cell"""
        self.grid.events.on("cellDblClick", create_proxy(handler))

    def cell_mouse_down(self, handler: Callable) -> None:
        """fires before releasing the left mouse button when clicking on a grid cell"""
        self.grid.events.on("cellMouseDown", create_proxy(handler))

    def cell_mouse_over(self, handler: Callable) -> None:
        """fires on moving the mouse pointer over a grid cell"""
        self.grid.events.on("cellMouseOver", create_proxy(handler))

    def cell_right_click(self, handler: Callable) -> None:
        """fires on right click on a grid cell"""
        self.grid.events.on("cellRightClick", create_proxy(handler))

    def footer_cell_click(self, handler: Callable) -> None:
        """fires on click on a grid footer cell"""
        self.grid.events.on("footerCellClick", create_proxy(handler))

    def footer_cell_dbl_click(self, handler: Callable) -> None:
        """fires on double-click on a grid footer cell"""
        self.grid.events.on("footerCellDblClick", create_proxy(handler))

    def footer_cell_mouse_down(self, handler: Callable) -> None:
        """fires on moving the mouse pointer over a grid footer cell"""
        self.grid.events.on("footerCellMouseDown", create_proxy(handler))

    def footer_cell_mouse_over(self, handler: Callable) -> None:
        """fires on moving the mouse pointer over a grid footer cell"""
        self.grid.events.on("footerCellMouseOver", create_proxy(handler))

    def footer_cell_right_click(self, handler: Callable) -> None:
        """fires on right click on a grid footer cell"""
        self.grid.events.on("footerCellRightClick", create_proxy(handler))

    def header_cell_click(self, handler: Callable) -> None:
        """fires on click on a grid header cell"""
        self.grid.events.on("headerCellClick", create_proxy(handler))
    
    def header_cell_dbl_click(self, handler: Callable) -> None:
        """fires on double-click on a grid footer cell"""
        self.grid.events.on("headerCellDblClick", create_proxy(handler))

    def header_cell_mouse_down(self, handler: Callable) -> None:
        """fires on moving the mouse pointer over a grid header cell"""
        self.grid.events.on("headerCellMouseDown", create_proxy(handler))

    def header_cell_mouse_over(self, handler: Callable) -> None:
        """fires on moving the mouse pointer over a grid header cell"""
        self.grid.events.on("headerCellMouseOver", create_proxy(handler))

    def header_cell_right_click(self, handler: Callable) -> None:
        """fires on right click on a grid header cell"""
        self.grid.events.on("headerCellRightClick", create_proxy(handler))

    # Key Navigation and Scroll
    
    def after_key_down(self, handler: Callable) -> None:
        """fires after the user is pressing a shortcut key"""
        self.grid.events.on("afterKeyDown", create_proxy(handler))

    def before_key_down(self, handler: Callable) -> None:
        """fires before the user is pressing a shortcut key"""
        self.grid.events.on("beforeKeyDown", create_proxy(handler))

    def scroll(self, handler: Callable) -> None:
        """fires on scrolling a grid"""
        self.grid.events.on("scroll", create_proxy(handler))

    # Sort and Filter
        
    def after_sort(self, handler: Callable) -> None:
        """fires after a column is sorted by clicking on its header"""
        self.grid.events.on("afterSort", create_proxy(handler))

    def before_filter(self, handler: Callable) -> None:
        """fires before the filterChange event is called"""
        self.grid.events.on("beforeFilter", create_proxy(handler))

    def before_sort(self, handler: Callable) -> None:
        """fires before a column is sorted by clicking on its header"""
        self.grid.events.on("beforeSort", create_proxy(handler))

    def filter_change(self, handler: Callable) -> None:
        """fires on typing text in an input of a column's header"""
        self.grid.events.on("filterChange", create_proxy(handler))

    # Column Drag and Drop
        
    def after_column_drag(self, handler: Callable) -> None:
        """fires after dragging of a column is finished"""
        self.grid.events.on("afterColumnDrag", create_proxy(handler))

    def after_column_drop(self, handler: Callable) -> None:
        """fires before the user has finished dragging a column but after the mouse button is released"""
        self.grid.events.on("afterColumnDrop", create_proxy(handler))

    def before_column_drag(self, handler: Callable) -> None:
        """fires before dragging of a column has started"""
        self.grid.events.on("beforeColumnDrag", create_proxy(handler))

    def before_column_drop(self, handler: Callable) -> None:
        """fires before the user has finished dragging and released the mouse button over a target column"""
        self.grid.events.on("beforeColumnDrop", create_proxy(handler))

    def cancel_column_drop(self, handler: Callable) -> None:
        """fires on moving a mouse pointer out of borders of a column while dragging the column"""
        self.grid.events.on("cancelColumnDrop", create_proxy(handler))

    def can_column_drop(self, handler: Callable) -> None:
        """fires when a dragged column is placed over a target column"""
        self.grid.events.on("canColumnDrop", create_proxy(handler))

    def drag_column_in(self, handler: Callable) -> None:
        """fires when a column is dragged to another potential target"""
        self.grid.events.on("dragColumnIn", create_proxy(handler))

    def drag_column_out(self, handler: Callable) -> None:
        """fires when a column is dragged out of a potential target"""
        self.grid.events.on("dragColumnOut", create_proxy(handler))

    def drag_column_start(self, handler: Callable) -> None:
        """fires when dragging of a column has started"""
        self.grid.events.on("dragColumnStart", create_proxy(handler))

    # Column Hide and Show
        
    def after_column_hide(self, handler: Callable) -> None:
        """fires after a column is hidden"""
        self.grid.events.on("afterColumnHide", create_proxy(handler))

    def after_column_show(self, handler: Callable) -> None:
        """fires after a column is shown"""
        self.grid.events.on("afterColumnShow", create_proxy(handler))

    def before_column_hide(self, handler: Callable) -> None:
        """fires before a column is hidden"""
        self.grid.events.on("beforeColumnHide", create_proxy(handler))

    def before_column_show(self, handler: Callable) -> None:
        """fires before a column is shown on a page"""
        self.grid.events.on("beforeColumnShow", create_proxy(handler))

    # Column Resize
        
    def after_resize_end(self, handler: Callable) -> None:
        """fires after resizing of a column is ended"""
        self.grid.events.on("afterResizeEnd", create_proxy(handler))

    def before_resize_start(self, handler: Callable) -> None:
        """fires before resizing of a column has started"""
        self.grid.events.on("beforeResizeStart", create_proxy(handler))

    def resize(self, handler: Callable) -> None:
        """fires on resizing a column"""
        self.grid.events.on("resize", create_proxy(handler))

    # Row Drag and Drop
        
    def after_row_drag(self, handler: Callable) -> None:
        """fires after dragging of a row is finished"""
        self.grid.events.on("afterRowDrag", create_proxy(handler))

    def after_row_drop(self, handler: Callable) -> None:
        """fires before the user has finished dragging a row but after the mouse button is released"""
        self.grid.events.on("afterRowDrop", create_proxy(handler))

    def before_row_drag(self, handler: Callable) -> None:
        """fires before dragging of a row has started"""
        self.grid.events.on("beforeRowDrag", create_proxy(handler))

    def before_row_drop(self, handler: Callable) -> None:
        """fires before the user has finished dragging and released the mouse button over a target row"""
        self.grid.events.on("beforeRowDrop", create_proxy(handler))

    def cancel_row_drop(self, handler: Callable) -> None:
        """fires on moving a mouse pointer out of borders of a row while dragging the row"""
        self.grid.events.on("cancelRowDrop", create_proxy(handler))

    def can_row_drop(self, handler: Callable) -> None:
        """fires when a dragged row is placed over a target row"""
        self.grid.events.on("canRowDrop", create_proxy(handler))

    def drag_row_in(self, handler: Callable) -> None:
        """fires when a row is dragged to another potential target"""
        self.grid.events.on("dragRowIn", create_proxy(handler))

    def drag_row_out(self, handler: Callable) -> None:
        """fires when a row is dragged out of a potential target"""
        self.grid.events.on("dragRowOut", create_proxy(handler))

    def drag_row_start(self, handler: Callable) -> None:
        """fires when dragging of a row has started"""
        self.grid.events.on("dragRowStart", create_proxy(handler))

    # Row Hide and Show
        
    def after_row_hide(self, handler: Callable) -> None:
        """fires after a row is hidden"""
        self.grid.events.on("afterRowHide", create_proxy(handler))

    def after_row_show(self, handler: Callable) -> None:
        """fires after a row is shown on a page"""
        self.grid.events.on("afterRowShow", create_proxy(handler))

    def before_row_hide(self, handler: Callable) -> None:
        """fires before a row is hidden"""
        self.grid.events.on("beforeRowHide", create_proxy(handler))

    def before_row_show(self, handler: Callable) -> None:
        """fires before a row is shown on a page"""
        self.grid.events.on("beforeRowShow", create_proxy(handler))

    # Row Resize
        
    def after_row_resize(self, handler: Callable) -> None:
        """fires after the height of a row is changed"""
        self.grid.events.on("afterRowResize", create_proxy(handler))

    def before_row_resize(self, handler: Callable) -> None:
        """fires before the height of a row is changed"""
        self.grid.events.on("beforeRowResize", create_proxy(handler))
        
    """ Grid properties getter and setter"""
    # Modules

    @property
    def adjust(self) -> Union[bool, str]:
        """Optional. Defines whether the width of columns is automatically adjusted to the width of their content"""
        return self.grid.adjust
    
    @adjust.setter
    def adjust(self, value: Union[bool, str]) -> None:
        self.grid.adjust = value

    @property
    def auto_empty_row(self) -> bool:
        """Optional. Adds an empty row after the last filled row in the Grid"""
        return self.grid.autoEmptyRow
    
    @auto_empty_row.setter
    def auto_empty_row(self, value: bool) -> None:
        self.grid.autoEmptyRow = value

    @property
    def auto_height(self) -> bool:
        """Optional. Makes long text split into multiple lines based on the width of the column, controls the automatic height adjustment of the header/footer and cells with data"""
        return self.grid.autoHeight
    
    @auto_height.setter
    def auto_height(self, value: bool) -> None:
        self.grid.autoHeight = value

    @property
    def auto_width(self) -> bool:
        """Optional. Makes grid's columns to fit the size of a grid"""
        return self.grid.autoWidth
    
    @auto_width.setter
    def auto_width(self, value: bool) -> None:
        self.grid.autoWidth = value

    @property
    def bottom_split(self) -> int:
        """Optional. Sets the number of frozen rows from the bottom"""
        return self.grid.bottomSplit
    
    @bottom_split.setter
    def bottom_split(self, value: int) -> None:
        self.grid.bottomSplit = value

    @property
    def columns(self) -> List[Dict[str, Any]]:
        """Required. Specifies the configuration of grid columns"""
        return self.grid.columns
    
    @columns.setter
    def columns(self, value: List[Dict[str, Any]]) -> None:
        self.grid.columns = value

    @property
    def css(self) -> str:
        """Optional. Adds style classes to Grid"""
        return self.grid.css
    
    @css.setter
    def css(self, value: str) -> None:
        self.grid.css = value

    @property
    def data(self) -> Callable:
        """Optional. Specifies an array of data objects to set into the grid"""
        return self.grid.data
    
    @data.setter
    def data(self, data_url: str) -> None:
        dataset = js.Librarydhx.DataCollection.new()
        dataset.load(data_url)
        self.grid.data = dataset

    def parse(self, data: List[Dict[str, Any]]) -> None:
        """loads data to the grid"""
        self.grid.data.parse(data)

    @property
    def drag_copy(self) -> bool:
        """Optional. Defines that a row is copied to a target during drag-n-drop"""
        return self.grid.dragCopy
    
    @drag_copy.setter
    def drag_copy(self, value: bool) -> None:
        self.grid.dragCopy = value

    @property
    def drag_item(self) -> str:
        """Optional. Enables the possibility to reorder grid columns or (and) rows by drag and drop"""
        return self.grid.dragItem
    
    @drag_item.setter
    def drag_item(self, value: str) -> None:
        self.grid.dragItem = value

    @property
    def drag_mode(self) -> str:
        """Optional. Enables drag-n-drop in Grid"""
        return self.grid.dragMode
    
    @drag_mode.setter
    def drag_mode(self, value: str) -> None:
        self.grid.dragMode = value

    @property
    def editable(self) -> bool:
        """Optional. Enables editing in Grid columns"""
        return self.grid.editable
    
    @editable.setter
    def editable(self, value: bool) -> None:
        self.grid.editable = value

    @property
    def event_handlers(self) -> Dict[Any, Any]:
        """Optional. Adds event handlers to the HTML elements of a custom template in a cell, or to the HTML elements defined in the data set, or to the header/footer cell"""
        return self.grid.eventHandlers
    
    @event_handlers.setter
    def event_handlers(self, value: Dict[Any, Any]) -> None:
        self.grid.eventHandlers = value

    @property
    def export_styles(self) -> Union[bool, List[str]]:
        """Optional. Defines the styles that will be sent to the export service when exporting Grid to PDF/PNG"""
        return self.grid.exportStyles
    
    @export_styles.setter
    def export_styles(self, value: Union[bool, List[str]]) -> None:
        self.grid.exportStyles = value

    @property
    def footer_auto_height(self) -> bool:
        """Optional. Allows adjusting the height of the footer for the content to fit in"""
        return self.grid.footerAutoHeight
    
    @footer_auto_height.setter
    def footer_auto_height(self, value: bool) -> None:
        self.grid.footerAutoHeight = value

    @property
    def footer_row_height(self) -> int:
        """Optional. Sets the height of rows in the footer"""
        return self.grid.footerRowHeight
    
    @footer_row_height.setter
    def footer_row_height(self, value: int) -> None:
        self.grid.footerRowHeight = value

    @property
    def footer_tool_tip(self) -> bool:
        """Optional. Controls the footer tooltips"""
        return self.grid.footerTooltip
    
    @footer_tool_tip.setter
    def footer_tool_tip(self, value: bool) -> None:
        self.grid.footerTooltip = value

    @property
    def header_auto_height(self) -> bool:
        """Optional. Allows adjusting the height of the header for the content to fit in"""
        return self.grid.headerAutoHeight
    
    @header_auto_height.setter
    def header_auto_height(self, value: bool) -> None:
        self.grid.headerAutoHeight = value

    @property
    def header_row_height(self) -> int:
        """Optional. Sets the height of rows in the header"""
        return self.grid.headerRowHeight
    
    @header_row_height.setter
    def header_row_height(self, value: int) -> None:
        self.grid.headerRowHeight = value

    @property
    def header_tooltip(self) -> bool:
        """Optional. Controls the header tooltips"""
        return self.grid.headerTooltip
    
    @header_tooltip.setter
    def header_tooltip(self, value: bool) -> None:
        self.grid.headerTooltip = value

    @property
    def height(self) -> Union[int, str]:
        """Optional. Sets the height of a grid or adjusts it automatically to the content"""
        return self.grid.height

    @height.setter
    def height(self, value: Union[int, str]) -> None:
        self.grid.height = value

    @property
    def html_enable(self) -> bool:
        """Optional. Specifies the HTML content (inner HTML) of Grid columns"""
        return self.grid.htmlEnable
    
    @html_enable.setter
    def html_enable(self, value: bool) -> None:
        self.grid.htmlEnable = value

    @property
    def key_navigation(self) -> bool:
        """Optional. Enables keyboard navigation in Grid"""
        return self.grid.keyNavigation
    
    @key_navigation.setter
    def key_navigation(self, value: bool) -> None:
        self.grid.keyNavigation = value

    @property
    def left_split(self) -> int:
        """Optional. Sets the number of frozen columns from the left"""
        return self.grid.leftSplit
    
    @left_split.setter
    def left_split(self, value: int) -> None:
        self.grid.leftSplit = value
    
    @property
    def multiselection(self) -> bool:
        """Optional. Enables multi-row/multi-cell selection in Grid"""
        return self.grid.multiselection
    
    @multiselection.setter
    def multiselection(self, value: bool) -> None:
        self.grid.multiselection = value

    @property
    def resizable(self) -> bool:
        """Optional. Defines whether columns can be resized"""
        return self.grid.resizable
    
    @resizable.setter
    def resizable(self, value: bool) -> None:
        self.grid.resizable = value

    @property
    def right_split(self) -> int:
        """Optional. Sets the number of frozen columns from the right"""
        return self.grid.rightSplit

    @right_split.setter
    def right_split(self, value: int) -> None:
        self.grid.rightSplit = value

    @property
    def row_css(self) -> Callable:
        """Optional. Sets style for a row"""
        return self.grid.rowCss
    
    @row_css.setter
    def row_css(self, value: Callable) -> None:
        self.grid.rowCss = value

    @property
    def row_height(self) -> int:
        """Optional. Defines the height of a row in a grid"""
        return self.grid.rowHeight
    
    @row_height.setter
    def row_height(self, value: int) -> None:
        self.grid.rowHeight = value

    @property
    def selection(self) -> Union[bool, str]:
        """Optional. Enables selection in a grid"""
        return self.grid.selection
    
    @selection.setter
    def selection(self, value: Union[bool, str]) -> None:
        self.grid.selection = value

    @property
    def sortable(self) -> bool:
        """Optional. Defines whether sorting on clicking headers of columns is enabled"""
        return self.grid.sortable
    
    @sortable.setter
    def sortable(self, value: bool) -> None:
        self.grid.sortable = value

    @property
    def spans(self) -> List[Dict[str, str]]:
        """Optional. Describes the configuration of cols/rows spans"""
        return self.grid.spans
    
    @spans.setter
    def spans(self, value: List[Dict[str, str]]) -> None:
        self.grid.spans = value

    @property
    def tooltip(self) -> bool:
        """Optional. Enables/disables all the tooltips of a column"""
        return self.grid.tooltip
    
    @tooltip.setter
    def tooltip(self, value: bool) -> None:
        self.grid.tooltip = value

    @property
    def top_split(self) -> int:
        """Optional. Sets the number of frozen rows from the top"""
        return self.grid.topSplit
    
    @top_split.setter
    def top_split(self, value: int) -> None:
        self.grid.topSplit = value

    @property
    def width(self) -> int:
        """Optional. Sets the width of a grid"""
        return self.grid.width
    
    @width.setter
    def width(self, value: int) -> None:
        self.grid.width = value

    # Selection API
        
    def disable(self) -> None:
        """disables selection of cells in Grid"""
        self.grid.selection.disable()

    def enable(self) -> None:
        """enables selection of cells in Grid"""
        self.grid.selection.enable()

    def get_cell(self) -> None:
        """returns the object of a selected cell"""
        self.grid.selection.getCell()

    def get_cells(self) -> None:
        """returns an array with config objects of selected cells"""
        self.grid.selection.getCells()

    def remove_cell(self) -> None:
        """unselects previously selected cells"""
        self.grid.selection.removeCell()
        
    def set_cell(self) -> None:
        """sets selection to specified cells"""
        self.grid.selection.setCell()

    # Export API
        
    def csv(self) -> None:
        """Exports data from a grid into a CSV file"""
        self.grid.export.csv()

    def pdf(self) -> None:
        """Exports data from a grid to a PDF file"""
        self.grid.export.pdf()

    def png(self) -> None:
        """Exports data from a grid to a PNG file"""
        self.grid.export.png()

    def xlsx(self) -> None:
        """Exports data from a grid to an Excel file"""
        self.grid.export.xlsx()
