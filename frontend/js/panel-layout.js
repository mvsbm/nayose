import { DisposableStore } from "./lifecycle.js";
import { ResizableRegion } from "./resizable-region.js";
const DEFAULT_STATE = { previewWidth: 420, previewCollapsed: false };
const MIN_PREVIEW_WIDTH = 352;
const MAX_PREVIEW_WIDTH = 640;
const STORAGE_KEY = "nayose.layout.admin-workbench";
function clampWidth(value) {
    return Math.min(MAX_PREVIEW_WIDTH, Math.max(MIN_PREVIEW_WIDTH, Math.round(value)));
}
function readState(storage) {
    try {
        const raw = storage.read(STORAGE_KEY);
        if (!raw)
            return { ...DEFAULT_STATE };
        const parsed = JSON.parse(raw);
        return {
            previewWidth: clampWidth(Number(parsed.previewWidth) || DEFAULT_STATE.previewWidth),
            previewCollapsed: parsed.previewCollapsed === true,
        };
    }
    catch {
        return { ...DEFAULT_STATE };
    }
}
function writeState(storage, state) {
    storage.write(STORAGE_KEY, JSON.stringify({ version: 1, ...state }));
}
export function bindPanelLayout(root, storage) {
    const lifetime = new DisposableStore();
    const workbench = root.querySelector("[data-panel-layout='admin-workbench']");
    const handle = root.querySelector("[data-panel-resizer='preview']");
    const toggle = root.querySelector("#gen-preview-toggle");
    const body = root.querySelector("#gen-preview-body");
    if (!workbench || !handle || !toggle || !body)
        return lifetime;
    const state = readState(storage);
    const renderWidth = (width) => {
        state.previewWidth = width;
        workbench.style.setProperty("--preview-width", `${width}px`);
        handle.setAttribute("aria-valuetext", `${width}px`);
    };
    const updateWidth = (width) => {
        renderWidth(width);
        writeState(storage, state);
    };
    const renderCollapse = (collapsed) => {
        state.previewCollapsed = collapsed;
        body.hidden = collapsed;
        toggle.setAttribute("aria-expanded", String(!collapsed));
        toggle.textContent = collapsed ? (toggle.dataset.expandLabel ?? "Expand") : (toggle.dataset.collapseLabel ?? "Collapse");
    };
    const updateCollapse = (collapsed) => {
        renderCollapse(collapsed);
        writeState(storage, state);
    };
    renderWidth(state.previewWidth);
    renderCollapse(state.previewCollapsed);
    const resizable = new ResizableRegion({
        handle,
        initialWidth: state.previewWidth,
        minWidth: MIN_PREVIEW_WIDTH,
        maxWidth: MAX_PREVIEW_WIDTH,
        defaultWidth: DEFAULT_STATE.previewWidth,
        arrowGrowth: "backward",
        pointerWidth: (_startWidth, _startClientX, clientX) => workbench.getBoundingClientRect().right - clientX,
        onWidthChange: updateWidth,
    });
    lifetime.add(resizable);
    const onToggle = () => updateCollapse(!state.previewCollapsed);
    toggle.addEventListener("click", onToggle);
    lifetime.add({ dispose: () => toggle.removeEventListener("click", onToggle) });
    return lifetime;
}
export function bindPanelSplit(root, storage, options) {
    const lifetime = new DisposableStore();
    const split = root.querySelector(`[data-panel-split="${options.id}"]`);
    const handle = split?.querySelector("[data-panel-resizer]");
    if (!split || !handle)
        return lifetime;
    const storageKey = `nayose.layout.${options.id}`;
    let initialWidth = options.defaultWidth;
    try {
        const parsed = JSON.parse(storage.read(storageKey) ?? "null");
        const storedWidth = parsed?.version === 1 || parsed?.version === undefined ? Number(parsed?.width) : Number.NaN;
        if (Number.isFinite(storedWidth))
            initialWidth = storedWidth;
    }
    catch {
        initialWidth = options.defaultWidth;
    }
    const media = options.mobileQuery ? window.matchMedia?.(options.mobileQuery) : null;
    const renderWidth = (width) => split.style.setProperty(options.cssVariable, `${width}px`);
    const persistWidth = (width) => {
        renderWidth(width);
        storage.write(storageKey, JSON.stringify({ version: 1, width }));
    };
    renderWidth(Math.min(options.maxWidth, Math.max(options.minWidth, Math.round(initialWidth))));
    const resizable = new ResizableRegion({
        handle,
        initialWidth,
        minWidth: options.minWidth,
        maxWidth: options.maxWidth,
        defaultWidth: options.defaultWidth,
        arrowGrowth: options.pointerSide === "left" ? "forward" : "backward",
        isDisabled: () => media?.matches ?? false,
        pointerWidth: (startWidth, startClientX, clientX) => options.pointerSide === "left"
            ? startWidth + clientX - startClientX
            : startWidth + startClientX - clientX,
        onWidthChange: persistWidth,
    });
    lifetime.add(resizable);
    return lifetime;
}
export const panelLayoutBounds = {
    minPreviewWidth: MIN_PREVIEW_WIDTH,
    maxPreviewWidth: MAX_PREVIEW_WIDTH,
    defaultPreviewWidth: DEFAULT_STATE.previewWidth,
};
