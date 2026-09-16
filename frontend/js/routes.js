export const NAVIGATION_GROUPS = [
    { id: "match", labelKey: "navMatch" },
    { id: "review", labelKey: "navReview" },
    { id: "evaluate", labelKey: "navEvaluate" },
    { id: "admin", labelKey: "navAdmin", capability: "admin" },
];
export const ROUTES = [
    { name: "search", nav: "search", path: "#/search", titleKey: "search", group: "match", icon: "search", menu: true },
    { name: "entities", nav: "entities", path: "#/entities", titleKey: "entityExplorer", group: "match", icon: "entities", menu: true },
    { name: "entity", nav: "entities", path: "#/entity", titleKey: "entityExplorer", group: "match", icon: "entities", menu: false },
    { name: "review", nav: "review", path: "#/review", titleKey: "review", group: "review", icon: "review", menu: true },
    { name: "data-entry", nav: "data-entry", path: "#/data-entry", titleKey: "dataEntry", group: "review", icon: "add", menu: true, capability: "dataEntry" },
    { name: "compare", nav: "review", path: "#/compare", titleKey: "compare", group: "review", icon: "compare", menu: false },
    { name: "metrics", nav: "metrics", path: "#/metrics", titleKey: "metrics", group: "evaluate", icon: "metrics", menu: true },
    { name: "confusion", nav: "confusion", path: "#/confusion", titleKey: "confusion", group: "evaluate", icon: "matrix", menu: true },
    { name: "compare-runs", nav: "compare-runs", path: "#/compare-runs", titleKey: "compareRuns", group: "evaluate", icon: "compare", menu: true },
    { name: "admin", nav: "admin", path: "#/admin", titleKey: "dataTrain", group: "admin", icon: "settings", menu: true, capability: "admin" },
];
const routeByName = new Map(ROUTES.map((route) => [route.name, route]));
export function isRouteName(value) {
    return routeByName.has(value);
}
export function routeDescriptor(value) {
    return routeByName.get(value) ?? ROUTES[0];
}
export function routeHash(name, args = []) {
    const route = routeDescriptor(name);
    return [route.path, ...args].join("/");
}
