APP_TITLE = 'Portable editor'
APP_ENTRYPOINT = 'main'
import js
from pyodide.ffi import create_proxy

callbacks = []
editor = None


async def main():
    global editor
    root = js.document.createElement('section')
    root.innerHTML = '<h1>Portable editor</h1><button id="open-editor">Open editor</button><p id="saved-code"></p><dialog id="editor-dialog"><div id="code"></div><button id="save-code">Save code</button></dialog>'
    js.document.body.appendChild(root)
    dialog = js.document.getElementById('editor-dialog')
    editor = js.PortableEditor.createEditor(
        js.document.getElementById('code'),
        js.localStorage.getItem('portable-code') or 'print("hello")',
    )

    def open_dialog(event):
        dialog.showModal()
        editor.requestMeasure()
        editor.focus()

    def save_code(event):
        value = editor.state.doc.toString()
        js.localStorage.setItem('portable-code', value)
        js.document.getElementById('saved-code').textContent = value
        dialog.close()

    for target, callback in [('open-editor', open_dialog), ('save-code', save_code)]:
        proxy = create_proxy(callback)
        callbacks.append(proxy)
        js.document.getElementById(target).onclick = proxy
