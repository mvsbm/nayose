import { NAVIGATION_WIDTH_MAX, NAVIGATION_WIDTH_MIN, NAVIGATION_WIDTH_DEFAULT } from "./store.js";
import { ResizableRegion } from "./resizable-region.js";
const MOBILE_QUERY = "(max-width: 820px)";
function labelFor(element) {
    return element.querySelector(".side-label, .activity-bar-label")?.textContent?.trim() ?? "";
}
export function mountNavigation(shell, activity, nav, resizer, toggle, scrim, store) {
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
        activity.querySelectorAll("a[data-activity]").forEach((item) => {
            const label = labelFor(item);
            if (!label)
                return;
            item.dataset.tooltip = label;
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
    const onActivityClick = (event) => {
        const link = event.target.closest("a[data-activity]");
        if (!link || media.matches)
            return;
        const active = link.getAttribute("aria-current") === "page";
        if (active) {
            event.preventDefault();
            store.setNavigationCollapsed(!collapsed);
        }
        else if (collapsed) {
            store.setNavigationCollapsed(false);
        }
    };
    toggle.addEventListener("click", onToggle);
    scrim.addEventListener("click", closeMobile);
    document.addEventListener("keydown", onKeydown);
    nav.addEventListener("click", onNavClick);
    activity.addEventListener("click", onActivityClick);
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
            activity.removeEventListener("click", onActivityClick);
            media.removeEventListener("change", onMediaChange);
            resizable?.dispose();
            unsubscribeState();
            unsubscribeWidth();
            unsubscribeLocale();
        },
    };
}
