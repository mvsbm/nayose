import { esc } from "./schema.js";

export const icon = (name: string, className = "icon"): string =>
  `<svg class="${esc(className)}" aria-hidden="true"><use href="/static/icons.svg#${esc(name)}"></use></svg>`;
