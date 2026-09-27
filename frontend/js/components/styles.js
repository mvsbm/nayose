const COMPONENT_CSS = `
:host(nayose-viewport) {
  display: block;
  min-inline-size: 0;
  min-block-size: 0;
  overflow: hidden;
}

:host(nayose-split-pane) {
  display: grid;
  min-inline-size: 0;
  min-block-size: 0;
}

:host(nayose-viewport) > [part="scrollport"] {
  inline-size: 100%;
  block-size: 100%;
  min-inline-size: 0;
  min-block-size: 0;
  overflow: var(--nayose-viewport-overflow, auto);
}

:host(nayose-split-pane) > slot {
  display: contents;
}
`;
const sheets = new WeakMap();
export function adoptComponentStyles(root) {
    const owner = root.ownerDocument.defaultView;
    const Sheet = owner?.CSSStyleSheet;
    if (Sheet && typeof Sheet.prototype.replaceSync === "function" && "adoptedStyleSheets" in root) {
        let sheet = sheets.get(root.ownerDocument);
        if (!sheet) {
            sheet = new Sheet();
            sheets.set(root.ownerDocument, sheet);
        }
        if (!root.adoptedStyleSheets.includes(sheet))
            root.adoptedStyleSheets = [...root.adoptedStyleSheets, sheet];
        if (!sheet.cssRules.length)
            sheet.replaceSync(COMPONENT_CSS);
        return;
    }
    const style = root.ownerDocument.createElement("style");
    style.textContent = COMPONENT_CSS;
    root.append(style);
}
