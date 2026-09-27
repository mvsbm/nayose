import { DisposableStore } from "./lifecycle.js";
import { SplitPane } from "./split-pane.js";
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
    const toggle = root.querySelector("#gen-preview-toggle");
    const body = root.querySelector("#gen-preview-body");
    if (!toggle || !body)
        return lifetime;
    const state = readState(storage);
    const split = SplitPane.mount(root, storage, {
        id: "admin-workbench",
        cssVariable: "--preview-width",
        defaultWidth: state.previewWidth,
        minWidth: MIN_PREVIEW_WIDTH,
        maxWidth: MAX_PREVIEW_WIDTH,
        pointerSide: "right",
        splitSelector: "[data-panel-layout='admin-workbench']",
        handleSelector: "[data-panel-resizer='preview']",
        persistWidth: width => {
            state.previewWidth = width;
            writeState(storage, state);
        },
    });
    if (split)
        lifetime.add(split);
    const renderCollapse = (collapsed) => {
        state.previewCollapsed = collapsed;
        body.hidden = collapsed;
        toggle.setAttribute("aria-expanded", String(!collapsed));
        toggle.textContent = collapsed ? (toggle.dataset.expandLabel ?? "Expand") : (toggle.dataset.collapseLabel ?? "Collapse");
    };
    renderCollapse(state.previewCollapsed);
    const onToggle = () => {
        renderCollapse(!state.previewCollapsed);
        writeState(storage, state);
    };
    toggle.addEventListener("click", onToggle);
    lifetime.add({ dispose: () => toggle.removeEventListener("click", onToggle) });
    return lifetime;
}
export function bindPanelSplit(root, storage, options) {
    const lifetime = new DisposableStore();
    const split = root.querySelector(`[data-panel-split="${options.id}"]`);
    if (split?.localName === "nayose-split-pane") {
        const component = split;
        component.configure(storage, options);
        lifetime.add(component);
        return lifetime;
    }
    const binding = SplitPane.mount(root, storage, options);
    if (binding)
        lifetime.add(binding);
    return lifetime;
}
export const panelLayoutBounds = {
    minPreviewWidth: MIN_PREVIEW_WIDTH,
    maxPreviewWidth: MAX_PREVIEW_WIDTH,
    defaultPreviewWidth: DEFAULT_STATE.previewWidth,
};
