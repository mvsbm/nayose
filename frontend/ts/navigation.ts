import { NAVIGATION_WIDTH_MAX, NAVIGATION_WIDTH_MIN, NAVIGATION_WIDTH_DEFAULT, type Store } from "./store.js";
import { ResizableRegion } from "./resizable-region.js";
import { NAVIGATION_GROUPS, ROUTES } from "./routes.js";
import { esc } from "./schema.js";
import { icon } from "./icons.js";

const MOBILE_QUERY = "(max-width: 820px)";

export function renderNavigation(nav: HTMLElement, store: Store): void {
  const groups = NAVIGATION_GROUPS.map((group) => {
    const routes = ROUTES.filter((route) => route.menu && route.group === group.id);
    const links = routes.map((route) => {
      const capability = "capability" in route ? route.capability : "";
      const hidden = capability && route.group !== "admin" ? " hidden" : "";
      const id = route.name === "data-entry" ? ' id="nav-data-entry"' : route.name === "admin" ? ' id="nav-admin"' : "";
      return `<a href="${esc(route.path)}" data-nav="${esc(route.nav)}"${id}${hidden}>${icon(route.icon, "icon side-icon")}<span class="side-label" data-i18n="${esc(route.titleKey)}">${esc(store.t(route.titleKey))}</span></a>`;
    }).join("");
    const hidden = "capability" in group ? " hidden" : "";
    const id = group.id === "admin" ? ' id="nav-admin-group"' : "";
    return `<details${id} data-nav-group="${esc(group.id)}"${hidden} open><summary data-nav-label="${esc(group.id)}"><span class="side-label" data-i18n="${esc(group.labelKey)}">${esc(store.t(group.labelKey))}</span></summary>${links}</details>`;
  }).join("");
  nav.innerHTML = `<a class="side-brand" href="#/search" aria-label="Nayose"><span class="side-brand-mark" aria-hidden="true">N</span><span><strong>Nayose</strong><small data-i18n="workspaceLabel">${esc(store.t("workspaceLabel"))}</small></span></a>${groups}`;
}

function labelFor(element: Element): string {
  return element.querySelector<HTMLElement>(".side-label")?.textContent?.trim() ?? "";
}

