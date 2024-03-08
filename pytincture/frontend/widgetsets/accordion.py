from typing import Any, Callable, Dict, List, Optional, Union
import js
import json
from enum import Enum
from pyodide.ffi import create_proxy

class PTAccordion:
    """
    Accordion widget
    """

    def __init__(self, widget_config: Dict[str, Any]) -> None:
        self.widget_config = widget_config
        self.toolbar = js.PTAccordion.new(None, js.JSON.parse(json.dumps(widget_config)))
        # Initialize your accordion here using widget_config

    def collapse(self, id=None) -> None:
        self.accordion.collapse(id)

    def expand(self, id=None) -> None:
        self.accordion.expand(id)