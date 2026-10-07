import { EditorView, basicSetup } from "codemirror";

export function createEditor(parent, doc) {
    return new EditorView({ parent, doc, extensions: [basicSetup] });
}
