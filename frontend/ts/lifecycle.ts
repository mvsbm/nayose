export interface Disposable {
  dispose(): void;
}

export function disposable(dispose: () => void): Disposable {
  let disposed = false;
  return {
    dispose: () => {
      if (disposed) return;
      disposed = true;
      dispose();
    },
  };
}

export class DisposableStore implements Disposable {
  private readonly items: Disposable[] = [];
  private disposed = false;

  add<T extends Disposable>(item: T): T {
    if (this.disposed) item.dispose();
    else this.items.push(item);
    return item;
  }

  addEventListener(
    target: EventTarget,
    type: string,
    listener: EventListenerOrEventListenerObject,
    options?: boolean | AddEventListenerOptions,
  ): void {
    target.addEventListener(type, listener, options);
    this.add(disposable(() => target.removeEventListener(type, listener, options)));
  }

  addTimeout(callback: () => void, delayMs: number): number {
    const timer = window.setTimeout(callback, delayMs);
    this.add(disposable(() => window.clearTimeout(timer)));
    return timer;
  }

  addInterval(callback: () => void, delayMs: number): number {
    const timer = window.setInterval(callback, delayMs);
    this.add(disposable(() => window.clearInterval(timer)));
    return timer;
  }

  get isDisposed(): boolean {
    return this.disposed;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const item of this.items.splice(0).reverse()) item.dispose();
  }
}

export class ViewScope extends DisposableStore {
  readonly controller = new AbortController();
  readonly signal = this.controller.signal;

  constructor(root?: HTMLElement) {
    super();
    if (root) {
      this.addEventListener(root, "unmount", () => this.dispose());
    }
  }

  override dispose(): void {
    if (this.isDisposed) return;
    this.controller.abort();
    super.dispose();
  }
}

export function createViewScope(root: HTMLElement): ViewScope {
  return new ViewScope(root);
}
