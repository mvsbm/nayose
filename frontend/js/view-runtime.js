import { esc } from "./schema.js";
import { createViewScope, disposable } from "./lifecycle.js";
const mountVersions = new WeakMap();
const mountScopes = new WeakMap();
export async function mountView(el, spec, parentScope) {
    const version = (mountVersions.get(el) ?? 0) + 1;
    mountVersions.set(el, version);
    mountScopes.get(el)?.dispose();
    const scope = createViewScope(el);
    if (parentScope) {
        const disposeWithParent = () => scope.dispose();
        if (parentScope.signal.aborted)
            scope.dispose();
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
    if (scope.isDisposed || mountVersions.get(el) !== version)
        return;
    el.innerHTML = spec.render(data);
    el.setAttribute("aria-busy", "false");
    spec.bind?.(el, data, scope);
}
