"""
form widget class(es) and methods
"""
from typing import Any, Callable, Dict, List, Optional, Union
import js
import json
from enum import Enum
from pyodide.ffi import create_proxy

"""
Button properties
Usage
{
    type: "button",
    name?: string,
    id?: string,
    
    text?: string,
    submit?: boolean, // false by default
    url?: string,
    
    css?: string,
    disabled?: boolean, // false by default
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    width?: string | number | "content", // "content" by default

    // button view
    circle?: boolean, // false by default
    color?: "danger" | "secondary" | "primary" | "success", // "primary" by default
    full?: boolean, // false by default
    icon?: string,
    loading?: boolean, // false by default
    size?: "small" | "medium", // "medium" by default
    view?: "flat" | "link", // "flat" by default
}


Description
type	(required) the type of a control, set it to "button"
name	(optional) the name of a control
id	(optional) the id of a control, auto-generated if not set
text	(optional) the text label of a button
submit	(optional) enables the button to send form data to a server, false by default
url	(optional) the URL the post request with form data will be sent to.
css	(optional) adds style classes to a control
disabled	(optional) defines whether a control is enabled (false) or disabled (true), false by default
height	(optional) the height of a control, "content" by default
hidden	(optional) defines whether a control is hidden, false by default
padding	(optional) sets padding between a cell and a border of a button control, "8px" by default
width	(optional) the width of a control, "content" by default
circle	(optional) makes the corners of a button round, false by default
color	(optional) defines the color scheme of a button: "danger" | "secondary" | "primary" | "success", "primary" by default
full	(optional) extends a button to the full width of a form, false by default
icon	(optional) an icon of the button
loading	(optional) adds a spinner into a button, false by default
size	(optional) defines the size of a button: "small" | "medium", "medium" by default
view	(optional) defines the look of a button: "flat" | "link", "flat" by default


Checkbox properties
Usage
{
    type: "checkbox",
    name?: string,
    id?: string,
    value?: string,
    checked?: boolean, // false by default
    text?: string,

    css?: string,
    disabled?: boolean, // false by default
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    required?: boolean, // false by default
    width?: string | number | "content", // "content" by default

    hiddenLabel?: boolean, // false by default
    label?: string,
    labelPosition?: "left" | "top", // "top" by default
    labelWidth?: string | number,

    helpMessage?: string,
    preMessage?: string,
    successMessage?: string,
    errorMessage?: string,
}

Description
type	(required) the type of a control, set it to "checkbox"
name	(optional) the name of a control
id	(optional) the id of a control, auto-generated if not set
value	(optional) the value of a checkbox
checked	(optional) defines the initial state of a checkbox, false (unchecked) by default
text	(optional) optional, the text value of a control. It's placed to the right of the control.
css	(optional) adds style classes to a control
disabled	(optional) defines whether a control is enabled (false) or disabled (true), false by default
height	(optional) the height of a control, "content" by default
hidden	(optional) defines whether a checkbox is hidden, false by default
padding	(optional) sets padding between a cell and a border of a Checkbox control, "8px" by default
required	(optional) defines whether a control is required, false by default
width	(optional) the width of a control, "content" by default
hiddenLabel	(optional) makes the label invisible, false by default
label	(optional) specifies a label for a control
labelPosition	(optional) defines the position of a label: "left" | "top", "top" by default
labelWidth	(optional) sets the width of the label of a control
helpMessage	(optional) adds a help message to a control
preMessage	(optional) a message that contains instructions for interacting with the control
successMessage	(optional) a message that appears in case of successful validation of the control value
errorMessage	(optional) a message that appears in case of error during validation of the control value


CheckboxGroup properties
Usage
{
    type: "checkboxGroup",
    name?: string,
    id?: string,
    options: {
        rows?: [
            {
                type: "checkbox",
                id?: string,
                value?: string,
                checked?: boolean, 
                css?: string,
                height?: string | number | "content",
                hidden?: boolean,
                padding?: string | number,
                text?: string,
                width?: string | number | "content",
            },
            // more checkboxes
        ],
        cols?: [
            {
                type: "checkbox",
                id?: string,
                value?: string,
                checked?: boolean,
                css?: string,
                height?: string | number | "content",
                hidden?: boolean,
                padding?: string | number,
                text?: string,
                width?: string | number | "content",
            },
            // more checkboxes
        ],
        css?: string,
        height?: string | number | "content", // "content" by default
        padding?: string | number, // "8px" by default
        width?: string | number | "content", // "content" by default
    },
    value?: {
        [id: string]: boolean | string,
        // more values
    },
    
    css?: string,
    disabled?: boolean, // false by default
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    required?: boolean, // false by default
    width?: string | number | "content", // "content" by default
    
    hiddenLabel?: boolean, // false by default
    label?: string,
    labelPosition?: "left"|"top", // "top" by default
    labelWidth?: string | number,

    helpMessage?: string,
    preMessage?: string,
    successMessage?: string,
    errorMessage?: string,
}

Description
CheckboxGroup properties
type	(required) the type of a control, set it to "checkboxGroup"
name	(optional) the name of a control
id	(optional) the id of a control, auto-generated if not set
options	(required) an object with options of a CheckboxGroup. The object can contain the following attributes:
rows - (optional) arranges checkboxes inside the CheckboxGroup control vertically
cols - (optional) arranges checkboxes inside the CheckboxGroup control horizontally
css - (optional) adds style classes to a CheckboxGroup
height - (optional) the height of a CheckboxGroup
padding - (optional) sets padding between a cell and a border of a CheckboxGroup
width - (optional) the width of a CheckboxGroup
value	(optional) an object with the initial value of a CheckboxGroup. The value contains a set of key:value pairs where key is the id of a checkbox and value defines the initial state of the checkbox. The option has a higher priority than the checked attribute of a checkbox.
css	(optional) adds style classes to a control
disabled	(optional) defines whether a control is enabled (false) or disabled (true)
height	(optional) the height of a control, "content" by default
hidden	(boolean) defines whether a CheckboxGroup is hidden, false by default
padding	(optional) sets padding between a cell and a border of a CheckboxGroup control, "8px" by default
required	(optional) defines whether a control is required, false by default
width	(optional) the width of a control, "content" by default
hiddenLabel	(optional) makes the label invisible, false by default
label	(optional) specifies a label for a control
labelPosition	(optional) defines the position of a label: "left" | "top", "top" by default
labelWidth	(optional) sets the width of the label of a control
helpMessage	(optional) adds a help message to a control
preMessage	(optional) a message that contains instructions for interacting with the control
successMessage	(optional) a message that appears in case of successful validation of the control value
errorMessage	(optional) a message that appears in case of error during validation of the control value
type	(required) the type of a control, set it to "checkbox"
id	(optional) the id of a control, auto-generated if not set
value	(optional) the value of a checkbox
checked	(optional) defines the initial state of a checkbox
css	(optional) adds style classes to a a checkbox
height	(optional) the height of a checkbox
hidden	(optional) defines whether a checkbox is hidden
padding	(optional) sets padding between a cell and a border of a checkbox
text	(optional) the text label of a checkbox
width	(optional) the width of a checkbox

Colorpicker properties
Usage
{
    type: "colorpicker",
    name?: string,
    id?: string,
    value?: string,
    
    css?: string,
    disabled?: boolean, // false by default
    editable?: boolean, // false by default
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    required?: boolean, // false by default
    validation?: (value: string) => boolean,
    width?: string | number | "content", // "content" by default
    
    customColors?: string[],
    grayShades?: boolean, // true by default
    icon?: string,
    mode?: "palette" | "picker", // "palette" by default
    palette?: string[][],
    paletteOnly?: boolean, // false by default
    pickerOnly?: boolean, // false by default
    placeholder?: string,

    hiddenLabel?: boolean, // false by default
    label?: string,
    labelPosition?: "left" | "top", // "top" by default
    labelWidth?: string | number,

    helpMessage?: string,
    preMessage?: string,
    successMessage?: string,
    errorMessage?: string,
}

Description
type	(required) the type of a control, set it to "colorpicker"
name	(optional) the name of a control
id	(optional) the id of a control, auto-generated if not set
value	(optional) the value of a colorpicker
css	(optional) adds style classes to a control
disabled	(optional) defines whether a control is enabled (false) or disabled (true), false by default
editable	(optional) allows a user to enter the value of the control manually, false by default
height	(optional) the height of a control, "content" by default
hidden	(optional) defines whether a control is hidden, false by default
padding	(optional) sets padding between a cell and a border of the ColorPicker control, "8px" by default
required	(optional) defines whether a control is required, false by default
validation	(optional) the validation function, takes as a parameter the value to validate and returns true/false to indicate the result of validation
width	(optional) the width of a control, "content" by default
customColors	(optional) shows a section with custom colors in the bottom part of the ColorPicker
grayShades	(optional) defines whether the section with gray shades is displayed in the palette, true by default
icon	(optional) the name of an icon from the used icon font
mode	(optional) the mode of a control: "palette" (default), "picker"
palette	(optional) contains arrays of colors you want to be shown in a colorpicker
paletteOnly	(optional) defines whether ColorPicker is shown only the palette mode, false by default
pickerOnly	(optional) defines whether ColorPicker is shown only the picker mode, false by default
placeholder	(optional) a tip for the input
hiddenLabel	(optional) makes the label invisible, false by default
label	(optional) specifies a label for a control
labelPosition	(optional) defines the position of a label: "left" | "top", "top" by default
labelWidth	(optional) sets the width of the label of a control
helpMessage	(optional) adds a help message to a control
preMessage	(optional) a message that contains instructions for interacting with the control
successMessage	(optional) a message that appears in case of successful validation of the control value
errorMessage	(optional) a message that appears in case of error during validation of the control value


Combo properties
Usage
{
    type: "combo",
    name?: string,
    id?: string,
    data?: object[],
    value?: string | number | array,
    
    css?: string,
    disabled?: boolean, // false by default
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    required?: boolean, // false by default
    validation?: (id: (string | number) | (string | number)[], text: string | string[]) => boolean,
    width?: string | number | "content", // "content" by default
    
    filter?: (item: any, input: string) => boolean,
    itemHeight?: number | string, // 32 by default
    itemsCount?: boolean | ((count: number) => string),
    listHeight?: number | string, // 224 by default
    multiselection?: boolean, // false by default
    newOptions?: boolean, // false by default
    placeholder?: string,
    readOnly?: boolean, // false by default
    selectAllButton?: boolean, // false by default
    template?: (item: any) => string,
    virtual?: boolean, // false by default
    
    hiddenLabel?: boolean, // false by default
    label?: string,
    labelPosition?: "left" | "top", // "top" by default
    labelWidth?: string | number,

    helpMessage?: string,
    preMessage?: string,
    successMessage?: string,
    errorMessage?: string,
}


Description
type	(required) the type of a control, set it to "combo"
name	(optional) the name of a control
id	(optional) the id of a control, auto-generated if not set
data	(optional) an array of Combo options. Each option is an object with a set of key:value pairs - attributes of options and their values.
The id attribute is returned and goes to form data. This attribute should always be fulfilled to avoid unexpected behavior
The value attribute is displayed in the input field
value	(optional) specifies the id(s) of Combo options from data collection which values should appear in the input:
if multiselection:true is set for a combo, the property can be set as an array of string/number values
(for example, value: ["id_1","id_2","id_3"], or value: [1, 2, 3])
if multiselection:false is set or the multiselection config is not defined, the property can be set as a string/number, or an array
(for example, value:"id_1", value: 1, or value: ["id_1"])
css	(optional) adds style classes to a control
disabled	(optional) defines whether a control is enabled (false) or disabled (true), false by default
height	(optional) the height of a control, "content" by default
hidden	(optional) defines whether a control is hidden, false by default
padding	(optional) sets padding between a cell and a border of the Combo control, "8px" by default
required	(optional) defines whether the field with Combo is required (for a form), false by default
validation	(optional) a callback function which allows to validate Combo options.
id - (required) the ID(s) of the option(s) to validate
text - (required) the value(s) of the option(s)
and returns true/false to indicate the result of validation
width	(optional) the width of a control, "content" by default
filter	(optional) sets a custom function for filtering Combo options. Check the details.
itemHeight	(optional) sets the height of a cell in the list of options, 32 by default
itemsCount	(optional) shows the total number of selected options
listHeight	(optional) sets the height of the list of options, 224 by default
multiselection	(optional) enables selection of multiple options in Combo, false by default
newOptions	(optional) allows end users to add new values into the list of combobox options. To add a new value, the user needs to type it into the input field and either press "Enter" or click on the appeared Create "newValue" option in the drop-down list, false by default
placeholder	(optional) sets a placeholder in the input of Combo
readOnly	(optional) makes Combo readonly (it is only possible to select options from the list, without entering words in the input), false by default
selectAllButton	(optional) defines whether the Select All button should be shown, false by default
template	(optional) sets a template of displaying options in the popup list
virtual	(optional) enables dynamic loading of data on scrolling the list of options, false by default
hiddenLabel	(optional) makes the label invisible, false by default
label	(optional) specifies a label for a control
labelPosition	(optional) defines the position of a label: "left" | "top", "top" by default
labelWidth	(optional) sets the width of the label of a control
helpMessage	(optional) adds a help message to a control
preMessage	(optional) a message that contains instructions for interacting with the control
successMessage	(optional) a message that appears in case of successful validation of the control value
errorMessage	(optional) a message that appears in case of error during validation of the control value


Container properties
Usage
{
    type: "container",
    name?: string,
    id?: string,
    html?: HTMLElement | string,
    
    css?: string,
    disabled?: boolean, // false by default
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    width?: string | number | "content", // "content" by default

    label?: string,
    labelWidth?: string | number,
    labelPosition?: "left" | "top", // "top" by default
    hiddenLabel?: boolean, // false by default
    helpMessage?: string
}

Description
type - (required) the type of a control, set it to "container"
name - (optional) the name of a control
id - (optional) the id of a control, auto-generated if not set
html - (optional) the HTML content of a control
css - (optional) adds style classes to a control string
disabled - (optional) defines whether a control is enabled (false) or disabled (true), false by default
height - (optional) the height of a control, "content" by default
hidden - (optional) defines whether a control is hidden, false by default
padding - (optional) sets padding between a cell and a border of a control, "8px" by default
width - (optional) the width of a control, "content" by default
label - (optional) specifies a label for a control
labelWidth - (optional) sets the width of the label of a control
labelPosition- (optional) defines the position of a label: "left" | "top", "top" by default
hiddenLabel - (optional) makes the label invisible, false by default
helpMessage- (optional) adds a help message to a control


Container properties
Usage
{
    type: "container",
    name?: string,
    id?: string,
    html?: HTMLElement | string,
    
    css?: string,
    disabled?: boolean, // false by default
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    width?: string | number | "content", // "content" by default

    label?: string,
    labelWidth?: string | number,
    labelPosition?: "left" | "top", // "top" by default
    hiddenLabel?: boolean, // false by default
    helpMessage?: string
}

Description
type - (required) the type of a control, set it to "container"
name - (optional) the name of a control
id - (optional) the id of a control, auto-generated if not set
html - (optional) the HTML content of a control
css - (optional) adds style classes to a control string
disabled - (optional) defines whether a control is enabled (false) or disabled (true), false by default
height - (optional) the height of a control, "content" by default
hidden - (optional) defines whether a control is hidden, false by default
padding - (optional) sets padding between a cell and a border of a control, "8px" by default
width - (optional) the width of a control, "content" by default
label - (optional) specifies a label for a control
labelWidth - (optional) sets the width of the label of a control
labelPosition- (optional) defines the position of a label: "left" | "top", "top" by default
hiddenLabel - (optional) makes the label invisible, false by default
helpMessage- (optional) adds a help message to a control


Fieldset properties
Usage
{
    type: "fieldset",
    name?: string,
    id?: string,

    hidden?: boolean, // false by default
    disabled?: boolean, // false by default

    css?: string,
    width?: string | number | "content", // "content" by default
    height?: string | number | "content", // "content" by default
    padding?: string | number, // "8px" by default

    label?: string,
    labelAlignment?: "left" | "right" | "center", // "left" by default
    rows?: IBlock,
    cols?: IBlock,
    align?: "start" | "center" | "end" | "between" | "around" | "evenly" // "start" by default
}


Description
type - (required) the type of a control, set it to "fieldset"
name - (optional) the name of a control
id - (optional) the id of a control, auto-generated if not set
hidden - (optional) defines whether a control is hidden, false by default
disabled - (optional) defines whether a control is enabled (false) or disabled (true), false by default
css - (optional) the name of a CSS class(es) applied to the control group
width - (optional) sets the width of the control group, "content" by default
height - (optional) sets the height of the control group, "content" by default
padding - (optional) sets the padding for the content inside the control group, "8px" by default
label - (optional) specifies a label for a control
labelAlignment - (optional) defines the position of the label: "left" | "right" | "center", "left" by default
rows - (optional) arranges controls inside the control group vertically
cols - (optional) arranges controls inside the control group horizontally
align - (optional) sets the alignment of controls inside the control group, "start" by default



nput properties
Usage
{
    type: "input",
    name?: string,
    id?: string,
    value?: string | number,
    
    css?: string,
    disabled?: boolean, // false by default
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    required?: boolean, // false by default
    validation?: string | (input: string | number) => boolean,
    width?: string | number | "content", // "content" by default

    autocomplete?: boolean, // false by default
    icon?: string,
    inputType?: "text" | "password" | "number", // "text" by default
    max?: number | string,
    maxlength?: number | string,
    min?: number | string,
    minlength?: number | string,
    placeholder?: string,
    readOnly?: boolean, // false by default

    hiddenLabel?: boolean, // false by default
    label?: string,
    labelPosition?: "left" | "top", // "top" by default
    labelWidth?: string | number,

    helpMessage?: string,
    preMessage?: string,
    successMessage?: string,
    errorMessage?: string,
}

Description
type	(required) the type of a control, set it to "input"
name	(optional) the name of a control
id	(optional) the id of a control, auto-generated if not set
value	(optional) the initial value of the input
css	(optional) adds style classes to a control
disabled	(optional) defines whether a control is enabled (false) or disabled (true), false by default
height	(optional) the height of a control, "content" by default
hidden	(optional) makes an input hidden, false by default
padding	(optional) sets padding between a cell and a border of the Input control, "8px" by default
required	(optional) defines whether a control is required, false by default
validation	(optional) the rule of input validation. Can be set in two ways:
as a predefined string value:
- "email" - validEmail
- "integer" - validInteger
- "numeric" - validNumeric
- "alphanumeric" - validAplhaNumeric
- "IPv4" - validIPv4
Can be used with inputType: "text", "password".
as a function that defines a custom validation rule. It takes as a parameter the value typed in the input and returns true, if the entered value is valid.
Can be used with inputType: "number" only.
width	(optional) the width of a control, "content" by default
autocomplete	(optional) enables/disables the autocomplete functionality of the input, false by default
icon	(optional) the name of an icon from the used icon font
inputType	(optional) sets the type of an input: "text", "password", "number".
Using the "number" type for the input sets the type of the value attribute to "number".
Use the "password" value to specify a field for entering a password. "text" by default
max	(optional) the maximal value allowed in the input.
The attribute works only with the input type: "number".
maxlength	(optional) the maximum number of characters allowed in the input.
The attribute works with the following input types: "text", "password".
min	(optional) the minimal value allowed in the input.
The attribute works only with the input type: "number".
minlength	(optional) the minimum number of characters allowed in the input.
The attribute works with the following input types: "text", "password".
placeholder	(optional) a tip for the input
readOnly	(optional) defines whether an input is readonly, false by default
hiddenLabel	(optional) makes the label invisible, false by default
label	(optional) specifies a label for a control
labelPosition	(optional) defines the position of a label: "left" | "top", "top" by default
labelWidth	(optional) sets the width of the label of a control
helpMessage	(optional) adds a help message to a control
preMessage	(optional) a message that contains instructions for interacting with the control
successMessage	(optional) a message that appears in case of successful validation of the control value
errorMessage	(optional) a message that appears in case of error during validation of the control value


RadioGroup and RadioButton properties
Usage
{
    type: "radiogroup",
    name?: string,
    id?: string,
    options: {
        rows?: [
            {
                type: "radiobutton",
                id?: string,
                value: string,
                checked?: boolean, // false by default 
                css?: string,
                height?: string | number | "content", // "content" by default
                disabled?: boolean,  // false by default
                hidden?: boolean,  // false by default
                padding?: string | number, // "8px" by default
                text?: string,
                width?: string | number | "content", // "content" by default
            },
            // more radio buttons
        ],
        cols?: [
            {
                type: "radiobutton",
                id?: string,
                value: string,
                checked?: boolean,
                css?: string,
                height?: string | number | "content",
                disabled?: boolean,
                hidden?: boolean,
                padding?: string | number,
                text?: string,
                width?: string | number | "content",
            },
            // more radio buttons
        ],
        css?: string,
        height?: string | number | "content",
        padding?: string | number,
        width?: string | number | "content",
    },
    value?: string,

    css?: string,
    disabled?: boolean, // false by default
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    required?: boolean, // false by default
    width?: string | number | "content", // "content" by default

    hiddenLabel?: boolean, // false by default
    label?: string,
    labelPosition?: "left" | "top", // "top" by default
    labelWidth?: string | number,
    
    helpMessage?: string,
    preMessage?: string,
    successMessage?: string,
    errorMessage?: string,
}

Description
RadioGroup properties
type	(required) the type of a control, set it to "radioGroup"
name	(optional) the name of a control
id	(optional) the id of a control, auto-generated if not set
options	(required) an object with options of a RadioGroup. The object can contain the following attributes:
rows - (optional) arranges radio buttons inside the RadioGroup control vertically
cols - (optional) arranges radio buttons inside the RadioGroup control horizontally
css - (optional) adds style classes to a RadioGroup
height - (optional) the height of a RadioGroup
padding - (optional) sets padding between a cell and a border of a RadioGroup
width - (optional) the width of a RadioGroup
value	(optional) the initial value of a RadioGroup. The option has a higher priority than the checked attribute of a RadioButton
css	(optional) adds style classes to a control
disabled	(optional) defines whether a control is enabled (false) or disabled (true), false by default
height	(optional) the height of a control, "content" by default
hidden	(optional) defines whether a RadioGroup is hidden, false by default
padding	(optional) sets padding between a cell and a border of a RadioGroup control, "8px" by default
required	(optional) defines whether a control is required, false by default
width	(optional) the width of a control, "content" by default
hiddenLabel	(optional) makes the label invisible, false by default
label	(optional) specifies a label for a control
labelPosition	(optional) defines the position of a label: "left" | "top", "top" by default
labelWidth	(optional) sets the width of the label of a control
helpMessage	(optional) adds a help message to a control
preMessage	(optional) a message that contains instructions for interacting with the control, applied for all radio buttons in a group
successMessage	(optional) a message that appears in case of successful validation of the control value, applied for all radio buttons in a group
errorMessage	(optional) a message that appears in case of error during validation of the control value, applied for all radio buttons in a group


RadioButton properties
type	(optional) the type of a control, set it to "radioButton"
id	(optional) the id of a control, auto-generated if not set
value	(required) the value of a radioButton
checked	(optional) defines the initial state of a radio button, only one radio button can be checked at a time, false by default
disabled	(optional) defines whether a control is enabled (false) or disabled (true), false by default
css	(optional) adds style classes to a control
height	(optional) the height of a control, "content" by default
hidden	(optional) defines whether a radio button is hidden, false by default
padding	(optional) sets padding between a cell and a border of a radio button, "8px" by default
text	(optional) the text label of a radio button
width	(optional) the width of a control, "content" by default


Select properties
Usage
{
    type: "select",
    name?: string,
    id?: string,
    options: [
        {
            value: string | number,
            content: string,
            disabled?: boolean,
        },
        // more options
    ],
    value?: string | number,

    css?: string,
    disabled?: boolean, // false by default
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    required?: boolean, // false by default
    validation?: (input: string | number | boolean) => boolean,
    width?: string | number | "content", // "content" by default
    
    icon?: string,

    hiddenLabel?: boolean, // false by default
    label?: string,
    labelPosition?: "left" | "top", // "top" by default
    labelWidth?: string | number,

    helpMessage?: string,
    preMessage?: string,
    successMessage?: string,
    errorMessage?: string,
}

Description
type	(required) the type of a control, set it to "select"
name	(optional) the name of a control
id	(optional) the id of a control, auto-generated if not set
options	(required) an array of Select options, each option is an object with a set of key:value pairs - attributes of options and their values:
value - (required) sets the value for the select option
content - (required) the content displayed in the select option
disabled - (optional) defines whether the option is enabled (false) or disabled (true)
value	(optional) the initial value of the select control
css	(optional) adds style classes to a control
disabled	(optional) defines whether a control is enabled (false) or disabled (true), false by default
height	(optional) the height of a control, "content" by default
hidden	(optional) defines whether a control is hidden, false by default
padding	(optional) sets padding between a cell and a border of the Select control, "8px" by default
required	(optional) defines whether a control is required, false by default
validation	(optional) the validation function, takes as a parameter the value to validate and returns true/false to indicate the result of validation
width	(optional) the width of a control, "content" by default
icon	(optional) the name of an icon from the used icon font
hiddenLabel	(optional) makes the label invisible, false by default
label	(optional) specifies a label for a control
labelPosition	(optional) defines the position of a label: "left" | "top", "top" by default
labelWidth	(optional) sets the width of the label of a control
helpMessage	(optional) adds a help message to a control
preMessage	(optional) a message that contains instructions for interacting with the control
successMessage	(optional) a message that appears in case of successful validation of the control value
errorMessage	(optional) a message that appears in case of error during validation of the control value


Simple Vault properties
Usage
{
    type: "simplevault",
    name?: string,
    id?: string,
    target?: string,
    value?: [
        {
            id?: string | number,
            file?: File,
            status?: "queue" | "inprogress" | "uploaded" | "failed",
            progress?: number,
            request?: XMLHttpRequest,
            path?: string,
            name?: string,
            [key: string]?: any
        },
        // more file objects
    ],

    css?: string,
    height?: string | number | "content", // "content" by default
    width?: string | number | "content", // "content" by default
    padding?: string | number, // "8px" by default
    hidden?: boolean, // false by default
    disabled?: boolean, // false by default
        
    fieldName?: string, // "file" by default
    params?: {
        [key: string]: any,
    },
    headerParams?: {
        [key: string]: any,
    },
    singleRequest?: boolean, // false by default
    updateFromResponse?: boolean, // true by default
    autosend?: boolean, // false by default
    accept?: string, // all file types by default

    validation?: (value: object[]) => boolean;
    required?: boolean, // false by default

    hiddenLabel?: boolean, // false by default
    label?: string,
    labelPosition?: "left" | "top", // "top" by default
    labelWidth?: string | number,

    helpMessage?: string,
    preMessage?: string,
    successMessage?: string,
    errorMessage?: string,
}

Description
type	(required) the type of a control, set it to "simpleVault"
name	(optional) the name of a control
id	(optional) the id of a control, auto-generated if not set
target	(optional) sets an URL to the server-side script that will process file upload
value	(optional) sets the default list of loaded files. Each file object can contain the following properties:
id - (optional) the id of the file
file - (optional) the File object, in case of loading to the server the property is obligatory
status - (optional) the status of the file ("queue", "inprogress", "uploaded", or "failed")
progress - (optional) the progress of the file upload
request - (optional) an XMLHttpRequest object sent to the server when an upload begins
name - (optional) the name of the file including the extension (for adding files from the server)
path - (optional) the path to the file on the computer starting from the name of the folder (in case a folder with files is added)
[key:string] - (optional) any key:value pair received as a server response (if the updateFromResponse property is enabled)
css	(optional) adds style classes to a control
height	(optional) the height of a control, "content" by default
width	(optional) the width of a control, "content" by default
padding	(optional) sets padding between a cell and a border of the SimpleVault control, "8px" by default
hidden	(optional) defines whether a control is hidden, false by default
disabled	(optional) defines whether a control is enabled (false) or disabled (true), false by default
fieldName	(optional) sets the name of the file field in the form data that is sent to the server, "file" by default
params	(optional) adds extra parameters for sending an XMLHttpRequest
headerParams	(optional) provides additional parameters for Request Headers
singleRequest	(optional) defines whether files are sent in one request, false by default
updateFromResponse	(optional) updates file attributes with the data from the server response, true by default
autosend	(optional) enables/disables automatic sending of an added file (files won't be sent if they fail validation), false by default
accept	(optional) allows specifying the type/extension that will be displayed in the dialog window during the file selection. Check details, all file types by default
validation	(optional) the validation function, takes as a parameter the value to validate and returns true/false to indicate the result of validation
required	(optional) defines whether a control is required, false by default
hiddenLabel	(optional) makes the label invisible, false by default
label	(optional) specifies a label for a control
labelPosition	(optional) defines the position of a label: "left" | "top", "top" by default
labelWidth	(optional) sets the width of the label of a control
helpMessage	(optional) adds a help message to a control
preMessage	(optional) a message that contains instructions for interacting with the control
successMessage	(optional) a message that appears in case of successful validation of the control value
errorMessage	(optional) a message that appears in case of error during validation of the control value


Slider properties
Usage
{
    type: "slider",
    name?: string,
    id?: string,
    value?: number | number[],
    
    css?: string,
    disabled?: boolean, // false by default
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    width?: string | number | "content", // "content" by default

    inverse?: boolean, // false by default
    majorTick?: number,
    max?: number, // 100 by default
    min?: number, // 0 by default
    mode?: "vertical" | "horizontal", // "horizontal" by default
    range?: boolean, // false by default
    step?: number, // 1 by default
    tick?: number,
    tickTemplate?: (position: number) => string,
    tooltip?: boolean, // true by default
    
    hiddenLabel?: boolean, // false by default
    label?: string,
    labelPosition?: "vertical" | "horizontal", // "top" by default
    labelWidth?: string | number,

    helpMessage?: string,
}

Description
type	(required) the type of a control, set it to "slider"
name	(optional) the name of a control
id	(optional) the id of a control, auto-generated if not set
value	(optional) the value (or values, if the range option is set to true) the thumb will be set at on initialization of the slider
css	(optional) adds style classes to a control
disabled	(optional) defines whether a control is enabled (false) or disabled (true), false by default
height	(optional) the height of a control, "content" by default
hidden	(optional) defines whether a control is hidden, false by default
padding	(optional) sets padding between a cell and a border of the Slider control, "8px" by default
width	(optional) the width of a control, "content" by default
inverse	(optional) enables/disables the inverse slider mode, false by default
majorTick	(optional) sets interval of rendering numeric values on the slider scale
max	(optional) the maximal value of slider, 100 by default
min	(optional) the minimal value of slider, 0 by default
mode	(optional) the direction of the Slider scale, "horizontal" by default
range	(optional) enables/disables the possibility to select a range of values on the slider, false by default
step	(optional) the step the slider thumb will be moved with, 1 by default
tick	(optional) sets the interval of steps for rendering the slider scale
tickTemplate	(optional) sets a template for rendering values on the scale
tooltip	(optional) enables prompt messages with ticks values on hovering over the slider thumb, true by default
hiddenLabel	(optional) makes the label invisible, false by default
label	(optional) specifies a label for a control
labelPosition	(optional) defines the position of a label: "left" | "top", "top" by default
labelWidth	(optional) sets the width of the label of a control
helpMessage	(optional) adds a help message to a control


Spacer properties
Usage
{
    type: "spacer",
    name?: string,
    id?: string,

    css?: string,
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    width?: string | number | "content", // "content" by default
}

Description
type	(required) the type of a control, set it to "spacer"
name	(optional) the name of a control
id	(optional) the id of a control, auto-generated if not set
css	(optional) adds style classes to a control
height	(optional) the height of a control, "content" by default
hidden	(optional) defines whether a control is hidden, false by default
padding	(optional) sets padding between a cell and a border of the Spacer control, "8px" by default
width	(optional) the width of a control, "content" by default


Text properties
Usage
{
    type: "text",
    name?: string,
    id?: string,
    value?: number | string,

    css?: string,
    disabled?: boolean, // false by default
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    width?: string | number | "content", // "content" by default

    inputType?: "text" | "password" | "number", // "text" by default
    
    hiddenLabel?: boolean, // false by default
    label?: string,
    labelPosition?: "left" | "top", // "top" by default
    labelWidth?: string | number,

    helpMessage?: string,
    preMessage?: string,
    successMessage?: string,
    errorMessage?: string,
}

Description
type	(required) the type of a control, set it to "text"
name	(optional) the name of a control
id	(optional) the id of a control, auto-generated if not set
value	(optional) the value of a text control
css	(optional) adds style classes to a control
disabled	(optional) defines whether a control is enabled (false) or disabled (true), false by default
height	(optional) the height of a control, "content" by default
hidden	(optional) defines whether a control is hidden, false by default
padding	(optional) sets padding between a cell and a border of the Text control, "8px" by default
width	(optional) the width of a control, "content" by default
inputType	(optional) sets the type of an input: "text", "password", "number".
Using the "number" type for the input sets the type of the value attribute to "number".
Use the "password" value to specify a field for entering a password. "text" by default
hiddenLabel	(optional) makes the label invisible, false by default
label	(optional) specifies a label for a control
labelPosition	(optional) defines the position of a label: "left" | "top", "top" by default
labelWidth	(optional) sets the width of the label of a control
helpMessage	(optional) adds a help message to a control
preMessage	(optional) a message that contains instructions for interacting with the control
successMessage	(optional) a message that appears in case of successful validation of the control value
errorMessage	(optional) a message that appears in case of error during validation of the control value


Textarea properties
Usage
{
    type: "textarea",
    name?: string,
    id?: string,
    value?: string,
    
    css?: string,
    disabled?: boolean, // false by default
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    required?: boolean, // false by default
    validation?: string | (input: string | number) => boolean,
    width?: string | number | "content", // "content" by default

    maxlength?: number | string,
    minlength?: number | string,
    placeholder?: string, 
    readOnly?: boolean, // false by default
    resizable?: boolean, // false by default

    hiddenLabel?: boolean, // false by default
    label?: string,
    labelPosition?: "left" | "top", // "top" by default
    labelWidth?: string | number,

    helpMessage?: string,
    preMessage?: string,
    successMessage?: string,
    errorMessage?: string,
}

Description
type	(required) the type of a control, set it to "textarea"
name	(optional) the name of a control
id	(optional) the id of a control, auto-generated if not set
value	(optional) the initial value of the textarea
css	(optional) adds style classes to a control
disabled	(optional) defines whether a control is enabled (false) or disabled (true), false by default
height	(optional) the height of a control, "content" by default
hidden	(optional) defines whether a control is hidden, false by default
padding	(optional) sets padding between a cell and a border of the Textarea control, "8px" by default
required	(optional) defines whether a control is required, false by default
validation	(optional) the rule of input validation. Can be set in two ways:
as a predefined string value:
- "email" - validEmail
- "integer" - validInteger
- "numeric" - validNumeric
- "alphanumeric" - validAplhaNumeric
- "IPv4" - validIPv4
as a function that defines a custom validation rule. It takes as a parameter the value typed in the input and returns true, if the entered value is valid.
width	(optional) the width of a control, "content" by default
maxlength	(optional) the maximum number of characters allowed in the textarea
minlength	(optional) the minimum number of characters allowed in the textarea
placeholder	(optional) a tip for the textarea
readOnly	(optional) defines whether a textarea is readonly, false by default
resizable	(optional) adds a resizer icon into a textarea, if set to true, false by default
hiddenLabel	(optional) makes the label invisible, false by default
label	(optional) specifies a label for a control
labelPosition	(optional) defines the position of a label: "left" | "top", "top" by default
labelWidth	(optional) sets the width of the label of a control
helpMessage	(optional) adds a help message to a control
preMessage	(optional) a message that contains instructions for interacting with the control
successMessage	(optional) a message that appears in case of successful validation of the control value
errorMessage	(optional) a message that appears in case of error during validation of the control value


Timepicker properties
Usage
{
    type: "timepicker",
    name?: string,
    id?: string,
    value?: Date | number | string | array | object,

    css?: string,
    disabled?: boolean, // false by default
    editable?: boolean, // false by default
    height?: string | number | "content", // "content" by default
    hidden?: boolean, // false by default
    padding?: string | number, // "8px" by default
    required?: boolean, // false by default
    validation?: (input: string) => boolean,
    width?: string | number | "content", // "content" by default
    
    controls?: boolean, // false by default
    icon?: string,
    placeholder?: string,
    timeFormat?: 12 | 24, // 24 by default
    valueFormat?: "string" | "timeObject", // "string" by default

    hiddenLabel?: boolean, // false by default 
    label?: string,
    labelPosition?: "left" | "top", // "top" by default
    labelWidth?: string | number,

    helpMessage?: string,
    preMessage?: string,
    successMessage?: string,
    errorMessage?: string,
}

Description
type - (required) the type of a control, set it to "timepicker"
name - (optional) the name of a control
id - (optional) the id of a control, auto-generated if not set
value - (optional) the initial value of the TimePicker control:
The date set as a number is the number of milliseconds since January 1, 1970, 00:00:00 UTC returned by the getTime() method of the Date object.
The value of a timepicker set as an array should have the following elements:
the hour value
the minutes value
the "AM/PM" identifier for 12-hour format as a string
The value set as an object:
for the 24-hour format contains key:value pairs for hours, minutes and their values:
{hour: 0, minute: 39}
for the 12-hour format contains key:value pairs for hours, minutes, am/pm identifiers and their values:
{hour: 6, minute: 0, AM: true}
css - (optional) adds style classes to a control
disabled - (optional) defines whether a control is enabled (false) or disabled (true), false by default
editable - (optional) allows a user to enter the value of the control manually, false by default
height - (optional) the height of a control, "content" by default
hidden - (optional) defines whether a control is hidden, false by default
padding - (optional) sets padding between a cell and a border of the TimePicker control, "8px" by default
required - (optional) defines whether a control is required, false by default
validation - (optional) the validation function, takes as a parameter the value to validate and returns true/false to indicate the result of validation
width - (optional) the width of a control, "content" by default
controls - (optional) defines whether a timepicker is equipped with the Close and Save buttons, false by default
icon - (optional) the name of an icon from the used icon font
placeholder - (optional) a tip for the input
timeFormat - (optional) defines what clock format is activated: the 12-hour or 24-hour one. Set the property to 12 or 24 (default) value, correspondingly, 24 by default
valueFormat - (optional) defines the format of the value to be applied when working with the events of the timepicker control: "string" (default), "timeObject"
hiddenLabel - (optional) makes the label invisible, false by default
label - (optional) specifies a label for a control
labelPosition - (optional) defines the position of a label: "left" | "top" (default)
labelWidth - (optional) sets the width of the label of a control
helpMessage - (optional) adds a help message to a control
preMessage - (optional) a message that contains instructions for interacting with the control
successMessage - (optional) a message that appears in case of successful validation of the control value
errorMessage - (optional) a message that appears in case of error during validation of the control value



Toggle properties
Usage
{
    type: "toggle",
    name?: string,
    id?: string,

    hidden?: boolean, // false by default
    disabled?: boolean, // false by default
    selected?: boolean, // false by default

    full?: boolean, // false by default
    text?: string,
    icon?: string,
    offText?: string,
    offIcon?: string,
    value?: string | number,

    css?: string,
    width?: string | number | "content", // "content" by default
    height?: string | number | "content", // "content" by default
    padding?: string | number // "8px" by default
}

Description
type - (required) the type of a control, set it to "toggle"
name - (optional) the name of a control
id - (optional) the id of a control, auto-generated if not set
hidden- (optional) defines whether a toggle is hidden, false by default
disabled - (optional) defines whether a control is enabled (false) or disabled (true), false by default
selected - (optional) defines the initial state of the toggle as selected (pressed), false by default
full - (optional) defines whether the toggle will be extended to the width specified by the width property, false by default
text - (optional) sets a text inside the toggle. When initialized together with the offText property, the specified text will be rendered in the selected (pressed) state
offText - (optional) sets the text that will be rendered in the unselected (unpressed) state of the toggle
icon - (optional) sets the class of an icon displayed inside the toggle. When initialized together with the offIcon property, the specified classes of icons will be rendered in the selected (pressed) state of the toggle
offIcon - (optional) sets the class of an icon that will be rendered in the unselected (unpressed) state of the toggle
value - (optional) specifies the value in the selected (pressed) state. If not defined, the selected property with the boolean value is used instead
css - (optional) adds style classes to a control
height - (optional) the height of a control, "content" by default
width - (optional) the width of a control, "content" by default
padding - (optional) sets padding between a cell and a border of a Toggle control, "8px" by default


ToggleGroup properties
Usage
{
    type: "toggleGroup",
    name?: string,
    id?: string,

    hidden?: boolean, // false by default
    disabled?: boolean, // false by default

    full?: boolean, // false by default
    gap?: number, // 0 by default
    multiselect?: boolean, // false by default

    options: [
        {
             id?: string,
             hidden?: boolean,
             disabled?: boolean,
             selected?: boolean,
             full?: boolean,
             text?: string,
             icon?: string,
             offText?: string,
             offIcon?: string,
             value?: string | number
        },
    ],
    value?: {
        [id: string]: boolean
    };

    css?: string,
    width?: string | number | "content", // "content" by default
    height?: string | number | "content", // "content" by default
    padding?: string | number // "8px" by default
}

Description
ToggleGroup properties
type - (required) the type of a control, set it to "toggleGroup"
name - (optional) the name of a control
id - (optional) the id of a control, auto-generated if not set
hidden- (optional) defines whether a ToggleGroup is hidden, false by default
disabled - (optional) defines whether a control is enabled (false) or disabled (true), false by default
full - (optional) defines whether the ToggleGroup will be extended to the width specified by the width property, false by default
gap - (optional) sets an offset between the elements (buttons) of an option, 0 by default
multiselection - (optional) defines the behavior that allows a multiple choice, false by default
options - (required) an array of ToggleGroup elements. An object of an element can contain the following attributes:
id - (optional) the id of a control, auto-generated if not set
hidden- (optional) defines whether a toggle button is hidden, false by default
disabled - (optional) defines whether a control is enabled (false) or disabled (true), false by default
selected - (optional) defines the initial state of the toggle as selected (pressed), false by default
full - (optional) defines whether the toggle will be extended to the width specified by the width property, false by default
text - (optional) sets a text inside the toggle. When initialized together with the offText property, the specified text will be rendered in the selected (pressed) state
offText - (optional) sets the text that will be rendered in the unselected (unpressed) state of the toggle
icon - (optional) sets the class of an icon displayed inside the toggle. When initialized together with the offIcon property, the specified classes of icons will be rendered in the selected (pressed) state of the toggle
offIcon - (optional) sets the class of an icon that will be rendered in the unselected (unpressed) state of the toggle
value - (optional) specifies the value in the selected (pressed) state. If not defined, the selected property with the boolean value is used instead
value - (optional) defines the state of elements on initialization. As a value it takes an object presented as a key:value pair, where the key is the id of an element and the value is the initial state of the element. It takes priority over the state of an element set in its configuration object
[id: string]: boolean - sets the state of an element
css - (optional) adds style classes to a control
height - (optional) the height of a control, "content" by default
width - (optional) the width of a control, "content" by default
padding - (optional) sets padding between a cell and a border of a ToggleGroup control, "8px" by default
Properties of a Toggle of ToggleGroup
id - (optional) the id of a control, auto-generated if not set
hidden- (optional) defines whether a toggle button is hidden, false by default
disabled - (optional) defines whether a control is enabled (false) or disabled (true), false by default
selected - (optional) defines the initial state of the toggle as selected (pressed), false by default
full - (optional) defines whether the toggle will be extended to the width specified by the width property, false by default
text - (optional) sets a text inside the toggle. When initialized together with the offText property, the specified text will be rendered in the selected (pressed) state
offText - (optional) sets the text that will be rendered in the unselected (unpressed) state of the toggle
icon - (optional) sets the class of an icon displayed inside the toggle. When initialized together with the offIcon property, the specified classes of icons will be rendered in the selected (pressed) state of the toggle
offIcon - (optional) sets the class of an icon that will be rendered in the unselected (unpressed) state of the toggle
value - (optional) specifies the value in the selected (pressed) state. If not defined, the selected property with the boolean value is used instead
"""

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
                 value: Optional[str] = None, full: Optional[bool] = False,
                 size: Optional[str] = "medium", css: Optional[str] = None,
                 width: Optional[Union[str, int]] = "content", height: Optional[Union[str, int]] = "content",
                 padding: Optional[Union[str, int]] = "8px", label: Optional[str] = None,
                 labelWidth: Optional[Union[str, int]] = None, labelPosition: Optional[str] = "top",
                 hiddenLabel: Optional[bool] = False, helpMessage: Optional[str] = None,
                 required: Optional[bool] = False, preMessage: Optional[str] = None,
                 successMessage: Optional[str] = None, errorMessage: Optional[str] = None,
                 validation: Optional[Callable[[object], bool]] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.text = text
        self.icon = icon
        self.value = value
        self.full = full
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

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "text": self.text,
            "icon": self.icon,
            "value": self.value,
            "full": self.full,
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
            "validation": self.validation
        }


