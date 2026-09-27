import { adoptComponentStyles } from "./styles.js";

export function createViewportElementClass(base: typeof HTMLElement): CustomElementConstructor {
  return class extends base {
    private scrollport: HTMLElement | undefined;

    connectedCallback(): void {
      if (!this.shadowRoot) this.attachShadow({ mode: "open" });
      if (!this.scrollport) {
        adoptComponentStyles(this.shadowRoot!);
        this.scrollport = this.ownerDocument.createElement("div");
        this.scrollport.setAttribute("part", "scrollport");
        const slot = this.ownerDocument.createElement("slot");
        this.scrollport.append(slot);
        this.shadowRoot!.append(this.scrollport);
        Object.defineProperty(this, "scrollTop", {
          configurable: true,
          get: () => this.scrollport?.scrollTop ?? 0,
          set: value => { if (this.scrollport) this.scrollport.scrollTop = Number(value) || 0; },
        });
        this.scrollport.addEventListener("scroll", this.forwardScroll);
      }
    }

    disconnectedCallback(): void {
      this.scrollport?.removeEventListener("scroll", this.forwardScroll);
    }

    get scrollElement(): HTMLElement {
      return this.scrollport ?? this;
    }

    private readonly forwardScroll = (): void => {
      const EventConstructor = this.ownerDocument.defaultView?.Event ?? Event;
      this.dispatchEvent(new EventConstructor("scroll"));
    };
  };
}
