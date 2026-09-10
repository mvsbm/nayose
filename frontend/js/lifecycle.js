export function disposable(dispose) {
    let disposed = false;
    return {
        dispose: () => {
            if (disposed)
                return;
            disposed = true;
            dispose();
        },
    };
}
export class DisposableStore {
    items = [];
    disposed = false;
    add(item) {
        if (this.disposed)
            item.dispose();
        else
            this.items.push(item);
        return item;
    }
    addEventListener(target, type, listener, options) {
        target.addEventListener(type, listener, options);
        this.add(disposable(() => target.removeEventListener(type, listener, options)));
    }
    addTimeout(callback, delayMs) {
        const timer = window.setTimeout(callback, delayMs);
        this.add(disposable(() => window.clearTimeout(timer)));
        return timer;
    }
    addInterval(callback, delayMs) {
        const timer = window.setInterval(callback, delayMs);
        this.add(disposable(() => window.clearInterval(timer)));
        return timer;
    }
    get isDisposed() {
        return this.disposed;
    }
    dispose() {
        if (this.disposed)
            return;
        this.disposed = true;
        for (const item of this.items.splice(0).reverse())
            item.dispose();
    }
}
export class ViewScope extends DisposableStore {
    controller = new AbortController();
    signal = this.controller.signal;
    constructor(root) {
        super();
        if (root) {
            this.addEventListener(root, "unmount", () => this.dispose());
        }
    }
    dispose() {
        if (this.isDisposed)
            return;
        this.controller.abort();
        super.dispose();
    }
}
export function createViewScope(root) {
    return new ViewScope(root);
}
