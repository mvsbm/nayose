import type { Disposable } from "./lifecycle.js";
import type { Storage } from "./storage.js";
import { ResizableRegion } from "./resizable-region.js";

export interface SplitPaneOptions {
  id: string;
  cssVariable: string;
  defaultWidth: number;
  minWidth: number;
  maxWidth: number;
  getMaxWidth?: () => number;
  pointerSide: "left" | "right";
  mobileQuery?: string;
  splitSelector?: string;
  handleSelector?: string;
  persistWidth?: (width: number) => void;
}

function readWidth(storage: Storage, key: string, fallback: number): number {
  try {
    const parsed = JSON.parse(storage.read(key) ?? "null") as { version?: unknown; width?: unknown; previewWidth?: unknown } | null;
    const version = parsed?.version;
    const width = Number(parsed?.width ?? parsed?.previewWidth);
    return (version === 1 || version === undefined) && Number.isFinite(width) ? width : fallback;
  } catch {
    return fallback;
  }
}

export class SplitPane implements Disposable {
  static mount(root: HTMLElement, storage: Storage, options: SplitPaneOptions): SplitPane | undefined {
    const splitSelector = options.splitSelector ?? `[data-panel-split="${options.id}"]`;
    const split = root.querySelector<HTMLElement>(splitSelector);
    const handle = split?.querySelector<HTMLElement>(options.handleSelector ?? "[data-panel-resizer]");
    if (!split || !handle) return undefined;
    return new SplitPane(split, handle, storage, options);
  }

  static mountElement(split: HTMLElement, storage: Storage, options: SplitPaneOptions): SplitPane | undefined {
    const handle = split.querySelector<HTMLElement>(options.handleSelector ?? "[data-panel-resizer]");
    return handle ? new SplitPane(split, handle, storage, options) : undefined;
  }

  private readonly split: HTMLElement;
  private readonly handle: HTMLElement;
  private readonly storage: Storage;
  private readonly options: SplitPaneOptions;
  private readonly storageKey: string;
  private readonly media: MediaQueryList | null;
  private readonly resizable: ResizableRegion;
  private readonly resizeTarget: Window | null;
  private maximumWidthValue: number;
  private disposed = false;

  private constructor(split: HTMLElement, handle: HTMLElement, storage: Storage, options: SplitPaneOptions) {
    if (!options.id || !options.cssVariable) throw new Error("SplitPane requires an id and CSS variable");
    if (options.minWidth > options.maxWidth) throw new Error("SplitPane minimum width exceeds maximum width");
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

  get currentWidth(): number {
    return this.resizable.currentWidth;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.resizeTarget?.removeEventListener("resize", this.constrainToAvailableWidth);
    this.resizable.dispose();
  }

  private readonly constrainToAvailableWidth = (): void => {
    this.refreshMaximumWidth();
    if (this.resizable.currentWidth > this.maximumWidthValue) this.resizable.setWidth(this.maximumWidthValue);
    else this.render(this.resizable.currentWidth);
  };

  private refreshMaximumWidth(): void {
    this.maximumWidthValue = this.calculateMaximumWidth();
  }

  private calculateMaximumWidth(): number {
    const configured = this.options.getMaxWidth?.() ?? this.options.maxWidth;
    return Number.isFinite(configured) ? Math.max(this.options.minWidth, configured) : this.options.maxWidth;
  }

  private persist(width: number): void {
    this.render(width);
    if (this.options.persistWidth) this.options.persistWidth(width);
    else this.storage.write(this.storageKey, JSON.stringify({ version: 1, width }));
  }

  private render(width: number): void {
    this.split.style.setProperty(this.options.cssVariable, `${width}px`);
    this.handle.setAttribute("aria-valuetext", `${width}px`);
  }
}
