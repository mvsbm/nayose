import { esc } from "./schema.js";
export const icon = (name, className = "icon") => `<svg class="${esc(className)}" aria-hidden="true"><use href="/static/icons.svg#${esc(name)}"></use></svg>`;
