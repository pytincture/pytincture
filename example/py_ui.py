
import json
import sys
from pytincture.frontend.widgetsets.layout import MainWindow
import copy

class py_ui(MainWindow):
    def __init__(self):
        super().__init__()
        self.set_theme("custom-theme-dark")
        self.load_ui()

    def load_ui(self):

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

        self.base_layout = self.add_layout(
            layout_config= {
                "css":"dhx_toolbar--text_color_white",
                "cols" :[
                    {"header":None,"width":"auto", "id": "left"},
                    {"header":None,"width":"100%", "id": "right"}
                ]
            }
        )

        self.sbmain = self.base_layout.add_sidebar(id="left",sidebar_config={}, data=sidebar_data)
        self.sbmain.collapse()

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

        self.sub_layout = self.base_layout.add_layout(
            id="right",
            layout_config = {
            "css":"dhx_layout-cell--bordered", "type":"line", 
                "rows" :[
                    {"header":None,"height":"auto", "id": "one"},
                    {"header":None,"height":"100%", "id": "two"}
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

        self.maintb = self.sub_layout.add_toolbar(
            id="one",
            toolbar_config = {"css":"dhx_toolbar--text_color_white"},
            data = toolbar_data
        )

        self.maintb.click(self.menu_clicked)

        self.sub_layout.add_grid(
            id="two",
            grid_config={"height": "100%", "width": "100%", "selection": "row", "multiselection": True},
            columns = copy.deepcopy(columns),
            data_url = "http://localhost:8070/appcode/dataset.json"
        )

    def menu_clicked(self, id, e):
        if id ==  "other":
            self.sbmain.toggle()

if __name__ == "__main__" and sys.platform != "emscripten":
    from pytincture import launch_service
    launch_service()
