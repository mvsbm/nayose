import type { Disposable } from "./lifecycle.js";

export interface ResizableRegionOptions {
  handle: HTMLElement;
  initialWidth: number;
  minWidth: number;
  maxWidth: number;
  defaultWidth: number;
  step?: number;
  arrowGrowth: "forward" | "backward";
  isDisabled?: () => boolean;
  pointerWidth(startWidth: number, startClientX: number, clientX: number): number;
  onWidthChange(width: number): void;
}

export class ResizableRegion implements Disposable {
  private readonly options: ResizableRegionOptions;
  private width: number;
  private dragging = false;
  private startWidth = 0;
  private startClientX = 0;
  private disposed = false;

  constructor(options: ResizableRegionOptions) {
    this.options = options;
    this.width = this.clamp(options.initialWidth);
    this.render();
    options.handle.addEventListener("pointerdown", this.onPointerDown);
    options.handle.addEventListener("keydown", this.onKeydown);
    options.handle.addEventListener("dblclick", this.onDoubleClick);
  }

  get currentWidth(): number {
    return this.width;
  }

  setWidth(width: number, notify = true): void {
    if (this.disposed) return;
    const next = this.clamp(width);
    if (next === this.width) return;
    this.width = next;
    this.render();
    if (notify) this.options.onWidthChange(next);
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.endDrag();
    this.options.handle.removeEventListener("pointerdown", this.onPointerDown);
    this.options.handle.removeEventListener("keydown", this.onKeydown);
    this.options.handle.removeEventListener("dblclick", this.onDoubleClick);
  }

  private readonly onPointerDown = (event: PointerEvent): void => {
    if (this.disposed || this.options.isDisabled?.() || event.button !== 0) return;
    event.preventDefault();
    this.dragging = true;
    this.startWidth = this.width;
    this.startClientX = event.clientX;
    this.options.handle.classList.add("is-dragging");
    document.addEventListener("pointermove", this.onPointerMove);
    document.addEventListener("pointerup", this.onPointerUp);
    document.addEventListener("pointercancel", this.onPointerUp);
  };

  private readonly onPointerMove = (event: PointerEvent): void => {
    if (!this.dragging) return;
    this.setWidth(this.options.pointerWidth(this.startWidth, this.startClientX, event.clientX));
  };

  private readonly onPointerUp = (): void => this.endDrag();

  private readonly onKeydown = (event: KeyboardEvent): void => {
    if (this.disposed || this.options.isDisabled?.()) return;
    const step = this.options.step ?? 16;
    const direction = this.options.arrowGrowth === "forward" ? 1 : -1;
    const delta = event.key === "ArrowRight" || event.key === "ArrowUp"
      ? direction * step
      : event.key === "ArrowLeft" || event.key === "ArrowDown"
        ? -direction * step
        : 0;
    const next = event.key === "Home"
      ? this.options.minWidth
      : event.key === "End"
        ? this.options.maxWidth
        : this.width + delta;
    if (!delta && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    this.setWidth(next);
  };

  private readonly onDoubleClick = (): void => this.setWidth(this.options.defaultWidth);

  private endDrag(): void {
    if (!this.dragging) return;
    this.dragging = false;
    this.options.handle.classList.remove("is-dragging");
    document.removeEventListener("pointermove", this.onPointerMove);
    document.removeEventListener("pointerup", this.onPointerUp);
    document.removeEventListener("pointercancel", this.onPointerUp);
  }

  private clamp(width: number): number {
    return Math.min(this.options.maxWidth, Math.max(this.options.minWidth, Math.round(width)));
  }

  private render(): void {
    this.options.handle.setAttribute("aria-valuemin", String(this.options.minWidth));
    this.options.handle.setAttribute("aria-valuemax", String(this.options.maxWidth));
    this.options.handle.setAttribute("aria-valuenow", String(this.width));
  }
}
