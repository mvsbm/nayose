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
    storage.write(STORAGE_KEY, JSON.stringify(state));
}
export function bindPanelLayout(root, storage) {
    const workbench = root.querySelector("[data-panel-layout='admin-workbench']");
    const handle = root.querySelector("[data-panel-resizer='preview']");
    const toggle = root.querySelector("#gen-preview-toggle");
    const body = root.querySelector("#gen-preview-body");
    if (!workbench || !handle || !toggle || !body)
        return;
    const state = readState(storage);
    const updateWidth = (width) => {
        state.previewWidth = clampWidth(width);
        workbench.style.setProperty("--preview-width", `${state.previewWidth}px`);
        handle.setAttribute("aria-valuenow", String(state.previewWidth));
        handle.setAttribute("aria-valuetext", `${state.previewWidth}px`);
        writeState(storage, state);
    };
    const updateCollapse = (collapsed) => {
        state.previewCollapsed = collapsed;
        body.hidden = collapsed;
        toggle.setAttribute("aria-expanded", String(!collapsed));
        toggle.textContent = collapsed ? (toggle.dataset.expandLabel ?? "Expand") : (toggle.dataset.collapseLabel ?? "Collapse");
        writeState(storage, state);
    };
    const resizeFromPointer = (clientX) => {
        const bounds = workbench.getBoundingClientRect();
        updateWidth(bounds.right - clientX);
    };
    updateWidth(state.previewWidth);
    updateCollapse(state.previewCollapsed);
    toggle.addEventListener("click", () => updateCollapse(!state.previewCollapsed));
    handle.addEventListener("pointerdown", event => {
        if (event.button !== 0)
            return;
        handle.setPointerCapture(event.pointerId);
        handle.classList.add("is-dragging");
        const move = (moveEvent) => resizeFromPointer(moveEvent.clientX);
        const finish = () => {
            handle.classList.remove("is-dragging");
            handle.removeEventListener("pointermove", move);
            handle.removeEventListener("pointerup", finish);
            handle.removeEventListener("pointercancel", finish);
        };
        handle.addEventListener("pointermove", move);
        handle.addEventListener("pointerup", finish);
        handle.addEventListener("pointercancel", finish);
    });
    handle.addEventListener("keydown", event => {
        if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault();
            updateWidth(state.previewWidth + (event.key === "ArrowLeft" ? 16 : -16));
        }
        else if (event.key === "Home") {
            event.preventDefault();
            updateWidth(MIN_PREVIEW_WIDTH);
        }
        else if (event.key === "End") {
            event.preventDefault();
            updateWidth(MAX_PREVIEW_WIDTH);
        }
    });
    handle.addEventListener("dblclick", () => updateWidth(DEFAULT_STATE.previewWidth));
}
export const panelLayoutBounds = {
    minPreviewWidth: MIN_PREVIEW_WIDTH,
    maxPreviewWidth: MAX_PREVIEW_WIDTH,
    defaultPreviewWidth: DEFAULT_STATE.previewWidth,
};
