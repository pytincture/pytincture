"""
form widget class(es) and methods
"""

from typing import Any, Callable, Dict, List, Optional, Union
import js
import json
from enum import Enum
from pyodide.ffi import create_proxy


class Avatar:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None, target: Optional[str] = None,
                 value: Optional[Dict[str, Any]] = None, hidden: Optional[bool] = False,
                 disabled: Optional[bool] = False, readOnly: Optional[bool] = False,
                 removeIcon: Optional[bool] = True, circle: Optional[bool] = False,
                 icon: Optional[str] = None, placeholder: Optional[str] = None,
                 preview: Optional[str] = None, alt: Optional[str] = None,
                 size: Optional[str] = "medium", css: Optional[str] = None,
                 width: Optional[Union[str, int]] = "content", height: Optional[Union[str, int]] = "content",
                 padding: Optional[Union[str, int]] = "8px", label: Optional[str] = None,
                 labelWidth: Optional[Union[str, int]] = None, labelPosition: Optional[str] = "top",
                 hiddenLabel: Optional[bool] = False, helpMessage: Optional[str] = None,
                 required: Optional[bool] = False, preMessage: Optional[str] = None,
                 successMessage: Optional[str] = None, errorMessage: Optional[str] = None,
                 validation: Optional[Callable[[object], bool]] = None, accept: Optional[str] = "image/*",
                 fieldName: Optional[str] = "file", autosend: Optional[bool] = False,
                 params: Optional[Dict[str, Any]] = None, headerParams: Optional[Dict[str, Any]] = None,
                 updateFromResponse: Optional[bool] = True) -> None:
        self.type = "avatar"
        self.name = name
        self.id = id
        self.target = target
        self.value = value
        self.hidden = hidden
        self.disabled = disabled
        self.readOnly = readOnly
        self.removeIcon = removeIcon
        self.circle = circle
        self.icon = icon
        self.placeholder = placeholder
        self.preview = preview
        self.alt = alt
        self.size = size
        self.css = css
        self.width = width
        self.height = height
        self.padding = padding
        self.label = label
        self.labelWidth = labelWidth
        self.labelPosition = labelPosition
        self.hiddenLabel = hiddenLabel
        self.helpMessage = helpMessage
        self.required = required
        self.preMessage = preMessage
        self.successMessage = successMessage
        self.errorMessage = errorMessage
        self.validation = validation
        self.accept = accept
        self.fieldName = fieldName
        self.autosend = autosend
        self.params = params
        self.headerParams = headerParams
        self.updateFromResponse = updateFromResponse

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "id": self.id,
            "target": self.target,
            "value": self.value,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "readOnly": self.readOnly,
            "removeIcon": self.removeIcon,
            "circle": self.circle,
            "icon": self.icon,
            "placeholder": self.placeholder,
            "preview": self.preview,
            "alt": self.alt,
            "size": self.size,
            "css": self.css,
            "width": self.width,
            "height": self.height,
            "padding": self.padding,
            "label": self.label,
            "labelWidth": self.labelWidth,
            "labelPosition": self.labelPosition,
            "hiddenLabel": self.hiddenLabel,
            "helpMessage": self.helpMessage,
            "required": self.required,
            "preMessage": self.preMessage,
            "successMessage": self.successMessage,
            "errorMessage": self.errorMessage,
            "validation": self.validation,
            "accept": self.accept,
            "fieldName": self.fieldName,
            "autosend": self.autosend,
            "params": self.params,
            "headerParams": self.headerParams,
            "updateFromResponse": self.updateFromResponse
        }


class Button:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 text: Optional[str] = None, icon: Optional[str] = None,
                 value: Optional[str] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.text = text
        self.icon = icon
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "text": self.text,
            "icon": self.icon,
            "value": self.value
        }


class Checkbox:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 label: Optional[str] = None, value: Optional[bool] = False) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.label = label
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "label": self.label,
            "value": self.value
        }


class CheckboxGroup:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 options: Optional[List[Dict[str, Any]]] = None,
                 value: Optional[List[str]] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.options = options
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "options": self.options,
            "value": self.value
        }

class Colorpicker:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 value: Optional[str] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "value": self.value
        }


class Combo:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 options: Optional[List[Dict[str, Any]]] = None,
                 value: Optional[str] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.options = options
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "options": self.options,
            "value": self.value
        }


class Container:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 children: Optional[List[Dict[str, Any]]] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.children = children

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "children": self.children
        }
    

class Datepicker:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 value: Optional[str] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "value": self.value
        }


class Fieldset:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 children: Optional[List[Dict[str, Any]]] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.children = children

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "children": self.children
        }


