from __future__ import annotations

from hashlib import new
from uuid import uuid4
from typing import TypeVar
import js
import json

from .table import Table
from .stackedwidget import StackedWidget
from .header import Header, HeaderItemTypes
from .cardpanel import CardPanel

TLayout = TypeVar("TLayout", bound="Layout")

class Layout:
    
    def __init__(self, widget_name, parent=None, session_id=""):
        
        if not parent and session_id == "":
            #Raise this since we always need one or the other
            raise()
            
        self.parent = parent

        self.widget_name = widget_name
        self.session_id = session_id or self.parent.session_id
        self._raw_layout = js.dhx.Layout
        self.initialized = False

        self.widget_config = {
            "orientation": "horizontal",
            "panel_qty": 1,
            "name": self.widget_name,
            "panel_id_list": [],
            "padding": "3px",
            "html": ""
        }

        if widget_name == "mainwindow":
            self.widget_config["panel_id_list"] = ["mainwindow"]
            self.init_widget()


    def translate_config(self):
        return {}

    @property
    def raw_widget(self):
        return self._raw_layout

    def _add_panels(self, orientation, qty, panel_id_list):
        """Set orientationm panel quantity, and panel id list"""
        self.widget_config["orientation"] = orientation
        self.widget_config["panel_qty"] = qty
        self.widget_config["panel_id_list"] = panel_id_list
               
    def init_widget(self):
        """Initialize the widget for the first time"""
        self._raw_layout.new(None if self.widgetname != "mainwindow" else "maindiv", js.JSON.parse(json.dumps(self.widget_config)))
        self.initialized = True

    def add_layout(
        self,
        panel_index: str = 0,
        orientation: str = "horizontal",
        panel_qty: int = 1,
        panel_id_list: list = None,
        html: str = ""
    ) -> TLayout:
        new_layout = Layout(str(uuid4()), parent=self)
        new_layout._add_panels(orientation, panel_qty, panel_id_list or ["panel"+str(n) for n in range(0, panel_qty)])
        self.attach_widget(new_layout, panel_index)
        if html:
            new_layout.set_html(0, html)
        return new_layout

    def set_html(self, panel_index, html):
        self._raw_layout.setHTML(panel_index, html)

    def add_stackedwidget(self, panel_index: int = 0, pages: int = 1) -> StackedWidget:
        new_stackedwidget = StackedWidget(str(uuid4()), parent=self)
        new_stackedwidget.pages = pages
        self.attach_widget(new_stackedwidget, panel_index)
        for apage in range(0, pages):
            new_stackedwidget.layouts[apage] = Layout(str(uuid4()), parent=new_stackedwidget)
            new_stackedwidget.layouts[apage]._add_panels("horizontal", 1, [])
            new_stackedwidget.attach_widget(apage, new_stackedwidget.layouts[apage])
            new_stackedwidget.layouts[apage].set_border_size(0, 0)
        return new_stackedwidget
        
    #def add_elementitem(self, element_id: str) -> ElementItem:
    #    new_elementitem = ElementItem(str(uuid4()),element_id, parent=self)
    #    new_elementitem.init_widget()
    #    return new_elementitem

    def set_width(self, panel_index, width):
        self._raw_layout.getCell(panel_index).width = width

    def set_height(self, panel_index, height):
        self._raw_layout.getCell(panel_index).width = height

    def set_border_size(self, panel_index, size_in_px):
        self._raw_layout.setBorderSize(panel_index, size_in_px)

    def add_table(
        self,
        panel_index: int = 0,
        columns: list = [],
        header_alignment: str = 'center',
        rows_per_page: int = 0,
        show_searchbox: bool = False,
        show_checkbox: bool = False,
        column_types:list = [],
        column_alignments:list = [],
        total_records:int = 0,
        recs_per_page_list: list = [10, 20],
        records: list = []
    ) -> Table:
        new_table = Table(str(uuid4()), self)
        new_table.show_searchbox(show_searchbox)
        new_table.show_checkbox(show_checkbox)
        new_table.column_alignments = column_alignments
        new_table.column_types = column_types
        new_table.total_records = total_records
        new_table.header_alignment = header_alignment
        if rows_per_page:
            new_table.show_paginations(True)
            new_table.set_paginations("min", 1)
            new_table.set_paginations("value", rows_per_page)
            new_table.set_paginations("step", 1)
            new_table.set_recs_per_page_options(recs_per_page_list)
        new_table.add_columns(columns)
        new_table.raw_widget.records = records
        self.attach_widget(new_table, panel_index)
        return new_table

    def attach_widget(self, widget, panel_id=None):
        """Attach a widget to the layout on a specific panel"""
        if not panel_id:
            panel_id = self.widget_config["panel_id_list"][0]
        if not widget.initialized:
            widget.init_widget()
        self._raw_layout.getCell(panel_id).attach(widget.raw_widget)
