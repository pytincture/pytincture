"""
Grid implementation
"""
from uuid import uuid4

Table = object


class Table:
    """ Table widget implementation"""


    def __init__(self, table_id, parent=None, toolbar=False, footer=False, session_id=""):

    # toolbar and footer are copied over from grid to maintain comptability 
        self.name = table_id
        self.parent = parent
        self.toolbar = toolbar
        self.footer = footer
        self.session_id = session_id or self.parent.session_id

        self._base_table = Table(
            table_id=self.name,
            parent=self.parent,
            session_id=self.session_id,
        )

        self.on_click_callable = None
        self.on_search_callable = None
        self.on_record_click_callable = None
        self.on_prev_click_callable = None
        self.on_next_click_callable = None
        self.on_input_enter_key_callable = None


        self.initialized = False

        self.column_defaults = {
            "size": "100px",
            "sortable": True,
            "searchable": True,
            "resizable": True,
            "hideable": True
        }

        self.widget_config = {}

    @property
    def raw_widget(self):
        """Returns protected base table class"""
        return self._base_table

    @property
    def url(self):
        return self.raw_widget.raw_widget._url

    @url.setter
    def url(self, url):
        self.raw_widget.raw_widget._url = url

    @property
    def column_types(self):
        return self.raw_widget.column_types

    @column_types.setter
    def column_types(self, column_types):
        self.raw_widget.column_types = column_types

    @property
    def column_alignments(self):
        return self.raw_widget.column_alignments

    @column_alignments.setter
    def column_alignments(self, column_alignments):
        self.raw_widget.column_alignments = column_alignments

    @property
    def header_alignment(self):
        return self.raw_widget.header_alignment

    @header_alignment.setter
    def header_alignment(self, header_alignment):
        self.raw_widget.header_alignment = header_alignment

    @property
    def total_records(self):
        return self.raw_widget.total_records

    @total_records.setter
    def total_records(self, total_records):
        self.raw_widget.total_records = total_records

    def init_widget(self):
        """Initialize the table widget for the first time"""
        self._base_table.init_widget()
        self.widget_config = self._base_table.config
        self.initialized = True

    def set_column_default(self, field, value):
        self.column_defaults[field] = value

    def add_columns(self, columns: list):
        """Add columns (setting properties)"""
        column_list =  []
        for acolumn in columns:
            if isinstance(acolumn, str):
                col = self.column_defaults.copy()
                col["field"] = acolumn
                col["caption"] = acolumn.title()
            elif isinstance(acolumn, dict):
                col = self.column_defaults
                col.update(acolumn)
                if not "field" in acolumn:
                    raise ValueError("field must be provided for column")
                elif not "caption" in acolumn or not col.get("caption"):
                    col["caption"] = col["field"].title()
                elif not "sort" in acolumn or not col.get("sort"):
                    col["sort"] = False
            else:
                raise TypeError(f"Invalid column entry, expected str or dict, got {type(acolumn)}")
            column_list.append(col.copy())            
        self._base_table.add_columns(*column_list)
        if self.initialized:
            self._base_table.update_columns()

    def set_recs_per_page_options(self, option_list):
        if not all(isinstance(_, int) for _ in option_list):
            raise TypeError("A list of integer is required for `set_recs_per_page_options()`")
        self._base_table.recs_per_page_options = option_list

    def show_checkbox(self, show_bool):
        if not (isinstance(show_bool, bool)):
            raise TypeError("boolean is required for `show_checkbox()`")
        self._base_table.show_checkbox = show_bool

    def show_paginations(self, show_bool):
        if not (isinstance(show_bool, bool)):
            raise TypeError("boolean is required for `show_paginations()`")
        self._base_table.show_paginations = show_bool

    def show_searchbox(self, show_bool):
        if not (isinstance(show_bool, bool)):
            raise TypeError("boolean is required for `show_searchbox()`")
        self._base_table.show_searchbox = show_bool

    def set_search_placeholder_text(self, text):
        if not (isinstance(text, str)):
            raise TypeError("str is required for `set_search_placeholder_text()`")
        self._base_table.search_placeholder_text = text

    def set_paginations(self, key, value):
        if not (isinstance(value, int)):
            raise TypeError("int is required for `set_paginations()` value param")
        if not (isinstance(key, str)) and key in ["step", "value", "min"]:
            raise ValueError('str type ("step", "value", "min") is required for `set_paginations()` key param')
        self._base_table.paginations[key] = value


    def load_records(self, data):
        if not (isinstance(data, list)):
            raise TypeError("Data of list type is required for `load_records()`")

        if not "recid" in data[0]:
            recid = 0
            for adata in data:
                recid += 1
                adata["recid"] = recid
        self._base_table.records = data

    def load_records(self, data: list, on_render=False):
        """Loads records from specified url."""
        if not "recid" in data[0]:
            recid = 0
            for adata in data:
                recid += 1
                adata["recid"] = recid
        self.data = data
        if on_render:
           self.raw_widget.raw_widget.onRender(self.data_load)
        else:
           self.data_load()


    def bind(self, binding_class, config={}):
        config["binding_class"] = binding_class.__name__
        config["binding_module"] = binding_class.__module__
        self._base_table.load(config)

    def data_load(self, *args):
        self._base_table.load(self.data)

    def on_input_enter_key(self, event_callable, ret_widget_values=[], event_position="after", block_signal=False):
        # TODO implement ret_widget_values
        # TODO implement block_signal if possible or remove
        self.on_input_enter_key_callable = event_callable
        self._base_table.on_input_enter_key(self.on_input_enter_key_return, ret_widget_values, event_position, block_signal)

    def on_input_enter_key_return(self, item_id):
        self.on_input_enter_key_callable(item_id)

    def on_click(self, event_callable, ret_widget_values=[], event_position="after", block_signal=False):
        """Handle on click event for table widget"""
        # TODO implement ret_widget_values
        # TODO implement block_signal if possible or remove
        self.on_click_callable = event_callable
        self._base_table.on_click(self.on_click_return, ret_widget_values, event_position, block_signal)

    def on_click_return(self, item_id): 
        """Table on click event return"""
        self.on_click_callable(item_id)

    def on_record_click(self, event_callable, ret_widget_values=[], block_signal=False):
        """Handle on click event for record checkbox"""
        # TODO implement block_signal if possible or remove
        self.on_record_click_callable = event_callable
        self._base_table.on_record_click(
            self.on_record_click_return, ret_widget_values, block_signal
        )

    def on_record_click_return(self, item_id):
        """Handle on click event for record checkbox return"""
        self.on_record_click_callable(item_id)

    def on_next_click(self, event_callable, ret_widget_values=[], block_signal=False):
        """Handle on click event for next page"""
        # TODO implement block_signal if possible or remove
        self.on_next_click_callable = event_callable
        self._base_table.on_next_click(
            self.on_next_click_return, ret_widget_values, block_signal
        )

    def on_next_click_return(self, current_page):
        """Handle on click event for next page return"""
        self.on_next_click_callable(current_page)

    def on_prev_click(self, event_callable, ret_widget_values=[], block_signal=False):
        """Handle on click event for next page"""
        # TODO implement block_signal if possible or remove
        self.on_prev_click_callable = event_callable
        self._base_table.on_prev_click(
            self.on_prev_click_return, ret_widget_values, block_signal
        )

    def on_prev_click_return(self, current_page):
        """Handle on click event for next page return"""
        self.on_prev_click_callable(current_page)