export function mountNavigation(
  shell: HTMLElement,
  nav: HTMLElement,
  resizer: HTMLElement,
  toggle: HTMLButtonElement,
  scrim: HTMLButtonElement,
  store: Store,
): { toggle: () => void; destroy: () => void } {
  const media = window.matchMedia?.(MOBILE_QUERY) ?? {
    matches: false,
    addEventListener: () => {},
    removeEventListener: () => {},
  };
  const saved = store.read("nayose.navigationCollapsed") ?? store.read("nayose.preferences");
  let collapsed = saved === null ? media.matches : store.navigationCollapsed;
  let wasMobile = media.matches;
  let width = store.navigationWidth;

  const applyWidth = (next: number): void => {
    width = Math.min(NAVIGATION_WIDTH_MAX, Math.max(NAVIGATION_WIDTH_MIN, Math.round(next)));
    shell.style.setProperty("--sidebar-width", `${width}px`);
    resizer.setAttribute("aria-valuenow", String(width));
  };

  const syncLabels = (): void => {
    nav.querySelectorAll<HTMLElement>("a[data-nav], summary[data-nav-label]").forEach((item) => {
      const label = labelFor(item);
      if (!label) return;
      item.dataset.tooltip = label;
      if (item.tagName === "A") item.setAttribute("aria-label", label);
    });
  };
  const sync = (): void => {
    const mobile = media.matches;
    shell.dataset.navCollapsed = String(collapsed);
    nav.dataset.navCollapsed = String(collapsed);
    toggle.dataset.navCollapsed = String(collapsed);
    applyWidth(width);
    resizer.hidden = collapsed || mobile;
    resizer.setAttribute("aria-hidden", String(resizer.hidden));
    toggle.setAttribute("aria-expanded", String(!collapsed));
    const toggleLabel = store.t(collapsed ? "expandNavigation" : "collapseNavigation");
    toggle.setAttribute("aria-label", toggleLabel);
    toggle.title = toggleLabel;
    const label = toggle.querySelector<HTMLElement>(".side-toggle-label");
    if (label) label.textContent = toggleLabel;
    nav.toggleAttribute("inert", collapsed);
    nav.setAttribute("aria-hidden", String(collapsed));
    scrim.hidden = !(!collapsed && mobile);
    syncLabels();
  };
  const onStoreChange = (): void => {
    collapsed = store.navigationCollapsed;
    sync();
  };
  let resizable: ResizableRegion | undefined;
  const onWidthChange = (): void => {
    width = store.navigationWidth;
    resizable?.setWidth(width, false);
    applyWidth(width);
  };
  const onLocaleChange = (): void => sync();
  const onMediaChange = (): void => {
    const mobile = media.matches;
    if (mobile && !wasMobile) collapsed = true;
    if (!mobile && wasMobile) collapsed = store.navigationCollapsed;
    wasMobile = mobile;
    sync();
  };
  const focusableNavigation = (): HTMLElement[] => [
    ...nav.querySelectorAll<HTMLElement>("a[href], summary, button, input, select, textarea, [tabindex]")
  ].filter((item) => !item.closest("[hidden]") && !item.hasAttribute("disabled") && item.tabIndex >= 0);
  const onToggle = (): void => {
    const openingMobile = media.matches && collapsed;
    store.toggleNavigation(collapsed);
    if (openingMobile) focusableNavigation()[0]?.focus();
  };
  const closeMobile = (): void => {
    if (!media.matches || collapsed) return;
    store.setNavigationCollapsed(true);
    toggle.focus();
  };
  const onKeydown = (event: KeyboardEvent): void => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "b") {
      event.preventDefault();
      onToggle();
    } else if (event.key === "Escape") {
      closeMobile();
    } else if (event.key === "Tab" && media.matches && !collapsed) {
      const items = focusableNavigation();
      const first = items[0];
      const last = items[items.length - 1];
      if (first && last && event.target === first && event.shiftKey) {
        event.preventDefault();
        last.focus();
      } else if (first && last && event.target === last && !event.shiftKey) {
        event.preventDefault();
        first.focus();
      }
    }
  };
  resizable = new ResizableRegion({
    handle: resizer,
    initialWidth: width,
    minWidth: NAVIGATION_WIDTH_MIN,
    maxWidth: NAVIGATION_WIDTH_MAX,
    defaultWidth: NAVIGATION_WIDTH_DEFAULT,
    arrowGrowth: "forward",
    isDisabled: () => collapsed || media.matches,
    pointerWidth: (startWidth, startClientX, clientX) => startWidth + clientX - startClientX,
    onWidthChange: (next) => store.setNavigationWidth(next),
  });
  const onNavClick = (event: MouseEvent): void => {
    if (media.matches && (event.target as HTMLElement).closest("a")) closeMobile();
  };
  toggle.addEventListener("click", onToggle);
  scrim.addEventListener("click", closeMobile);
  document.addEventListener("keydown", onKeydown);
  nav.addEventListener("click", onNavClick);
  media.addEventListener("change", onMediaChange);
  const unsubscribeState = store.on("navigationCollapsed", onStoreChange);
  const unsubscribeWidth = store.on("navigationWidth", onWidthChange);
  const unsubscribeLocale = store.on("locale", onLocaleChange);
  sync();

  return {
    toggle: onToggle,
    destroy: (): void => {
      toggle.removeEventListener("click", onToggle);
      scrim.removeEventListener("click", closeMobile);
      document.removeEventListener("keydown", onKeydown);
      nav.removeEventListener("click", onNavClick);
      media.removeEventListener("change", onMediaChange);
      resizable?.dispose();
      unsubscribeState();
      unsubscribeWidth();
      unsubscribeLocale();
    },
  };
}