class Checkbox:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 label: Optional[str] = None, value: Optional[bool] = False,
                 size: Optional[str] = "medium", css: Optional[str] = None,
                 width: Optional[Union[str, int]] = "content", height: Optional[Union[str, int]] = "content",
                 padding: Optional[Union[str, int]] = "8px", labelWidth: Optional[Union[str, int]] = None,
                 labelPosition: Optional[str] = "top", hiddenLabel: Optional[bool] = False,
                 helpMessage: Optional[str] = None, required: Optional[bool] = False,
                 preMessage: Optional[str] = None, successMessage: Optional[str] = None,
                 errorMessage: Optional[str] = None, validation: Optional[Callable[[object], bool]] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.label = label
        self.value = value
        self.size = size
        self.css = css
        self.width = width
        self.height = height
        self.padding = padding
        self.labelWidth = labelWidth
        self.labelPosition = labelPosition
        self.hiddenLabel = hiddenLabel
        self.helpMessage = helpMessage
        self.required = required
        self.preMessage = preMessage
        self.successMessage = successMessage
        self.errorMessage = errorMessage
        self.validation = validation

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "label": self.label,
            "value": self.value,
            "size": self.size,
            "css": self.css,
            "width": self.width,
            "height": self.height,
            "padding": self.padding,
            "labelWidth": self.labelWidth,
            "labelPosition": self.labelPosition,
            "hiddenLabel": self.hiddenLabel,
            "helpMessage": self.helpMessage,
            "required": self.required,
            "preMessage": self.preMessage,
            "successMessage": self.successMessage,
            "errorMessage": self.errorMessage,
            "validation": self.validation
        }