class Input:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 value: Optional[str] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "value": self.value
        }


class Radiogroup:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 options: Optional[List[Dict[str, Any]]] = None,
                 value: Optional[str] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.options = options
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "options": self.options,
            "value": self.value
        }


class Select:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 options: Optional[List[Dict[str, Any]]] = None,
                 value: Optional[str] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.options = options
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "options": self.options,
            "value": self.value
        }


class SimpleVault:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 value: Optional[str] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "value": self.value
        }


class Slider:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 value: Optional[int] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "value": self.value
        }


class Spacer:
    def __init__(self, height: Optional[Union[str, int]] = None) -> None:
        self.height = height

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "height": self.height
        }


class Text:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 value: Optional[str] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "value": self.value
        }


class Textarea:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 value: Optional[str] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "value": self.value
        }


class Timepicker:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 value: Optional[str] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "value": self.value
        }


class Toggle:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 value: Optional[bool] = False) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "value": self.value
        }


class ToggleGroup:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 options: Optional[List[Dict[str, Any]]] = None,
                 value: Optional[str] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.options = options
        self.value = value

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "options": self.options,
            "value": self.value
        }
    
class FormTypes:
    Avatar = Avatar
    Button = Button
    Checkbox = Checkbox
    CheckboxGroup = CheckboxGroup
    Colorpicker = Colorpicker
    Combo = Combo
    Container = Container
    Datepicker = Datepicker
    Fieldset = Fieldset
    Input = Input
    Radiogroup = Radiogroup
    Select = Select
    SimpleVault = SimpleVault
    Slider = Slider
    Spacer = Spacer
    Text = Text
    Textarea = Textarea
    Timepicker = Timepicker
    Toggle = Toggle
    ToggleGroup = ToggleGroup



