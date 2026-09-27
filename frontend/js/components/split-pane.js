import { SplitPane } from "../split-pane.js";
import { adoptComponentStyles } from "./styles.js";
export function createSplitPaneElementClass(base) {
    return class extends base {
        binding;
        pending;
        connectedCallback() {
            if (!this.shadowRoot)
                this.attachShadow({ mode: "open" });
            if (!this.shadowRoot.querySelector("slot")) {
                adoptComponentStyles(this.shadowRoot);
                for (const name of ["first", "sash", "second"]) {
                    const slot = this.ownerDocument.createElement("slot");
                    slot.name = name;
                    this.shadowRoot.append(slot);
                }
            }
            this.bindPending();
        }
        disconnectedCallback() {
            this.binding?.dispose();
            this.binding = undefined;
        }
        configure(storage, options) {
            this.pending = { storage, options };
            this.bindPending();
        }
        dispose() {
            this.binding?.dispose();
            this.binding = undefined;
            this.pending = undefined;
        }
        bindPending() {
            if (!this.isConnected || !this.pending)
                return;
            this.binding?.dispose();
            this.binding = SplitPane.mountElement(this, this.pending.storage, this.pending.options);
        }
    };
}
