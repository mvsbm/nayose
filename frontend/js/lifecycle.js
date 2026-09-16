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
    items = new Set();
    timeouts = new Map();
    disposed = false;
    add(item) {
        if (this.disposed)
            item.dispose();
        else
            this.items.add(item);
        return item;
    }
    remove(item) {
        this.items.delete(item);
    }
    addEventListener(target, type, listener, options) {
        target.addEventListener(type, listener, options);
        this.add(disposable(() => target.removeEventListener(type, listener, options)));
    }
    addTimeout(callback, delayMs) {
        let owned;
        const timer = window.setTimeout(() => {
            if (owned)
                this.remove(owned);
            this.timeouts.delete(timer);
            callback();
        }, delayMs);
        owned = this.add(disposable(() => {
            window.clearTimeout(timer);
            this.timeouts.delete(timer);
        }));
        if (!this.disposed)
            this.timeouts.set(timer, owned);
        return timer;
    }
    clearTimeout(timer) {
        if (timer === undefined)
            return;
        window.clearTimeout(timer);
        const owned = this.timeouts.get(timer);
        if (owned) {
            this.remove(owned);
            this.timeouts.delete(timer);
        }
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
        const items = [...this.items].reverse();
        this.items.clear();
        this.timeouts.clear();
        for (const item of items)
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