class CheckboxGroup:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 options: Optional[List[Dict[str, Any]]] = None,
                 value: Optional[List[str]] = None,
                 size: Optional[str] = "medium", css: Optional[str] = None,
                 width: Optional[Union[str, int]] = "content", height: Optional[Union[str, int]] = "content",
                 padding: Optional[Union[str, int]] = "8px", label: Optional[str] = None,
                 labelWidth: Optional[Union[str, int]] = None, labelPosition: Optional[str] = "top",
                 hiddenLabel: Optional[bool] = False, helpMessage: Optional[str] = None,
                 required: Optional[bool] = False, preMessage: Optional[str] = None,
                 successMessage: Optional[str] = None, errorMessage: Optional[str] = None,
                 validation: Optional[Callable[[object], bool]] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.options = options
        self.value = value
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

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "options": self.options,
            "value": self.value,
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
            "validation": self.validation
        }

class Colorpicker:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 value: Optional[str] = None, width: Optional[Union[str, int]] = "content",
                 height: Optional[Union[str, int]] = "content", padding: Optional[Union[str, int]] = "8px",
                 label: Optional[str] = None, labelWidth: Optional[Union[str, int]] = None,
                 labelPosition: Optional[str] = "top", hiddenLabel: Optional[bool] = False,
                 helpMessage: Optional[str] = None, required: Optional[bool] = False,
                 preMessage: Optional[str] = None, successMessage: Optional[str] = None,
                 errorMessage: Optional[str] = None, validation: Optional[Callable[[object], bool]] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.value = value
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

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "value": self.value,
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
            "validation": self.validation
        }