class Form():
    def __init__(self, widget_config: Dict[str, Any]) -> None:
        self.toolbar = js.dhx.Window
        self.widget_config = widget_config
        self.window = self.toolbar.new(None, js.JSON.parse(json.dumps(widget_config)))

    def blur(self, name: str) -> None:
        """
        removes focus from a control of Form
        """
        self.window.blur(name)

    def clear(self, method: Optional[str] = None) -> None:
        """
        clears a form
        """
        self.window.clear(method)

    def destructor(self) -> None:
        """
        removes a form instance and releases occupied resources
        """
        self.window.destructor()

    def disable(self) -> None:
        """
        disables a form on a page
        """
        self.window.disable()

    def enable(self) -> None:
        """
        enables a disabled form
        """
        self.window.enable()

    def for_each(self, callback: Callable[[object, int, List[object]], Any]) -> None:
        """
        iterates over all controls of a form
        """
        self.window.forEach(callback)

    def get_item(self, name: str) -> Any:
        """
        gives access to the object of Form control
        """
        return self.window.getItem(name)

    def get_properties(self, name: Optional[str] = None) -> Union[object, Dict[str, object]]:
        """
        returns objects with available configuration attributes of Form controls
        """
        return self.window.getProperties(name)

    def get_value(self, as_form_data: Optional[bool] = False) -> object:
        """
        gets current values/states of controls
        """
        return self.window.getValue(as_form_data)

    def hide(self) -> None:
        """
        hides a form
        """
        self.window.hide()

    def is_disabled(self, name: Optional[str] = None) -> bool:
        """
        checks whether a form is disabled
        """
        return self.window.isDisabled(name)

    def is_visible(self, name: Optional[str] = None) -> bool:
        """
        checks whether a form is visible
        """
        return self.window.isVisible(name)

    def paint(self) -> None:
        """
        repaints Form on a page
        """
        self.window.paint()

    def send(self, url: str, method: Optional[str] = None, as_form_data: Optional[bool] = False) -> Union[Promise[Any], None]:
        """
        sends a form to the server
        """
        return self.window.send(url, method, as_form_data)

    def set_focus(self, name: str) -> None:
        """
        sets focus to a Form control by its id
        """
        self.window.setFocus(name)

    def set_properties(self, arg: Union[str, Dict[str, object]], properties: Optional[object] = None) -> None:
        """
        allows changing available configuration attributes of Form controls dynamically
        """
        self.window.setProperties(arg, properties)

    def set_value(self, obj: object) -> None:
        """
        sets values/states for controls
        """
        self.window.setValue(obj)

    def show(self) -> None:
        """
        shows a form on the page
        """
        self.window.show()

    def validate(self, silent: Optional[bool] = False) -> bool:
        """
        validates form fields
        """
        return self.window.validate(silent)

    def after_change_properties(self, name: str, properties: object) -> None:
        """
        fires after configuration attributes of a Form control have been changed dynamically
        """
        pass

    def after_hide(self, name: str, value: Optional[Any] = None, id: Optional[str] = None) -> None:
        """
        fires after a Form control or its element is hidden
        """
        pass

    def after_send(self) -> None:
        """
        fires after a form is sent to the server
        """
        pass

    def after_show(self, name: str, value: Optional[Any] = None, id: Optional[str] = None) -> None:
        """
        fires after a Form control or its element is shown
        """
        pass

    def after_validate(self, name: str, value: Any, is_valid: bool) -> None:
        """
        fires after validation of form fields is finished
        """
        pass

    def before_change(self, name: str, value: Any) -> Union[bool, None]:
        """
        fires before changing the value of a control
        """
        pass

    def before_change_properties(self, name: str, properties: Any) -> Union[bool, None]:
        """
        fires before configuration attributes of a Form control are changed dynamically
        """
        pass

    def before_hide(self, name: Union[str, int], value: Optional[Any] = None, id: Optional[str] = None) -> Union[bool, None]:
        """
        fires before a Form control or its element is hidden
        """
        pass

    def before_send(self) -> Union[bool, None]:
        """
        fires before a form is sent to the server
        """
        pass

    def before_show(self, name: str, value: Optional[Any] = None, id: Optional[str] = None) -> Union[bool, None]:
        """
        fires before a Form control or its element is shown
        """
        pass

    def before_validate(self, name: str, value: Any) -> Union[bool, None]:
        """
        fires before validation of form fields has started
        """
        pass

    def on_blur(self, name: str, value: Any, id: Optional[str] = None) -> None:
        """
        fires when a control of Form has lost focus
        """
        pass

    def on_change(self, name: str, new_value: Any) -> None:
        """
        fires on changing the value of a control
        """
        pass

    def on_click(self, name: str, e: Event) -> Any:
        """
        fires after a click on a button in a form
        """
        pass

    def on_focus(self, name: str, value: Any, id: Optional[str] = None) -> None:
        """
        fires when a control of Form has received focus
        """
        pass

    def on_keydown(self, event: KeyboardEvent, name: str, id: Optional[str] = None) -> None:
        """
        fires when any key is pressed
        """
        pass

    @property
    def align(self) -> Optional[str]:
        """
        Optional. Sets the alignment of controls inside the control group
        """
        return self.window.align

    @align.setter
    def align(self, value: Optional[str]) -> None:
        self.window.align = value

    @property
    def cols(self) -> Optional[List[object]]:
        """
        Optional. Arranges controls inside the control group horizontally
        """
        return self.window.cols

    @cols.setter
    def cols(self, value: Optional[List[object]]) -> None:
        self.window.cols = value

    @property
    def css(self) -> Optional[str]:
        """
        Optional. The name of a CSS class(es) applied to the control group
        """
        return self.window.css

    @css.setter
    def css(self, value: Optional[str]) -> None:
        self.window.css = value

    @property
    def disabled(self) -> Optional[bool]:
        """
        Optional. Makes a form disabled
        """
        return self.window.disabled

    @disabled.setter
    def disabled(self, value: Optional[bool]) -> None:
        self.window.disabled = value

    @property
    def height(self) -> Optional[Union[str, int]]:
        """
        Optional. Sets the height of the control group
        """
        return self.window.height

    @height.setter
    def height(self, value: Optional[Union[str, int]]) -> None:
        self.window.height = value

    @property
    def hidden(self) -> Optional[bool]:
        """
        Optional. Defines whether a form is hidden
        """
        return self.window.hidden

    @hidden.setter
    def hidden(self, value: Optional[bool]) -> None:
        self.window.hidden = value

    @property
    def padding(self) -> Optional[Union[str, int]]:
        """
        Optional. Sets padding for content inside the control group
        """
        return self.window.padding

    @padding.setter
    def padding(self, value: Optional[Union[str, int]]) -> None:
        self.window.padding = value

    @property
    def rows(self) -> Optional[List[object]]:
        """
        Optional. Arranges controls inside the control group vertically
        """
        return self.window.rows

    @rows.setter
    def rows(self, value: Optional[List[object]]) -> None:
        self.window.rows = value

    @property
    def title(self) -> Optional[str]:
        """
        Optional. Specifies the title of the control group
        """
        return self.window.title

    @title.setter
    def title(self, value: Optional[str]) -> None:
        self.window.title = value

    @property
    def width(self) -> Optional[Union[str, int]]:
        """
        Optional. Sets the width of the control group
        """
        return self.window.width

    @width.setter
    def width(self, value: Optional[Union[str, int]]) -> None:
        self.window.width = value

