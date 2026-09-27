import { disposable, type Disposable } from "./lifecycle.js";

export class Viewport implements Disposable {
  readonly element: HTMLElement;
  private readonly listeners = new Set<Disposable>();
  private disposed = false;

  private get scrollTarget(): HTMLElement {
    return this.element.shadowRoot?.querySelector<HTMLElement>('[part="scrollport"]') ?? this.element;
  }

  constructor(element: HTMLElement) {
    if (!element.classList.contains("viewport")) throw new Error("Viewport requires the viewport primitive class");
    this.element = element;
  }

  get scrollTop(): number {
    return this.scrollTarget.scrollTop;
  }

  onScroll(listener: EventListener, options?: boolean | AddEventListenerOptions): Disposable {
    if (this.disposed) return disposable(() => undefined);
    this.element.addEventListener("scroll", listener, options);
    const subscription = disposable(() => {
      this.element.removeEventListener("scroll", listener, options);
      this.listeners.delete(subscription);
    });
    this.listeners.add(subscription);
    return subscription;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const listener of this.listeners) listener.dispose();
    this.listeners.clear();
  }
}

export function findViewport(root: ParentNode, selector: string): Viewport | undefined {
  const element = root.querySelector<HTMLElement>(selector);
  return element ? new Viewport(element) : undefined;
}