class Combo:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 options: Optional[List[Dict[str, Any]]] = None,
                 value: Optional[str] = None, width: Optional[Union[str, int]] = "content",
                 height: Optional[Union[str, int]] = "content", padding: Optional[Union[str, int]] = "8px",
                 label: Optional[str] = None, labelWidth: Optional[Union[str, int]] = None,
                 labelPosition: Optional[str] = "top", hiddenLabel: Optional[bool] = False,
                 helpMessage: Optional[str] = None, required: Optional[bool] = False,
                 preMessage: Optional[str] = None, successMessage: Optional[str] = None,
                 errorMessage: Optional[str] = None, validation: Optional[Callable[[object], bool]] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.options = options
        self.value = value
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

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "options": self.options,
            "value": self.value,
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
            "validation": self.validation
        }


class Container:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 children: Optional[List[Dict[str, Any]]] = None,
                 width: Optional[Union[str, int]] = "content",
                 height: Optional[Union[str, int]] = "content",
                 padding: Optional[Union[str, int]] = "8px",
                 label: Optional[str] = None,
                 labelWidth: Optional[Union[str, int]] = None,
                 labelPosition: Optional[str] = "top",
                 hiddenLabel: Optional[bool] = False,
                 helpMessage: Optional[str] = None,
                 required: Optional[bool] = False,
                 preMessage: Optional[str] = None,
                 successMessage: Optional[str] = None,
                 errorMessage: Optional[str] = None,
                 validation: Optional[Callable[[object], bool]] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.children = children
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

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "children": self.children,
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
            "validation": self.validation
        }


