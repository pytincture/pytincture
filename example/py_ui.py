
"""
 Example application
"""
import sys
from dhxpyt.layout import MainWindow
from dhxpyt.accordion import PTAccordion
import json
import copy

from py_ui_data import py_ui_data as pud

import form_window


class py_ui(MainWindow):
    def __init__(self):
        super().__init__()
        self.set_theme("dark") 
        self.fwin = form_window.FormExample()
        self.pud = pud()
        self.load_ui()

    def load_ui(self):

        # Create a column based layout and add it to the mainwindow
        #  left column will be for a sidebar
        #  right column will be for a toolbar and a grid
        self.base_layout = self.add_layout(
            layout_config= {
                "css":"dhx_toolbar--text_color_white",
                "cols" :[
                    {"width":"auto", "id": "left"},
                    {"width":"100%", "id": "right"}
                ]
            }
        )

        # Create a sidebar and add it to the left column
        self.sbmain = self.base_layout.add_sidebar(id="left", data=self.pud.sidebar_data)
        # Have the sidebar start off in collapsed mode
        self.sbmain.collapse()

    
        # Create a layout for the right column
        #  top row will be for a toolbar
        #  bottom row will be for a grid
        self.sub_layout = self.base_layout.add_layout(
            id="right",
            layout_config = {
            "css":"dhx_layout-cell--bordered", "type":"line", 
                "rows" :[
                    {"height":"auto", "id": "top"},
                    {"height":"100%", "id": "bottom"}
                ]
            }
        )

        # Create a toolbar and add it to the top row
        self.maintb = self.sub_layout.add_toolbar(
            id="top",
            toolbar_config = {"css":"dhx_toolbar--text_color_white"},
            data = self.pud.toolbar_data
        )

        # Attach a signal to the main toolbar to handle clicks
        self.maintb.click(self.menu_clicked)

        # Create a grid and add it to the bottom row
        self.grid = self.sub_layout.add_grid(
            id="bottom",
            grid_config={"height": "100%", "width": "100%", "selection": "row", "multiselection": True},
            columns = copy.deepcopy(self.pud.grid_column_data)
        )

        self.grid.parse(self.pud.dataset())
        print(self.pud.printme())

    def menu_clicked(self, id, e):
        # Handle clicks on the main toolbar
        # if the id is "other" then toggle the sidebar
        # if the id is "add" then show the form window
        if id ==  "other":
            self.sbmain.toggle()
        elif id == "add":
            self.fwin.show()

if __name__ == "__main__" and sys.platform != "emscripten":
    from pytincture import launch_service
    launch_service()
