from pytincture.dataclass import backend_for_frontend

@backend_for_frontend
class py_ui_data:
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

    grid_column_data = [
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

    form_data = [
        {
            "type": "fieldset",
            "name": "bookInfo",
            "label": "Book Information",
            "rows": [
                {
                    "type": "input",
                    "name": "title",
                    "required": True,
                    "label": "Title",
                    "placeholder": "Enter book title",
                },
                {
                    "type": "input",
                    "name": "authors",
                    "required": True,
                    "label": "Authors",
                    "placeholder": "Enter authors",
                },
                {
                    "type": "input",
                    "inputType": "number",
                    "name": "average_rating",
                    "label": "Average Rating",
                    "placeholder": "Enter average rating",
                },
                {
                    "type": "datepicker",
                    "name": "publication_date",
                    "label": "Publication Date",
                    "placeholder": "Select publication date",
                },
                {
                    "type": "input",
                    "name": "isbn13",
                    "label": "ISBN-13",
                    "placeholder": "Enter ISBN-13",
                },
                {
                    "type": "input",
                    "name": "language_code",
                    "label": "Language Code",
                    "placeholder": "Enter language code",
                },
                {
                    "type": "input",
                    "inputType": "number",
                    "name": "num_pages",
                    "label": "Number of Pages",
                    "placeholder": "Enter number of pages",
                },
                {
                    "type": "input",
                    "inputType": "number",
                    "name": "ratings_count",
                    "label": "Ratings Count",
                    "placeholder": "Enter ratings count",
                },
                {
                    "type": "input",
                    "inputType": "number",
                    "name": "text_reviews_count",
                    "label": "Text Reviews Count",
                    "placeholder": "Enter text reviews count",
                },
                {
                    "type": "input",
                    "name": "publisher",
                    "label": "Publisher",
                    "placeholder": "Enter publisher",
                },
            ]
        },
        {
            "align": "end",
            "cols": [
                {
                    "type": "button",
                    "name": "cancel",
                    "view": "link",
                    "text": "Cancel",
                },
                {
                    "type": "button",
                    "name": "submit",
                    "view": "flat",
                    "text": "Submit",
                    "submit": True,
                },
            ]
        }
    ]

    def dataset(self):
        return open("dataset.json", "r").read()
    
    def printme(self):
        return "hello how are you"

class showmeoff():
    def __init__(self):
        print("I am a class that is not decorated with @backend_for_frontend")
