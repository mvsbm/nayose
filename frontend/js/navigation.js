import { NAVIGATION_WIDTH_MAX, NAVIGATION_WIDTH_MIN, NAVIGATION_WIDTH_DEFAULT } from "./store.js";
import { ResizableRegion } from "./resizable-region.js";
import { NAVIGATION_GROUPS, ROUTES, routeDescriptor } from "./routes.js";
import { esc } from "./schema.js";
import { icon } from "./icons.js";
const MOBILE_QUERY = "(max-width: 820px)";
export function renderActivityBar(bar, store) {
    const groups = NAVIGATION_GROUPS.map((group) => {
        const route = ROUTES.find((candidate) => candidate.menu && candidate.group === group.id);
        if (!route)
            return "";
        const hidden = "capability" in group ? " hidden" : "";
        const id = group.id === "admin" ? ' id="activity-admin"' : "";
        return `<a class="activity-item" href="${esc(route.path)}" data-activity-group="${esc(group.id)}"${id}${hidden} data-i18n-aria="${esc(group.labelKey)}" aria-label="${esc(store.t(group.labelKey))}" title="${esc(store.t(group.labelKey))}">${icon(route.icon, "icon activity-bar-icon")}<span class="activity-item-label" data-i18n="${esc(group.labelKey)}">${esc(store.t(group.labelKey))}</span></a>`;
    }).join("");
    bar.innerHTML = groups;
}
export function setActiveSidebarGroup(nav, groupId) {
    nav.querySelectorAll("[data-sidebar-group]").forEach((group) => {
        group.toggleAttribute("hidden", group.dataset.sidebarGroup !== groupId);
    });
}
export function renderWorkbenchTabs(tabs, store, routeName) {
    const descriptor = routeDescriptor(routeName);
    const routes = descriptor.group === "evaluate"
        ? ROUTES.filter((route) => route.menu && route.group === "evaluate")
        : [descriptor];
    tabs.innerHTML = routes.map((route) => `<a class="workbench-tab" href="${esc(route.path)}" role="tab" aria-controls="view" aria-selected="${route.name === routeName}"${route.name === routeName ? ' aria-current="page"' : ""}>${icon(route.icon, "icon workbench-tab-icon")}<span class="workbench-tab-label" data-i18n="${esc(route.titleKey)}">${esc(store.t(route.titleKey))}</span></a>`).join("");
}
export function updateWorkbenchSelection(activity, tabs, routeName) {
    const descriptor = routeDescriptor(routeName);
    activity.querySelectorAll("[data-activity-group]").forEach((item) => {
        const active = item.dataset.activityGroup === descriptor.group;
        item.toggleAttribute("aria-current", active);
    });
    tabs.querySelectorAll("[role=tab]").forEach((item) => {
        item.setAttribute("aria-selected", String(item.getAttribute("aria-current") === "page"));
    });
}
export function renderNavigation(nav, store, activeGroup = "match") {
    const groups = NAVIGATION_GROUPS.map((group) => {
        const routes = ROUTES.filter((route) => route.menu && route.group === group.id);
        const links = routes.map((route) => {
            const capability = "capability" in route ? route.capability : "";
            const hidden = capability && route.group !== "admin" ? " hidden" : "";
            const id = route.name === "data-entry" ? ' id="nav-data-entry"' : route.name === "admin" ? ' id="nav-admin"' : "";
            return `<a href="${esc(route.path)}" data-nav="${esc(route.nav)}"${id}${hidden}>${icon(route.icon, "icon side-icon")}<span class="side-label" data-i18n="${esc(route.titleKey)}">${esc(store.t(route.titleKey))}</span></a>`;
        }).join("");
        const hidden = group.id === activeGroup ? "" : " hidden";
        const id = group.id === "admin" ? ' id="nav-admin-group"' : "";
        return `<section class="side-view" data-sidebar-group="${esc(group.id)}"${id}${hidden} aria-label="${esc(store.t(group.labelKey))}"><div class="side-header"><strong class="side-header-title" data-i18n="${esc(group.labelKey)}">${esc(store.t(group.labelKey))}</strong><button class="button button-toolbar side-header-action" type="button" data-i18n-aria="sidebarActions" data-i18n-title="sidebarActions" aria-label="${esc(store.t("sidebarActions"))}" title="${esc(store.t("sidebarActions"))}">${icon("more", "icon")}</button></div><details class="side-group" open><summary><span class="side-label" data-i18n="sideViews">${esc(store.t("sideViews"))}</span></summary>${links}</details></section>`;
    }).join("");
    nav.innerHTML = `<a class="side-brand" href="#/search" aria-label="Nayose"><span class="side-brand-mark" aria-hidden="true">N</span><span><strong>Nayose</strong><small data-i18n="workspaceLabel">${esc(store.t("workspaceLabel"))}</small></span></a>${groups}`;
}
export function mountActivityBar(bar, store, toggleNavigation) {
    const onClick = (event) => {
        const item = event.target.closest(".activity-item");
        if (!item || item.hidden)
            return;
        const active = item.getAttribute("aria-current") === "page";
        if (store.navigationCollapsed || active) {
            event.preventDefault();
            toggleNavigation();
            if (!active)
                window.location.hash = item.getAttribute("href") ?? "#/search";
        }
    };
    bar.addEventListener("click", onClick);
    return { destroy: () => bar.removeEventListener("click", onClick) };
}
function labelFor(element) {
    return element.querySelector(".side-label")?.textContent?.trim() ?? "";
}
export function mountNavigation(shell, nav, resizer, toggle, scrim, store) {
    const media = window.matchMedia?.(MOBILE_QUERY) ?? {
        matches: false,
        addEventListener: () => { },
        removeEventListener: () => { },
    };
    const saved = store.read("nayose.navigationCollapsed") ?? store.read("nayose.preferences");
    let collapsed = saved === null ? media.matches : store.navigationCollapsed;
    let wasMobile = media.matches;
    let width = store.navigationWidth;
    const applyWidth = (next) => {
        width = Math.min(NAVIGATION_WIDTH_MAX, Math.max(NAVIGATION_WIDTH_MIN, Math.round(next)));
        shell.style.setProperty("--sidebar-width", `${width}px`);
        resizer.setAttribute("aria-valuenow", String(width));
    };
    const syncLabels = () => {
        nav.querySelectorAll("a[data-nav], summary[data-nav-label]").forEach((item) => {
            const label = labelFor(item);
            if (!label)
                return;
            item.dataset.tooltip = label;
            if (item.tagName === "A")
                item.setAttribute("aria-label", label);
        });
    };
    const sync = () => {
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
        const label = toggle.querySelector(".side-toggle-label");
        if (label)
            label.textContent = toggleLabel;
        nav.toggleAttribute("inert", collapsed);
        nav.setAttribute("aria-hidden", String(collapsed));
        scrim.hidden = !(!collapsed && mobile);
        syncLabels();
    };
    const onStoreChange = () => {
        collapsed = store.navigationCollapsed;
        sync();
    };
    let resizable;
    const onWidthChange = () => {
        width = store.navigationWidth;
        resizable?.setWidth(width, false);
        applyWidth(width);
    };
    const onLocaleChange = () => sync();
    const onMediaChange = () => {
        const mobile = media.matches;
        if (mobile && !wasMobile)
            collapsed = true;
        if (!mobile && wasMobile)
            collapsed = store.navigationCollapsed;
        wasMobile = mobile;
        sync();
    };
    const focusableNavigation = () => [
        ...nav.querySelectorAll("a[href], summary, button, input, select, textarea, [tabindex]")
    ].filter((item) => !item.closest("[hidden]") && !item.hasAttribute("disabled") && item.tabIndex >= 0);
    const onToggle = () => {
        const openingMobile = media.matches && collapsed;
        store.toggleNavigation(collapsed);
        if (openingMobile)
            focusableNavigation()[0]?.focus();
    };
    const closeMobile = () => {
        if (!media.matches || collapsed)
            return;
        store.setNavigationCollapsed(true);
        toggle.focus();
    };
    const onKeydown = (event) => {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "b") {
            event.preventDefault();
            onToggle();
        }
        else if (event.key === "Escape") {
            closeMobile();
        }
        else if (event.key === "Tab" && media.matches && !collapsed) {
            const items = focusableNavigation();
            const first = items[0];
            const last = items[items.length - 1];
            if (first && last && event.target === first && event.shiftKey) {
                event.preventDefault();
                last.focus();
            }
            else if (first && last && event.target === last && !event.shiftKey) {
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
    const onNavClick = (event) => {
        if (media.matches && event.target.closest("a"))
            closeMobile();
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
        destroy: () => {
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
