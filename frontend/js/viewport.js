import { disposable } from "./lifecycle.js";
export class Viewport {
    element;
    listeners = new Set();
    disposed = false;
    get scrollTarget() {
        return this.element.shadowRoot?.querySelector('[part="scrollport"]') ?? this.element;
    }
    constructor(element) {
        if (!element.classList.contains("viewport"))
            throw new Error("Viewport requires the viewport primitive class");
        this.element = element;
    }
    get scrollTop() {
        return this.scrollTarget.scrollTop;
    }
    onScroll(listener, options) {
        if (this.disposed)
            return disposable(() => undefined);
        this.element.addEventListener("scroll", listener, options);
        const subscription = disposable(() => {
            this.element.removeEventListener("scroll", listener, options);
            this.listeners.delete(subscription);
        });
        this.listeners.add(subscription);
        return subscription;
    }
    dispose() {
        if (this.disposed)
            return;
        this.disposed = true;
        for (const listener of this.listeners)
            listener.dispose();
        this.listeners.clear();
    }
}
export function findViewport(root, selector) {
    const element = root.querySelector(selector);
    return element ? new Viewport(element) : undefined;
}
