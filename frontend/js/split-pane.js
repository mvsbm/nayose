import { ResizableRegion } from "./resizable-region.js";
function readWidth(storage, key, fallback) {
    try {
        const parsed = JSON.parse(storage.read(key) ?? "null");
        const version = parsed?.version;
        const width = Number(parsed?.width ?? parsed?.previewWidth);
        return (version === 1 || version === undefined) && Number.isFinite(width) ? width : fallback;
    }
    catch {
        return fallback;
    }
}
export class SplitPane {
    static mount(root, storage, options) {
        const splitSelector = options.splitSelector ?? `[data-panel-split="${options.id}"]`;
        const split = root.querySelector(splitSelector);
        const handle = split?.querySelector(options.handleSelector ?? "[data-panel-resizer]");
        if (!split || !handle)
            return undefined;
        return new SplitPane(split, handle, storage, options);
    }
    static mountElement(split, storage, options) {
        const handle = split.querySelector(options.handleSelector ?? "[data-panel-resizer]");
        return handle ? new SplitPane(split, handle, storage, options) : undefined;
    }
    split;
    handle;
    storage;
    options;
    storageKey;
    media;
    resizable;
    resizeTarget;
    maximumWidthValue;
    disposed = false;
    constructor(split, handle, storage, options) {
        if (!options.id || !options.cssVariable)
            throw new Error("SplitPane requires an id and CSS variable");
        if (options.minWidth > options.maxWidth)
            throw new Error("SplitPane minimum width exceeds maximum width");
        this.split = split;
        this.handle = handle;
        this.storage = storage;
        this.options = options;
        this.storageKey = `nayose.layout.${options.id}`;
        this.resizeTarget = split.ownerDocument.defaultView;
        this.media = options.mobileQuery && this.resizeTarget?.matchMedia ? this.resizeTarget.matchMedia(options.mobileQuery) : null;
        this.maximumWidthValue = this.calculateMaximumWidth();
        this.resizable = new ResizableRegion({
            handle,
            initialWidth: readWidth(storage, this.storageKey, options.defaultWidth),
            minWidth: options.minWidth,
            maxWidth: () => this.maximumWidthValue,
            defaultWidth: options.defaultWidth,
            arrowGrowth: options.pointerSide === "left" ? "forward" : "backward",
            isDisabled: () => this.media?.matches ?? false,
            pointerWidth: (startWidth, startClientX, clientX) => {
                this.refreshMaximumWidth();
                return options.pointerSide === "left"
                    ? startWidth + clientX - startClientX
                    : startWidth + startClientX - clientX;
            },
            onWidthChange: width => this.persist(width),
        });
        this.render(this.resizable.currentWidth);
        this.resizeTarget?.addEventListener("resize", this.constrainToAvailableWidth);
    }
    get currentWidth() {
        return this.resizable.currentWidth;
    }
    dispose() {
        if (this.disposed)
            return;
        this.disposed = true;
        this.resizeTarget?.removeEventListener("resize", this.constrainToAvailableWidth);
        this.resizable.dispose();
    }
    constrainToAvailableWidth = () => {
        this.refreshMaximumWidth();
        if (this.resizable.currentWidth > this.maximumWidthValue)
            this.resizable.setWidth(this.maximumWidthValue);
        else
            this.render(this.resizable.currentWidth);
    };
    refreshMaximumWidth() {
        this.maximumWidthValue = this.calculateMaximumWidth();
    }
    calculateMaximumWidth() {
        const configured = this.options.getMaxWidth?.() ?? this.options.maxWidth;
        return Number.isFinite(configured) ? Math.max(this.options.minWidth, configured) : this.options.maxWidth;
    }
    persist(width) {
        this.render(width);
        if (this.options.persistWidth)
            this.options.persistWidth(width);
        else
            this.storage.write(this.storageKey, JSON.stringify({ version: 1, width }));
    }
    render(width) {
        this.split.style.setProperty(this.options.cssVariable, `${width}px`);
        this.handle.setAttribute("aria-valuetext", `${width}px`);
    }
}
