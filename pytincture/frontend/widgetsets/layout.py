from __future__ import annotations

from hashlib import new
from uuid import uuid4
from typing import TypeVar
import js
import json

from .grid import dhxGrid
from .stackedwidget import StackedWidget
from .header import Header, HeaderItemTypes
from .cardpanel import CardPanel

TLayout = TypeVar("TLayout", bound="Layout")


import js
import json

"""
Layout API
Layout methods
Name	Description
destructor()	removes a Layout instance and releases occupied resources
forEach()	iterates over all specified layout cells
getCell()	returns the config object of a cell
progressHide()	hides the progress bar in the Layout container
progressShow()	shows the progress bar in the Layout container
removeCell()	removes a specified cell
 
Layout events
Name	Description
afterAdd	fires after adding a new cell
afterCollapse	fires after a cell is collapsed
afterExpand	fires after expanding a Layout cell
afterHide	fires after a cell is hidden
afterRemove	fires after removing a cell
afterResizeEnd	fires after resizing of a cell is ended
afterShow	fires after a cell is shown
beforeAdd	fires before adding a cell
beforeCollapse	fires before a cell is collapsed
beforeExpand	fires before expanding a Layout cell
beforeHide	fires before a cell is hidden
beforeRemove	fires before removing a cell
beforeResizeStart	fires before resizing of a cell has started
beforeShow	fires before a cell is shown
resize	fires on resizing a cell
 
Layout properties
Name	Description
cols	Optional. An array of columns objects
css	Optional. The name of a CSS class(es) applied to Layout
rows	Optional. An array of rows objects
type	Optional. Defines the type of borders between cells inside a layout
 
Cell API
Cell methods
Name	Description
attach()	attaches a DHTMLX component into a Layout cell
attachHTML()	adds an HTML content into a Layout cell
collapse()	collapses a specified cell
detach()	detaches an attached DHTMLX component or HTML content from a cell
expand()	expands a collapsed cell
getParent()	returns the parent of a cell
getWidget()	returns the widget attached to a layout cell
hide()	hides a specified cell
isVisible()	checks whether a cell is visible
paint()	repaints Layout on a page
progressHide()	hides the progress bar in a cell
progressShow()	shows the progress bar in a cell
show()	shows a hidden cell
toggle()	expands/collapses a Layout cell
 
Cell properties
Name	Description
align	Optional. Sets the alignment of content inside a cell
collapsable	Optional. Defines whether a cell can be collapsed
collapsed	Optional. Defines whether a cell is collapsed
css	Optional. The name of a CSS class(es) applied to a cell of Layout
gravity	Optional. Sets the "weight" of a cell in relation to other cells placed in the same row and within one parent
header	Optional. Adds a header with text for a cell
headerHeight	Optional. Sets the height of the header of a Layout cell
headerIcon	Optional. An icon used in the header of a cell
headerImage	Optional. An image used in the header of a cell
height	Optional. Sets the height of a cell
hidden	Optional. Defines whether a cell is hidden
html	Optional. Sets HTML content for a cell
id	Optional. The id of a cell
maxHeight	Optional. The maximal height to be set for a cell
maxWidth	Optional. The maximal width to be set for a cell
minHeight	Optional. The minimal height to be set for a cell
minWidth	Optional. The minimal width to be set for a cell
on	Optional. Adds handlers to DOM events of a cell
padding	Optional. Defines the distance between a cell and the border of layout
progressDefault	Optional. Defines whether the progress bar must be shown in a cell in the absence of the component/HTML content in the cell
resizable	Optional. Defines whether a cell can be resized
type	Optional. Defines the type of borders between cells inside rows and columns of a layout
width	Optional. Sets the width of a cell 
"""

class Layout:
    def __init__(self):
        """ similar to grid.py implementation implement layout api"""
        self.layout = js.dhx.Layout
        self.layout.new(js.JSON.parse(json.dumps(self.widget_config)))
        self.initialized = False

    """ Layout methods """
    def destructor(self):
        """removes a Layout instance and releases occupied resources"""
        self.layout.destructor()

    def forEach(self, callback, parentID, level):
        """iterates over all specified layout cells"""
        self.layout.forEach(lambda cell, index, array: callback(cell, index, array), parentID, level)

    def getCell(self, id):
        """returns the config object of a cell"""
        return self.layout.getCell(id)
    
    def progressHide(self):
        """hides the progress bar in the Layout container"""
        self.layout.progressHide()

    def progressShow(self):
        """shows the progress bar in the Layout container"""
        self.layout.progressShow()

    def removeCell(self, id):
        """removes a specified cell"""
        self.layout.removeCell(id)

    """ Layout events """
    def afterAdd(self, callback):
        """fires after adding a new cell"""
        self.layout.events.afterAdd = lambda id, config: callback(id, config)

    """ Layout properties """

    """ Cell API """

    """ Cell methods """

    """ Cell properties """

    """ Layout config """

    

     
