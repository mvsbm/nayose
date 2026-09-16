import type { Storage } from "./storage.js";
import { DisposableStore } from "./lifecycle.js";
import { ResizableRegion } from "./resizable-region.js";

export interface PanelLayoutState {
  previewWidth: number;
  previewCollapsed: boolean;
}

const DEFAULT_STATE: PanelLayoutState = { previewWidth: 420, previewCollapsed: false };
const MIN_PREVIEW_WIDTH = 352;
const MAX_PREVIEW_WIDTH = 640;
const STORAGE_KEY = "nayose.layout.admin-workbench";

function clampWidth(value: number): number {
  return Math.min(MAX_PREVIEW_WIDTH, Math.max(MIN_PREVIEW_WIDTH, Math.round(value)));
}

function readState(storage: Storage): PanelLayoutState {
  try {
    const raw = storage.read(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_STATE };
    const parsed = JSON.parse(raw) as Partial<PanelLayoutState>;
    return {
      previewWidth: clampWidth(Number(parsed.previewWidth) || DEFAULT_STATE.previewWidth),
      previewCollapsed: parsed.previewCollapsed === true,
    };
  } catch {
    return { ...DEFAULT_STATE };
  }
}

function writeState(storage: Storage, state: PanelLayoutState): void {
  storage.write(STORAGE_KEY, JSON.stringify({ version: 1, ...state }));
}

export function bindPanelLayout(root: HTMLElement, storage: Storage): DisposableStore {
  const lifetime = new DisposableStore();
  const workbench = root.querySelector<HTMLElement>("[data-panel-layout='admin-workbench']");
  const handle = root.querySelector<HTMLElement>("[data-panel-resizer='preview']");
  const toggle = root.querySelector<HTMLButtonElement>("#gen-preview-toggle");
  const body = root.querySelector<HTMLElement>("#gen-preview-body");
  if (!workbench || !handle || !toggle || !body) return lifetime;

  const state = readState(storage);
  const renderWidth = (width: number): void => {
    state.previewWidth = width;
    workbench.style.setProperty("--preview-width", `${width}px`);
    handle.setAttribute("aria-valuetext", `${width}px`);
  };
  const updateWidth = (width: number): void => {
    renderWidth(width);
    writeState(storage, state);
  };
  const renderCollapse = (collapsed: boolean): void => {
    state.previewCollapsed = collapsed;
    body.hidden = collapsed;
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.textContent = collapsed ? (toggle.dataset.expandLabel ?? "Expand") : (toggle.dataset.collapseLabel ?? "Collapse");
  };
  const updateCollapse = (collapsed: boolean): void => {
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
  const onToggle = (): void => updateCollapse(!state.previewCollapsed);
  toggle.addEventListener("click", onToggle);
  lifetime.add({ dispose: () => toggle.removeEventListener("click", onToggle) });
  return lifetime;
}

export interface PanelSplitOptions {
  id: string;
  cssVariable: string;
  defaultWidth: number;
  minWidth: number;
  maxWidth: number;
  pointerSide: "left" | "right";
  mobileQuery?: string;
}

export function bindPanelSplit(root: HTMLElement, storage: Storage, options: PanelSplitOptions): DisposableStore {
  const lifetime = new DisposableStore();
  const split = root.querySelector<HTMLElement>(`[data-panel-split="${options.id}"]`);
  const handle = split?.querySelector<HTMLElement>("[data-panel-resizer]");
  if (!split || !handle) return lifetime;

  const storageKey = `nayose.layout.${options.id}`;
  let initialWidth = options.defaultWidth;
  try {
    const parsed = JSON.parse(storage.read(storageKey) ?? "null") as { version?: unknown; width?: unknown } | null;
    const storedWidth = parsed?.version === 1 || parsed?.version === undefined ? Number(parsed?.width) : Number.NaN;
    if (Number.isFinite(storedWidth)) initialWidth = storedWidth;
  } catch {
    initialWidth = options.defaultWidth;
  }
  const media = options.mobileQuery ? window.matchMedia?.(options.mobileQuery) : null;
  const renderWidth = (width: number): void => split.style.setProperty(options.cssVariable, `${width}px`);
  const persistWidth = (width: number): void => {
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
