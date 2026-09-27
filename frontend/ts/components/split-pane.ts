import type { Storage } from "../storage.js";
import { SplitPane, type SplitPaneOptions } from "../split-pane.js";
import { adoptComponentStyles } from "./styles.js";

export interface ConfigurableSplitPane extends HTMLElement {
  configure(storage: Storage, options: SplitPaneOptions): void;
  dispose(): void;
}

export function createSplitPaneElementClass(base: typeof HTMLElement): CustomElementConstructor {
  return class extends base implements ConfigurableSplitPane {
    private binding: SplitPane | undefined;
    private pending: { storage: Storage; options: SplitPaneOptions } | undefined;

    connectedCallback(): void {
      if (!this.shadowRoot) this.attachShadow({ mode: "open" });
      if (!this.shadowRoot!.querySelector("slot")) {
        adoptComponentStyles(this.shadowRoot!);
        for (const name of ["first", "sash", "second"]) {
          const slot = this.ownerDocument.createElement("slot");
          slot.name = name;
          this.shadowRoot!.append(slot);
        }
      }
      this.bindPending();
    }

    disconnectedCallback(): void {
      this.binding?.dispose();
      this.binding = undefined;
    }

    configure(storage: Storage, options: SplitPaneOptions): void {
      this.pending = { storage, options };
      this.bindPending();
    }

    dispose(): void {
      this.binding?.dispose();
      this.binding = undefined;
      this.pending = undefined;
    }

    private bindPending(): void {
      if (!this.isConnected || !this.pending) return;
      this.binding?.dispose();
      this.binding = SplitPane.mountElement(this, this.pending.storage, this.pending.options);
    }
  };
}
