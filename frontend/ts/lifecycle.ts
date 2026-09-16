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
  private readonly items = new Set<Disposable>();
  private readonly timeouts = new Map<number, Disposable>();
  private disposed = false;

  add<T extends Disposable>(item: T): T {
    if (this.disposed) item.dispose();
    else this.items.add(item);
    return item;
  }

  private remove(item: Disposable): void {
    this.items.delete(item);
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
    let owned: Disposable | undefined;
    const timer = window.setTimeout(() => {
      if (owned) this.remove(owned);
      this.timeouts.delete(timer);
      callback();
    }, delayMs);
    owned = this.add(disposable(() => {
      window.clearTimeout(timer);
      this.timeouts.delete(timer);
    }));
    if (!this.disposed) this.timeouts.set(timer, owned);
    return timer;
  }

  clearTimeout(timer: number | undefined): void {
    if (timer === undefined) return;
    window.clearTimeout(timer);
    const owned = this.timeouts.get(timer);
    if (owned) {
      this.remove(owned);
      this.timeouts.delete(timer);
    }
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
    const items = [...this.items].reverse();
    this.items.clear();
    this.timeouts.clear();
    for (const item of items) item.dispose();
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
