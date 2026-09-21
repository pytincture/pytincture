// A widget-scoped view of browser APIs. It never patches the application's DOM
// or global eval and only suppresses bytes/URLs verified and loaded by the host.
export function createWidgetAssetBridge(root, assets) {
    const scripts = new Set(assets.filter(item => item.type === 'script').map(item => item.text.trim()));
    const styles = new Set(assets.filter(item => item.type === 'style').map(item => item.text.trim()));
    const scriptUrls = new Set(assets.filter(item => item.type === 'script').map(item => item.url));
    const styleUrls = new Set(assets.filter(item => item.type === 'style').map(item => item.url));
    const proxies = new WeakMap();
    const unwrap = new WeakMap();
    const duplicate = node => {
        const tag = node?.tagName?.toLowerCase();
        if (tag === 'script') return node.src ? scriptUrls.has(node.src) : scripts.has((node.textContent || '').trim());
        if (tag === 'style') return styles.has((node.textContent || '').trim());
        return tag === 'link' && node.rel === 'stylesheet' && styleUrls.has(node.href);
    };
    const completed = node => {
        // Older asynchronous loaders still receive their successful load event.
        root.queueMicrotask(() => node.dispatchEvent(new root.Event('load')));
        return node;
    };
    const wrap = target => {
        if (!target || !['object', 'function'].includes(typeof target)) return target;
        if (proxies.has(target)) return proxies.get(target);
        const proxy = new Proxy(target, {
            get(object, key) {
                if (object === root && key === 'eval') return code => {
                    if (typeof code === 'string' && scripts.has(code.trim())) return undefined;
                    return root.eval(code);
                };
                const value = Reflect.get(object, key, object);
                if (value === root || value === root.document || value === root.document?.head || value === root.document?.body || value === root.document?.documentElement) return wrap(value);
                if (typeof value !== 'function') return value;
                return new Proxy(value, {
                    apply(fn, receiver, args) {
                        receiver = unwrap.get(receiver) || receiver;
                        if (['appendChild', 'insertBefore', 'replaceChild'].includes(key) && duplicate(args[0])) {
                            completed(args[0]);
                            return key === 'replaceChild' ? receiver.removeChild(args[1]) : args[0];
                        }
                        if (['append', 'prepend'].includes(key)) {
                            args = args.filter(node => {
                                if (!duplicate(node)) return true;
                                completed(node);
                                return false;
                            });
                        }
                        const result = Reflect.apply(fn, receiver, args.map(arg => unwrap.get(arg) || arg));
                        if (result === root.document?.head || result === root.document?.body) return wrap(result);
                        return result;
                    },
                });
            },
            set(object, key, value) { return Reflect.set(object, key, value, object); },
        });
        proxies.set(target, proxy);
        unwrap.set(proxy, target);
        return proxy;
    };
    return wrap(root);
}