class Datepicker:
    def __init__(self, name: Optional[str] = None, id: Optional[str] = None,
                 hidden: Optional[bool] = False, disabled: Optional[bool] = False,
                 value: Optional[str] = None, width: Optional[Union[str, int]] = "content",
                 height: Optional[Union[str, int]] = "content", padding: Optional[Union[str, int]] = "8px",
                 label: Optional[str] = None, labelWidth: Optional[Union[str, int]] = None,
                 labelPosition: Optional[str] = "top", hiddenLabel: Optional[bool] = False,
                 helpMessage: Optional[str] = None, required: Optional[bool] = False,
                 preMessage: Optional[str] = None, successMessage: Optional[str] = None,
                 errorMessage: Optional[str] = None, validation: Optional[Callable[[object], bool]] = None) -> None:
        self.name = name
        self.id = id
        self.hidden = hidden
        self.disabled = disabled
        self.value = value
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

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "hidden": self.hidden,
            "disabled": self.disabled,
            "value": self.value,
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
            "validation": self.validation
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

    def send(self, url: str, method: Optional[str] = None, as_form_data: Optional[bool] = False) -> Dict:
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

    def on_click(self, name: str, e: object) -> Any:
        """
        fires after a click on a button in a form
        """
        pass

    def on_focus(self, name: str, value: Any, id: Optional[str] = None) -> None:
        """
        fires when a control of Form has received focus
        """
        pass

    def on_keydown(self, event: object, name: str, id: Optional[str] = None) -> None:
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

