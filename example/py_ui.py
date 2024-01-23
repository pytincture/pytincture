
"""
 Example application
"""
import sys
from pytincture.frontend.widgetsets.layout import MainWindow
import copy

import form_window


class py_ui(MainWindow):
    def __init__(self):
        super().__init__()
        self.set_theme("custom-theme-dark")
        self.fwin = form_window.FormExample()
        self.load_ui()

    def load_ui(self):

        # Create a column based layout and add it to the mainwindow
        #  left column will be for a sidebar
        #  right column will be for a toolbar and a grid
        self.base_layout = self.add_layout(
            layout_config= {
                "css":"dhx_toolbar--text_color_white",
                "cols" :[
                    {"header":None,"width":"auto", "id": "left"},
                    {"header":None,"width":"100%", "id": "right"}
                ]
            }
        )


        sidebar_data = [
            {"id": "dashboard", "value": "Dashboard", "icon": "mdi mdi-view-dashboard"},
            {"id": "statistics", "value": "Statistics", "icon": "mdi mdi-chart-line"},
            {"id": "reports", "value": "Reports", "icon": "mdi mdi-file-chart"},
            {"type": "separator"},
            {"id": "posts", "value": "Posts", "icon": "mdi mdi-square-edit-outline", "items": [
                {"id": "addPost", "value": "New Post", "icon": "mdi mdi-plus"},
                {"id": "allPost", "value": "Posts", "icon": "mdi mdi-view-list"},
                {"id": "categoryPost", "value": "Category", "icon": "mdi mdi-tag"}
            ]},
            {"id": "pages", "value": "Pages", "icon": "mdi mdi-file-outline", "items": [
                {"id": "addPage", "value": "New Page", "icon": "mdi mdi-plus"},
                {"id": "allPage", "value": "Pages", "icon": "mdi mdi-view-list"},
                {"id": "categoryPages", "value": "Category", "icon": "mdi mdi-tag"}
            ]},
            {"id": "messages", "value": "Messages", "count": 18, "icon": "mdi mdi-email-mark-as-unread"},
            {"id": "media", "value": "Media", "icon": "mdi mdi-folder-multiple-image"},
            {"id": "links", "value": "Links", "icon": "mdi mdi-link"},
            {"id": "comments", "value": "Comments", "icon": "mdi mdi-comment-multiple-outline", "count": "118", "countColor": "primary", "items": [
                {"id": "myComments", "value": "My Comments", "count": 15, "icon": "mdi mdi-account"},
                {"id": "allComments", "value": "All Comments", "count": 103, "countColor": "primary", "icon": "mdi mdi-comment-multiple-outline"}
            ]},
            {"type": "spacer"},
            {"id": "notification", "value": "Notification", "count": 25, "icon": "mdi mdi-bell", "countColor": "primary"},
            {"id": "configuration", "value": "Configuration", "icon": "mdi mdi-settings", "items": [
                {"id": "myAccount", "value": "My Account", "icon": "mdi mdi-account-settings"},
                {"id": "general", "value": "General Configuration", "icon": "mdi mdi-tune"}
            ]}
        ]

        # Create a sidebar and add it to the left column
        self.sbmain = self.base_layout.add_sidebar(id="left",sidebar_config={}, data=sidebar_data)
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
                    {"header":None,"height":"auto", "id": "top"},
                    {"header":None,"height":"100%", "id": "bottom"}
                ]
        })

        toolbar_data = [
            {
                "id": "other",
                "type": "button",
                "view": "link",
                "circle": True,
                "color": "secondary",
                "icon": "mdi mdi-menu"
            },
            {
                "id": "add",
                "icon": "mdi mdi-plus",
                "value": "Add"
            }
        ]

        # Create a toolbar and add it to the top row
        self.maintb = self.sub_layout.add_toolbar(
            id="top",
            toolbar_config = {"css":"dhx_toolbar--text_color_white"},
            data = toolbar_data
        )

        # Attach a signal to the main toolbar to handle clicks
        self.maintb.click(self.menu_clicked)

        columns = [
            { "width": 300, "id": "title", "header": [{ "text": "Title" }] },
            { "width": 200, "id": "authors", "header": [{ "text": "Authors" }] },
            { "width": 80, "id": "average_rating", "header": [{ "text": "Rating" }] },
            { "width": 150, "id": "publication_date", "header": [{ "text": "Publication date" }] },
            { "width": 150, "id": "isbn13", "header": [{ "text": "isbn" }] },
            { "width": 90, "id": "language_code", "header": [{ "text": "Language" }] },
            { "width": 90, "id": "num_pages", "header": [{ "text": "Pages" }] },
            { "width": 120, "id": "ratings_count", "header": [{ "text": "Raiting count" }] },
            { "width": 100, "id": "text_reviews_count", "header": [{ "text": "Text reviews count" }] },
            { "width": 200, "id": "publisher", "header": [{ "text": "Publisher" }] }
        ]

        # Create a grid and add it to the bottom row
        self.sub_layout.add_grid(
            id="bottom",
            grid_config={"height": "100%", "width": "100%", "selection": "row", "multiselection": True},
            columns = copy.deepcopy(columns),
            data_url = "http://localhost:8070/appcode/dataset.json"
        )

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
