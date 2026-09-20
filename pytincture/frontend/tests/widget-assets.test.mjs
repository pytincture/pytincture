import assert from 'node:assert/strict';
import test from 'node:test';
import {createWidgetAssetBridge} from '../widget-assets.js';

test('old widget eval and style loaders adopt verified assets without changing global APIs', async () => {
    const evaluated = [], inserted = [], loaded = [];
    const head = {appendChild(node) { assert.equal(this, head); inserted.push(node); return node; }};
    const document = {head, createElement(tag) { assert.equal(this, document); return {tagName:tag, textContent:'', dispatchEvent:e=>loaded.push(e.type)}; }};
    const root = {document, eval:code=>evaluated.push(code), queueMicrotask, Event};
    root.window = root;
    const bridge = createWidgetAssetBridge(root, [
        {type:'script',text:'widget = 1;',url:'https://app.test/vendor/widget.js'},
        {type:'style',text:'.widget { color: blue; }',url:'https://app.test/vendor/widget.css'},
    ]);
    bridge.eval('widget = 1;');
    bridge.window.eval(' widget = 1;\n');
    bridge.eval('unrelated = 2;');
    root.eval('widget = 1;');
    const style = bridge.document.createElement('style');
    style.textContent = '.widget { color: blue; }';
    bridge.document.head.appendChild(style);
    const custom = bridge.document.createElement('style');
    custom.textContent = '.app { color: red; }';
    bridge.document.head.appendChild(custom);
    await new Promise(resolve=>queueMicrotask(resolve));
    assert.deepEqual(evaluated,['unrelated = 2;','widget = 1;']);
    assert.deepEqual(inserted,[custom]);
    assert.deepEqual(loaded,['load']);
});

test('bridge keeps constructors, static properties and nonduplicate DOM calls working', () => {
    class Widget { static version = 3; constructor(value) {this.value=value;} }
    const root = {Widget, document:{}, value:1, getValue(){return this.value;}};
    const bridge = createWidgetAssetBridge(root, []);
    assert.equal(new bridge.Widget(42).value,42);
    assert.equal(bridge.Widget.version,3);
    assert.equal(bridge.getValue(),1);
    bridge.value=2;
    assert.equal(root.value,2);
});
