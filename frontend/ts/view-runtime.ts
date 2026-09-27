import { esc } from "./schema.js";
import { createViewScope, disposable, type ViewScope } from "./lifecycle.js";

export interface MountSpec<T> {
  skeleton?: string;
  loadingText?: string;
  load: (signal: AbortSignal) => Promise<T>;
  render: (data: T) => string;
  bind?: (el: HTMLElement, data: T, scope: ViewScope) => void;
}

const mountVersions = new WeakMap<HTMLElement, number>();
const mountScopes = new WeakMap<HTMLElement, ViewScope>();

export async function mountView<T>(el: HTMLElement, spec: MountSpec<T>, parentScope?: ViewScope): Promise<void> {
  const version = (mountVersions.get(el) ?? 0) + 1;
  mountVersions.set(el, version);
  mountScopes.get(el)?.dispose();
  const scope = createViewScope(el);
  if (parentScope) {
    const disposeWithParent = (): void => scope.dispose();
    if (parentScope.signal.aborted) scope.dispose();
    else {
      parentScope.signal.addEventListener("abort", disposeWithParent, { once: true });
      scope.add(disposable(() => parentScope.signal.removeEventListener("abort", disposeWithParent)));
    }
  }
  mountScopes.set(el, scope);
  if (spec.skeleton) {
    el.setAttribute("aria-busy", "true");
    el.innerHTML = `<div role="status" aria-live="polite" aria-label="${esc(spec.loadingText ?? "")}">${spec.skeleton}</div>`;
  }
  const data = await spec.load(scope.signal);
  if (scope.isDisposed || mountVersions.get(el) !== version) return;
  el.innerHTML = spec.render(data);
  el.setAttribute("aria-busy", "false");
  spec.bind?.(el, data, scope);
}
