import { createSplitPaneElementClass } from "./split-pane.js";
import { createViewportElementClass } from "./viewport.js";

export function defineComponents(): void {
  const owner = globalThis.window;
  const registry = owner?.customElements ?? globalThis.customElements;
  const base = owner?.HTMLElement ?? globalThis.HTMLElement;
  if (!registry || !base) return;
  if (!registry.get("nayose-viewport")) registry.define("nayose-viewport", createViewportElementClass(base));
  if (!registry.get("nayose-split-pane")) registry.define("nayose-split-pane", createSplitPaneElementClass(base));
}
